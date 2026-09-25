"""缺陷复现测试的公共夹具（P1 数据层 + P2 检索层）。

这些测试断言的是**正确行为**，所以在新核心落地前它们必然失败——这正是
DEBUG_LOG 里"修复前确实是红的"的证据。

两条设计说明：

* **P1（数据层）**：为什么要绕一圈"在临时目录建一份 clean.db"——
  starter 的 `DataTools` 本身不是全错，它错在 `_where()` 的右开区间与
  `query_metrics()` 的 v2 口径，所以复现测试从"清洗表"这一层往下断言，
  不直接调 `Service`，否则清洗缺陷会一起混进来，定位不出是哪一层坏了。
* **P2（检索层）**：**不能用 `tests/conftest.py` 里那个 `client` 夹具**——
  它把 `Retriever.search` 换成了固定返回，用它测检索等于测替身。
  这一层一律从 `knowledge_base/` 造真实索引。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# tests/ 与 tests/defects/ 下各有一个 conftest.py，两者在 sys.modules 里的名字
# 都是 "conftest"。所以这里不能用 `from conftest import ...`——会撞成循环导入。
# 按路径把父级 conftest 显式载入一次，取它的 EXPECTED 与 WORKSPACE。
_PARENT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
_spec = importlib.util.spec_from_file_location("_p1_parent_conftest", _PARENT_CONFTEST)
_parent = importlib.util.module_from_spec(_spec)
sys.modules["_p1_parent_conftest"] = _parent
_spec.loader.exec_module(_parent)

EXPECTED = _parent.EXPECTED
WORKSPACE = _parent.WORKSPACE
KB_DIR = WORKSPACE / "knowledge_base"

#: 六条剔除规则的期望计数（独立复算，期望值写在 tests/conftest.EXPECTED 里）。
EXPECTED_REMOVED = EXPECTED["removed"]
EXPECTED_VALID = EXPECTED["valid_sales_rows"]
EXPECTED_RAW = EXPECTED["raw_sales_rows"]

#: 期望进入索引的文档数（目录里 36 个文件，其中 README.md 没有 KB 编号）。
EXPECTED_KB_DOCS = 35

#: 只用 starter 那个 .md 白名单时会丢掉的三篇（D7）。
#: 它们恰好都是题库金标答案所在的文档。
LOST_WITHOUT_TXT_HTML = ("KB-022", "KB-061", "KB-062")


@pytest.fixture(scope="session")
def source_db() -> Path:
    return WORKSPACE / "data" / "pos.db"


@pytest.fixture(scope="module")
def legacy_clean_db(tmp_path_factory, source_db: Path) -> Path:
    """用当前清洗实现建一份 clean.db。

    这个夹具在做复现测试时指向的是 **starter 老实现**（`kbqa.cleaning`），
    新核心（`kbqa.core.cleaning`）落地后改指新实现——同一批断言，
    先红后绿，红→绿的分界就是 `fix: D1–D4` 那个 commit。

    只依赖 `sales_clean` 这个名字：新 schema 用兼容视图提供它，
    所以断言不用跟着 schema 一起改。
    """
    from kbqa.core.cleaning import build_clean_db

    target = tmp_path_factory.mktemp("clean") / "clean.db"
    build_clean_db(source_db, target)
    return target


@pytest.fixture(scope="module")
def legacy_tools(legacy_clean_db: Path):
    """指向那份 clean.db 的数据工具（当前实现）。"""
    from kbqa.core.datatools import DataTools
    from kbqa.core.metrics import MetricsEngine

    return DataTools(MetricsEngine(legacy_clean_db))


def row_count(db: Path, where: str = "1=1", params: tuple = ()) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM sales_clean WHERE %s" % where, params).fetchone()[0])
    finally:
        conn.close()


def scalar(db: Path, sql: str, params: tuple = ()):
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def table_exists(db: Path, name: str) -> bool:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0] > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# P2 检索层夹具
#
# 复现阶段指向 starter 老实现（`kbqa.loader` / `kbqa.index` / `kbqa.retriever`）；
# 新核心（`kbqa.core.*`）落地后改指新实现——同一批断言，先红后绿。
# ---------------------------------------------------------------------------

#: 复现阶段 = starter 老实现；新核心落地后改成 "kbqa.core"。
SEARCH_PACKAGE = "kbqa.core"


def build_index(kb_dir: Path = KB_DIR):
    """按当前实现加载知识库并建索引。"""
    module = __import__("%s.index" % SEARCH_PACKAGE, fromlist=["build_index"])
    return module.build_index(kb_dir)


def load_documents(kb_dir: Path = KB_DIR):
    """按当前实现加载知识库，返回（文档列表, warnings）。"""
    module = __import__("%s.loader" % SEARCH_PACKAGE, fromlist=["load_knowledge_base"])
    return module.load_knowledge_base(kb_dir)


@pytest.fixture(scope="session")
def kb_dir() -> Path:
    return KB_DIR


@pytest.fixture(scope="session")
def documents():
    docs, _warnings = load_documents()
    return docs


@pytest.fixture(scope="session")
def index():
    return build_index()


@pytest.fixture(scope="session")
def retriever(index):
    from datetime import date

    module = __import__("%s.retriever" % SEARCH_PACKAGE, fromlist=["Retriever"])
    return module.Retriever(index, date(2026, 9, 1))


@pytest.fixture(scope="session")
def today():
    from datetime import date

    return date(2026, 9, 1)


def doc_of(documents, doc_id: str):
    for document in documents:
        if document.doc_id == doc_id:
            return document
    return None
