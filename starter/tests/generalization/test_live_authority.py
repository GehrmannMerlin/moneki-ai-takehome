"""Generalization Round 2 — Live Fact Ledger & Finalisation Authority 的红测试。

原则（任务书 §38–§50）：

* 先对**当前** production 代码写"正确行为"的断言，让它红；
* 断言的是**机制**，不是公开题库的金标——所有数值都用合成值
  （73 / 70 / 1377 / 2468 / 111 / 222 …），门店商品用合成编号；
* 不碰仓库真实 ``data/`` 与 ``knowledge_base/`` 的期望值；
* 覆盖的候选缺陷（编号以 DEBUG_LOG 实际确认为准）：
  - 生产数字提取把 KB 编号 / 日期 / 时间当经营数字（R2-D3）；
  - evidence compaction 同时裁掉模型看到的工具结果（R2-D1）；
  - evidence = "调用过就全部输出"，与调用顺序绑定（R2-D2）；
  - live finaliser 校验失败后调用第二套 Answerer 重答（R2-D4）；
  - 工具返回 scalar/None 让 live 循环抛 TypeError（R2-D5）。
"""

from __future__ import annotations

import copy
import json

import pytest


# --------------------------------------------------------------------------- 夹具


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    """模块级共享的真实 Service（只读；不进仓库 var/）。

    只为拿到 planner / retriever / facts —— 取数一律用注入的假工具，
    所以断言不依赖公开数据集的任何数值。
    """
    import os

    var = tmp_path_factory.mktemp("r2-live-var")
    os.environ["VAR_DIR"] = str(var)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)
    from kbqa.config import load_settings
    from kbqa.service import Service

    return Service(load_settings())


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
    """按剧本回话的假模型，**逐次记录 messages 与 tools**。

    记录 messages 是 R2 的关键：要能断言"模型这一轮到底看到了什么工具结果"。
    """

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


def _engine(service, client, run_tool=None):
    from kbqa.live import LiveEngine

    return LiveEngine(
        client,
        service.answerer,
        run_tool or service.run_tool,
        service.settings.today.isoformat(),
        service.data_period,
        budget=60.0,
    )


def _trace(question: str):
    from kbqa.trace import Trace

    return Trace(trace_id="t-r2-test", question=question, session_id="r2")


def _plan(service, question: str):
    return service.planner.plan(question)


def _tool_messages(client) -> list[str]:
    """全部调用里，role=tool 的消息内容（模型实际读到的工具结果）。"""
    out = []
    for call in client.calls:
        for message in call["messages"]:
            if message.get("role") == "tool":
                out.append(message.get("content") or "")
    return out


def _numbers_of(result) -> int:
    from kbqa.live import _numbers_in

    blob = json.dumps(result, ensure_ascii=False, default=str)
    return len(_numbers_in(blob))


def _business_numbers(text: str) -> list[float]:
    from kbqa.live import _numbers_in

    return _numbers_in(text)


# =========================================================== 数字语义（RED-01）


def test_kb_and_entity_ids_are_not_business_numbers():
    """KB 编号 / 门店商品编号 / 订单号 / trace id 都不是经营数字。

    真实 trace 里出现过 ``KB-001 → -1``、``KB-029 → -29``、``07-31 → -31``
    被当成"模型编造的数字"，直接导致 DeepSeek 正确答案进 fallback。
    """
    text = "参照 KB-001 与 KB-029 的口径，涉及 S02、P06、ORD123456、t-20260901-0001。"
    assert _business_numbers(text) == [], (
        "编号被当成了经营数字：%s" % _business_numbers(text))


def test_dates_and_times_are_not_business_numbers():
    """各种写法的日期与时间都不算经营数字。"""
    text = "2026-07-31、2026/7/31、31-07-2026、2026年7月31日、7月31日、23:00。"
    assert _business_numbers(text) == [], (
        "日期/时间被当成了经营数字：%s" % _business_numbers(text))


def test_real_money_counts_and_percent_are_extracted():
    """真正是答案的金额 / 数量 / 百分比 / 万 要能取出来。"""
    values = _business_numbers("营业额 1,377 元（¥1377，占 76.56%），约 1.23 万。")
    for want in (1377.0, 76.56, 12300.0):
        assert any(abs(v - want) <= 0.011 for v in values), (
            "应提取到 %s，实际 %s" % (want, values))


