"""缺陷 D9：HTML 不剥标签直接入库（`loader.py:178-182`）。

```python
elif fmt == "html":
    # html 直接按文本入库，标签也就那么几个，BM25 自己会忽略。
    match_title = _HTML_TITLE.search(text)
```

"BM25 自己会忽略"是错的：标签会真的进 postings，把正文的词频稀释掉，
`<style>` 块里那一大段 CSS（`font-family`/`padding`/`border-radius`…）
还会变成一堆假词参与打分。而且 `quote` 里带着标签，
评测的逐字校验（先剥标签再比）必然不过。

评测脚本 `run_eval.py:211-214` 的 `html_to_text` 会先删 `<script>`/`<style>`、
再删所有标签、最后反转义实体。
"""

from __future__ import annotations

from conftest import KB_DIR

KB061 = KB_DIR / "reference" / "KB-061_常见问题FAQ.html"


def _doc(documents, doc_id: str):
    return next((d for d in documents if d.doc_id == doc_id), None)


def test_html_document_has_no_tags(documents):
    """入库正文里不能有标签残留。"""
    doc = _doc(documents, "KB-061")
    assert doc is not None, "KB-061 没有进索引（D7）"
    assert "<" not in doc.text and ">" not in doc.text, (
        "KB-061 正文里还有标签：%r" % doc.text[:200])


def test_html_document_has_no_style_or_script(documents):
    """`<style>`/`<script>` 的内容整块剥掉，不能变成假词。"""
    doc = _doc(documents, "KB-061")
    assert doc is not None
    for leak in ("border-radius", "font-family", "querySelector", "dataLayer"):
        assert leak not in doc.text, "KB-061 正文里残留了 %s（style/script 没剥）" % leak


def test_html_entities_unescaped(documents):
    """HTML 实体要反转义：正文里应是 `&` 而不是 `&amp;`。"""
    doc = _doc(documents, "KB-061")
    assert doc is not None
    assert "&amp;" not in doc.text and "&lt;" not in doc.text, "HTML 实体没有反转义"
    assert "&nbsp;" not in doc.text


def test_html_keeps_real_content(documents):
    """剥完之后真正的正文要留着。"""
    doc = _doc(documents, "KB-061")
    assert doc is not None
    assert "怎么开发票" in doc.text, "剥标签把正文也剥掉了"
    assert "Wi-Fi" in doc.text


def test_html_title_extracted(documents):
    """标题从 `<title>` 取，且不要带站点后缀。"""
    doc = _doc(documents, "KB-061")
    assert doc is not None
    assert doc.title and "合味餐饮" not in doc.title, (
        "KB-061 的标题是 %r，应剥掉 ` - 合味餐饮` 这类站点后缀" % doc.title)


def test_chunks_of_html_have_no_tags(index):
    """真正进检索的是 chunk，所以 chunk 里也不能有标签。"""
    docs = {chunk.doc_id for chunk in index.chunks}
    assert "KB-061" in docs, "KB-061 没有产出任何 chunk"
    for chunk in index.chunks:
        if chunk.doc_id != "KB-061":
            continue
        assert "<" not in chunk.text, "KB-061 的 chunk 里有标签：%r" % chunk.text[:120]
