"""P5 现场调试演练抓到的两个缺陷的回归测试（先红后绿）。

演练方法论本身是 P5 的考核点：复现 → 看 trace → 假设 → 最小实验 → 修复 → 红测试 → 回归。

* **D25** `core/intent.classify` —— 「六月卖得最好的单品是什么」planner 判了
  `top_products`，意图复核因为认不出指标词又掰回 doc，答成门店档案。
  复核层必须认识 planner 认识的所有"能查库"的形状（此前已补过 payment，这次补 rank）。
* **D26** `answerer._doc_block` —— KB 里真没有答案时，把 H1 标题
  （"# 门店档案：S02 Makai Poke"）原样引用出来充当回答。标题不是事实，宁可拒答。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def service(tmp_var):
    import os

    os.environ["VAR_DIR"] = str(tmp_var)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)
    from kbqa.config import load_settings
    from kbqa.service import Service

    return Service(load_settings())


# --------------------------------------------------------------------------- D25

def test_rank_question_routes_to_top_products(service):
    """演练 1：销量排行类问题必须走 top_products，不许被意图复核掰回 doc。"""
    answer = service.chat("drill-1", "S03 六月卖得最好的单品是什么？")
    assert answer["answer_type"] in ("data", "hybrid"), (
        "排行问题被答成了 %s：%s" % (answer["answer_type"], answer["answer"][:120]))
    assert answer["data_evidence"], "排行数字来自 top_products 工具，必须有 data_evidence"


def test_intent_recheck_recognizes_rank_words(service):
    """D25 根因：意图分类把销量排行词当作指标信号。"""
    from kbqa.core import intent as intent_mod

    intent = intent_mod.classify("S03 六月卖得最好的单品是什么？")
    assert intent.kind == "data", "被分成 %s（hints: %s）" % (intent.kind, intent.hints)


# --------------------------------------------------------------------------- D26

def test_heading_title_is_not_cited_as_fact(service):
    """演练 2：知识库真没有「推荐菜」时，不许拿 H1 标题充当回答。"""
    answer = service.chat("drill-2", "S02 的推荐菜是什么？")
    for citation in answer["citations"]:
        assert citation["quote"].lstrip("#").strip() != "门店档案：S02 Makai Poke", (
            "把文档标题当事实引用了：%s" % citation)
    if answer["answer_type"] == "doc" and answer["citations"]:
        assert "门店档案" not in answer["answer"][:60] or "：# " not in answer["answer"], (
            "回答正文里出现原样标题，像答了其实什么都没说：%s" % answer["answer"][:120])