def test_number_semantics_match_evaluator_contract(run_eval_module):
    """与官方 evaluator 的 extract_numbers 逐条交叉验证（合成字符串）。"""
    samples = [
        "KB-001 KB-029 S02 P06 ORD123456 t-20260901-0001",
        "2026-07-31 2026/7/31 31-07-2026 2026年7月31日 7月31日 23:00",
        "1,377 ¥1377 1377元 76.56% 1.23万",
        "净营业额 1377.00 元，销量 73 件，客单价 18.86 元。",
    ]
    for text in samples:
        mine = sorted(round(v, 6) for v in _business_numbers(text))
        theirs = sorted(round(v, 6) for v in run_eval_module.extract_numbers(text))
        assert mine == theirs, (
            "生产实现与 evaluator 口径不一致\n  文本：%s\n  生产：%s\n  评测：%s"
            % (text, mine, theirs))


# =============================================== 模型上下文不被收口污染（RED-02）


def _broad_result(n: int = 10) -> dict:
    """宽查询：n 个商品 × 4 个数字字段。

    旧实现的数字提取把商品编号 ``P9xx`` 也当数字，于是每行 5 个数字：
    n=10 → 50 + 6（起止日期） = 56，占掉全局证据预算 60 的绝大部分，
    随后到来的精确查询只剩 4 个数字的预算、被换成 truncated stub。
    """
    return {
        "start": "2026-07-01",
        "end": "2026-07-31",
        "products": [
            {"product_id": "P9%02d" % i, "net_revenue": 1000.0 + i,
             "refund_amount": 100.0 + i, "qty": 200 + i, "orders": 500 + i}
            for i in range(1, n + 1)
        ],
    }


def _exact_result() -> dict:
    return {"start": "2026-07-12", "end": "2026-07-12", "store_id": "S91",
            "product_id": "P91", "net_revenue": 1377, "orders": 41, "qty": 73}


def test_model_sees_uncompacted_exact_result_after_broad_query(service):
    """先宽后精确：精确结果进入模型时必须是**完整事实**，不是 truncated stub。

    这是 R2 的核心架构不变量：Model Context Budget ≠ API Evidence Budget。
    """
    def fake_run_tool(name, params):
        if params.get("product_id"):
            return _exact_result()
        return _broad_result()

    plan = _plan(service, "2026 年 7 月 S91 的净营业额是多少？")
    client = ScriptedClient([
        _tool("query_metrics", {"start": "2026-07-01", "end": "2026-07-31"}, seq=0),
        _tool("query_metrics", {"start": "2026-07-12", "end": "2026-07-12",
                                "product_id": "P91"}, seq=1),
        _content("净营业额 1377 元，订单 41 单，销量 73 件。"),
    ])
    _engine(service, client, fake_run_tool).answer(plan, _trace("x"), [])

    blobs = _tool_messages(client)
    exact_seen = False
    for blob in blobs:
        try:
            parsed = json.loads(blob)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("qty") == 73:
            exact_seen = True
    assert exact_seen, (
        "模型从没看到完整的精确工具结果——它只看到收口后的 stub。\n"
        "模型实际读到的工具消息：\n%s" % "\n".join(blobs))


# ================================================= 证据与调用顺序无关（RED-03）


def test_evidence_selection_is_order_independent(service):
    """宽查询与精确查询的先后，不能决定谁被选进 data_evidence。"""
    def run(order):
        def fake_run_tool(name, params):
            return _exact_result() if params.get("product_id") else _broad_result()

        if order == "broad_first":
            script = [
                _tool("query_metrics", {"start": "2026-07-01", "end": "2026-07-31"}, seq=0),
                _tool("query_metrics", {"start": "2026-07-12", "end": "2026-07-12",
                                        "product_id": "P91"}, seq=1),
            ]
        else:
            script = [
                _tool("query_metrics", {"start": "2026-07-12", "end": "2026-07-12",
                                        "product_id": "P91"}, seq=0),
                _tool("query_metrics", {"start": "2026-07-01", "end": "2026-07-31"}, seq=1),
            ]
        script.append(_content("净营业额 1377 元，订单 41 单，销量 73 件。"))
        plan = _plan(service, "2026 年 7 月 S91 的净营业额是多少？")
        client = ScriptedClient(script)
        return _engine(service, client, fake_run_tool).answer(plan, _trace("x"), [])

    first, second = run("broad_first"), run("exact_first")

    for name, answer in (("broad→exact", first), ("exact→broad", second)):
        assert answer.data_evidence, "%s：没有选出任何证据" % name
        total = sum(_numbers_of(item["result"]) for item in answer.data_evidence)
        assert total <= 60, "%s：证据数字合计 %d > 60" % (name, total)
        for item in answer.data_evidence:
            blob = json.dumps(item["result"], ensure_ascii=False, default=str)
            assert len(blob.encode("utf-8")) <= 4096, "%s：单条结果超 4096 字节" % name
        # 最终回答用到的三个数字，必须能在选中的证据里找到
        pool = []
        for item in answer.data_evidence:
            pool.extend(_business_numbers(json.dumps(item["result"], ensure_ascii=False)))
        for want in (1377.0, 41.0, 73.0):
            assert any(abs(v - want) <= 0.011 for v in pool), (
                "%s：证据里找不到回答用到的 %s" % (name, want))

    def signature(answer):
        return sorted((item["tool"], json.dumps(item["params"], sort_keys=True))
                      for item in answer.data_evidence)

    assert signature(first) == signature(second), (
        "调用顺序改变了最终证据集合：\n  %s\n  %s" % (signature(first), signature(second)))


