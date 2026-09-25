"""缺陷 D1：清洗层完全不清洗（`cleaning.py:77-102`）。

断言的是 KB-001 v3 §3 六条剔除规则跑完**应该**得到的结果：
保留 18290 行、六项剔除 8/150/30/10/40/100、合计剔除 338，且守恒。

starter 原样下这些断言全红——`clean_rows()` 里一个剔除分支都没有。
"""

from __future__ import annotations

import pytest

from conftest import (
    EXPECTED_RAW,
    EXPECTED_REMOVED,
    EXPECTED_VALID,
    row_count,
    scalar,
    table_exists,
)


def test_valid_rows_18290(legacy_clean_db):
    """按 KB-001 v3 清洗后应保留 18290 行（销售 18196 + 退款 94）。"""
    got = row_count(legacy_clean_db)
    assert got == EXPECTED_VALID, (
        "清洗后保留了 %d 行，应为 %d（原始 %d 行，应剔除 %d 行）"
        % (got, EXPECTED_VALID, EXPECTED_RAW, EXPECTED_RAW - EXPECTED_VALID))


def test_removed_breakdown(legacy_clean_db):
    """六项剔除计数必须能逐一取到，且与独立复算一致（首因归因，按序）。"""
    report = _cleaning_report(legacy_clean_db)
    removed = report.get("removed") or {}
    problems = []
    for rule, want in EXPECTED_REMOVED.items():
        got = removed.get(rule)
        if got != want:
            problems.append("%s：期望 %s，实际 %s" % (rule, want, got))
    assert not problems, "剔除分布对不上：%s（完整报告：%s）" % ("；".join(problems), removed)


def test_conservation(legacy_clean_db):
    """保留 + 剔除 == 原始行数。这条不成立就说明有行被静默丢了或重复计入。"""
    report = _cleaning_report(legacy_clean_db)
    removed = report.get("removed") or {}
    kept = report.get("kept_rows")
    total_removed = sum(v for k, v in removed.items() if k != "note_unparseable_amount")
    assert kept is not None, "清洗报告里没有 kept_rows"
    assert kept + total_removed == EXPECTED_RAW, (
        "守恒被打破：保留 %s + 剔除 %s = %s，原始 %s"
        % (kept, total_removed, (kept or 0) + total_removed, EXPECTED_RAW))
    assert kept == EXPECTED_VALID


def test_calendar_illegal_date_rejected(legacy_clean_db):
    """`'2026-13-45'` 正则合法但日历非法，必须剔除（否则 valid_sales_rows 变 18293）。"""
    if not table_exists(legacy_clean_db, "rejects_audit"):
        pytest.fail("没有 rejects_audit 表，无法逐条核对剔除原因（D1 未修复）")
    rows = _audit(legacy_clean_db, "1_unparseable_date")
    assert len(rows) == 8, "规则 1 应有 8 行，实际审计到 %d 行" % len(rows)
    joined = " ".join(rows)
    assert "2026-13-45" in joined, "审计记录里没有 '2026-13-45' 这 3 行"
    assert joined.count("2026-13-45") == 3, (
        "'2026-13-45' 应出现 3 次，实际 %d 次" % joined.count("2026-13-45"))


def test_refund_rows_kept(legacy_clean_db):
    """退款行不剔除（v3 计入净营业额）：应保留 94 行，且全部在保留集合里。"""
    got = row_count(legacy_clean_db, "amount_cents < 0")
    assert got == 94, "退款行应保留 94 行，实际 %d 行" % got
    report = _cleaning_report(legacy_clean_db)
    assert report.get("kept_refund_rows") == 94, (
        "报告的 kept_refund_rows 是 %s，应为 94" % report.get("kept_refund_rows"))


def test_ids_normalised(legacy_clean_db):
    """编号必须 trim + upper 之后再判脏外键，否则 ' s99 ' 这类脏值会漏网。"""
    bad = scalar(
        legacy_clean_db,
        "SELECT COUNT(*) FROM sales_clean "
        "WHERE store_id <> UPPER(TRIM(store_id)) OR product_id <> UPPER(TRIM(product_id))",
    )
    assert bad == 0, "有 %s 行的门店/商品编号没有规范化（trim + upper）" % bad


def test_no_dirty_foreign_keys(legacy_clean_db):
    """保留下来的行不许再有脏外键。"""
    bad_store = scalar(
        legacy_clean_db,
        "SELECT COUNT(*) FROM sales_clean WHERE store_id NOT IN (SELECT store_id FROM stores)")
    bad_product = scalar(
        legacy_clean_db,
        "SELECT COUNT(*) FROM sales_clean WHERE product_id NOT IN (SELECT product_id FROM products)")
    assert bad_store == 0 and bad_product == 0, (
        "保留行里还有脏外键：门店 %s 行、商品 %s 行" % (bad_store, bad_product))


def test_no_duplicate_rows(legacy_clean_db):
    """七字段完全重复的行只保留组内首行。"""
    dup = scalar(
        legacy_clean_db,
        "SELECT COUNT(*) FROM ("
        "  SELECT order_id, date, store_id, product_id, qty, amount_cents, payment"
        "  FROM sales_clean"
        "  GROUP BY order_id, date, store_id, product_id, qty, amount_cents, payment"
        "  HAVING COUNT(*) > 1)",
    )
    assert dup == 0, "还有 %s 组七字段完全重复的行没去掉" % dup


def _cleaning_report(db) -> dict:
    """从 meta 表读清洗报告（新旧 schema 都从这里读）。"""
    import json

    raw = scalar(db, "SELECT value FROM meta WHERE key='cleaning_report'")
    return json.loads(raw) if raw else {}


def _audit(db, rule: str) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        return [str(r[0]) for r in conn.execute(
            "SELECT line_snapshot FROM rejects_audit WHERE rule = ?", (rule,))]
    finally:
        conn.close()
