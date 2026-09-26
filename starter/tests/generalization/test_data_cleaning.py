"""T-DATA-01 ~ T-DATA-13：数据清洗的泛化语义（合成数据，机制断言）。

依据 KB-001 v3：§2 规范化、§3 六条剔除、§4 指标定义。
所有期望值都是"机制应该怎样"，不含任何公开金标。
"""

from __future__ import annotations

from kbqa.core.cleaning import build_clean_db, clean_rows
from kbqa.core.metrics import MetricsEngine
from kbqa.core.normalize import parse_amount_cents, parse_date, parse_qty

from synth import DEFAULT_PRODUCTS, DEFAULT_STORES, make_pos_db, sale

STORES = {s[0] for s in DEFAULT_STORES}
PRODUCTS = {p[0] for p in DEFAULT_PRODUCTS}


def _clean(sales):
    """对一批销售行跑清洗，返回 (kept, report)。"""
    return clean_rows(list(sales), STORES, PRODUCTS)


# --- 规范化（T-DATA-01 ~ 04）-------------------------------------------------


def test_t_data_01_store_id_whitespace_and_lowercase_normalized():
    """带空白与小写的门店编号规范化后保留，不当脏外键剔除。"""
    kept, report = _clean([sale(store_id=" s91 ")])
    assert [line.store_id for line in kept] == ["S91"], report.as_dict()


def test_t_data_02_product_id_whitespace_and_lowercase_normalized():
    kept, report = _clean([sale(product_id=" p91 ")])
    assert [line.product_id for line in kept] == ["P91"], report.as_dict()


def test_t_data_03_three_date_formats_same_day():
    """三种日期写法解析到同一天；DD-MM-YYYY 日在前。"""
    for text in ("2026-07-25", "2026/7/25", "25-07-2026"):
        assert parse_date(text) is not None, text
        assert parse_date(text).isoformat() == "2026-07-25", text
    # 日大于 12 的样本：解析方向不能搞反
    assert parse_date("07-06-2026").isoformat() == "2026-06-07"


def test_t_data_04_currency_symbols_both_accepted():
    """全角/半角货币符号的金额是同一个数。"""
    assert parse_amount_cents("¥38.00") == (3800, "ok")
    assert parse_amount_cents("￥38.00") == (3800, "ok")
    assert parse_amount_cents("38.00") == (3800, "ok")


# --- 剔除与保留（T-DATA-05 ~ 12）----------------------------------------------


def test_t_data_05_negative_amount_kept_as_refund():
    kept, report = _clean([sale(order_id="R1", amount="-12.00", qty="1")])
    assert len(kept) == 1 and kept[0].is_refund == 1, report.as_dict()


def test_t_data_06_empty_amount_rejected():
    kept, report = _clean([sale(amount=""), sale(amount="   ")])
    assert kept == []
    assert report.removed["2_empty_amount"] == 2, report.as_dict()


def test_t_data_07_qty_le_zero_rejected():
    kept, report = _clean([sale(qty="0"), sale(qty="-2"), sale(qty="-1")])
    assert kept == []
    assert report.removed["3_qty_le_zero"] == 3, report.as_dict()


def test_t_data_08_fractional_qty_must_not_be_silently_truncated():
    """小数 qty 不能被截断成整数后当合法行保留（KB-001 §2.4 按整数解析）。"""
    # 解析层：真正的小数必须判为不可解析
    assert parse_qty("1.5") is None
    assert parse_qty("2.7") is None
    # 整数值（含 "3"/3/"3.0"）合法
    assert parse_qty("3") == 3
    assert parse_qty(3) == 3
    # 清洗层：小数 qty 进入剔除，且不是以截断后的值保留
    kept, report = _clean([sale(order_id="F1", qty="1.5", amount="30.00")])
    assert kept == [], "小数 qty 被截断保留：%r" % [l.as_tuple() for l in kept]
    assert sum(report.removed.values()) == 1, report.as_dict()


def test_t_data_09_invalid_store_fk_rejected():
    kept, report = _clean([sale(store_id="S99")])
    assert kept == []
    assert report.removed["4_store_not_in_stores"] == 1


def test_t_data_10_invalid_product_fk_rejected():
    kept, report = _clean([sale(product_id="P99")])
    assert kept == []
    assert report.removed["5_product_not_in_products"] == 1


def test_t_data_11_exact_duplicate_only_one_kept():
    row = sale(order_id="D1")
    kept, report = _clean([row, dict(row), row])
    # 七字段完全相同 → 只留一条；两条重复被剔除
    assert len(kept) == 1
    assert report.removed["6_duplicate_row"] == 2, report.as_dict()


def test_t_data_12_same_order_different_products_all_kept_one_order(tmp_path):
    sales = [
        sale(order_id="M1", product_id="P91", amount="12.00", qty="1"),
        sale(order_id="M1", product_id="P92", amount="25.00", qty="1"),
    ]
    kept, report = _clean(sales)
    assert len(kept) == 2, "同订单不同商品是合法多行订单"
    assert report.removed["6_duplicate_row"] == 0

    from pathlib import Path
    make_pos_db(tmp_path / "data" / "pos.db", sales)
    clean = build_clean_db(tmp_path / "data" / "pos.db", tmp_path / "var" / "clean.db")
    assert clean.kept_rows == 2
    engine = MetricsEngine(tmp_path / "var" / "clean.db")
    try:
        summary = engine.summary("2026-07-01", "2026-07-01")
        assert summary["orders"] == 1, "多行订单只算一单：%r" % summary
        assert summary["qty"] == 2
    finally:
        engine.close()


# --- amount=0（T-DATA-13，KB-001 §4：销售行 amount>0，退款行 amount<0）----------


def test_t_data_13_zero_amount_is_neither_sale_nor_refund(tmp_path):
    """amount=0 既不是销售行也不是退款行，不得产生订单/销量/营业额/有效行。"""
    sales = [
        sale(order_id="Z1", amount="30.00", qty="2"),      # 正常销售
        sale(order_id="Z2", amount="0.00", qty="3"),       # 零金额
        sale(order_id="Z3", amount="0", qty="1"),          # 零金额另一种写法
        sale(order_id="R1", amount="-8.00", qty="1"),      # 退款
    ]
    kept, report = _clean(sales)
    zero_rows = [line for line in kept if line.amount_cents == 0]
    assert zero_rows == [], "amount=0 的行被当成有效行保留了：%r" % [
        line.as_tuple() for line in zero_rows]
    assert report.kept_rows == 2, report.as_dict()
    assert report.kept_sales_rows == 1 and report.kept_refund_rows == 1

    make_pos_db(tmp_path / "data" / "pos.db", sales)
    clean = build_clean_db(tmp_path / "data" / "pos.db", tmp_path / "var" / "clean.db")
    assert clean.kept_rows == 2, "零金额行混进了 clean.db 的保留行"
    engine = MetricsEngine(tmp_path / "var" / "clean.db")
    try:
        summary = engine.summary("2026-05-01", "2026-08-31")
        assert summary["orders"] == 1, "零金额行产生了订单：%r" % summary
        assert summary["qty"] == 1, "零金额行贡献了销量：%r" % summary
        assert summary["net_revenue"] == 22.0, summary
        assert engine.valid_sales_rows() == 2, "valid_sales_rows 含零金额行"
    finally:
        engine.close()
