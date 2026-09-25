"""live 模式三类失分的复现测试（配置真实 Key 后发现，先红后绿）。

三个缺陷都对着**评测脚本的检查项**写，不是对着"我觉得应该怎样"写：

* **D27** `live.py` 把模型每次工具调用的原始 result 原样累计进 data_evidence，
  没有尺寸收口。评测 `_check_evidence_hygiene`：单条 result ≤4096 字节、
  **全部 result 合计**数字 ≤60 个。真实模型爱用 `top_products(limit=20/30)`、
  整月 `daily_metrics` 这类大结果（D06/C07/T03-1/X04 四题，回答本身全对，
  纯粹证据超标判红）。
* **D28** 估算数字纪律没有传导给模型。H02 的失败检查是
  `numbers_none=[150]`（店长周报的估算写进了回答）与 `cite_none=[KB-024]`
  （2025 年的旧活动方案被引用）。注意 **KB-050（周报）不在 cite_none 里**，
  且 C07 合法引用的 KB-029 也是 estimates_only——所以不能一刀切剔除估算文档，
  只能：按问题年份过滤引用 + 估算文档的数字不进"白名单"（模型真写了 150，
  数字校验就把它打回模板回答）。
* **D29** `MAX_TOOL_ROUNDS=4` 撞上真实模型"检索不到就换词再试"的行为
  （trace t-20260901-0026：4 轮 8 次合理但无果的检索，然后 refusal）。
  修法不是简单调大轮数：轮数用尽后要**强制作答**（最后一轮不带工具），
  让"没找到"以正文形式说出来，而不是 LLMError。
"""

from __future__ import annotations

import json

import pytest

from kbqa.llm import LLMReply
from kbqa.live import LiveEngine


@pytest.fixture()
def service(tmp_var):
    """真实 Service，VAR_DIR 指向独立临时目录（与 test_p3_pipeline 同款）。"""
    import os

    os.environ["VAR_DIR"] = str(tmp_var)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)
    from kbqa.config import load_settings
    from kbqa.service import Service

    return Service(load_settings())

#: 评测脚本的上限（run_eval.py MAX_EVIDENCE_*，复制到这里钉死：
#: 如果哪天评测改了上限，这条测试应该红，提醒同步）。
MAX_EVIDENCE_RESULT_BYTES = 4096
MAX_EVIDENCE_NUMBERS = 60


# --------------------------------------------------------------------------- 假模型


def _content_reply(text: str) -> LLMReply:
    return LLMReply(
        message={"role": "assistant", "content": text},
        finish_reason="stop",
        content=text,
        tool_calls=[],
        elapsed=0.01,
    )


def _tool_reply(name: str, args: dict, seq: int = 0) -> LLMReply:
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
    """按剧本回话的假模型；记录每次调用收到的 tools，供断言。

    `forced` 是强制作答轮（tools=None，模型没拿到工具定义）的固定回话——
    真实 API 在没给工具时不可能返回 tool_calls，这里如实模拟。
    """

    def __init__(self, replies: list[LLMReply], forced: LLMReply | None = None) -> None:
        self.replies = list(replies)
        self.forced = forced
        self.calls: list[dict] = []

    def chat_with_retry(self, messages, tools=None, budget=None, on_call=None):
        self.calls.append({"tools": tools})
        if tools is None and self.forced is not None:
            return self.forced
        if not self.replies:
            raise AssertionError("假模型的剧本演完了还被调用（工具循环没收敛）")
        return self.replies.pop(0)


def _engine(service, client) -> LiveEngine:
    return LiveEngine(
        client,
        service.answerer,
        service.run_tool,
        service.settings.today.isoformat(),
        service.data_period,
        budget=60.0,
    )


def _trace(service, question: str):
    from kbqa.trace import Trace

    return Trace(trace_id="t-test-live", question=question, session_id="test")


def _numbers_of(result) -> int:
    """与评测同口径地数一条 result 里的数字（日期遮蔽）。"""
    from kbqa.live import _numbers_in

    blob = json.dumps(result, ensure_ascii=False, default=str)
    return len(_numbers_in(blob))


# --------------------------------------------------------------------------- D27


def _huge_top_products(n: int = 30) -> dict:
    """n 个商品 × 4 个数字字段的排行结果——真实模型在 D06/X04 里就是这么调的。"""
    return {
        "start": "2026-07-01",
        "end": "2026-07-31",
        "store_id": None,
        "products": [
            {
                "product_id": "P%02d" % i,
                "product_name": "商品%02d" % i,
                "net_revenue": 1000.0 + i,
                "qty": 20 + i,
                "orders": 5 + i,
            }
            for i in range(1, n + 1)
        ],
    }


