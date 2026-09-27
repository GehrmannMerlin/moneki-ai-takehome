"""规划：一句话进来，决定查数还是查文档、查哪段时间、哪家店。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

from . import entities as E
from .conversation import ConversationState
from .followup import FollowUps
from .timeparse import TimeSpec, parse_time

#: 意图 -> 检索时补充的领域同义词。纯语言层面的扩写，帮助“卖多少钱”命中“售价/调价”。
INTENT_KEYWORDS = {
    "price": ("售价", "价格", "调价", "单价"),
    "target": ("目标", "达标", "方案"),
    "anomaly": ("通知", "公告", "原因", "说明"),
    "payment": ("支付", "收款", "终端"),
    "hours": ("营业时间", "闭店", "延长"),
}


@dataclass
class Plan:
    """一次 turn 的**唯一规划权威**。

    Planner 返回之后的 Plan 就是语义终态：`intent` / `kind` / `window` /
    `store_id` / `product_id` / `metric` / `needs_data` / `needs_docs`
    不再被 Service 二次改写（泛化 R3）。

    `intent` 与 `needs_data` / `needs_docs` 的关系是**派生的、成对的**：

    ==========  ==========  ==========
    intent      needs_data  needs_docs
    ==========  ==========  ==========
    data        True        False
    doc         False       True
    hybrid      True        True
    refusal     False       False
    clarify     False       False
    ==========  ==========  ==========
    """

    question: str
    standalone: str
    search_query: str
    intent: str = "data"
    kind: str = "summary"
    window: Optional[tuple[str, str]] = None
    compare_window: Optional[tuple[str, str]] = None
    as_of: Optional[date] = None
    year: Optional[int] = None
    store_id: Optional[str] = None
    product_id: Optional[str] = None
    metric: str = "net_revenue"
    needs_data: bool = False
    needs_docs: bool = False
    refusal: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    #: 每个槽位是怎么来的：explicit / inherited / default / derived / none。
    #: 工具范围策略靠它区分"用户没提门店"(none) 与"planner 没解析出门店"。
    provenance: dict = field(default_factory=dict)
    continuation: bool = False

    def as_trace(self) -> dict:
        return {
            "question": self.question,
            "standalone_question": self.standalone,
            "search_query": self.search_query,
            "intent": self.intent,
            "kind": self.kind,
            "window": self.window,
            "compare_window": self.compare_window,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "year": self.year,
            "store_id": self.store_id,
            "product_id": self.product_id,
            "metric": self.metric,
            "needs_data": self.needs_data,
            "needs_docs": self.needs_docs,
            "provenance": self.provenance,
            "continuation": self.continuation,
            "refusal": self.refusal,
            "notes": self.notes,
        }

    # -- 不变量 ---------------------------------------------------------------

    def validate(self) -> list[str]:
        """返回违反不变量的地方（空列表 = 自洽）。

        只检查**语义成对**的那几条；缺什么就报什么，不偷偷替你修。
        """
        problems: list[str] = []
        pair = {
            "data": (True, False),
            "doc": (False, True),
            "hybrid": (True, True),
            "refusal": (False, False),
            "clarify": (False, False),
        }
        if self.intent not in pair:
            return ["intent 未知：%r" % self.intent]
        want_data, want_docs = pair[self.intent]
        if self.needs_data != want_data or self.needs_docs != want_docs:
            problems.append(
                "intent=%s 要求 needs_data=%s/needs_docs=%s，实际 %s/%s"
                % (self.intent, want_data, want_docs, self.needs_data, self.needs_docs))
        return problems

    # -- 给 live 模型的结构化范围 ----------------------------------------------

    def as_model_context(self) -> dict:
        """发给 live 模型的**可信应用层规划**（结构化，不是自然语言）。

        它表达"应用已经把问题解析成什么"，模型不该重新猜门店/商品/时间/指标。
        """
        return {
            "intent": self.intent,
            "kind": self.kind,
            "standalone_question": self.standalone,
            "resolved_scope": {
                "window": list(self.window) if self.window else None,
                "compare_window": list(self.compare_window) if self.compare_window else None,
                "as_of": self.as_of.isoformat() if self.as_of else None,
                "store_id": self.store_id,
                "product_id": self.product_id,
                "metric": self.metric,
            },
            "needs": {"data": self.needs_data, "documents": self.needs_docs},
            "search_query": self.search_query,
            "provenance": dict(self.provenance),
            "continuation": self.continuation,
        }


class Planner:
    def __init__(
        self,
        catalog: E.Catalog,
        today: date,
        data_period: dict,
        scout: Optional[Callable[[str], tuple[float, float]]] = None,
    ) -> None:
        self.catalog = catalog
        self.today = today
        self.data_period = data_period
        self.followups = FollowUps(catalog, today)
        #: 给一句话“探个底”：返回（词表覆盖率，检索最高分）。越界判断要靠它。
        self.scout = scout or (lambda text: (1.0, 100.0))

    def plan(
        self, question: str,
        state: Optional[ConversationState | list[dict]] = None,
    ) -> Plan:
        resolution = self.followups.resolve(question, state)
        inherited = resolution.inherited
        plan = Plan(
            question=question,
            standalone=question,
            search_query=question,
            continuation=resolution.continuation,
        )
        if not resolution.continuation and E.looks_like_follow_up(question) and len(question.strip()) <= 12:
            plan.intent, plan.kind = "clarify", "need_context"
            plan.refusal = "这句像是追问，但这个会话里没有上文。请把问题补完整，例如“7 月的净营业额是多少”。"
            return plan
        if resolution.continuation:
            plan.notes.append("这是一句追问，按 ConversationState 补全缺失槽位。")
            plan.slots["conversation_mode"] = "follow_up"
            plan.slots["follow_up_operator"] = resolution.operator
            plan.slots["topic_query"] = resolution.topic_query
        else:
            plan.slots["conversation_mode"] = "new_topic"

        # 越界判断只看当前 utterance；state 只补语义槽位，不生成新的自然语言。
        head = E.head_clause(question)
        reason = E.out_of_scope(question, *self.scout(head))
        if reason:
            plan.intent, plan.kind = "refusal", "out_of_scope"
            plan.notes.append("越界判断：%s" % reason)
            plan.refusal = (
                "这个问题超出了系统能回答的范围：%s。数据库里只有 %s 至 %s 的销售明细，"
                "所以我不能回答。" % (reason, self.data_period["start"], self.data_period["end"])
            )
            return plan

        spec = parse_time(question, self.today)
        self.followups.inherit_time(plan, spec, question, inherited)
        if resolution.operator == "current":
            plan.as_of = self.today
            inherited["historical"] = False
        else:
            plan.as_of = spec.as_of or (
                date.fromisoformat(str(inherited["as_of"])[:10])
                if resolution.continuation and inherited.get("as_of") else self.today
            )
        plan.year = spec.year
        store_id, unknown_store = self.catalog.find_store(question)
        product_id, unknown_product = self.catalog.find_product(question)
        plan.store_id = store_id or (inherited.get("store_id") if not unknown_store else None)
        plan.product_id = product_id or (inherited.get("product_id") if not unknown_product else None)
        #: 记录槽位来源，供 _finalize 生成 provenance。**不重新解析**，
        #: 只记"这个值是问句里写的、还是上一轮继承的、还是压根没有"。
        plan.slots["store_source"] = (
            "explicit" if store_id else ("inherited" if plan.store_id else "none"))
        plan.slots["product_source"] = (
            "explicit" if product_id else ("inherited" if plan.product_id else "none"))

        if unknown_store:
            plan.intent = "refusal"
            plan.kind = "unknown_entity"
            plan.refusal = "数据库里没有 %s 这家门店，现有门店是 %s。" % (
                unknown_store,
                "、".join("%s %s" % (s["store_id"], s["store_name"]) for s in self.catalog.stores),
            )
            return plan
        if unknown_product:
            plan.intent = "refusal"
            plan.kind = "unknown_entity"
            plan.refusal = "商品表里没有 %s 这个商品编号。" % unknown_product
            return plan

        if resolution.ambiguous:
            plan.intent, plan.kind = "clarify", "ambiguous_reference"
            plan.refusal = "这句里的指代不够明确；请说明具体门店或商品。"
            return plan

        self._fill_measure(plan, spec, inherited)
        if plan.slots.get("needs_month"):
            plan.intent, plan.kind = "clarify", "need_month"
            plan.refusal = "只说了“%s 号”，没说是哪个月。数据区间是 %s 至 %s，请把月份补上。" % (
                plan.slots.get("loose_day", ""),
                self.data_period["start"],
                self.data_period["end"],
            )
            return plan
        self._choose_kind(plan, spec, inherited)
        self._check_period(plan, spec)
        self._build_search_query(plan, spec)
        # 区间闸与意图复核**都收进 Planner**（泛化 R3）：返回之后，Plan 的规划字段
        # 不再被 Service / Answerer / LiveEngine 任何一处改写，这就是"一个 turn 只有
        # 一个规划权威"的落地方式。顺序与它们原先在 Service 里的一致，
        # 免得改变既有问题的判定结果。
        gated = plan.intent != "refusal" and self._period_gate(plan, question)
        if not gated:
            self._reconcile_intent(plan)
        self._finalize(plan, spec, inherited)
        recent = list(inherited.get("recent_windows") or [])
        if plan.window and plan.provenance.get("window") != "default":
            recent.append(plan.window)
        deduped: list = []
        for window in recent:
            if window not in deduped:
                deduped.append(window)
        plan.slots.update(
            {
                "recent_windows": deduped[-3:],
                "store_id": plan.store_id,
                "product_id": plan.product_id,
                "metric": plan.metric,
                "window": plan.window,
                "kind": plan.kind,
            }
        )
        return plan

    # -- 细节 -------------------------------------------------------------------

    def _fill_measure(self, plan: Plan, spec: TimeSpec, inherited: dict) -> None:
        text = plan.standalone
        # 指标词从"去掉被否定的排行分句"后的文本里取：出现排行词本身不代表要排行，
        # 所以「不要按销量排名，只看 S91 7 月营业额」的 metric 是 net_revenue 而不是 qty。
        metric = E.find_metric(E.focus_text_for_metric(text))
        plan.metric = metric or inherited.get("metric") or "net_revenue"
        plan.notes.append(
            "识别：指标=%s 门店=%s 商品=%s 时间=%s"
            % (
                metric or "未指定",
                plan.store_id or "全部",
                plan.product_id or "全部",
                spec.labels or "未指定",
            )
        )
        plan.slots["metric_explicit"] = bool(metric)
        plan.slots["time_explicit"] = bool(spec.explicit and spec.windows)
        plan.slots["time_scoped"] = bool(
            spec.explicit or spec.relative_now or inherited.get("window")
        )
        # 问旧版有两种问法：给了具体日期的，按那天生效的版本选（as-of）；
        # 只说“以前/旧口径”没给日期的，才整体解除“已废止”过滤。
        dated = bool(spec.windows) and spec.as_of is not None and spec.as_of < self.today
        plan.slots["historical"] = bool(
            (E.wants_historical(text) and not dated)
            or (inherited.get("historical") and plan.slots.get("conversation_mode") == "follow_up")
        )
        if plan.slots.get("follow_up_operator") == "current":
            plan.slots["historical"] = False
        if plan.slots.get("follow_up_operator") == "historical":
            plan.slots["historical"] = True
        plan.slots["as_of_dated"] = dated
        if plan.slots["historical"]:
            plan.notes.append("问的是过去那一版的规定，已把已废止的文档放回检索范围。")
        elif dated and E.wants_historical(text):
            plan.notes.append("问的是 %s 当时的规定，按 effective_from 选当时生效的版本。" % spec.as_of)

    def _choose_kind(self, plan: Plan, spec: TimeSpec, inherited: dict) -> None:
        """先判断这是“问数字”还是“问规定”，再细分到具体的取数方式。"""
        text = plan.standalone
        windows = list(spec.windows)
        if not windows and spec.relative_now and not spec.whole_period:
            # “今天卖了多少”问的就是今天，数据区间之外的话会被 _check_period 拦住。
            windows = [(self.today.isoformat(), self.today.isoformat())]
        elif spec.whole_period or not windows:
            windows = [(self.data_period["start"], self.data_period["end"])]
        plan.window = windows[0]
        explicit_metric = bool(plan.slots.get("metric_explicit"))
        asks_policy = E.has_any(text, E.POLICY_WORDS)
        # 排行意图的唯一入口：出现排行词 ≠ 要排行。
        # 「不要按销量排名」「反馈最多不代表营业额最高」都在**排除**排行解读。
        asks_rank = E.asks_ranking(text)
        asks_payment = E.has_any(text, E.PAYMENT_WORDS)
        asks_why = E.has_any(text, E.WHY_WORDS)
        asks_target = E.has_any(text, E.TARGET_WORDS)
        asks_price = E.has_any(text, E.PRICE_WORDS)
        asks_amount = E.has_any(text, ("多少", "几", "是多少", "有多少")) or asks_rank

        asks_business = E.has_any(text, E.BUSINESS_WORDS)
        abnormal = E.is_abnormal(text)
        has_subject = bool(plan.store_id or plan.product_id or plan.slots.get("time_explicit"))
        # 只有问句里真的点到了数据库能算的东西，才允许走取数路线。
        operator = plan.slots.get("follow_up_operator")
        may_query = bool(
            explicit_metric
            or asks_payment
            or E.has_any(text, E.SALES_RANK_WORDS)
            or (asks_business and plan.slots.get("time_scoped"))
            or (plan.continuation and (
                plan.store_id or plan.product_id or inherited.get("window")
                or operator in ("actual", "dimension_shift", "reason", "comparison")
            ))
        )
        compares = len(windows) > 1 and E.has_any(text, E.TREND_WORDS)
        if asks_target:
            plan.kind, plan.intent = "target", "hybrid"
        elif asks_price and plan.product_id:
            plan.kind, plan.intent = "price", "hybrid"
        elif (asks_why or abnormal) and has_subject and (
            explicit_metric or abnormal or operator == "reason"
        ):
            # “怎么这么低”“一单都没有”也是在问原因，不必出现“为什么”三个字。
            plan.kind, plan.intent = "anomaly", "hybrid"
        elif compares:
            # 两个时间 + 比较说法：问的就是这两段时间的数字，指标没写就按净营业额。
            plan.window, plan.compare_window = windows[0], windows[1]
            plan.kind, plan.intent = "compare", "data"
        elif asks_policy and not (explicit_metric and plan.slots.get("time_explicit")):
            # 问规定的时候，即使句子里出现了指标名，也该去知识库。
            plan.kind, plan.intent = "doc", "doc"
        elif not may_query:
            plan.kind, plan.intent = "doc", "doc"

        elif len(windows) > 1 and E.has_any(text, E.TREND_WORDS):
            plan.window, plan.compare_window = windows[0], windows[1]
            plan.kind, plan.intent = "compare", "data"
        elif asks_payment:
            plan.kind, plan.intent = "payment", "data"
        elif asks_rank and E.has_any(text, E.CATEGORY_WORDS):
            plan.kind, plan.intent = "category", "data"
        elif E.has_any(text, E.STORE_WORDS) and not plan.store_id:
            # “各门店 7 月营业额分别是多少”没有排名词，但要的就是分店明细。
            plan.kind, plan.intent = "by_store", "data"
        elif asks_rank:
            plan.kind, plan.intent = "top_products", "data"
        elif E.has_any(text, E.DAILY_WORDS):
            plan.kind, plan.intent = "daily", "data"
        else:
            plan.kind, plan.intent = "summary", "data"

        # 路由：问“多少/多久/几”的就是要数字，问“为什么/原因”的就是要说法。
        #
        # **注意这里只在"取数路线已经成立"时才压低意图**（`may_query`）。
        # starter 原来是无条件压的：
        #
        #     if E.has_any(text, ("多少", "多久", "几")):
        #         plan.intent = "data"
        #
        # 于是「储值充值现在的赠送规则是什么？」先被上面判成 doc（第 228 行
        # `asks_policy` 分支），又被这里因为句子里有个"多少"压回 data + summary，
        # 最后答成"知识库里没有该商品的调价通知"。
        # `may_query` 是前面几行刚算出来的"这个问题真的能查库吗"，
        # 用它当门刚好把"问规定的多少"排除掉，同时不动"问数字的多少"。
        if may_query and E.has_any(text, ("多少", "多久", "几")):
            plan.intent = "data"
            # "target" 不在压低名单里：「卖了多少份？达到目标了吗」同时问实际值与目标，
            # 压成 summary 会丢掉达标判定（H02 的 text_any 就是因此红的）。
            if plan.kind in ("doc", "anomaly", "price"):
                plan.kind = "summary"
        elif E.has_any(text, ("为什么", "原因", "怎么回事", "咋回事")):
            plan.intent, plan.kind = "doc", "doc"

        plan.slots["asks_why"] = bool(asks_why or abnormal)
        plan.slots["about_names"] = E.asks_about_names(text)
        # 什么抓手都没有时（没有指标、时间、门店、商品、支付方式、排名，
        # 连一个具体数字或制度词都没有），宁可反问，也不要拿一个不相干的结果糊弄。
        plan.slots["underspecified"] = not (
            explicit_metric
            or plan.slots.get("time_scoped")
            or plan.store_id
            or plan.product_id
            or asks_payment
            or asks_rank
            or asks_policy
            or re.search(r"\d", text)
        )
        if spec.relative_now and not spec.windows and plan.intent != "data":
            # “现在的售价/现在的规定”问的是哪一版生效，不是今天的销量：区间恢复成全区间。
            plan.window = (self.data_period["start"], self.data_period["end"])
        plan.needs_data = plan.kind not in ("doc",)
        plan.needs_docs = plan.intent in ("doc", "hybrid")
        _ = asks_amount
        if spec.first_month:
            plan.notes.append("按“首月”处理：以该商品在数据库里的首个销售日所在自然月为区间。")

    def _check_period(self, plan: Plan, spec: TimeSpec) -> None:
        """问到数据区间之外的时间，如实说没有数据，不猜。"""
        if not plan.needs_data or not plan.window:
            return
        start, end = plan.window
        if end < self.data_period["start"] or start > self.data_period["end"]:
            plan.intent = "refusal"
            plan.kind = "out_of_period"
            plan.refusal = "数据库里只有 %s 至 %s 的销售明细，%s 至 %s 没有任何数据。" % (
                self.data_period["start"],
                self.data_period["end"],
                start,
                end,
            )

    def _period_gate(self, plan: Plan, question: str) -> bool:
        """问句里**显式写出**的月份与数据区间没有交集吗。

        与 `_check_period()` 互补：那个用解析出的**时间窗**判，这个用问句里写出的
        **月份**判。两者都是 Planner 的职责——泛化 R3 把原先 Service 里的第二次
        区间判断收进来，Service 不再有第二个 planning mutation point。
        命中返回 True（已改成区间外拒答），调用方据此跳过意图复核（与旧顺序一致）。
        """
        from .core import guard as guard_mod
        from .core import routing as routing_mod

        reason = routing_mod.off_range(question, plan, self.data_period)
        if not reason:
            return False
        plan.intent, plan.kind = "refusal", "out_of_period"
        plan.refusal = guard_mod.GuardResult(
            True, "out_of_range", reason).answer(self.data_period)
        plan.notes.append("区间闸：%s" % reason)
        return True

    def _reconcile_intent(self, plan: Plan) -> None:
        """把 `core.intent` 的判定并进 Planner —— Planner 是唯一的规划权威。

        这一层原来在 Service 里（`core.routing.apply_intent`）：Planner 返回之后又跑
        一次分类、再改写 `intent`/`kind`，等于第二套规划权威。泛化 R3 收进来之后，
        返回的 Plan 就是最终 Plan，Service 只负责 trace / 排期 / 落历史。

        与旧实现只有一处语义差别：真 hybrid 现在**直接**是 `intent="hybrid"`
        （旧实现把它压成 `intent="data"` + `slots["two_part"]=True`，
        让 Answerer 再补文档那一半）——行为相同，数据模型不再撒谎。
        """
        from .core import intent as intent_mod
        from .core import routing as routing_mod

        if plan.intent == "clarify":
            return
        if plan.intent == "refusal" and plan.kind == "need_context":
            # 追问但没有上文：真的没法答，意图复核不该把它掰成能回答的问题。
            return

        if (
            plan.continuation
            and plan.intent in ("data", "hybrid")
            and not any(word in plan.standalone for word in ("规定", "政策", "流程", "怎么", "如何", "是什么"))
        ):
            # A short continuation such as “那 7 月呢？” is intentionally not
            # self-contained.  The semantic planner has already resolved it from
            # the canonical state; running the standalone classifier here would
            # see only a pronoun and incorrectly turn a data follow-up into doc/doc.
            plan.notes.append("意图复核：语义续问沿用 planner 的数据结论")
            plan.slots["intent_confidence"] = 1.0
            plan.slots["intent_hints"] = ["conversation_state"]
            return

        planner_intent, planner_kind = plan.intent, plan.kind
        hinted = intent_mod.find_metric(E.focus_text_for_metric(plan.standalone))
        intent = intent_mod.classify(plan.standalone, metric_word=hinted)

        if plan.kind == "compare":
            # 追问还原的 standalone 是拼接产物、常常没有指标词，分类器会判成 doc；
            # 但 planner 那边有更硬的证据：两个时间窗 + 涨跌词。保留 planner 的结论。
            plan.notes.append("意图复核：planner 已判定两期对比，保留其结论")
        else:
            year = plan.as_of.year if plan.as_of else self.today.year
            misread = (
                plan.intent == "refusal" and plan.kind == "out_of_period"
                and not routing_mod.explicit_months(plan.standalone, year)
            )
            if intent.kind == "doc":
                plan.intent, plan.kind = "doc", "doc"
                if misread:
                    plan.slots["window_ignored"] = True
            elif intent.kind == "hybrid":
                plan.intent = "hybrid"
                if misread:
                    plan.kind = "price" if intent.price_now else "summary"
                    plan.slots["window_ignored"] = True
                elif intent.price_now and plan.kind in ("summary", "doc"):
                    plan.kind = "price"
                elif plan.kind == "doc":
                    # doc 是"问规定"的形状；数字那一半要按汇总取数。
                    plan.kind = "summary"
            elif misread:
                # 意图复核也说是 data，但 planner 的 refusal 来自"现在"被误当时间窗：
                # 放行成数据问题，让槽位继承去补时间窗。
                plan.intent, plan.kind = "data", "summary"
                plan.slots["window_ignored"] = True

        plan.slots["intent_confidence"] = intent.confidence
        plan.slots["intent_hints"] = intent.hints
        if intent.why:
            plan.slots["asks_why"] = True
        plan.notes.append(
            "意图复核：planner=%s/%s → 最终=%s/%s"
            % (planner_intent, planner_kind, plan.intent, plan.kind))

    def _finalize(self, plan: Plan, spec: TimeSpec, inherited: dict) -> None:
        """Plan 定稿：派生 needs、生成 provenance、校验不变量。

        `needs_data` / `needs_docs` **只由 intent 派生**（唯一权威），
        所以"intent=doc 却 needs_data=True"这种自相矛盾在结构上不可能出现。
        """
        plan.needs_data = plan.intent in ("data", "hybrid")
        plan.needs_docs = plan.intent in ("doc", "hybrid")
        plan.provenance = {
            "store": plan.slots.get("store_source") or "none",
            "product": plan.slots.get("product_source") or "none",
            "window": (
                "derived" if spec.first_month
                else "explicit" if spec.explicit
                else "inherited" if inherited.get("window")
                else "default"
            ),
            "metric": (
                "explicit" if plan.slots.get("metric_explicit")
                else "inherited" if inherited.get("metric")
                else "default"
            ),
        }
        problems = plan.validate()
        if problems:
            # 不静默修：出现这种状态说明代码有 bug，直接炸出来（测试/现场都能立刻看到）。
            raise AssertionError("plan 不变量被破坏：%s / %s" % (problems, plan.as_trace()))

    def _build_search_query(self, plan: Plan, spec: TimeSpec) -> None:
        # “现在/今天/目前”只是判生效日期用的，检索时是纯噪声，去掉。
        text = plan.standalone
        for word in ("现在", "今天", "目前", "当前", "此刻", "的时候"):
            text = text.replace(word, "")
        plan.slots["clean_question"] = text.strip() or plan.standalone
        parts = [plan.slots["clean_question"]]
        if plan.continuation and plan.slots.get("topic_query"):
            parts.append(plan.slots["topic_query"])
        if plan.store_id:
            parts.append("%s %s" % (plan.store_id, self.catalog.store_name(plan.store_id)))
        if plan.product_id:
            parts.append(self.catalog.product_name(plan.product_id))
        for key, words in INTENT_KEYWORDS.items():
            if key == "price" and plan.kind == "price":
                parts.extend(words)
            elif key == "target" and plan.kind == "target":
                parts.extend(words)
            elif key == "anomaly" and plan.kind == "anomaly":
                parts.extend(words)
            elif key == "payment" and plan.kind == "payment":
                parts.extend(words)
            elif key == "hours" and E.has_any(plan.standalone, ("营业到", "几点", "营业时间", "开门", "关门")):
                parts.extend(words)
        plan.search_query = " ".join(parts)


