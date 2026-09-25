"""缺陷 D10：切块丢掉每篇文档的尾部（`chunker.py:41`）。

```python
for number, start in enumerate(range(0, len(text) - CHUNK_SIZE, CHUNK_SIZE), start=1):
    piece = text[start : start + CHUNK_SIZE]
```

`range(0, len(text) - 300, 300)` 的上界算错了：`len(text)=1000` 时
`range(0, 700, 300)` → 0/300/600，`text[600:900]` 之后那 100 字**永远不会被任何 chunk 覆盖**。
量一下更容易看出来：短于 300 字的文档一块都不切，直接落进 `if not chunks` 分支
（整篇变一块）；长文档则丢尾巴。

而且定长切点落在句子中间，没有标题层级意识——一个"退款时限"的标题可能
和上一节最后半句话粘在同一块里。

后果：关键事实恰好在文尾时检索不到（通知的联系人、政策的最后一条、邮件的落款）。
"""

from __future__ import annotations

import pytest

from conftest import KB_DIR

#: 覆盖不变式的判据：把每篇文档的 chunks 按顺序拼起来，应等于该文档的正文。
#: 允许 chunk 之间丢掉的只有空白（chunk 边界处的换行），其余一个字都不能少。
_WS = str.maketrans("", "", " \t\r\n\u3000")


def _squeeze(text: str) -> str:
    return text.translate(_WS)


@pytest.mark.parametrize("doc_id", ["KB-001", "KB-029", "KB-060", "KB-061", "KB-062", "KB-022"])
def test_full_coverage(index, doc_id):
    """chunks 拼接 == 文档正文（去空白后比较），文尾不能丢。"""
    doc_chunks = [c for c in index.chunks if c.doc_id == doc_id]
    assert doc_chunks, "%s 没有产出任何 chunk" % doc_id
    joined = _squeeze("".join(chunk.text for chunk in doc_chunks))

    document = None
    from conftest import load_documents

    for candidate in load_documents()[0]:
        if candidate.doc_id == doc_id:
            document = candidate
            break
    assert document is not None, "%s 不在索引里" % doc_id

    source = _squeeze(document.text)
    assert joined == source, (
        "%s 的 chunk 覆盖不完整：正文 %d 字，chunk 拼起来 %d 字，丢了 %d 字"
        % (doc_id, len(source), len(joined), len(source) - len(joined)))


def test_tail_of_short_doc_present(index):
    """KB-042（营业时间总表）不长，整篇必须都在 chunk 里。

    它末尾的"临时调整以通知为准"正是 C03/R03 需要的一句话。
    """
    joined = _squeeze("".join(c.text for c in index.chunks if c.doc_id == "KB-042"))
    assert "临时调整以通知为准" in joined, (
        "KB-042 的 chunk 里没有'临时调整以通知为准'——这句话在文尾，被切块丢了")


def test_tail_of_policy_present(index):
    """KB-013（退款政策 v2）的最后一条时限必须在 chunk 里。"""
    joined = _squeeze("".join(c.text for c in index.chunks if c.doc_id == "KB-013"))
    assert "24" in joined and "退款" in joined


def test_all_documents_covered(index, documents):
    """全量覆盖不变式：每篇文档都不能丢字。"""
    broken = []
    for document in documents:
        doc_chunks = [c for c in index.chunks if c.doc_id == document.doc_id]
        joined = _squeeze("".join(chunk.text for chunk in doc_chunks))
        source = _squeeze(document.text)
        if joined != source:
            broken.append("%s(丢 %d 字)" % (document.doc_id, len(source) - len(joined)))
    assert not broken, "有 %d 篇文档的 chunk 覆盖不完整：%s" % (len(broken), "、".join(broken[:8]))


def test_chunks_are_reasonably_sized(index):
    """切块要有尺寸意识：不能整篇一大块，也不能碎成一堆十几字的块。

    这里只卡一个宽松的带：90% 的 chunk 在 200~500 字之间（表格密集文档允许例外）。
    """
    lengths = sorted(len(chunk.text) for chunk in index.chunks)
    assert lengths, "没有 chunk"
    in_band = sum(1 for n in lengths if 200 <= n <= 500)
    ratio = in_band / len(lengths)
    assert ratio >= 0.9, (
        "只有 %.0f%% 的 chunk 落在 200~500 字（共 %d 块，长度分布 %s…）"
        % (ratio * 100, len(lengths), lengths[:5]))


def test_chunk_ids_are_unique_and_ordered(index):
    """`chunk_id` 形如 `KB-001#3`，同一文档内唯一且从 1 开始连续。"""
    import re

    seen = set()
    per_doc: dict[str, list[int]] = {}
    for chunk in index.chunks:
        assert chunk.chunk_id not in seen, "chunk_id 重复：%s" % chunk.chunk_id
        seen.add(chunk.chunk_id)
        match = re.fullmatch(r"(KB-\d{3})#(\d+)", chunk.chunk_id)
        assert match, "chunk_id 形状不对：%s" % chunk.chunk_id
        assert match.group(1) == chunk.doc_id, (
            "chunk_id 的前缀 %s 与 doc_id %s 不一致" % (match.group(1), chunk.doc_id))
        per_doc.setdefault(chunk.doc_id, []).append(int(match.group(2)))
    for doc_id, numbers in per_doc.items():
        assert sorted(numbers) == list(range(1, len(numbers) + 1)), (
            "%s 的 chunk 编号不连续：%s" % (doc_id, sorted(numbers)))
