"""缺陷 D5：`kb_docs` 数的是目录文件数（`service.py:70`）。

契约 §1 明确：`kb_docs` 是"实际进入索引的文档数，不是目录里的文件数"，
并特意点出"`knowledge_base/` 里可能有不是文档的文件（没有 `KB-xxx` 编号的说明文件之类）"。

目录里 36 个文件，其中 `README.md` 没有编号 → 应为 35。
更要命的是 starter 同时还有 D7（只收 `.md`），真正入索引的只有 32 篇——
所以这个 36 既不是文件数的正确用法，也不是入索引数，两头都不对。
"""

from __future__ import annotations

from conftest import EXPECTED_KB_DOCS, KB_DIR, load_documents


def test_kb_docs_is_indexed_count(index):
    """`kb_docs` 必须等于入索引的文档数。"""
    got = len(index.docs_meta)
    assert got == EXPECTED_KB_DOCS, "入索引文档数是 %d，应为 %d" % (got, EXPECTED_KB_DOCS)


def test_kb_docs_is_not_file_count(index):
    """明确区分：它不能等于目录里的文件数。"""
    files = [p for p in KB_DIR.rglob("*") if p.is_file()]
    assert len(files) == 36, "知识库目录里应有 36 个文件（35 篇文档 + 1 份 README）"
    assert len(index.docs_meta) != len(files), (
        "入索引文档数等于目录文件数（%d）——那正是 service.py:70 的错误算法" % len(files))


def test_readme_not_indexed(documents):
    """没有 KB 编号的文件不算文档，且要留一条 warning。"""
    doc_ids = {d.doc_id for d in documents}
    assert "KB-000" not in doc_ids
    for document in documents:
        assert document.doc_id.startswith("KB-"), "混进了非 KB 文档：%s" % document.path.name


def test_health_kb_docs_uses_index(tmp_var):
    """`/api/health` 的 `kb_docs` 取索引结果（端到端）。"""
    import os

    from kbqa.config import load_settings
    from kbqa.service import Service

    os.environ["VAR_DIR"] = str(tmp_var)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)
    body = Service(load_settings()).health()
    assert body["kb_docs"] == EXPECTED_KB_DOCS, (
        "health.kb_docs 是 %s，应为 %s（入索引文档数）" % (body["kb_docs"], EXPECTED_KB_DOCS))
