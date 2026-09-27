"""Generalization Round 3 — Structured Planner Authority 的红测试。

原则（任务书 §54–§76、§86）：

* 先对**当前** production 代码写"正确行为"的断言，让它红；
* 断言的是**机制**，不是公开题库的金标——门店/商品/日期/别名一律用合成的
  （S91 / S92 / P91 / P92 / 北极星店 / 星云茶 / 夜航茶），不依赖公开的
  S02 / P06 / 牛肉poke；
* 不碰仓库真实 ``data/`` 与 ``knowledge_base/``；
* 覆盖的候选缺陷（编号以 DEBUG_LOG 实际确认为准）：

  - 生产 Service 在 Planner 返回后又跑一次意图分类并改写 Plan（R3-D1）；
  - Plan 的 intent/kind 与 needs_data/needs_docs 可能自相矛盾（R3-D2）；
  - 真 hybrid 被塞进 ``slots['two_part']``，intent 撒谎说 data（R3-D3）；
  - live 模型收不到结构化 Plan，只能重新猜门店/商品/时间/指标（R3-D4）；
  - 模型的工具调用可以偏离 Planner 已解析的 scope（R3-D5）；
  - RANK_WORDS 不理解否定，"不要排名"被当成排行（R3-D6）；
  - slot 没有统一的 explicit/inherited/default provenance（R3-D7）；
  - 门店/商品编号紧跟中文时解析不出（``\\b`` 边界）；
  - "X 月" 紧跟在编号后时月份被吃掉（"S91 7 月" → "917月"）。
"""

from __future__ import annotations

import copy
import json
from datetime import date

import pytest

TODAY = date(2026, 9, 1)
DATA_PERIOD = {"start": "2026-05-01", "end": "2026-08-31"}


# --------------------------------------------------------------------------- 夹具


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    """真实的 Service（只读；不进仓库 var/）。取数一律用注入的假工具。"""
    import os

    var = tmp_path_factory.mktemp("r3-var")
    os.environ["VAR_DIR"] = str(var)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)
    from kbqa.config import load_settings
    from kbqa.service import Service

    return Service(load_settings())


def _synthetic_catalog():
    """完全合成的目录：两家店、两件商品、一条别名（夜航茶 → 星云茶）。"""
    from kbqa.core.aliases import AliasTable
    from kbqa.entities import Catalog

    aliases = AliasTable(
        canonical_of={"夜航茶": "星云茶"},
        aliases_of={"星云茶": ["星云茶", "夜航茶"]},
    )
    return Catalog(
        stores=[
            {"store_id": "S91", "store_name": "北极星店"},
            {"store_id": "S92", "store_name": "晨星小馆"},
        ],
        products=[
            {"product_id": "P91", "product_name": "星云茶", "unit_price": 12.5},
            {"product_id": "P92", "product_name": "灯塔堡", "unit_price": 25.0},
        ],
        aliases=aliases,
    )


@pytest.fixture()
def synth_planner():
    """合成 Planner：不依赖公开数据集里的任何实体。"""
    from kbqa.planner import Planner

    return Planner(_synthetic_catalog(), TODAY, DATA_PERIOD)


# --------------------------------------------------------------------------- live 夹具


def _content(text: str):
    from kbqa.llm import LLMReply

    return LLMReply(
        message={"role": "assistant", "content": text},
        finish_reason="stop",
        content=text,
        tool_calls=[],
        elapsed=0.01,
    )


def _tool(name: str, args: dict, seq: int = 0):
    from kbqa.llm import LLMReply

    call = {
        "id": "call-%d" % seq,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }
    return LLMReply(
        message={"role": "assistant", "content": None, "tool_calls": [call]},
        finish_reason="tool_calls",
        content="",
        tool_calls=[call],
        elapsed=0.01,
    )


class ScriptedClient:
    """按剧本回话的假模型，逐次记录 messages。"""

    def __init__(self, replies, forced=None):
        self.replies = list(replies)
        self.forced = forced
        self.calls: list[dict] = []

    def chat_with_retry(self, messages, tools=None, budget=None, on_call=None):
        self.calls.append({"tools": tools, "messages": copy.deepcopy(messages)})
        if tools is None and self.forced is not None:
            return self.forced
        if not self.replies:
            raise AssertionError("假模型的剧本演完了还被调用（多了一次 completion）")
        return self.replies.pop(0)


