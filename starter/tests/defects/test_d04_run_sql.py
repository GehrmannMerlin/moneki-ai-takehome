"""缺陷 D4：`run_sql` 可执行任意 SQL 且会 commit（`tools.py:74-79`）。

契约与作业都要求数据库**只读**：`/api/chat` 无论收到什么指令都不许改数据，
评测每道安全题之后都会重查一次 `/api/metrics/summary` 比对
（`post.metrics_unchanged`）。

starter 的 `run_sql()` 直接 `self.conn.execute(sql)` 再 `self.conn.commit()`，
连接也不是只读模式（`cleaning.py:70-74` 那个叫 `open_readonly` 的函数
其实只是普通 `connect`），所以一条 `DELETE` 就能改掉数据。

P1 的处理：把这条通道整个移除。P3 会以"只读 SQL 闸"的形态重新引入
（`SELECT`/`WITH` 开头 + 必须有 `FROM` + `mode=ro` 连接），届时的验收是
`test_metrics_unchanged_after_chat`（chat 之后重查指标不变）。

> 测试方法上的一个坑（第一版踩过）：不能用「DROP TABLE 之后再试 UPDATE」来判——
> 一次失败的 DDL 会把当前 SQLite 连接留在异常状态，后续写语句会静默影响 0 行，
> 于是测试"通过"了，但它证明的是连接坏了，不是数据被保护了。
> 判据必须是**数据本身有没有变**，而不是语句抛没抛错。
"""

from __future__ import annotations

import pytest

DROP = "DROP TABLE sales_clean"
WRITES = [
    ("update", "UPDATE sales_clean SET amount_cents = 0"),
    ("delete", "DELETE FROM sales_clean"),
    ("insert", "INSERT INTO stores VALUES ('S99','x','y','z')"),
]


def _fresh_tools(db):
    from kbqa.tools import DataTools

    return DataTools(db)


def test_no_writable_sql_channel(legacy_tools):
    """清洗表之上不许存在一条能执行写操作的 SQL 通道。

    starter 原样下这条是红的（`run_sql` 存在且能写）；P1 移除该通道后转绿。
    """
    assert not hasattr(legacy_tools, "run_sql"), (
        "DataTools 仍然暴露 run_sql()：任意 SQL 都能执行，且连接会 commit")


@pytest.mark.parametrize("label,sql", WRITES + [("drop", DROP)],
                         ids=[w[0] for w in WRITES] + ["drop"])
def test_write_attempt_leaves_data_unchanged(legacy_clean_db, label, sql):
    """写语句不许改到数据：攻击前后的指标必须逐字段相同。

    每个用例一条独立连接与独立查询，避免上一条语句把连接弄脏后
    影响这一条的判据（那正是第一版假绿的原因）。
    """
    tools = _fresh_tools(legacy_clean_db)
    before = tools.query_metrics("2026-06-01", "2026-06-30")
    try:
        tools.run_sql(sql)
    except Exception:  # noqa: BLE001 - 拒绝执行本身就是正确行为
        pass

    check = _fresh_tools(legacy_clean_db)
    after = check.query_metrics("2026-06-01", "2026-06-30")
    assert after == before, (
        "执行 %s（%s）之后指标变了：\n  前 %s\n  后 %s" % (label, sql, before, after))


def test_engine_connection_is_readonly(tmp_var, legacy_clean_db):
    """口径引擎自己的连接必须落在只读模式上，不能只靠上层不发写语句。"""
    import sqlite3

    from kbqa.core.metrics import MetricsEngine

    # 前提：文件本身可写，所以"写不进去"只可能来自连接模式
    probe = sqlite3.connect(str(legacy_clean_db))
    try:
        probe.execute("CREATE TABLE _p1_write_probe (x INTEGER)")
        probe.rollback()
    finally:
        probe.close()

    engine = MetricsEngine(legacy_clean_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            engine.conn.execute("INSERT INTO stores VALUES ('S99','x','y','z')")
    finally:
        engine.close()


def test_data_period_is_clean(legacy_tools):
    """数据区间必须是干净的 ISO 日期，不能是 '' 或 'N/A'。

    starter 的 `data_period()` 直接 MIN/MAX 在未规范化的 date 字符串上，
    结果是 `{start: "", end: "N/A"}`——它同时是 P3 区间闸的判据来源。
    """
    period = legacy_tools.data_period()
    assert period.get("start") == "2026-05-01", (
        "数据区间起点是 %r，应为 '2026-05-01'" % period.get("start"))
    assert period.get("end") == "2026-08-31", (
        "数据区间终点是 %r，应为 '2026-08-31'" % period.get("end"))


def test_valid_sales_rows_counts_non_rejected(legacy_tools):
    """`/api/health` 的 valid_sales_rows 必须是"保留行数"。"""
    got = legacy_tools.valid_sales_rows()
    assert got == 18290, "valid_sales_rows 是 %s，应为 18290" % got