def test_evidence_total_numbers_within_evaluator_budget(service):
    """D27：模型调了 top_products(30)，落进 data_evidence 的数字合计必须 ≤60。

    评测 `evidence_hygiene` 数的是**全部 result 合计**的数字（"穷举数字不是证据"），
    所以收口必须是全局预算，不是单条裁剪。
    """
    huge = _huge_top_products(30)
    seen: dict = {}

    def fake_run_tool(name, params):
        seen["name"] = name
        return huge

    plan = service.planner.plan("7 月卖得最好的商品是什么？")
    client = ScriptedClient([
        _tool_reply("top_products", {"start": "2026-07-01", "end": "2026-07-31", "limit": 30}),
        _content_reply("销量数字见下方数据证据。"),
    ])
    engine = LiveEngine(
        client, service.answerer, fake_run_tool,
        service.settings.today.isoformat(), service.data_period, budget=60.0,
    )
    answer = engine.answer(plan, _trace(service, "x"), [])
    assert seen["name"] == "top_products"
    assert answer.data_evidence, "有工具结果就该有证据"
    total = sum(_numbers_of(item["result"]) for item in answer.data_evidence)
    assert total <= MAX_EVIDENCE_NUMBERS, (
        "全部 result 合计 %d 个数字，超过评测上限 %d——真实 Key 下 D06/X04 就是这么红的"
        % (total, MAX_EVIDENCE_NUMBERS))


def test_evidence_single_result_within_byte_limit_and_stub(service):
    """D27：不可裁剪的超大结果要收口成占位说明，而不是原样落库。

    单条 result 序列化 ≤4096 字节；裁不动（没有长列表可砍）就整条换成 stub。
    """
    #: 100 个标量数字字段的 dict——没有列表可裁，只能 stub。
    giant = {"field_%03d" % i: float(i) for i in range(1, 101)}

    def fake_run_tool(name, params):
        return giant

    plan = service.planner.plan("6 月的净营业额是多少？")
    client = ScriptedClient([
        _tool_reply("query_metrics", {"start": "2026-06-01", "end": "2026-06-30"}),
        _content_reply("具体数字见数据证据。"),
    ])
    engine = LiveEngine(
        client, service.answerer, fake_run_tool,
        service.settings.today.isoformat(), service.data_period, budget=60.0,
    )
    answer = engine.answer(plan, _trace(service, "x"), [])
    assert answer.data_evidence
    for item in answer.data_evidence:
        blob = json.dumps(item["result"], ensure_ascii=False, default=str)
        assert len(blob.encode("utf-8")) <= MAX_EVIDENCE_RESULT_BYTES, (
            "单条 result %d 字节，超过评测上限 %d" % (len(blob.encode("utf-8")),
                                                    MAX_EVIDENCE_RESULT_BYTES))
        assert _numbers_of(item["result"]) <= MAX_EVIDENCE_NUMBERS


# --------------------------------------------------------------------------- D28


def _h02_plan(service):
    return service.planner.plan("618 当天 S02 的牛肉poke 卖了多少份？达到目标了吗？")


def test_citations_filter_wrong_year_docs(service):
    """D28：问 2026 的 618，就不能引用 2025 年的方案（cite_none=[KB-024]）。"""
    engine = _engine(service, ScriptedClient([]))
    plan = _h02_plan(service)
    cited = engine._citations(plan, ["KB-050", "KB-024", "KB-023"])
    ids = {c["doc_id"] for c in cited}
    assert "KB-024" not in ids, (
        "问 2026 年的活动却引用了 2025 年的方案 KB-024——评测 cite_none 判的就是这个；"
        "实际引用：%s" % sorted(ids))


def test_citations_keep_doc_when_year_matches(service):
    """D28 的反向护栏：问 2025 年的 618，KB-024 就是正确引用，不许被误杀。

    过滤必须跟着"问题问的是哪一年"走，不是无脑剔除归档文档——
    归档 ≠ 废止（mock 管线对归档文档照答不误）。
    """
    engine = _engine(service, ScriptedClient([]))
    plan = service.planner.plan("2025 年 618 活动的目标销量是多少？")
    cited = engine._citations(plan, ["KB-024", "KB-023"])
    ids = {c["doc_id"] for c in cited}
    assert "KB-024" in ids, "问 2025 年的活动，KB-024 反而被过滤了：%s" % sorted(ids)


def test_estimate_numbers_not_in_allowed_pool(service):
    """D28 兜底：估算文档（estimates_only）的数字不许进"允许出现的数字"白名单。

    模型要是真把店长周报的"大概 150 份"写进回答，数字校验就必须能抓到它、
    打回按工具结果渲染的模板回答——前提是 150 不在白名单里。
    """
    engine = _engine(service, ScriptedClient([]))
    plan = _h02_plan(service)
    allowed = engine._allowed_numbers(plan, [], [{"doc_id": "KB-050", "quote": "…"}])
    assert 150.0 not in allowed, (
        "估算数字 150 进了白名单——模型写它就不被发现，numbers_none 判的就是这个")