class ToolSpy:
    """记录每次 run_tool 的 (name, params)，返回可检查的 JSON object。"""

    def __init__(self, result=None):
        self.calls: list[tuple[str, dict]] = []
        self.result = result if result is not None else {"ok": True}

    def __call__(self, name: str, params: dict):
        self.calls.append((name, copy.deepcopy(params)))
        return self.result


def _engine(service, client, run_tool):
    from kbqa.live import LiveEngine

    return LiveEngine(
        client,
        service.answerer,
        run_tool,
        service.settings.today.isoformat(),
        service.data_period,
        budget=60.0,
    )


def _trace(question: str):
    from kbqa.trace import Trace

    return Trace(trace_id="t-r3-test", question=question, session_id="r3")


def _steps(trace) -> list[dict]:
    return trace.as_dict()["steps"]


def _step(trace, name: str) -> list[dict]:
    return [s for s in _steps(trace) if s["step"] == name]


def _json_block(text: str) -> dict:
    """从文本里抠出第一个 JSON object（系统消息里的结构化 plan context）。"""
    start = text.find("{")
    assert start >= 0, "文本里没有 JSON object"
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def _system_text(client) -> str:
    first = client.calls[0]["messages"]
    return "\n".join(m.get("content") or "" for m in first if m.get("role") == "system")


# ======================================================= RED-01 Canonical 不变量


def test_plan_invariants_hold_for_every_shape(service):
    """intent 与 needs_data/needs_docs 必须成对一致（任务书 §10）。"""
    cases = [
        ("S02 6 月净营业额是多少？", ("data", True, False)),
        ("外卖订单多久内可以申请退款？", ("doc", False, True)),
        ("7 月 S02 的营业额为什么比别的周低这么多？", ("hybrid", True, True)),
        ("9 月的营业额是多少？", ("refusal", False, False)),
        ("那 7 月呢？", ("clarify", False, False)),
    ]
    for question, want in cases:
        plan = service.planner.plan(question)
        got = (plan.intent, plan.needs_data, plan.needs_docs)
        assert got == want, "「%s」→ %s，期望 %s" % (question, got, want)


def test_plan_validate_reports_no_violation(service):
    """Planner 返回的 Plan 必须自洽（intent ↔ needs）。"""
    for question in [
        "S02 6 月净营业额是多少？",
        "外卖订单多久内可以申请退款？",
        "7 月 S02 的营业额为什么比别的周低这么多？",
        "9 月的营业额是多少？",
    ]:
        plan = service.planner.plan(question)
        assert plan.validate() == [], "「%s」不变量被破坏：%s" % (question, plan.validate())


def test_plan_validate_flags_inconsistent_state():
    """validate() 必须能识别矛盾状态，而不是无脑返回空。"""
    from kbqa.planner import Plan

    bad = Plan(question="x", standalone="x", search_query="x")
    bad.intent, bad.kind = "doc", "doc"
    bad.needs_data, bad.needs_docs = True, False
    assert bad.validate(), "doc 意图却 needs_data=True，validate 应当报错"


# ============================================== RED-02 Planner 之后不得二次分类


def test_service_does_not_reclassify_after_planner(service, monkeypatch):
    """Planner 返回后，整条链路不得再调用意图分类改写 Plan（任务书 §56）。"""
    from kbqa.core import intent as intent_mod

    real = intent_mod.classify
    seen: list[str] = []

    def spy(text, **kwargs):
        seen.append(text)
        return real(text, **kwargs)

    monkeypatch.setattr(intent_mod, "classify", spy)

    question = "S02 6 月净营业额是多少？"
    service.planner.plan(question)
    planned = len(seen)

    seen.clear()
    service.chat("r3-authority", question)
    assert len(seen) == planned, (
        "Service 在 Planner 之外又做了一次意图分类（%d 次 > 规划阶段的 %d 次）"
        % (len(seen), planned))


def test_trace_exposes_single_canonical_plan(service):
    """trace 里只有一份最终 Plan；不得出现"复核改变了 planner"的二次规划步骤。"""
    payload = service.chat("r3-trace", "7 月 S02 的营业额为什么比别的周低这么多？")
    trace = service.get_trace(payload["trace_id"])
    plans = [s for s in trace["steps"] if s["step"] == "plan"]
    assert len(plans) == 1, "plan 步骤应当只有一个，实际 %d" % len(plans)
    detail = plans[0]["detail"]
    for key in ("intent", "needs_data", "needs_docs", "provenance"):
        assert key in detail, "canonical plan 缺少 %s：%s" % (key, sorted(detail))
    for step in trace["steps"]:
        blob = json.dumps(step.get("detail"), ensure_ascii=False, default=str)
        assert "planner_intent" not in blob, (
            "trace 里仍记录了「二次规划」（%s）：%s" % (step["step"], blob[:200]))


