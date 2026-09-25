"""P1 缺陷复现测试的公共夹具。

这些测试断言的是**正确行为**，所以在新核心落地前它们必然失败——这正是
DEBUG_LOG 里"修复前确实是红的"的证据。

为什么要绕一圈"在临时目录建一份 clean.db"：
starter 的 `kbqa.tools.DataTools` 本身**不是**错的，它错在 `_where()` 的右开区间
与 `query_metrics()` 的 v2 口径。所以复现测试从"清洗表"这一层往下断言，
不直接调 `Service`——否则清洗缺陷会一起混进来，定位不出是哪一层坏了。
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

#: 六条剔除规则的期望计数（独立复算，期望值写在 tests/conftest.EXPECTED 里）。
EXPECTED_REMOVED = EXPECTED["removed"]
EXPECTED_VALID = EXPECTED["valid_sales_rows"]
EXPECTED_RAW = EXPECTED["raw_sales_rows"]


@pytest.fixture(scope="session")
def source_db() -> Path:
    return WORKSPACE / "data" / "pos.db"


@pytest.fixture(scope="module")
def legacy_clean_db(tmp_path_factory, source_db: Path) -> Path:
    """用 **starter 原有的** `kbqa.cleaning.build_clean_db` 建一份 clean.db。

    只依赖 `sales_clean` 这一张表——旧 schema 直接建表，新 schema 用兼容视图提供，
    所以同一个夹具在新核心落地前后都能跑，差别只在"表里的内容对不对"。
    """
    from kbqa.cleaning import build_clean_db

    target = tmp_path_factory.mktemp("legacy") / "clean.db"
    build_clean_db(source_db, target)
    return target


@pytest.fixture(scope="module")
def legacy_tools(legacy_clean_db: Path):
    """指向那份 clean.db 的 starter DataTools。"""
    from kbqa.tools import DataTools

    return DataTools(legacy_clean_db)


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