# ===================================== 正确模型答案不被改坏 / 不许语义回退（RED-04/05）


class _ExplodingAnswerer:
    """任何一次 answer() 调用都是违规——live finaliser 不许再交给第二套作答器。"""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, item):
        return getattr(self._real, item)

    def answer(self, *args, **kwargs):  # pragma: no cover - 命中即测试失败
        raise AssertionError(
            "live finaliser 把问题重新交给了 mock Answerer 作答（R2-D4 语义回退）")


def _exploding_service(service, monkeypatch):
    real = service.answerer
    monkeypatch.setattr(service, "answerer", _ExplodingAnswerer(real), raising=False)
    return service


def test_correct_model_answer_survives_finalisation(service, monkeypatch):
    """完全受支持的正确答案，必须原样活到最终 response。

    真实历史失败：DeepSeek 原始回答正确，但生产数字提取把 ``KB-901``/日期
    误判为编造数字，触发 fallback，正确答案被换成另一套答案。
    """
    _exploding_service(service, monkeypatch)

    def fake_run_tool(name, params):
        return {"qty": 73, "target": 70, "store_id": "S91"}

    text = ("S91 在 2026-07-31 的销量是 73 份，目标 70 份，已经达标；"
            "口径见 KB-901 的说明。")
    plan = _plan(service, "2026 年 6 月 S91 的销量是多少？")
    client = ScriptedClient([
        _tool("query_metrics", {"start": "2026-06-01", "end": "2026-06-30"}, seq=0),
        _content(text),
    ])
    answer = _engine(service, client, fake_run_tool).answer(plan, _trace("x"), [])

    assert "73" in answer.answer and "70" in answer.answer, (
        "正确答案在 finalisation 里被改坏了：%r" % answer.answer)
    assert answer.answer.strip() == text.strip(), (
        "受支持的模型答案不应被改写：\n  期望：%s\n  实际：%s" % (text, answer.answer))


def test_semantic_fallback_is_forbidden_in_live(service, monkeypatch):
    """live 模式下，校验失败**不许**调用 Answerer.answer() 重新回答原问题。

    Answerer 只保留给 mock / 无 Key 降级；live 的失败路径是
    validate → repair → refusal，不是换一套引擎重答。
    """
    _exploding_service(service, monkeypatch)

    def fake_run_tool(name, params):
        return {"qty": 73, "target": 70}

    plan = _plan(service, "2026 年 6 月 S91 的销量是多少？")
    client = ScriptedClient([
        _tool("query_metrics", {"start": "2026-06-01", "end": "2026-06-30"}, seq=0),
        _content("S91 的销量是 999999 份。"),                    # 编造数字
        _content("S91 的销量是 73 份，目标 70 份。"),            # repair 修正
    ])
    answer = _engine(service, client, fake_run_tool).answer(plan, _trace("x"), [])

    assert "73" in answer.answer, "repair 后的正确答案没有生效：%r" % answer.answer
    assert "999999" not in answer.answer


# ============================================ 一次有界 repair，失败即拒答（RED-06/07）


def test_unsupported_claim_gets_exactly_one_bounded_repair(service, monkeypatch):
    """初始回答有编造数字 → 恰好一次 repair；repair 轮不带工具。"""
    _exploding_service(service, monkeypatch)

    def fake_run_tool(name, params):
        return {"qty": 73, "target": 70}

    plan = _plan(service, "2026 年 6 月 S91 的销量是多少？")
    client = ScriptedClient([
        _tool("query_metrics", {"start": "2026-06-01", "end": "2026-06-30"}, seq=0),
        _content("S91 的销量是 999999 份。"),
        _content("S91 的销量是 73 份，目标 70 份，已达标。"),
    ])
    answer = _engine(service, client, fake_run_tool).answer(plan, _trace("x"), [])

    assert len(client.calls) == 3, (
        "期望 3 次 completion（取数 + 初始 + 一次 repair），实际 %d 次"
        % len(client.calls))
    assert client.calls[-1]["tools"] is None, "repair 轮不该带上工具"
    assert answer.answer.strip() == "S91 的销量是 73 份，目标 70 份，已达标。"