# ================================================= RED-03 hybrid 必须是真 hybrid


def test_hybrid_is_really_hybrid(synth_planner):
    """真 hybrid 直接表达为 intent=hybrid，不靠 slots['two_part'] 偷改语义。"""
    plan = synth_planner.plan("7 月 S91 的营业额为什么比别的周低这么多？")
    assert plan.intent == "hybrid", "实际 intent=%s" % plan.intent
    assert plan.needs_data and plan.needs_docs
    assert not plan.slots.get("two_part"), (
        "仍依赖 two_part 表达 hybrid：slots=%s" % plan.slots.get("two_part"))


def test_two_part_no_longer_drives_answerer(synth_planner):
    """two_part 不再是核心决策依据：hybrid 的 Answerer 分派只看 canonical intent。"""
    import inspect

    from kbqa import answerer as answerer_mod
    from kbqa import routing as routing_mod  # noqa: F401  (旧 helper 可能保留)

    source = inspect.getsource(answerer_mod)
    assert "two_part" not in source, "Answerer 仍在消费 two_part"


# ============================================ RED-04 live 收到结构化 plan context


def test_live_receives_structured_plan_context(service):
    """live 的 system 消息里必须带机器可读的 resolved plan。"""
    question = "7 月 S02 的营业额为什么比别的周低这么多？"
    plans = service.planner.plan(question)
    client = ScriptedClient([_content("已按计划完成。")])
    spy = ToolSpy()
    _engine(service, client, spy).answer(plans, _trace(question), [])

    system = _system_text(client)
    ctx = _json_block(system)
    scope = ctx.get("resolved_scope") or {}
    assert ctx.get("intent") == plans.intent, "plan context 的 intent 与 canonical plan 不一致"
    assert "store_id" in scope and scope["store_id"] == "S02"
    assert "product_id" in scope
    assert "window" in scope and scope["window"] == list(plans.window)
    assert "metric" in scope
    assert ctx.get("needs", {}).get("data") is True
    assert ctx.get("needs", {}).get("documents") is True


def test_plan_context_is_structural_not_natural_language(service):
    """structured context 是数据，不是又一段自然语言提示。"""
    question = "S02 6 月净营业额是多少？"
    plan = service.planner.plan(question)
    ctx = plan.as_model_context()
    assert ctx["resolved_scope"]["store_id"] == "S02"
    assert ctx["resolved_scope"]["metric"] == "net_revenue"
    assert ctx["needs"] == {"data": True, "documents": False}
    assert ctx["provenance"]["store"] == "explicit"


# =============================================== RED-05 缺失参数按 Plan 补齐


def test_missing_scope_is_filled_from_plan(synth_planner):
    """模型省略门店/商品时，按 Plan 补齐（任务书 §25）。"""
    from kbqa.toolpolicy import PlanToolPolicy

    plan = synth_planner.plan("S91 7 月 12 日 P91 的净营业额是多少？")
    assert plan.store_id == "S91" and plan.product_id == "P91"
    policy = PlanToolPolicy(plan)
    effective, meta = policy.apply("query_metrics", {
        "start": plan.window[0], "end": plan.window[1]})
    assert meta["status"] in ("filled", "ok")
    assert effective["store_id"] == "S91", effective
    assert effective["product_id"] == "P91", effective


def test_live_autofills_scope_and_receipt_matches_plan(synth_planner, service):
    """端到端：模型只给日期，实际执行的 tool params 仍带 canonical 门店/商品。"""
    plan = synth_planner.plan("S91 7 月 12 日 P91 的净营业额是多少？")
    day = plan.window[0]
    client = ScriptedClient([
        _tool("query_metrics", {"start": day, "end": day}, seq=1),
        _content("已按计划完成。"),
    ])
    spy = ToolSpy({"net_revenue": 1.0})
    _engine(service, client, spy).answer(plan, _trace("q"), [])

    assert spy.calls, "工具没有被调用"
    name, params = spy.calls[0]
    assert name == "query_metrics"
    assert params.get("store_id") == "S91", params
    assert params.get("product_id") == "P91", params
    # RED-18：receipt 记录的就是实际执行的 params
    trace = _trace("q")
    _engine(service, ScriptedClient([
        _tool("query_metrics", {"start": day, "end": day}, seq=1),
        _content("已按计划完成。"),
    ]), ToolSpy({"net_revenue": 1.0})).answer(plan, trace, [])
    created = _step(trace, "tool_receipt_created")
    assert created, "没有登记 tool receipt"
    assert created[0]["detail"]["params"].get("store_id") == "S91"


