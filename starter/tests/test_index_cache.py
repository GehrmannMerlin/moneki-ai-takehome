"""缓存读回来的索引必须和写下去的那一份**完全等价**（P2 回归，实测踩到过）。

这是一条"端到端等价性"测试，不针对某个具体缺陷编号——它抓的是一类错误：
**索引落盘时丢掉的信息，从缓存读回来之后会让检索悄悄变差。**

实际踩到的那一次：`load_index` 从 JSON 重建 `BM25Index` 时会重新算 postings，
而当时 `_prepare_tokenizer`（把别名词典挂进 jieba）是在重建**之后**才调的。
于是缓存索引里 `吞拿鱼三明治` 这类别名被 jieba 按默认词典切碎。

症状极其隐蔽：**新进程直接 `build_index` 检索正常，走缓存就少几篇**。
R10「S04 为什么不卖吞拿鱼三明治了」因此从绿变红，而 `kb_chunks` 看起来完全正常
（113 块都在，只是分词不一样）。评测暴露它的方式是逐条对金标。

修法不是"补一次挂词典"，而是**把分词结果冻结进索引**（`Chunk.tokens` 随盘落盘）：
jieba 的 `add_word` 是全局累积的，只要还在"重建时重新分词"，
索引就会随运行时的词典状态漂移。冻结之后，检索用的词只由落盘那一刻决定。

> 说明一条实测到的限制：同一个进程里反复调 `build_index()`，postings 仍可能有
> ±2 个词的差异——jieba 的全局词典会被 `add_word` 持续影响，这是第三方库的行为，
> 不是我们的逻辑错。真正需要保证、也已经保证的是
> **"写下去的那份 == 读回来的那份"**，也就是下面这些断言。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# 见 tests/defects/conftest.py 的同类说明：两个 conftest.py 在 sys.modules 里同名，
# 按路径显式载入父级那一份来取 WORKSPACE。
_PARENT = Path(__file__).resolve().parent / "conftest.py"
_spec = importlib.util.spec_from_file_location("_p2_parent_conftest", _PARENT)
_parent = importlib.util.module_from_spec(_spec)
sys.modules["_p2_parent_conftest"] = _parent
_spec.loader.exec_module(_parent)

KB_DIR = _parent.WORKSPACE / "knowledge_base"

#: 别名词典里的写法，jieba 默认词典会把它们切碎。
ALIAS_TERMS = ("吞拿鱼三明治", "三文鱼poke", "牛肉poke", "味增拉面", "冷萃乌龙茶")


@pytest.fixture()
def round_trip(tmp_path):
    """写一次、读一次，返回 (写下去的, 读回来的)。

    **必须是函数级**：`tests/conftest.py` 的 `client` 夹具会把
    `Retriever.search` 换成固定返回、且**不还原**，所以模块级的索引夹具
    可能在补丁生效前就建好了，之后所有检索断言都在测替身
    （实测踩到：单跑 12 passed，全量跑就 3 failed，报"top-5 里缺少 KB-029"）。

    另外 `rebuild=True` 每次重新写缓存，顺带保证不会读上一次测试遗留的索引。
    """
    from kbqa.core.index import load_index

    cache = tmp_path / "index.json"
    written = load_index(KB_DIR, cache, rebuild=True)
    loaded = load_index(KB_DIR, cache)
    return written, loaded


def test_cache_key_matches(round_trip):
    written, loaded = round_trip
    from kbqa.core.index import content_key

    assert loaded.key == written.key == content_key(KB_DIR)


def test_same_postings(round_trip):
    """postings 的键**与词频**都要一样，不只是块数一样。"""
    written, loaded = round_trip

    missing = sorted(set(written.postings) - set(loaded.postings))
    extra = sorted(set(loaded.postings) - set(written.postings))
    assert not missing and not extra, (
        "缓存索引的 postings 与写下去的不一致：少 %d 个词（例：%s）、多 %d 个（例：%s）"
        % (len(missing), missing[:6], len(extra), extra[:6]))

    # 词频也要一致：只比键集合会漏掉"同一个词但 tf 变了"的情况
    for term in ("吞拿鱼三明治", "退款", "S04"):
        if term not in written.postings:
            continue
        assert [(p.chunk_index, p.freq) for p in written.postings[term]] == \
               [(p.chunk_index, p.freq) for p in loaded.postings[term]], (
            "%s 的 postings 明细不一致" % term)


def test_tokens_frozen_and_round_tripped(round_trip):
    """分词结果必须冻结在 chunk 上，并且原样读回来。"""
    written, loaded = round_trip

    assert all(chunk.tokens for chunk in written.chunks), (
        "有 chunk 没有冻结 tokens——`build_index` 忘了给它们分词")
    assert [chunk.tokens for chunk in written.chunks] == \
           [chunk.tokens for chunk in loaded.chunks], "chunk.tokens 落盘后变了"


@pytest.mark.parametrize("term", ALIAS_TERMS)
def test_alias_terms_survive_the_round_trip(round_trip, term):
    """别名词典里的写法在缓存索引里不能被切碎。"""
    written, loaded = round_trip

    assert term in written.postings, "前提不成立：%s 不在写下去的索引里" % term
    assert term in loaded.postings, (
        "%s 在缓存索引里被切碎了——从缓存读回来时丢掉了落盘时的分词结果" % term)


def test_second_read_is_stable(tmp_path):
    """连读两次也要一样（缓存不能是"读一次就坏"的）。"""
    from kbqa.core.index import load_index

    cache = tmp_path / "index.json"
    load_index(KB_DIR, cache, rebuild=True)
    first = load_index(KB_DIR, cache)
    second = load_index(KB_DIR, cache)
    assert set(first.postings) == set(second.postings)


@pytest.mark.parametrize("query,gold", [
    ("S04 为什么不卖吞拿鱼三明治了", "KB-029"),
    ("三文鱼那次断供供应商赔了多少钱", "KB-022"),
    ("Super Souper 周五晚上营业到几点", "KB-062"),
])
def test_same_top5_from_cache(round_trip, query, gold):
    """同一查询在"写下去的那份"与"读回来的那份"上必须给出同一个 top-5。"""
    from datetime import date

    from kbqa.core.retriever import Retriever

    written, loaded = round_trip
    hits_written = [h.doc_id for h in
                    Retriever(written, date(2026, 9, 1)).search(query, top_k=5).hits]
    hits_loaded = [h.doc_id for h in
                   Retriever(loaded, date(2026, 9, 1)).search(query, top_k=5).hits]
    assert hits_loaded == hits_written, (
        "%r 在两份索引上的 top-5 不同：\n  读缓存 %s\n  写下去 %s"
        % (query, hits_loaded, hits_written))
    assert gold in hits_loaded, "%r 的 top-5 里缺少 %s：%s" % (query, gold, hits_loaded)
