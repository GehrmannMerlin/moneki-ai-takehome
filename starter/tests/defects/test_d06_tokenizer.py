"""缺陷 D6：按空白分词，中文整句成一个 token（`tokenizer.py:20-22`）。

`normalise(text).split()` 对中文等于"整句一个词"，BM25 只有查询串与文档里
出现**完全相同整句**时才可能命中。这是"检索答非所问"的总根源：
HANDOVER 宣称的"检索命中率 95%"在数学上就不成立。

基线 retrieval 15 题只对 6 题，通过的全是查询串很短或英文的情况。
"""

from __future__ import annotations

import pytest

#: 真实查询（题库 R 系题的问法）。
QUERIES = [
    "外卖订单多久内可以退款",
    "Super Souper 周五营业到几点",
    "牛肉poke 六月卖了多少钱",
    "会员现在单笔充值满 500 送多少",
]

#: 中文查询切成词之后，至少应该有这么多个 token（整句一个显然不达标）。
MIN_TOKENS = 5


@pytest.mark.parametrize("query", QUERIES)
def test_chinese_query_tokenizes_into_words(query):
    """中文查询必须切成多个词，而不是整句一个 token。"""
    from kbqa.tokenizer import tokenize

    tokens = tokenize(query)
    assert len(tokens) >= MIN_TOKENS, (
        "%r 只切出 %d 个 token（%s）——按空白切的话中文整句就是一个词，"
        "BM25 对中文实质失效" % (query, len(tokens), tokens))


def test_refund_query_splits_into_meaningful_words():
    """R02 的查询要能切出"退款""外卖"这类真正的词。"""
    from kbqa.tokenizer import tokenize

    tokens = tokenize("外卖订单多久内可以退款")
    for want in ("退款", "外卖"):
        assert any(want in token for token in tokens), (
            "'外卖订单多久内可以退款' 切出的 token %s 里没有包含 %r 的词" % (tokens, want))


def test_english_aliases_not_split_apart():
    """商品别名不能被切碎（`牛肉poke` 是一个整体）。"""
    from kbqa.tokenizer import tokenize

    tokens = tokenize("牛肉poke 六月卖了多少钱")
    assert any("poke" in token for token in tokens), (
        "别名 'poke' 被切碎了：%s" % tokens)


def test_query_and_document_share_tokens(index):
    """查询的词必须真的能在索引里命中文档。

    这是"分词对不对"的终局判据：换一个能命中的查询当然容易，
    这里是说——同一个查询在**正确分词**下应该连上某个具体文档。
    """
    from kbqa.tokenizer import tokenize

    tokens = [t for t in tokenize("外卖订单多久内可以退款") if len(t) >= 2]
    assert tokens, "分词结果为空"
    hit_terms = [term for term in tokens if index.doc_freq.get(term)]
    assert len(hit_terms) >= 2, (
        "查询 %s 里只有 %d 个词在语料里出现过（%s）——分词与文档分词对不上"
        % (tokens, len(hit_terms), hit_terms))


def test_content_tokens_drops_stopwords():
    """内容词要滤掉虚词（保留 starter 的 STOP_CHARS 语义）。"""
    from kbqa.tokenizer import content_tokens

    kept = content_tokens("外卖订单多久内可以退款")
    assert "的" not in kept and "了" not in kept
    assert any("退款" in token for token in kept)