# =============================================== RED-06 门店冲突必须被拒绝


def test_scope_conflict_is_rejected(synth_planner):
    """模型把门店换成另一家：不得静默覆盖，必须结构化拒绝（任务书 §26）。"""
    from kbqa.toolpolicy import PlanToolPolicy

    plan = synth_planner.plan("S91 7 月的净营业额是多少？")
    policy = PlanToolPolicy(plan)
    effective, meta = policy.apply("query_metrics", {
        "start": plan.window[0], "end": plan.window[1], "store_id": "S92"})
    assert meta["status"] == "rejected", meta
    assert meta["field"] == "store_id"
    assert effective is None or effective.get("store_id") != "S92"


def test_live_rejects_conflicting_store(synth_planner, service):
    """端到端：冲突调用不执行数据库工具，且 trace 记 tool_scope_rejected。"""
    plan = synth_planner.plan("S91 7 月的净营业额是多少？")
    day = plan.window
    client = ScriptedClient([
        _tool("query_metrics", {"start": day[0], "end": day[1], "store_id": "S92"}, seq=1),
        _content("好的，我不查了。"),
    ])
    spy = ToolSpy({"net_revenue": 1.0})
    trace = _trace("q")
    _engine(service, client, spy).answer(plan, trace, [])

    assert spy.calls == [], "冲突调用仍被执行了：%s" % spy.calls
    rejected = _step(trace, "tool_scope_rejected")
    assert rejected, "trace 里没有 tool_scope_rejected"
    tool_messages = [
        m.get("content") or ""
        for call in client.calls for m in call["messages"] if m.get("role") == "tool"
    ]
    assert any("scope" in m or "范围" in m for m in tool_messages), (
        "没有把结构化 scope conflict 返回给模型：%s" % tool_messages)


# =============================================== RED-07 显式时间窗不得被扩大


def test_explicit_window_conflict_is_rejected(synth_planner):
    """Plan 的显式时间窗不得被模型换成别的一组时间（任务书 §27）。"""
    from kbqa.toolpolicy import PlanToolPolicy

    plan = synth_planner.plan("S91 7 月 12 日的净营业额是多少？")
    assert plan.window == ("2026-07-12", "2026-07-12"), plan.window
    policy = PlanToolPolicy(plan)
    effective, meta = policy.apply("query_metrics", {
        "start": "2026-07-01", "end": "2026-07-31"})
    assert meta["status"] == "rejected", meta
    assert meta["field"] in ("start", "end", "window"), meta


def test_explicit_window_conflict_is_rejected_in_live(synth_planner, service):
    plan = synth_planner.plan("S91 7 月 12 日的净营业额是多少？")
    client = ScriptedClient([
        _tool("query_metrics", {"start": "2026-07-01", "end": "2026-07-31"}, seq=1),
        _content("好的。"),
    ])
    spy = ToolSpy({"net_revenue": 1.0})
    trace = _trace("q")
    _engine(service, client, spy).answer(plan, trace, [])
    assert spy.calls == [], "扩大的时间窗被放行了：%s" % spy.calls
    assert _step(trace, "tool_scope_rejected"), "trace 里没有 tool_scope_rejected"


# ========================================== RED-08/09/10 维度工具的两个例外


def test_by_store_is_not_scoped_by_store(synth_planner):
    """by_store 自己就在门店维度展开：不得注入某一家 store。"""
    from kbqa.toolpolicy import PlanToolPolicy

    plan = synth_planner.plan("7 月 P91 各门店销量")
    assert plan.kind == "by_store", plan.kind
    policy = PlanToolPolicy(plan)
    effective, meta = policy.apply("by_store", {
        "start": plan.window[0], "end": plan.window[1]})
    assert effective.get("product_id") == "P91", effective
    assert "store_id" not in effective, "by_store 被注入了 store：%s" % effective