def test_failed_repair_returns_structured_refusal(service, monkeypatch):
    """repair 仍然编造 → 结构化拒答；不许第三次模型调用，也不许 mock 作答。"""
    _exploding_service(service, monkeypatch)

    def fake_run_tool(name, params):
        return {"qty": 73, "target": 70}

    plan = _plan(service, "2026 年 6 月 S91 的销量是多少？")
    client = ScriptedClient([
        _tool("query_metrics", {"start": "2026-06-01", "end": "2026-06-30"}, seq=0),
        _content("S91 的销量是 999999 份。"),
        _content("S91 的销量是 888888 份。"),                    # repair 又编造
    ])
    answer = _engine(service, client, fake_run_tool).answer(plan, _trace("x"), [])

    assert answer.answer_type == "refusal", (
        "repair 失败后应是结构化拒答，实际 %r" % answer.answer_type)
    assert len(client.calls) == 3, "不许有第三次模型调用，实际 %d 次" % len(client.calls)
    assert "999999" not in answer.answer and "888888" not in answer.answer, (
        "拒答里不许保留编造数字：%r" % answer.answer)


# ======================================================= 工具结果契约（RED-08）


def test_run_tool_normalises_scalar_and_none(service):
    """scalar / None 的工具结果在边界统一成可检查的 JSON object。"""
    scalar = service.run_tool("first_sale_date", {"product_id": service.tools.products()[0]["product_id"]})
    assert isinstance(scalar, dict), (
        "first_sale_date 的字符串结果没有包成 JSON object：%r" % (scalar,))

    # 不存在的商品 → 工具成功执行但没有数据 → {"value": null}，而不是裸 None
    missing = service.run_tool("first_sale_date", {"product_id": "PZZZ"})
    assert isinstance(missing, dict), "None 结果没有包成 JSON object：%r" % (missing,)
    assert missing.get("value", "MISSING") is None, (
        "无数据应当表达为 value=null：%r" % (missing,))


def test_scalar_tool_result_does_not_crash_live_loop(service):
    """LiveEngine 永远收到 JSON object：scalar / None 都不许抛 TypeError。"""
    def fake_run_tool(name, params):
        return None                                        # 旧实现：'error' not in None → TypeError

    plan = _plan(service, "2026 年 6 月 S91 的销量是多少？")
    client = ScriptedClient([
        _tool("first_sale_date", {"product_id": "P91"}, seq=0),
        _content("没有查到该商品的销售记录。"),
    ])
    answer = _engine(service, client, fake_run_tool).answer(plan, _trace("x"), [])
    assert answer.answer.strip()

    def fake_scalar(name, params):
        return "2026-08-01"

    client2 = ScriptedClient([
        _tool("first_sale_date", {"product_id": "P91"}, seq=0),
        _content("该商品首次销售日是 2026 年 8 月 1 日。"),
    ])
    answer2 = _engine(service, client2, fake_scalar).answer(plan, _trace("x"), [])
    assert answer2.answer.strip()


# ==================================================== 最小证据选择（RED-09）


def test_evidence_contains_only_answer_supporting_receipts(service):
    """调了三个数据工具，回答只用一个的数字 → 证据只该有那一个。

    Evidence 是 final answer 的证据，不是 tool call history。
    """
    def fake_run_tool(name, params):
        if name == "query_metrics":
            return {"start": params.get("start"), "end": params.get("end"),
                    "store_id": "S91", "net_revenue": 111, "qty": 11}
        if name == "by_store":
            return {"stores": [{"store_id": "S91", "qty": 22, "net_revenue": 222},
                               {"store_id": "S92", "qty": 33, "net_revenue": 333}]}
        return {"products": [{"product_id": "P91", "qty": 44, "net_revenue": 444},
                             {"product_id": "P92", "qty": 55, "net_revenue": 555}]}

    plan = _plan(service, "2026 年 7 月 S91 的净营业额是多少？")
    client = ScriptedClient([
        _tool("query_metrics", {"start": "2026-07-01", "end": "2026-07-31"}, seq=0),
        _tool("by_store", {"start": "2026-07-01", "end": "2026-07-31"}, seq=1),
        _tool("top_products", {"start": "2026-07-01", "end": "2026-07-31"}, seq=2),
        _content("S91 的净营业额是 111 元，销量 11 件。"),
    ])
    answer = _engine(service, client, fake_run_tool).answer(plan, _trace("x"), [])

    tools = [item["tool"] for item in answer.data_evidence]
    assert tools == ["query_metrics"], (
        "最终只用了 query_metrics 的数字，证据却带了 %s——"
        "未被回答使用的宽查询不该占证据预算" % tools)


