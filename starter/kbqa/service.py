"""把各个部件接起来：规划、取数、检索、作答。"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from .answerer import Answerer
from .schemas import Answer
from .core import guard
from .core import intent as intent_mod
from .core import routing as routing_mod
from .core.cleaning import build_clean_db
from .core.datatools import DataTools
from .core.index import load_index
from .core.metrics import MetricsEngine
from .core.retriever import Retriever
from .core.store import SessionStore, TraceStore
from .docfacts import DocFacts
from .config import Settings, load_settings
from .entities import Catalog
from .live import LiveEngine
from .llm import LLMClient, LLMError
from .planner import Planner
from .toolspec import TOOL_NAMES, TOOLS
from .trace import Trace

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INT_PARAMS = {"top_k", "limit"}


def _as_json_object(result):
    """工具边界的统一契约：LiveEngine 永远收到可检查、可序列化的 JSON object。

    三种语义必须能区分开（R2-D5 / §26）：

    * 工具成功执行且有结果 → ``dict`` 原样（多数数据工具）；
    * 工具成功执行但结果是个标量（``first_sale_date`` 返回日期字符串）→
      ``{"value": ...}``；没有查到 → ``{"value": null}``（"成功但没有数据"）；
    * 工具执行失败 → ``{"error": "..."}``（由 ``run_tool`` 自己构造）。

    历史缺陷：``first_sale_date`` 直接返回 ``str`` / ``None``，而 ``live.py``
    用 ``"error" not in result`` 判断，于是 ``None`` 触发
    ``TypeError: argument of type 'NoneType' is not iterable``（MT05 真实复现）。
    """
    if isinstance(result, dict):
        return result
    return {"value": result}


class Service:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or load_settings()
        # 会话与 trace 落 SQLite（修 D14：starter 是全局单链表，session_id 被忽略）。
        # 放 var/ 下，rebuild 不动它们——trace 是"回答过程的证据"。
        store = self.settings.var_dir / "app.db"
        self.sessions = SessionStore(store)
        self.traces = TraceStore(store)
        self.rebuild(only_if_missing=True)

    # -- 启动与重建 -------------------------------------------------------------

    def rebuild(self, only_if_missing: bool = False) -> None:
        settings = self.settings
        # R1 / D3：clean.db 存在 ≠ 有效。启动时校验它到底是从哪份数据建出来的
        # （数据指纹 + build_manifest），不匹配就明确要求 rebuild，不静默复用。
        from .core.manifest import (
            data_fingerprint, load_manifest, stale_artifact_error, write_manifest,
        )
        data_fp = data_fingerprint(settings.data_dir)
        if only_if_missing and settings.clean_db.exists():
            manifest = load_manifest(settings.var_dir)
            stored = (manifest or {}).get("data_fingerprint")
            if stored != data_fp:
                raise stale_artifact_error(data_fp, stored)
        elif not only_if_missing or not settings.clean_db.exists():
            build_clean_db(settings.source_db, settings.clean_db)
        # P1：口径引擎是唯一的指标出口，看板、/api/metrics/*、问答的 data_evidence
        # 三处都走它，所以"口径一致"是结构保证，不是纪律。
        self.engine = MetricsEngine(settings.clean_db)
        self.tools = DataTools(self.engine)
        # 索引内容寻址：KB 内容变了 key 就变，缓存自动失效（修 D11）
        self.index = load_index(settings.kb_dir, settings.index_path, rebuild=not only_if_missing)
        self.retriever = Retriever(self.index, settings.today)
        self.catalog = Catalog(
            stores=self.tools.stores(), products=self.tools.products(), aliases=self.index.aliases
        )
        self.data_period = self.tools.data_period()
        self.facts = DocFacts(self.index)
        self.answerer = Answerer(
            self.tools, self.retriever, self.catalog, settings.today, self.data_period, self.facts
        )
        self.planner = Planner(self.catalog, settings.today, self.data_period, self._scout)
        # manifest 记录本次进程实际持有的产物来源与统计（全部动态值，无写死）。
        write_manifest(settings.var_dir, {
            "data_fingerprint": data_fp,
            "kb_fingerprint": self.index.key,
            "valid_sales_rows": self.tools.valid_sales_rows(),
            "kb_docs": len(self.index.docs_meta),
            "kb_chunks": len(self.index.chunks),
        })

    def _scout(self, text: str) -> tuple[float, float]:
        """给一句话探底：它的词在知识库里有多少、检索最高分多少。

        越界判断只看这两个数，不看话题词表：知识库真讲这件事就一定照答。
        """
        result = self.retriever.search(text, top_k=1)
        return self.facts.vocab_coverage(text), (result.hits[0].score if result.hits else 0.0)

    # -- 只读接口 ---------------------------------------------------------------

    def health(self) -> dict:
        report = self.tools.cleaning_report()
        return {
            "status": "ok",
            "llm_mode": self.settings.llm_mode,
            # 修 D5：契约 §1 要的是"实际进入索引的文档数"，不是目录里的文件数。
            # 无 KB 编号的文件（knowledge_base/README.md）不算文档。
            "kb_docs": len(self.index.docs_meta),
            "kb_chunks": len(self.index.chunks),
            "valid_sales_rows": self.tools.valid_sales_rows(),
            "today": self.settings.today.isoformat(),
            "data_period": self.data_period,
            "cleaning_report": report,
            "index_key": self.index.key[:12],
            "kb_warnings": self.index.warnings,
        }

    def metrics_summary(self, start: str, end: str, store_id=None, product_id=None) -> dict:
        return self.tools.query_metrics(start, end, store_id, product_id)

    def metrics_daily(self, start: str, end: str, store_id=None, product_id=None) -> dict:
        return self.tools.daily_metrics(start, end, store_id, product_id)

    def retrieve(self, query: str, top_k: int = 5) -> dict:
        """契约 §4：片段够就恰好给 top_k 条，不够才少给。

        `top_k` 大于索引里的片段总数时按总数封顶——这正是契约允许少给的那种情况。
        """
        wanted = max(1, min(int(top_k or 5), len(self.index.chunks) or 1))
        result = self.retriever.search(query or "", top_k=wanted)
        return {"results": [hit.as_result() for hit in result.hits]}

    # -- 工具执行（live 模式下由模型驱动） ---------------------------------------

    def run_tool(self, name: str, params: dict) -> dict:
        if name not in TOOL_NAMES:
            return {"error": "没有这个工具：%s，可用工具：%s" % (name, "、".join(TOOL_NAMES))}
        schema = next(
            tool["function"]["parameters"] for tool in TOOLS if tool["function"]["name"] == name
        )
        cleaned: dict[str, Any] = {}
        for key, value in (params or {}).items():
            if key not in schema["properties"]:
                continue
            if key in _INT_PARAMS:
                try:
                    cleaned[key] = int(value)
                except (TypeError, ValueError):
                    return {"error": "参数 %s 应该是整数，收到 %r" % (key, value)}
                continue
            if value is None:
                continue
            text = str(value).strip()
            if key.startswith(("start", "end")) or key == "date":
                if not _ISO_DATE.match(text):
                    return {"error": "参数 %s 必须是 YYYY-MM-DD，收到 %r" % (key, value)}
            cleaned[key] = text
        for key in schema.get("required", []):
            if key not in cleaned:
                return {"error": "缺少必填参数 %s" % key}
        try:
            if name == "search_kb":
                return self.retrieve(cleaned["query"], cleaned.get("top_k", 5))
            # R2-D5：标量/None 结果在这里统一成 JSON object，不让 TypeError 冒到 live 循环。
            return _as_json_object(getattr(self.tools, name)(**cleaned))
        except (TypeError, ValueError) as exc:
            return {"error": "工具 %s 执行失败：%s" % (name, exc)}
        except AttributeError:
            # 工具声明与实现不同步时给结构化错误，不要让 /api/chat 变成 500
            return {"error": "没有这个工具：%s，可用工具：%s" % (name, "、".join(TOOL_NAMES))}

    # -- 问答 -------------------------------------------------------------------

    def chat(self, session_id: Optional[str], question: str) -> dict:
        trace = Trace(
            trace_id=self.traces.new_id(self.settings.today.isoformat()),
            question=question or "",
            session_id=session_id,
        )
        answer = self._answer(trace, session_id, question or "")
        payload = {
            "answer": answer.answer,
            "answer_type": answer.answer_type,
            "citations": answer.citations,
            "data_evidence": answer.data_evidence,
            "trace_id": trace.trace_id,
        }
        trace.step("response", {"answer_type": answer.answer_type, "notes": answer.notes})
        self.traces.save(trace)
        return payload

    def _answer(self, trace: Trace, session_id: Optional[str], question: str) -> Answer:
        try:
            if not question.strip():
                return Answer(answer="没有收到问题内容，请再说一次。", answer_type="clarify")

            # ① 安全闸**前置**：写操作意图 / 系统信息套取 / 问了不存在的实体 /
            #    问了系统无从观察的事 → 直接拒答。
            #    措辞只从模板取，绝不拼用户输入（S03 的 text_none 会因此判红）。
            started = time.perf_counter()
            guarded = guard.check(
                question,
                known_stores={s["store_id"] for s in self.tools.stores()},
                known_products={p["product_id"] for p in self.tools.products()},
            )
            trace.step("guard", {"blocked": guarded.blocked, "kind": guarded.kind,
                                 "reason": guarded.reason}, started=started)
            if guarded.blocked:
                return Answer(answer=guarded.answer(self.data_period),
                              answer_type="refusal", notes=[guarded.reason])

            history = self.sessions.history(session_id)
            started = time.perf_counter()
            # **必须把历史传进去**：追问解析（"那 7 月呢"）靠它补全指代，
            # 不传的话 planner 只能判"这个会话里没有上文"→ clarify，
            # 多轮类 9 分全灭。P3 第一版这里漏了 `history`，是测试逼出来的。
            plan = self.planner.plan(question, history)
            trace.step("plan", plan.as_trace(), started=started)

            # ② 区间闸：解析出的时间窗与数据区间**无交集** → 拒答（不带数字）。
            #    注意与 metrics API 相反：API 对空区间照契约返回 0，
            #    chat 要如实说"没有数据"（F01 的 numbers_none_beyond_question）。
            if plan.intent != "refusal":
                gate = routing_mod.off_range(question, plan, self.data_period)
                trace.step("period_gate", {"blocked": bool(gate), "reason": gate or ""})
                if gate:
                    blocked = guard.GuardResult(True, "out_of_range", gate)
                    return Answer(answer=blocked.answer(self.data_period),
                                  answer_type="refusal", notes=[gate])

            # ③ 意图复核：starter 的 planner 把"多久""现在"当时间窗，
            #    会把纯文档问题路由成数据汇总（doc 类 16 分全灭的根因）。
            #
            # **必须用 `plan.standalone`（追问还原后的问题）来分类，不能用原句。**
            # 「那 7 月呢？」原句里既没有指标词也没有时间窗，按原句分类会判成 doc；
            # 还原之后是「7 月 的净营业额是多少？」，才看得出是数据问题。
            # 这个坑是 T01 的红测试逼出来的：不修的话第 2 轮会去引 KB-001。
            #
            # `metric_word` 只在**问句里真的出现了指标词**时才传。
            # `plan.metric` 默认值是 `net_revenue`，无条件传等于告诉分类器
            # "这题问的是净营业额"，于是「储值充值现在的赠送规则是什么？」
            # 被判成 hybrid/price，答出"知识库里没有该商品的调价通知"。
            hinted_metric = intent_mod.find_metric(plan.standalone)
            intent = intent_mod.classify(plan.standalone, metric_word=hinted_metric)
            trace.step("intent", {"kind": intent.kind, "confidence": intent.confidence,
                                  "metric": intent.metric, "hints": intent.hints,
                                  "classified_text": plan.standalone,
                                  "planner_intent": plan.intent, "planner_kind": plan.kind})
            plan = routing_mod.apply_intent(plan, intent, self.data_period)

            answer = self._run_engine(plan, trace, history)
            self.sessions.append(
                session_id,
                {
                    "question": question,
                    "standalone": plan.standalone,
                    "slots": plan.slots,
                    "answer": answer.answer,
                    "answer_type": answer.answer_type,
                },
            )
            return answer
        except Exception as exc:  # noqa: BLE001 - 不管里面出什么事，接口都得给个像样的回答
            trace.error("pipeline", exc)
            return Answer(
                answer="抱歉，我暂时无法回答这个问题。内部出错了，真实原因记在 trace 里。",
                answer_type="refusal",
                notes=["pipeline 异常：%s" % exc],
            )

    def _run_engine(self, plan, trace: Trace, history: list[dict]) -> Answer:
        if not self.settings.live or plan.intent == "refusal":
            started = time.perf_counter()
            answer = self.answerer.answer(plan, trace)
            trace.step("answer_mock", {"answer_type": answer.answer_type}, started=started)
            return answer
        client = LLMClient(
            self.settings.llm_base_url,
            self.settings.llm_api_key,
            self.settings.llm_model,
            timeout=self.settings.llm_timeout,
        )
        engine = LiveEngine(
            client,
            self.answerer,
            self.run_tool,
            self.settings.today.isoformat(),
            self.data_period,
            budget=self.settings.chat_budget,
        )
        started = time.perf_counter()
        try:
            answer = engine.answer(plan, trace, history)
            trace.step("answer_live", {"answer_type": answer.answer_type}, started=started)
            return answer
        except LLMError as exc:
            trace.error("llm", exc)
            trace.step("answer_live_failed", {"kind": exc.kind, "detail": exc.detail}, started=started)
            return Answer(
                answer="模型服务这次没有正常返回（%s），为了不给出没有依据的数字，这个问题先不回答。"
                "可以稍后重试；失败的真实原因记在 trace 里。" % _reason_cn(exc),
                answer_type="refusal",
                notes=["live 模式失败：%s" % exc.detail],
            )

    # -- trace ------------------------------------------------------------------

    def get_trace(self, trace_id: str) -> Optional[dict]:
        return self.traces.get(trace_id)


def _reason_cn(exc: LLMError) -> str:
    mapping = {
        "timeout": "调用超时",
        "http_error": "接口返回错误码 %s" % (exc.status or ""),
        "empty_content": "返回了空回答",
        "length": "输出额度被思考耗尽",
        "content_filter": "被内容过滤拦截",
        "insufficient_system_resource": "服务端资源不足",
        "aborted": "请求被中止",
        "bad_tool_args": "工具参数无法解析",
        "bad_json": "返回的不是合法 JSON",
        "budget": "整体耗时接近时限",
        "transport": "网络异常",
        "tool_loop": "工具调用没有收敛",
    }
    return mapping.get(exc.kind, exc.kind)