def test_top_products_is_not_scoped_by_product(synth_planner):
    """top_products 在商品维度展开：不得注入某个 product。"""
    from kbqa.toolpolicy import PlanToolPolicy

    plan = synth_planner.plan("7 月 S91 哪个商品销量最高？")
    assert plan.kind == "top_products", plan.kind
    policy = PlanToolPolicy(plan)
    effective, meta = policy.apply("top_products", {
        "start": plan.window[0], "end": plan.window[1]})
    assert effective.get("store_id") == "S91", effective
    assert "product_id" not in effective, "top_products 被注入了 product：%s" % effective


def test_compare_periods_uses_canonical_windows(synth_planner):
    """compare 必须用 Plan 的 A/B 两段，且不能被换成 C/D。"""
    from kbqa.toolpolicy import PlanToolPolicy

    plan = synth_planner.plan("S91 7 月的客单价跟 6 月比，是涨了还是跌了？")
    assert plan.kind == "compare" and plan.compare_window, (plan.kind, plan.window)
    a, b = plan.window, plan.compare_window
    policy = PlanToolPolicy(plan)
    effective, meta = policy.apply("compare_periods", {})
    assert (effective["start_a"], effective["end_a"]) == a, effective
    assert (effective["start_b"], effective["end_b"]) == b, effective
    assert effective.get("store_id") == "S91", effective

    rejected, meta2 = policy.apply("compare_periods", {
        "start_a": "2026-01-01", "end_a": "2026-01-31",
        "start_b": "2026-02-01", "end_b": "2026-02-28"})
    assert meta2["status"] == "rejected", meta2


def test_by_store_category_stays_open(synth_planner):
    """by_store_category 只在时间维度上受约束。"""
    from kbqa.toolpolicy import PlanToolPolicy

    plan = synth_planner.plan("7 月哪个品类营业额最高？")
    policy = PlanToolPolicy(plan)
    effective, meta = policy.apply("by_store_category", {})
    assert effective["start"] == plan.window[0] and effective["end"] == plan.window[1]
    assert "store_id" not in effective and "product_id" not in effective


# =============================================== RED-11 first_sale_date 的范围


def test_first_sale_date_scope(synth_planner):
    """first_sale_date 只允许 Plan 里那一个商品。"""
    from kbqa.toolpolicy import PlanToolPolicy

    plan = synth_planner.plan("P91 首月销量是多少？")
    assert plan.product_id == "P91", plan.product_id
    policy = PlanToolPolicy(plan)
    effective, meta = policy.apply("first_sale_date", {"product_id": "P92"})
    assert meta["status"] == "rejected", meta
    filled, meta2 = policy.apply("first_sale_date", {})
    assert filled.get("product_id") == "P91", filled


def test_first_sale_date_cannot_invent_product(synth_planner):
    """Plan 没有解析出商品时，模型不能凭空 invent 一个。"""
    from kbqa.toolpolicy import PlanToolPolicy

    plan = synth_planner.plan("7 月哪个商品销量最高？")
    assert plan.product_id is None
    policy = PlanToolPolicy(plan)
    _effective, meta = policy.apply("first_sale_date", {"product_id": "P92"})
    assert meta["status"] == "rejected", meta


# =============================================== RED-12/13/14 否定与真排行


def test_negated_rank_is_not_ranking(synth_planner):
    """「不要按销量排名」不得走排行（任务书 §42）。"""
    plan = synth_planner.plan("不要按销量排名，只看 S91 7 月营业额。")
    assert plan.kind == "summary", "否定句被判成了 %s" % plan.kind
    assert plan.metric == "net_revenue", "被否定子句里的「销量」带偏成 %s" % plan.metric
    assert plan.store_id == "S91"
    assert plan.window == ("2026-07-01", "2026-07-31"), plan.window


def test_contrast_is_not_rank(synth_planner):
    """「A 最多不代表 B 最高」是反差句，不是排行请求（任务书 §67）。"""
    plan = synth_planner.plan("顾客评价最多不代表营业额最高，S91 7 月真实营业额是多少？")
    assert plan.kind != "top_products", "反差句被判成了 top_products"
    assert plan.store_id == "S91"
    assert plan.metric == "net_revenue"