# =============================================== 宽列表证据投影（RED-10）


def test_broad_list_evidence_projection_keeps_needed_fields(service):
    """by_store（5 店 × 多指标）：回答只用 qty，投影应只留必要字段。"""
    stores = [
        {"store_id": "S9%d" % i, "store_name": "门店%d" % i, "category": "合成",
         "district": "合成区", "start": "2026-07-01", "end": "2026-07-31",
         "net_revenue": 1000.0 * i, "refund_amount": 10.0 * i, "orders": 100 * i,
         "aov": 12.5 * i, "qty": 20 + i, "product_id": None}
        for i in range(1, 6)
    ]

    def fake_run_tool(name, params):
        return {"start": params.get("start"), "end": params.get("end"), "stores": copy.deepcopy(stores)}

    plan = _plan(service, "2026 年 7 月各门店的销量分别是多少？")
    client = ScriptedClient([
        _tool("by_store", {"start": "2026-07-01", "end": "2026-07-31"}, seq=0),
        _content("各门店销量：S91 是 21 件，S92 是 22 件，S93 是 23 件，"
                 "S94 是 24 件，S95 是 25 件。"),
    ])
    answer = _engine(service, client, fake_run_tool).answer(plan, _trace("x"), [])

    assert answer.data_evidence
    total = 0
    for item in answer.data_evidence:
        blob = json.dumps(item["result"], ensure_ascii=False, default=str)
        assert len(blob.encode("utf-8")) <= 4096, "单条 result 超 4096 字节"
        total += _numbers_of(item["result"])
        rows = item["result"].get("stores") or []
        for row in rows:
            assert "store_id" in row, "投影把 store_id 也砍掉了"
            assert "qty" in row, "投影把回答用到的 qty 砍掉了"
            assert "refund_amount" not in row, (
                "投影保留了回答没用到的字段：%s" % sorted(row))
    assert total <= 60, "证据数字合计 %d > 60" % total


# =============================================== Receipt 不可变（RED-11）


def test_tool_receipt_is_immutable_across_projections(service):
    """raw_result 在 model projection / evidence projection 之后必须一字不变。"""
    from kbqa.ledger import FactLedger

    raw = {"start": "2026-07-01", "end": "2026-07-31",
           "stores": [{"store_id": "S91", "qty": 73, "net_revenue": 1377}]}
    snapshot = copy.deepcopy(raw)

    ledger = FactLedger()
    receipt = ledger.add(tool="by_store", params={"start": "2026-07-01"},
                         result=raw, source="data")

    ledger.model_projection(receipt)
    ledger.evidence_projection(receipt, answer_numbers=[73.0])

    assert receipt.result == snapshot, "projection 改动了 canonical receipt"
    assert raw == snapshot, "projection 甚至改动了调用方持有的原对象"


# =============================================== Trace 溯源（RED-12）


def test_trace_exposes_receipt_selection_and_validation(service):
    """trace 必须能回答"模型为什么知道这个数字"。"""
    def fake_run_tool(name, params):
        return {"qty": 73, "net_revenue": 1377}

    plan = _plan(service, "2026 年 7 月 S91 的销量是多少？")
    client = ScriptedClient([
        _tool("query_metrics", {"start": "2026-07-01", "end": "2026-07-31"}, seq=0),
        _content("S91 的销量是 73 件，净营业额 1377 元。"),
    ])
    trace = _trace("x")
    _engine(service, client, fake_run_tool).answer(plan, trace, [])

    steps = {step["step"] for step in trace.as_dict()["steps"]}
    for want in ("tool_receipt_created", "evidence_selected",
                 "evidence_projected", "final_validation"):
        assert want in steps, (
            "trace 缺少步骤 %r；现有：%s" % (want, sorted(steps)))

    # receipt 步骤里要能看到 tool / params / result 摘要
    receipt_steps = [s for s in trace.as_dict()["steps"]
                     if s["step"] == "tool_receipt_created"]
    assert receipt_steps, "没有 tool_receipt_created 步骤"
    detail = receipt_steps[0]["detail"]
    blob = json.dumps(detail, ensure_ascii=False, default=str)
    assert "query_metrics" in blob and "qty" in blob, (
        "receipt 步骤看不到 tool 与 result：%s" % blob)
