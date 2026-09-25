"""缺陷 D3：v2/v3 口径混淆（`tools.py:96-109`）。

KB-001 v3（现行）与 KB-002 v2（已废止）的差异恰好三处：

| 项 | v2 | v3 |
|---|---|---|
| 退款行 | 剔除 | **计入净营业额** |
| amount 为空 | 回填 qty×unit_price | **直接剔除，不回填** |
| 客单价分母 | 明细行数 | **有效订单数**（销售行 DISTINCT order_id） |

starter 的 `query_metrics()` 把 `is_refund = 0` 写进 WHERE、`refund_amount` 硬编码 0、
`COUNT(*)` 当订单数——三处全按 v2 走，所以 M01–M04 五个指标全错。
"""

from __future__ import annotations

import pytest

from conftest import scalar

#: 题库 M01 / M02 / M04 的期望值（五指标）。
CASES = [
    ("M01", dict(start="2026-06-01", end="2026-06-30"),
     dict(net_revenue=156757.00, refund_amount=953.00, orders=4311, aov=36.36, qty=6496)),
    ("M02", dict(start="2026-07-01", end="2026-07-31", store_id="S02"),
     dict(net_revenue=41740.00, refund_amount=107.00, orders=875, aov=47.70, qty=1395)),
    ("M03", dict(start="2026-08-01", end="2026-08-31", product_id="P21"),
     dict(net_revenue=11024.00, refund_amount=16.00, orders=461, aov=23.91, qty=689)),
    ("M04", dict(start="2026-06-18", end="2026-06-18", store_id="S02", product_id="P06"),
     dict(net_revenue=3625.00, refund_amount=0.00, orders=53, aov=68.40, qty=125)),
]


@pytest.mark.parametrize("case_id,params,expect", CASES, ids=[c[0] for c in CASES])
def test_summary_v3(legacy_tools, case_id, params, expect):
    """v3 现行口径下五个指标逐一命中题库期望值。"""
    got = legacy_tools.query_metrics(**params)
    problems = []
    for field, want in expect.items():
        actual = got.get(field)
        tol = 0.01 if isinstance(want, float) else 0
        if actual is None or abs(float(actual) - float(want)) > tol:
            problems.append("%s：期望 %s，实际 %s" % (field, want, actual))
    assert not problems, "%s（%s）对不上：%s" % (case_id, params, "；".join(problems))


def test_refund_included_in_net_revenue(legacy_tools):
    """v3 净营业额**含**退款行；退款金额单独用 refund_amount 表达。"""
    got = legacy_tools.query_metrics("2026-06-01", "2026-06-30")
    assert got["refund_amount"] == pytest.approx(953.00, abs=0.01), (
        "退款金额是 %s，应为 953.00——starter 把它硬编码成 0 了" % got["refund_amount"])
    # 净额 = 全部行（含退款）之和；若把退款排掉，数字会偏大正好等于退款额
    assert got["net_revenue"] == pytest.approx(156757.00, abs=0.01), (
        "净营业额是 %s，应为 156757.00（含退款的净额）" % got["net_revenue"])


def test_orders_is_distinct_sales_orders(legacy_tools, legacy_clean_db):
    """订单数 = 销售行 DISTINCT order_id，不是 COUNT(*) 明细行数。"""
    got = legacy_tools.query_metrics("2026-06-01", "2026-06-30")
    distinct = scalar(
        legacy_clean_db,
        "SELECT COUNT(DISTINCT order_id) FROM sales_clean "
        "WHERE date >= '2026-06-01' AND date <= '2026-06-30' AND is_refund = 0")
    rows = scalar(
        legacy_clean_db,
        "SELECT COUNT(*) FROM sales_clean "
        "WHERE date >= '2026-06-01' AND date <= '2026-06-30' AND is_refund = 0")
    assert got["orders"] == distinct, (
        "订单数是 %s，应为 DISTINCT order_id 的 %s" % (got["orders"], distinct))
    assert distinct != rows, (
        "这条测试的前提不成立：DISTINCT 与行数恰好相同（%s），换个区间再测" % rows)


def test_aov_denominator_is_orders(legacy_tools):
    """客单价分母是有效订单数（4311），不是明细行数。

    这条**必须写死期望值**。第一版写成 `round(net/orders, 2)` 对照——那是拿函数
    自己的输出算期望，starter 的净额与订单数同时错，比值恰好还能对上，
    于是测试在坏代码上也是绿的（已经红过一次的教训，记进 AI_USAGE.md）。
    """
    got = legacy_tools.query_metrics("2026-06-01", "2026-06-30")
    assert got["aov"] == pytest.approx(36.36, abs=0.01), (
        "客单价是 %s，应为 36.36（= 156757.00 ÷ 4311 有效订单）" % got["aov"])
    # 顺带证明"分母是订单数"这件事：净额 ÷ aov 应约等于订单数
    assert round(got["net_revenue"] / got["aov"]) == 4311, (
        "净额 %s ÷ 客单价 %s ≈ %s，不是有效订单数 4311——分母用错了"
        % (got["net_revenue"], got["aov"], round(got["net_revenue"] / (got["aov"] or 1))))


def test_qty_nets_refunds(legacy_tools):
    """销量 = 销售 qty − 退款 qty（v3）。"""
    got = legacy_tools.query_metrics("2026-06-01", "2026-06-30")
    assert got["qty"] == 6496, "销量是 %s，应为 6496（已扣掉退款件数）" % got["qty"]


def test_v2_and_v3_diverge(legacy_tools):
    """两套口径必须能分别取到，且结果不同——这是版本类题目的地基。

    v2 与 v3 的差异：退款行剔除、空 amount 回填后保留。
    所以 v2 的净营业额不含退款、退款金额恒为 0。
    """
    import inspect

    v3 = legacy_tools.query_metrics("2026-06-01", "2026-06-30")
    if "caliber" not in inspect.signature(legacy_tools.query_metrics).parameters:
        pytest.fail("query_metrics 不支持 caliber 参数，v2/v3 两套口径无法分别取到")
    v2 = legacy_tools.query_metrics("2026-06-01", "2026-06-30", caliber="v2")
    assert v2["refund_amount"] == 0.0, "v2 口径下退款金额应为 0（v2 不把退款计入）"
    assert v2["net_revenue"] != v3["net_revenue"], (
        "v2 与 v3 的净营业额相同（%s），说明口径没有参数化" % v2["net_revenue"])