def test_true_ranking_still_works(synth_planner):
    """反向护栏：真排行问题必须照旧正确分派（任务书 §43）。"""
    assert synth_planner.plan("7 月哪家门店营业额最高？").kind == "by_store"
    assert synth_planner.plan("7 月哪个商品销量最高？").kind == "top_products"
    assert synth_planner.plan("7 月哪个品类营业额最高？").kind == "category"


# =================================================== RED-15 动态目录 / 别名


def test_dynamic_catalog_and_alias(synth_planner):
    """换一套数据（新门店/新商品/新别名）不改代码也能解析出正确 Plan。"""
    assert synth_planner.plan("北极星店 7 月销量是多少？").store_id == "S91"
    assert synth_planner.plan("星云茶 7 月销量是多少？").product_id == "P91"
    assert synth_planner.plan("夜航茶 7 月销量是多少？").product_id == "P91"


def test_entity_code_adjacent_to_cjk(synth_planner):
    """编号紧跟中文（"S91当月"）时也要解析得出。"""
    plan = synth_planner.plan("7月顾客反馈说S91好评最多，S91当月实际营业额是多少？")
    assert plan.store_id == "S91", "编号紧跟中文时没解析出门店：%s" % plan.store_id


def test_month_after_entity_code(synth_planner):
    """「S91 7 月」这种写法不能因为拼接把月份吃掉。"""
    for text in ("S91 7 月销量", "S91 7月销量", "7 月 S91 销量"):
        plan = synth_planner.plan(text)
        assert plan.window == ("2026-07-01", "2026-07-31"), (
            "「%s」→ window=%s" % (text, plan.window))


# ======================================================= RED-16 provenance


def test_provenance_explicit(synth_planner):
    plan = synth_planner.plan("S91 7 月销量")
    assert plan.provenance["store"] == "explicit", plan.provenance
    assert plan.provenance["window"] == "explicit", plan.provenance
    assert plan.provenance["metric"] == "explicit", plan.provenance


def test_provenance_inherited_on_follow_up(synth_planner):
    """追问继承来的门店要记成 inherited，不能混成 explicit。"""
    history = [{
        "question": "S91 六月的销量是多少？",
        "standalone": "六月的销量是多少？",
        "slots": {"store_id": "S91", "metric": "qty"},
    }]
    plan = synth_planner.plan("那 7 月呢？", history)
    assert plan.store_id == "S91", "追问没有继承门店"
    assert plan.provenance["store"] == "inherited", plan.provenance
    assert plan.provenance["window"] == "explicit", plan.provenance


def test_open_dimension_is_none_provenance(synth_planner):
    """「哪家店最高」是有意的维度展开，store 的 provenance 是 none，不是遗漏。"""
    plan = synth_planner.plan("7 月哪家门店营业额最高？")
    assert plan.provenance["store"] == "none", plan.provenance


# ======================================================= 历史问题形态回归


def test_regression_negated_rank_shape(service):
    """H071 类型（通用写法）：反差句不走排行，落 summary/query_metrics。"""
    plan = service.planner.plan(
        "7 月顾客反馈说 S05 好评最多，S05 当月实际营业额是多少？"
        "不要把「好评最多」解释成营业额最高。")
    assert plan.kind != "top_products", "仍被 FAQ 式排行吃掉了：%s" % plan.kind
    assert plan.metric == "net_revenue"


def test_regression_explicit_scope_all_parsed(service):
    """H001 类型：日期/门店/商品/指标全部进 Plan。"""
    plan = service.planner.plan(
        "2026 年 618 当天，Makai 的牛肉poke 实际卖了多少份？")
    assert plan.store_id == "S02"
    assert plan.product_id == "P06"
    assert plan.window == ("2026-06-18", "2026-06-18")
    assert plan.metric == "qty"


def test_regression_by_store_dimension(service):
    """H047 类型：商品 × 各门店 是 by_store，不是 top_products。"""
    plan = service.planner.plan("8月冷萃乌龙各店实际销量分别是多少？哪家最高？")
    assert plan.kind == "by_store", plan.kind
    assert plan.metric == "qty"
    assert plan.window == ("2026-08-01", "2026-08-31")


def test_regression_compare_keeps_two_windows(service):
    """H029 类型：两期范围不能被弄错。"""
    plan = service.planner.plan("熟客 7 月的销量和 6 月比差了多少？")
    assert plan.kind == "compare", plan.kind
    assert plan.window and plan.compare_window
    assert plan.window != plan.compare_window
