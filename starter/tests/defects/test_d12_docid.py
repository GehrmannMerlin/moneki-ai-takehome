"""缺陷 D12：`hit.doc_id` 张冠李戴（`retriever.py:275-276`）。

```python
hit = self._hit(position, score, filtered)     # ← 这里 doc_id 已经是正确的
# 第几条命中就取排序里的第几篇文档。
hit.doc_id = ordered[len(hits)].doc_id          # ← 又被覆写成别人的
```

`self._hit()` 返回的 `Hit.doc_id` 本来就等于 `chunk.doc_id`（第 208 行），
紧接着被 `ordered[len(hits)].doc_id` 覆盖。`len(hits)` 是"已经收集了几条"，
`ordered` 是"按分数排好序的全部 chunk"——两个下标毫无关系，
所以返回的 doc_id 是**排在第 n 位的那个 chunk 所属的文档**，不是这条 hit 自己的。

后果：`citations` 指向错文档、`gold_all` 大面积红、`quotes_verbatim` 拿到
"quote 不属于该 doc_id"的失败。这是检索类失分里最隐蔽的一处——
分数对、文本对，只有 doc_id 是错的。
"""

from __future__ import annotations

import re

import pytest

QUERIES = [
    "外卖订单多久内可以退款",
    "Super Souper 周五营业到几点",
    "会员储值充值送多少",
    "员工折扣几折",
    "过敏原对照表",
    "salmon",
    "味噌拉面",
    "S03 停业",
]


def test_hit_docid_matches_chunk_owner_on_every_chunk(index, today):
    """**确定性判据**：遍历每个 chunk 单独查一次，断言返回的 doc_id == 该 chunk 的真实归属。

    为什么不用"随便挑几个查询"：`retriever.py:276` 那行覆写是
    `hit.doc_id = ordered[len(hits)].doc_id`，而 `ordered` 就是按分数排好的全部 chunk。
    要触发它，必须让**先处理的 chunk 被"每篇限占一格"跳过**，位置才会错开。
    挑查询来撞这件事不可靠（我第一版就是这样，8 个查询里只有 1 个撞上）。
    逐 chunk 遍历则每个位置都试一遍，稳定命中。
    """
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    wrong = []
    for chunk in index.chunks[:60]:
        result = retriever.search(chunk.text[:40], top_k=5)
        for hit in result.hits:
            owner = _owner(index, hit.chunk_id)
            if owner and hit.doc_id != owner:
                wrong.append("%s 应属 %s，被写成 %s" % (hit.chunk_id, owner, hit.doc_id))
    assert not wrong, (
        "有 %d 条命中的 doc_id 与它 chunk 的真实归属不符（例：%s）——"
        "retriever.py:276 用 `ordered[len(hits)].doc_id` 覆写了正确的 doc_id"
        % (len(wrong), wrong[0]))


@pytest.mark.parametrize("query", QUERIES)
def test_hit_docid_owns_chunk(retriever, query):
    """任意查询：每个 hit 的 doc_id 必须等于它 chunk_id 的 `#` 前缀。"""
    result = retriever.search(query, top_k=5)
    assert result.hits, "%r 一条都没检索到" % query
    bad = [
        "%s ↔ %s" % (hit.doc_id, hit.chunk_id)
        for hit in result.hits
        if not hit.chunk_id.startswith(hit.doc_id + "#")
    ]
    assert not bad, (
        "%r 的命中里 doc_id 与 chunk_id 对不上：%s——"
        "retriever.py:276 用排序位置覆写了真实 doc_id" % (query, "、".join(bad)))


def test_hit_text_belongs_to_docid(retriever, index):
    """更强的判据：hit.text 必须真的属于它自称的那个文档。"""
    result = retriever.search("外卖订单多久内可以退款", top_k=5)
    for hit in result.hits:
        if hit.padded:
            continue
        expected = next(
            (c.text for c in index.chunks if c.chunk_id == hit.chunk_id), None)
        assert expected is not None, "chunk_id %s 不在索引里" % hit.chunk_id
        assert hit.text == expected, (
            "hit.text 与它自称的 chunk（%s）内容不一致" % hit.chunk_id)
        assert index.chunks and _owner(index, hit.chunk_id) == hit.doc_id


def _owner(index, chunk_id: str) -> str:
    for chunk in index.chunks:
        if chunk.chunk_id == chunk_id:
            return chunk.doc_id
    return ""


def test_all_hits_are_consistent_across_queries(retriever):
    """批量扫一遍：不许出现"多数对、个别错"这种模式。"""
    bad = []
    for query in QUERIES:
        for hit in retriever.search(query, top_k=5).hits:
            if not re.match(r"^KB-\d{3}#\d+$", hit.chunk_id):
                bad.append("chunk_id 形状不对：%s" % hit.chunk_id)
                continue
            if not hit.chunk_id.startswith(hit.doc_id + "#"):
                bad.append("%s ↔ %s" % (hit.doc_id, hit.chunk_id))
    assert not bad, "doc_id 与 chunk_id 不一致：%s" % "、".join(bad[:10])
