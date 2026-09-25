"""缺陷 D7：loader 只收 `.md`/`.markdown`（`loader.py:12`）。

`SUPPORTED_SUFFIXES = {".md", ".markdown"}` 把三篇文档整个丢掉：

* `KB-022_Supplier_Email_Salmon_Incident.txt`（英文供应商邮件，R04 金标）
* `KB-061_常见问题FAQ.html`（HTML，过敏原/FAQ 相关）
* `KB-062_旧OA导出_营业时间调整通知.txt`（GBK，**C03/R03 的金标答案**）

`kb_docs` 因此只有 32，`kb_chunks=53`。丢掉 KB-062 的后果最直接：
"Super Souper 周五营业到几点"只能命中 KB-042 的 21:30，而正确答案是 23:00。
"""

from __future__ import annotations

from conftest import LOST_WITHOUT_TXT_HTML, load_documents


def test_all_three_lost_docs_are_indexed(documents):
    """三篇被丢的文档必须都在索引里。txt 两篇 + html 一篇。"""
    doc_ids = {d.doc_id for d in documents}
    missing = [doc_id for doc_id in LOST_WITHOUT_TXT_HTML if doc_id not in doc_ids]
    assert not missing, (
        "%s 没有进索引——SUPPORTED_SUFFIXES 只收 .md/.markdown 时它们整个丢掉"
        % "、".join(missing))


def test_document_count_is_35(documents):
    """35 篇（目录里 36 个文件，README.md 没有 KB 编号）。"""
    assert len(documents) == 35, "加载到 %d 篇文档，应为 35 篇" % len(documents)


def test_txt_email_loaded(documents):
    """KB-022 是 .txt，必须能读进来且有正文。"""
    doc = next((d for d in documents if d.doc_id == "KB-022"), None)
    assert doc is not None, "KB-022（.txt）没有进索引"
    assert len(doc.text) > 500, "KB-022 正文只有 %d 字，明显没读全" % len(doc.text)
    assert "Salmon" in doc.text, "KB-022 正文里找不到 'Salmon'"


def test_html_doc_loaded(documents):
    """KB-061 是 .html，必须进索引。"""
    doc = next((d for d in documents if d.doc_id == "KB-061"), None)
    assert doc is not None, "KB-061（.html）没有进索引"
    assert doc.fmt == "html"
    assert len(doc.text) > 500


def test_plain_md_still_loaded(documents):
    """回归：原有的 .md 不能被新后缀逻辑弄丢。"""
    doc_ids = {d.doc_id for d in documents}
    for doc_id in ("KB-001", "KB-003", "KB-013", "KB-042", "KB-060"):
        assert doc_id in doc_ids, "%s 丢了" % doc_id


def test_warning_for_non_doc_file(documents):
    """没有 KB 编号的文件要跳过并留 warning（README.md）。"""
    _docs, warnings = load_documents()
    assert any("README" in w for w in warnings), (
        "跳过 README.md 时没有留 warning：%s" % warnings)
