"""P3 复查发现的混合问答缺陷回归测试（先红后绿）。

公开题库从 88.00 到 100.00 的四条修复，每条对着评测检查项写断言：

* **D19** `planner._choose_kind` —— 「卖了多少份？达到目标了吗」里的"多少"把
  `kind=target` 压成 `summary`，达标判定丢失（H02 的 `text_any` 红）。
* **D20** `core/intent.classify` —— 「现金支付占比」不被当作经营指标，
  「…占比是多少？为什么」缺数据侧，按纯文档答（H05 的 `answer_type_in` /
  `numbers_all` / `evidence_required` 三项全红）。
* **D21** `answerer._merge_doc_side` —— why 类问题 `_cause_block` 已判定没有
  文档能解释，合并层仍把相邻文档引上（H06 的 `cite_max=0` 红）。
* **D22** `answerer._doc_block` —— 跨语言答案句与中文问句零词面重叠时
  句子级打分得 0、永远浮不出（C04/T02 的 `fact_all`/`cite_all` 红，
  答案在英文邮件 KB-022 的 "credit note of CNY 8,600" 里）。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def service(tmp_var):
    """真实 Service，VAR_DIR 指向独立临时目录。"""
    import os

    os.environ["VAR_DIR"] = str(tmp_var)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)
    from kbqa.config import load_settings
    from kbqa.service import Service

    return Service(load_settings())


# --------------------------------------------------------------------------- D19

def test_target_verdict_kept(service):
    """H02：达标问题的回答必须给出"达标/未达标"结论（评测 `text_any`）。"""
    answer = service.chat("d19", "618 当天 S02 的牛肉poke 卖了多少份？达到目标了吗？")
    assert answer["answer_type"] == "hybrid", answer["answer"]
    assert any(word in answer["answer"] for word in ("达标", "达到目标", "完成目标", "达成")), (
        "回答里没有达标结论：%s" % answer["answer"])
    assert answer["citations"], "目标值来自活动方案，必须有引用"
    assert answer["data_evidence"], "实际销量来自数据库，必须有 data_evidence"


def test_planner_keeps_target_kind(service):
    """D19 根因：planner 不得把显式的 target 压成 summary。"""
    plan = service.planner.plan("618 当天 S02 的牛肉poke 卖了多少份？达到目标了吗？", [])
    assert plan.kind == "target", "kind 被压成了 %s" % plan.kind


# --------------------------------------------------------------------------- D20

def test_payment_share_why_is_hybrid(service):
    """H05：支付占比 + 为什么 → hybrid，带 100%% 与 data_evidence，引 KB-027。"""
    answer = service.chat("d20", "8 月 3 日 S05 的现金支付占比是多少？为什么会这样？")
    assert answer["answer_type"] == "hybrid", answer["answer"]
    assert "100" in answer["answer"], "当天全现金，回答应含 100：%s" % answer["answer"]
    assert answer["data_evidence"], "支付占比来自 payment_mix，必须有 data_evidence"
    assert any(c["doc_id"] == "KB-027" for c in answer["citations"]), (
        "原因在 KB-027（POS 终端故障报告），实际引用：%s" % answer["citations"])


def test_payment_words_count_as_metric(service):
    """D20 根因：意图分类要把支付类词当作指标信号。"""
    from kbqa.core import intent as intent_mod

    intent = intent_mod.classify("8 月 3 日 S05 的现金支付占比是多少？为什么会这样？")
    assert intent.kind == "hybrid", "被分成 %s" % intent.kind


# --------------------------------------------------------------------------- D21

def test_why_without_explanation_cites_nothing(service):
    """H06：没有文档解释的异常，严禁拿相邻文档凑引用（评测 `cite_max=0`）。"""
    answer = service.chat("d21", "S02 在 8 月 17 日到 19 日为什么一分钱营业额都没有？")
    assert answer["citations"] == [], (
        "没有文档解释时引用必须为空，实际：%s" % answer["citations"])
    assert answer["answer_type"] in ("data", "refusal"), answer["answer_type"]
    assert "0.00" in answer["answer"] or "0 元" in answer["answer"], (
        "数字事实本身仍要给出：%s" % answer["answer"])


# --------------------------------------------------------------------------- D22

def test_compensation_amount_from_english_doc(service):
    """C04：赔偿金额在英文邮件 KB-022 里，回答要含 8600 并引用 KB-022。"""
    answer = service.chat("d22", "三文鱼那次断供，供应商最后赔了我们多少钱？")
    assert any(c["doc_id"] == "KB-022" for c in answer["citations"]), (
        "必须引用 KB-022，实际：%s" % answer["citations"])
    assert "8,600" in answer["answer"] or "8600" in answer["answer"], (
        "回答里要有赔偿金额 8600：%s" % answer["answer"])


def test_compensation_followup(service):
    """T02 第 2 轮：追问「供应商后来赔了多少？」同样要命中 KB-022 与 8600。"""
    service.chat("d22-follow", "三文鱼poke 七月初为什么停售了？")
    answer = service.chat("d22-follow", "供应商后来赔了多少？")
    assert any(c["doc_id"] == "KB-022" for c in answer["citations"]), (
        "必须引用 KB-022，实际：%s" % answer["citations"])
    assert "8,600" in answer["answer"] or "8600" in answer["answer"], answer["answer"]


def test_value_rescue_skips_estimates_only(service):
    """D22 边界：兜底不取周报/纪要里的估算数字（KB-001 §5.2）。"""
    from kbqa.entities import expected_value_kind

    plan = service.planner.plan("三文鱼那次断供，供应商最后赔了我们多少钱？", [])
    result = service.answerer._search(plan)
    want = expected_value_kind(plan.standalone)
    for item in service.answerer._value_rescue(plan, result, want):
        meta = service.retriever.index.docs_meta.get(item["doc_id"], {})
        assert not meta.get("estimates_only"), "兜底取了估算文档 %s" % item["doc_id"]
