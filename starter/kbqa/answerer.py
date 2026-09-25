"""组装回答：数字来自工具结果，文档事实来自检索到的原文。"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Optional

from . import render
from .docfacts import DocFacts, carries
from .entities import Catalog, expected_value_kind, focus_kinds
from .hybrid import HybridAnswers
from .planner import Plan
from .core.retriever import Retriever, SearchResult
from .schemas import Answer
from .core.tokenizer import content_tokens, normalise, tokenize

#: 拒答闸门。两个互补的信号：
#: `vocab` —— 问题里的词有多少在整个知识库的词表里出现过（“工资”“下雨”一个都找不到）；
#: `top_score` —— 检索最高分，衡量“有没有哪篇文档确实在谈这件事”。
#: 阈值是拿几十句话试出来的，偏保守，宁可少答也不要硬答。
VOCAB_HARD_GATE = 0.25
VOCAB_SOFT_GATE = 0.45
RETRIEVAL_SOFT_GATE = 12.0
#: 问得太泛时的反问阈值：检索连一个像样的命中都没有。
CLARIFY_SCORE = 8.0
#: 拼给作答用的资料最长多少字，太长了没必要。
MAX_CONTEXT_CHARS = 200


class Answerer(HybridAnswers):
    def __init__(
        self,
        tools,
        retriever: Retriever,
        catalog: Catalog,
        today: date,
        data_period: dict,
        facts: Optional[DocFacts] = None,
    ):
        self.tools = tools
        self.retriever = retriever
        self.catalog = catalog
        self.today = today
        self.data_period = data_period
        self.facts = facts or DocFacts(retriever.index)

    # -- 基础设施 ---------------------------------------------------------------

    def _call(self, evidence: list[dict], name: str, **params) -> dict:
        result = getattr(self.tools, name)(**params)
        trimmed = result
        if name == "daily_metrics" and len(result.get("days", [])) > 31:
            trimmed = {"days": result["days"][:31], "days_total": len(result["days"])}
        evidence.append({"tool": name, "params": params, "result": trimmed})
        return result

    def _scope(self, plan: Plan, window=None) -> str:
        window = window or plan.window
        store = (
            "%s %s" % (plan.store_id, self.catalog.store_name(plan.store_id))
            if plan.store_id
            else "全部门店"
        )
        product = self.catalog.product_name(plan.product_id) if plan.product_id else ""
        return render.scope_label(window, store, product)

    def _doc_block(self, plan: Plan, result: SearchResult, limit: int = 2) -> tuple[str, list[dict], float]:
        """从检索结果里取事实：返回（正文、引用、置信度）。

        **排序以文档级检索分为主、句子级分为次**：
        哪篇文档该被引用，取决于整篇的相关性（P2 修好的检索是可靠的）；
        句子级分只用来决定"引这篇的哪一句"。starter 这里只按句子分升序排、
        取的还是分数最低的那几个——「外卖订单多久内可以申请退款」于是引到了
        KB-061（发票）和 KB-011（储值），真正答对的 KB-013 因为句子分稍低被排在最后。
        """
        candidates = self._candidates(plan, result, require_value=True)
        if not candidates:
            candidates = self._candidates(plan, result, require_value=False)
        want = expected_value_kind(plan.standalone)
        if want and not any(carries(want, item["sentence"]) for item in candidates):
            best_so_far = max((item["score"] for item in candidates), default=1.0) or 1.0
            for item in self._value_rescue(plan, result, want):
                item["score"] = 0.9 * best_so_far
                candidates.append(item)
        # 兜底救回来的候选**优先**：它带着问句要的那种值（金额/数量），
        # 而 `limit=2` 意味着只有前两名能进 citations。
        # 按 doc_score 排的话，「供应商赔了多少钱」的 KB-022（英文邮件，doc_score 最低）
        # 会被挤到第三位、被 `limit` 挡掉——兜底逻辑等于白写。
        # 检索的文档级分只决定"优先看哪篇"，而"哪篇真的答上了问题"由 carries() 判定，
        # 后者是更强的信号。
        candidates.sort(
            key=lambda item: (not item.get("rescued"), -round(item["doc_score"], 3),
                              -round(item["score"], 2), item["effective_from"]))
        lines: list[str] = []
        citations: list[dict] = []
        used_terms: set[str] = set()
        best_score = candidates[0]["raw"] if candidates else 0.0
        query_terms = set(content_tokens(plan.search_query))
        best = candidates[0]["score"] if candidates else 0.0
        for candidate in candidates:
            # 一篇文档引一句就够；第二条引用要来自另一篇、而且确实补充了新信息。
            if any(candidate["doc_id"] == cited["doc_id"] for cited in citations):
                continue
            # 第二条引用必须确实有分量，否则宁可只引一条，别“什么都引一遍”。
            if citations and candidate["score"] < 0.6 * best:
                break
            new_terms = set(tokenize(candidate["sentence"])) & query_terms
            if citations and not candidate.get("rescued") and not (new_terms - used_terms):
                continue
            unit = candidate["unit"]
            # 现场调试演练 2 的教训：引用标题等于没引用——
            # 「S02 的推荐菜是什么」KB 里真没有答案，于是把 H1 标题
            # "# 门店档案：S02 Makai Poke" 原样引了出来，看着像答了其实什么都没说。
            # 这种句子宁可放弃（让位给拒答），也不能当"事实"给出去。
            title = candidate["meta"].get("title", "")
            if title and _skeleton(unit.text) == _skeleton(title):
                continue
            if "reason" in focus_kinds(plan.standalone):
                unit = self.facts.extend_to_cause(unit)
            citation = self.facts.cite(candidate["doc_id"], unit.text)
            if citation is None:
                continue
            used_terms |= new_terms
            lines.append(
                "%s《%s》%s：%s"
                % (
                    candidate["doc_id"],
                    candidate["meta"].get("title", ""),
                    self.facts.version_note(candidate["meta"]),
                    self.facts.render(candidate["doc_id"], unit.text),
                )
            )
            citations.append(citation)
            if len(citations) >= limit:
                break
        if plan.slots.get("historical") and citations:
            superseded = self.retriever.index.docs_meta.get(citations[0]["doc_id"], {})
            successor = superseded.get("superseded_by")
            if successor:
                lines.append(
                    "这一版已经废止，现行规定见 %s《%s》，两者口径不同，报表一律按现行版。"
                    % (successor, self.retriever.index.docs_meta.get(successor, {}).get("title", ""))
                )
        if len(citations) > 1 and want and all(carries(want, c["quote"]) for c in citations):
            dates = [
                self.retriever.index.docs_meta.get(cited["doc_id"], {}).get("effective_from") or ""
                for cited in citations
            ]
            if dates[0] and dates[1] and dates[0] > dates[1]:
                lines.append("两份文档口径不一致时，以生效日期更新的 %s 为准。" % citations[0]["doc_id"])
        return "\n".join(lines)[:MAX_CONTEXT_CHARS], citations, best_score

    def _value_rescue(self, plan: Plan, result: SearchResult, want: str) -> list[dict]:
        """跨语言/低词面重叠时的兜底：按文档级相关性，直接找"带问句要的那种值"的句子。

        C04 的教训：「供应商赔了我们多少钱」的答案在英文邮件 KB-022 里
        （"credit note of CNY 8,600"），它与中文问句**一个词都不重叠**，
        句子级打分得 0、永远浮不出来；但文档级检索明明找到了它。
        问句要的是"钱"这个形状时，直接到相关文档里找带金额的句子。
        """
        for hit in self.answerable_hits(plan, result):
            meta = self.retriever.index.docs_meta.get(hit.doc_id, {})
            if meta.get("estimates_only"):
                continue  # KB-001 §5.2：周报/纪要里的数字是估算，不能当答案
            for unit in self.facts.units(hit.doc_id):
                if len(unit.text) < 8 and unit.kind != "table":
                    continue
                if not carries(want, unit.text):
                    continue
                return [
                    {
                        "score": 0.0,  # 由调用方按当前 best 折算
                        "raw": 0.0,
                        "doc_score": float(hit.score),
                        "doc_id": hit.doc_id,
                        "meta": meta,
                        "unit": unit,
                        "sentence": unit.text,
                        "effective_from": meta.get("effective_from") or "",
                        "rescued": True,
                    }
                ]
        return []

    def _candidates(self, plan: Plan, result: SearchResult, require_value: bool) -> list[dict]:
        """把各文档的候选句放在一起比较，按检索分数的相对高低加权。"""
        candidates: list[dict] = []
        top_score = max((hit.score for hit in result.hits), default=0.0) or 1.0
        for hit in self.answerable_hits(plan, result):
            meta = self.retriever.index.docs_meta.get(hit.doc_id, {})
            ranked = self.facts.rank(
                plan.search_query, hit.doc_id, limit=3, require_value=require_value
            )
            # KB-001 §5.2：周报与纪要里的**数字**是估算，问经营数字时才让位；
            # 问决议、原因、日期时，纪要就是权威出处。
            numeric = bool(plan.slots.get("metric_explicit")) and plan.needs_data
            estimate_penalty = 0.5 if (numeric and meta.get("estimates_only")) else 1.0
            if plan.slots.get("historical"):
                # 问的就是旧版：被取代的那一版才是答案，现行版让位。
                estimate_penalty *= 1.5 if meta.get("superseded_by") else 0.6
            for score, unit in ranked:
                candidates.append(
                    {
                        "score": score * (max(hit.score, 0.0) / top_score) ** 0.5 * estimate_penalty,
                        "raw": score,
                        #: 文档级检索分：决定"该引哪几篇"，句子分只决定"引这篇的哪一句"
                        "doc_score": float(hit.score),
                        "doc_id": hit.doc_id,
                        "meta": meta,
                        "unit": unit,
                        "sentence": unit.text,
                        "effective_from": meta.get("effective_from") or "",
                    }
                )
        return candidates

    def answerable_hits(self, plan: Plan, result: SearchResult, limit: int = 5) -> list:
        """能拿来作答的命中。

        别名词典是用来把“味噌拉面”归一成“味增拉面”的工具书，不是事实出处：
        问过敏原、问价格、问停没停售，答案都不在词典里。只有问“这两个名字是不是
        一回事”时，它才是答案。
        """
        dictionary = self.retriever.index.aliases.source_doc
        hits = [
            hit
            for hit in result.ranked
            if plan.slots.get("about_names") or hit.doc_id != dictionary
        ]
        return hits[:limit]

    def _search(self, plan: Plan, window=None, trace=None) -> SearchResult:
        started = time.perf_counter()
        result = self.retriever.search(
            plan.search_query,
            top_k=5,
            as_of=plan.as_of,
            store_id=plan.store_id,
            year=plan.year,
            window=window,
            numeric=bool(plan.slots.get("metric_explicit")) and plan.needs_data,
            historical=bool(plan.slots.get("historical")),
        )
        if trace is not None:
            trace.step("search", result.as_trace(), started=started)
        return result

    # -- 入口 -------------------------------------------------------------------

    def answer(self, plan: Plan, trace=None) -> Answer:
        if plan.intent in ("refusal", "clarify"):
            return Answer(answer=plan.refusal or "无法回答这个问题。", answer_type=plan.intent)
        if plan.kind in ("target", "price", "anomaly"):
            return getattr(self, "_answer_%s" % plan.kind)(plan, trace)
        if plan.intent == "doc":
            answer = self._answer_doc(plan, trace)
            return self._merge_data_side(plan, answer, trace)
        answer = self._answer_data(plan, trace)
        return self._merge_doc_side(plan, answer, trace)

    def _merge_doc_side(self, plan: Plan, answer: Answer, trace=None) -> Answer:
        """一句话里既问了数字又问了规定时，把文档那一半也答上。"""
        if not plan.slots.get("two_part") or answer.answer_type != "data":
            return answer
        if plan.slots.get("cause_not_found"):
            # H06（cite_max=0）：`_cause_block` 已经判定没有文档能解释这段异常，
            # 这里再把检索到的相邻文档引上，等于拿不相关文档硬凑一个原因。
            return answer
        body, citations, confidence = self._doc_block(plan, self._search(plan, trace=trace))
        if not citations:
            return answer
        answer.answer = answer.answer + "\n" + body
        answer.citations = citations
        answer.answer_type = "hybrid"
        return answer

    def _merge_data_side(self, plan: Plan, answer: Answer, trace=None) -> Answer:
        """文档问题里还夹着一个能查的数字时，把数字也给出来。"""
        if not plan.slots.get("two_part") or answer.answer_type != "doc" or not plan.window:
            return answer
        evidence: list[dict] = []
        result = self._call(
            evidence,
            "query_metrics",
            start=plan.window[0],
            end=plan.window[1],
            store_id=plan.store_id,
            product_id=plan.product_id,
        )
        answer.answer = render.describe_metrics(result, self._scope(plan), plan.metric) + "\n" + answer.answer
        answer.data_evidence = evidence
        answer.answer_type = "hybrid"
        return answer

    # -- 纯数据 -----------------------------------------------------------------

    def _answer_data(self, plan: Plan, trace=None) -> Answer:
        if plan.slots.get("underspecified"):
            return Answer(
                answer="没太听明白要看哪个指标、哪段时间或者哪家店。"
                "可以说得具体一点，例如“7 月 S02 的净营业额是多少”。",
                answer_type="clarify",
            )
        evidence: list[dict] = []
        start, end = plan.window
        scope = self._scope(plan)
        if plan.kind == "compare":
            first, second = plan.window, plan.compare_window
            result = self._call(
                evidence,
                "compare_periods",
                start_a=first[0],
                end_a=first[1],
                start_b=second[0],
                end_b=second[1],
                store_id=plan.store_id,
                product_id=plan.product_id,
            )
            text = render.describe_compare(
                result, plan.metric, self._scope(plan, first), self._scope(plan, second)
            )
        elif plan.kind == "payment":
            result = self._call(evidence, "payment_mix", start=start, end=end, store_id=plan.store_id)
            focus = next(
                (name for name in result.get("payments", {}) if name in plan.standalone), ""
            )
            text = render.describe_payment(result, scope, focus)
        elif plan.kind == "top_products":
            result = self._call(
                evidence, "top_products", start=start, end=end, store_id=plan.store_id, limit=10
            )
            text = render.describe_top(result, scope)
        elif plan.kind == "by_store":
            result = self._call(evidence, "by_store", start=start, end=end, product_id=plan.product_id)
            text = render.describe_by_store(result, scope)
        elif plan.kind == "category":
            result = self._call(evidence, "by_store_category", start=start, end=end)
            text = render.describe_category(result, scope)
        elif plan.kind == "daily":
            result = self._call(
                evidence,
                "daily_metrics",
                start=start,
                end=end,
                store_id=plan.store_id,
                product_id=plan.product_id,
            )
            text = render.describe_daily(result, scope)
        else:
            result = self._call(
                evidence,
                "query_metrics",
                start=start,
                end=end,
                store_id=plan.store_id,
                product_id=plan.product_id,
            )
            text = render.describe_metrics(result, scope, plan.metric)
        if plan.slots.get("asks_why"):
            cause, citations = self._cause_block(plan, start, end, trace)
            if citations:
                return Answer(
                    answer=text + cause,
                    answer_type="hybrid",
                    citations=citations,
                    data_evidence=evidence,
                )
            # 没有文档能解释：记下这个事实，`_merge_doc_side` 不得再拿相邻文档凑引用。
            plan.slots["cause_not_found"] = True
            text += "知识库里没有找到能解释这段时间的通知或说明，所以只能给出数字本身。"
        return Answer(answer=text, answer_type="data", data_evidence=evidence)

    # -- 纯文档 -----------------------------------------------------------------

    def _context(self, result: SearchResult, limit: int = 900) -> str:
        """把命中的那篇文档**相关的那几段**拼进来——不是整篇。

        starter 是 `"\\n".join(chunk.text for chunk in chunks_of(hit.doc_id))`，
        等于把整篇文档塞进 answer：实测 C05=1407、C06=2748、S03=2709 字，
        全部超过契约 §5 的 1200 字硬上限。

        现在只取命中 chunk 本身，并且**逐字截取**（quote 校验要和原文一致，
        所以不能改写、不能拼接），超长就从句末边界往回截。
        """
        blocks: list[str] = []
        budget = limit
        for hit in result.ranked[:2]:
            for chunk in self.retriever.index.chunks_of(hit.doc_id):
                if chunk.text != hit.text:
                    continue                             # 只要命中的那一段
                text = chunk.text.strip()
                if len(text) > budget:
                    text = _truncate_at_sentence(text, budget)
                blocks.append(text)
                budget -= len(text)
                break
            if budget <= 120:
                break
        return ("\n".join(blocks) + "\n") if blocks else ""

    def _should_refuse(self, plan: Plan, confidence: float, top_score: float) -> Optional[str]:
        """三个信号一起判断“知识库里到底有没有这件事”。"""
        vocab = self.facts.vocab_coverage(plan.slots.get("clean_question") or plan.standalone)
        if vocab < VOCAB_HARD_GATE:
            return "问题里的关键词在知识库里一个都找不到（词表覆盖率 %.2f）" % vocab
        if vocab < VOCAB_SOFT_GATE and top_score < RETRIEVAL_SOFT_GATE:
            return "词表覆盖率 %.2f、检索最高分 %.1f 都偏低（最佳句子覆盖率 %.2f）" % (
                vocab,
                top_score,
                confidence,
            )
        return None

    def _answer_doc(self, plan: Plan, trace=None) -> Answer:
        result = self._search(plan, trace=trace)
        body, citations, confidence = self._doc_block(plan, result)
        top_score = result.ranked[0].score if result.ranked else 0.0
        reason = self._should_refuse(plan, confidence, top_score)
        if not citations or reason:
            return Answer(
                answer="知识库里没有找到能回答这个问题的内容，我不能编。"
                "可以换个说法，或者确认这件事是否有成文规定。",
                answer_type="refusal",
                notes=[reason or "没有可以逐字引用的原文"],
            )
        if plan.slots.get("underspecified") and top_score < CLARIFY_SCORE:
            # 问得太泛、检索也没有明显命中：宁可反问，也不要拿一段不相干的原文充数。
            return Answer(
                answer="这个问题我没抓住重点：是想查某段时间的经营数字，还是想看某条规定？"
                "补一个指标、时间或者门店，我就能答。",
                answer_type="clarify",
                notes=["检索最高分 %.1f，且问题里没有指标、时间或门店" % top_score],
            )
        # **引用正文就是答案**：不要在前面再拼一遍整篇文档。
        # starter 拼了（`self._context(result) + body`），于是 answer 变成
        # "整篇原文 + 结论"，超 1200 字上限的题全部判红（D17）。
        return Answer(answer=body, answer_type="doc", citations=citations)


def _skeleton(text: str) -> str:
    """去掉所有标点/符号/空白后的骨架，用于"这句话是不是就是标题"的判断。

    标题写法不统一（"门店档案 S02 Makai Poke" vs 正文 H1 "# 门店档案：S02 Makai Poke"），
    逐字比会漏；只留中文字符与字母数字再比，才抓得住"换了个写法的同一个标题"。
    """
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text or "")


def _truncate_at_sentence(text: str, limit: int) -> str:
    """从文末的句号/换行处往回截，尽量不留半句话。"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for mark in ("。", "；", "\n", "！", "？"):
        position = window.rfind(mark)
        if position >= limit // 2:
            return window[: position + 1]
    return window
