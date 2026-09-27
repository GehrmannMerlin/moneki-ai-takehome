"""把各个部件接起来：规划、取数、检索、作答。"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from .answerer import Answerer
from .schemas import Answer
from .authority import authority_of, numeric_authority_of
from .core import guard
from .core.cleaning import build_clean_db
from .core.datatools import DataTools
from .core.index import load_index
from .core.metrics import MetricsEngine
from .core.retriever import Retriever
from .core.sanitize import sanitize
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

#: `search_kb` 的**结构化范围**参数：由 LiveEngine 从 canonical Plan 填充，
#: 模型不需要、也不应该自己设置——它们不在工具 schema 里。放在这里是为了
#: `run_tool` 能从 params 里认出它们，同时不污染给模型看的工具定义。
_KNOWLEDGE_SCOPE_KEYS = ("as_of", "store_id", "historical", "year", "window", "numeric")


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


def _as_date(value):
    """把 JSON 边界来的日期（`"2026-03-31"`）还原成 `date`；已是 date 就原样返回。"""
    from datetime import date as _date_cls

    if value is None or isinstance(value, _date_cls):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        year, month, day = (int(part) for part in text[:10].split("-"))
        return _date_cls(year, month, day)
    except (ValueError, TypeError):
        return None


def _as_window(value):
    """把 `["2026-03-01", "2026-03-31"]` 还原成 Retriever 要的 `(start, end)`。"""
    if not value:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        start, end = str(value[0]).strip(), str(value[1]).strip()
        if start and end:
            return (start, end)
    return None


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

        **这是面向人/evaluator 的公开接口**：返回**原文**（含可能存在的攻击句），
        引用逐字校验对的就是它。live 模型走的是下面的 `retrieve_for_model`。
        """
        wanted = max(1, min(int(top_k or 5), len(self.index.chunks) or 1))
        result = self.retriever.search(query or "", top_k=wanted)
        return {"results": [hit.as_result() for hit in result.hits]}

    def retrieve_for_model(self, query: str, top_k: int = 5, as_of=None,
                           store_id=None, year=None, window=None, numeric=False,
                           historical=None) -> dict:
        """live 模型专用的检索投影：**安全文本 + 溯源元数据**，绝不回原始来源。

        与公开 `retrieve()` 的三点区别（R4 信任边界）：

        * 只给**真正命中**的片段（`ranked`）——凑数的 padded 片段不是证据；
        * `text` 是 sanitize 之后的安全文本；原始来源放在 `source_text` /
          `source_dropped`，它们会被 `FactLedger.model_projection` 摘掉，
          **永不进模型上下文**（citation 与 trace 仍用原始来源）；
        * 每条附带来源权威与版本元数据，并回带本次检索的结构化 scope 与过滤明细。

        `as_of` / `window` 允许是字符串/列表（从 JSON 边界来），这里统一还原成
        `date` / `tuple` 再交给 Retriever。
        """
        scope = {
            "as_of": _as_date(as_of),
            "store_id": store_id,
            "year": int(year) if year not in (None, "") else None,
            "window": _as_window(window),
            "numeric": bool(numeric),
            "historical": historical,
        }
        result = self.retriever.search(
            query or "", top_k=max(1, int(top_k or 5)),
            as_of=scope["as_of"], store_id=scope["store_id"], year=scope["year"],
            window=scope["window"], numeric=scope["numeric"],
            historical=scope["historical"],
        )
        items = []
        for hit in result.ranked:
            meta = hit.meta or {}
            safe_text, dropped = sanitize(hit.text)
            items.append({
                "doc_id": hit.doc_id,
                "chunk_id": hit.chunk_id,
                "score": round(hit.score, 4),
                "text": safe_text,
                "authority": authority_of(meta),
                "numeric_authority": numeric_authority_of(meta),
                "effective_from": meta.get("effective_from"),
                "status": meta.get("status") or meta.get("state"),
                "sanitized": bool(dropped),
                "dropped_instructions": len(dropped),
                # 原始来源：仅供应用内部做 citation / trace，绝不给模型。
                "source_text": hit.text,
                "source_dropped": dropped,
            })
        return {
            "results": items,
            "scope": {
                "as_of": as_of if isinstance(as_of, str) else scope["as_of"].isoformat()
                if scope["as_of"] else None,
                "store_id": scope["store_id"],
                "historical": historical,
            },
            "filtered": result.filtered,
        }

    # -- 工具执行（live 模式下由模型驱动） ---------------------------------------

    def run_tool(self, name: str, params: dict) -> dict:
        if name not in TOOL_NAMES:
            return {"error": "没有这个工具：%s，可用工具：%s" % (name, "、".join(TOOL_NAMES))}
        if name == "search_kb":
            return self._run_search_kb(params or {})
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
            # R2-D5：标量/None 结果在这里统一成 JSON object，不让 TypeError 冒到 live 循环。
            return _as_json_object(getattr(self.tools, name)(**cleaned))
        except (TypeError, ValueError) as exc:
            return {"error": "工具 %s 执行失败：%s" % (name, exc)}
        except AttributeError:
            # 工具声明与实现不同步时给结构化错误，不要让 /api/chat 变成 500
            return {"error": "没有这个工具：%s，可用工具：%s" % (name, "、".join(TOOL_NAMES))}

    def _run_search_kb(self, params: dict) -> dict:
        """`search_kb` 的执行：query/top_k 来自模型，范围来自 Plan（应用填充）。

        范围参数（as_of/store/historical/window/year）不在给模型看的工具 schema 里，
        由 LiveEngine 从 canonical Plan 注入；模型就算硬塞也会被覆盖（见 LiveEngine）。
        """
        query = str(params.get("query") or "").strip()
        if not query:
            return {"error": "缺少必填参数 query"}
        try:
            top_k = int(params.get("top_k", 5))
        except (TypeError, ValueError):
            return {"error": "参数 top_k 应该是整数，收到 %r" % params.get("top_k")}
        scope = {key: params.get(key) for key in _KNOWLEDGE_SCOPE_KEYS
                 if params.get(key) is not None}
        try:
            return self.retrieve_for_model(query, top_k=top_k, **scope)
        except (TypeError, ValueError) as exc:
            return {"error": "工具 search_kb 执行失败：%s" % exc}

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
            #
            # 返回的 Plan 就是本 turn 的**唯一规划权威**（泛化 R3）：区间闸与意图复核
            # 都已经在 Planner 内部完成，Service 不再重新分类、也不再改 Plan 的任何
            # 规划字段。Service 只做：trace → 选引擎 → 落历史。
            plan = self.planner.plan(question, history)
            trace.step("plan", plan.as_trace(), started=started)

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