# --------------------------------------------------------------------------- D29


def test_no_explanation_answer_clears_citations(service):
    """D31（H06 复盘）：回答明说"没有找到"解释时，引用必须清空（cite_max=0）。

    真实 Key 复盘：模型把数据事实与"没有找到"都说对了，但为了展示
    "我查过了"点名了别家门店的停业通知（S03 的 KB-020、S05 的 KB-027）
    当例子——评测对"why 类且无解释文档"判 cite_max=0。
    mock 管线早有对应规则（缺陷 #22 的 cause_not_found 槽位），
    live 侧对齐：正文明说没找到，就不许挂擦边引用。
    """
    plan = service.planner.plan("S02 在 8 月 17 日到 19 日为什么一分钱营业额都没有？")
    client = ScriptedClient([
        _tool_reply("daily_metrics",
                    {"start": "2026-08-17", "end": "2026-08-19", "store_id": "S02"}),
        _content_reply(
            "S02 在 8 月 17—19 日营业额均为 0 元、订单 0 单，三天完全没有交易。"
            "停业的具体原因没有找到相关通知或说明；我查过停业通知与例会纪要 "
            "[KB-020] [KB-027] [KB-029]，都只涉及其他门店。"),
    ])
    answer = _engine(service, client).answer(plan, _trace(service, "x"), [])
    assert not answer.citations, (
        "正文已明说'没有找到'，却还挂着引用 %s——评测 cite_max=0 判的就是这个"
        % [c["doc_id"] for c in answer.citations])
    assert "没有找到" in answer.answer, "清引用不该动正文"
    assert answer.answer_type in ("data", "hybrid", "refusal"), (
        "清引用后 answer_type 变成 %s——评测允许 data/hybrid/refusal" % answer.answer_type)


def test_explanation_found_keeps_citations(service):
    """D31 的反向护栏：找到了原因并引用正确的文档时，不许误杀。

    H01 有真解释文档（S03 停业通知 KB-020），回答里"没有找到"只可能出现在
    别的子句里；主句是"原因是 [KB-020]"时不触发清空。
    """
    plan = service.planner.plan("S03 六月第二周的营业额为什么比别的周低？")
    client = ScriptedClient([
        _tool_reply("search_kb", {"query": "S03 停业 通知"}),
        _content_reply(
            "S03 六月第二周前三天营业额为 0。原因是门店自 6 月 8 日起停业 4 天整改排烟管道 "
            "[KB-020]。"),
    ])
    answer = _engine(service, client).answer(plan, _trace(service, "x"), [])
    cited = {c["doc_id"] for c in answer.citations}
    assert "KB-020" in cited, "找到了原因的引用被误杀了：%s" % sorted(cited)


def test_tool_loop_exhaustion_forces_final_answer(service):
    """D29：模型连续要工具、永不给正文时，最后一轮必须**不带工具强制作答**。

    真实 Key 下的 H06/T02-3：模型换关键词检索了 4 轮还没收敛，
    旧实现直接 LLMError("tool_loop") → refusal"工具调用没有收敛"——
    这句话不含"没有找到"等期望说法，text_any/numbers_all/evidence_required 全红。
    正确行为：轮数用尽后再给模型一次**没有工具可调**的机会，让它把
    "没有找到相关文档"和数据事实用正文说出来。
    """
    #: 前若干轮永远在要工具；强制作答轮（没拿到工具定义）只能给正文。
    script = [_tool_reply("search_kb", {"query": "尝试第%d轮" % i}, seq=i) for i in range(12)]
    client = ScriptedClient(
        script,
        forced=_content_reply("没有找到解释这种情况的文档；已知数据事实是这几天营业额为 0。"),
    )
    plan = service.planner.plan("S02 在 8 月 17 日到 19 日为什么一分钱营业额都没有？")

    engine = _engine(service, client)
    answer = engine.answer(plan, _trace(service, "x"), [])

    assert answer.answer.strip(), "强制作答轮没有产出正文"
    tool_rounds = [c for c in client.calls if c["tools"] is not None]
    forced = [c for c in client.calls if c["tools"] is None]
    assert forced, "没有出现'不带工具'的强制作答轮"
    assert tool_rounds, "常规轮消失了"
    # 强制作答必须是最后一次调用
    assert client.calls[-1]["tools"] is None, "最后一次调用还带着工具"
    # 强制作答轮之后不许再调模型
    assert len(client.calls) == len(tool_rounds) + 1, (
        "强制作答之后还有 %d 次调用" % (len(client.calls) - len(tool_rounds) - 1))
