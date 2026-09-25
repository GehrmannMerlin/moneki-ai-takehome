"""缺陷 D2：指标区间是右开区间（`tools.py:53`）。

契约 §2 与 KB-001 §4 都要求**闭区间**：`start=end=某日` 时必须把该日的数据算进去。
starter 的 `_where()` 写的是 `date >= ? AND date < ?`，所以单日查询恒为空。

连带后果就是交接文档里那句"月底那几天跟财务对不上，应该是四舍五入的事"——
不是四舍五入，是区间末日整天都没算。
"""

from __future__ import annotations

import pytest

from conftest import scalar


#: M04 用的正是单日区间（2026-06-18 / S02 / P06），题库期望值：
#: net 3625.00、orders 53、aov 68.40、qty 125、refund 0.00。
SINGLE_DAY = {"start": "2026-06-18", "end": "2026-06-18", "store_id": "S02", "product_id": "P06"}
SINGLE_DAY_EXPECT = {
    "net_revenue": 3625.00, "refund_amount": 0.00, "orders": 53, "aov": 68.40, "qty": 125,
}


def test_closed_interval_single_day(legacy_tools):
    """start == end 时该日数据必须计入——这是右开区间缺陷最直接的复现。"""
    got = legacy_tools.query_metrics(**SINGLE_DAY)
    assert got["orders"] == SINGLE_DAY_EXPECT["orders"], (
        "单日区间 %s..%s 的订单数是 %s，应为 %s——右开区间 `date < end` 会把这一天整个排除"
        % (SINGLE_DAY["start"], SINGLE_DAY["end"], got["orders"],
           SINGLE_DAY_EXPECT["orders"]))
    assert got["net_revenue"] == pytest.approx(SINGLE_DAY_EXPECT["net_revenue"], abs=0.01)
    assert got["qty"] == SINGLE_DAY_EXPECT["qty"]
    assert got["aov"] == pytest.approx(SINGLE_DAY_EXPECT["aov"], abs=0.01)


def test_closed_interval_includes_last_day(legacy_tools):
    """区间最后一天的数据必须计入：跨月区间的末日就是"月底对不上"的那一天。"""
    whole = legacy_tools.query_metrics("2026-06-01", "2026-06-30")
    without_last = legacy_tools.query_metrics("2026-06-01", "2026-06-29")
    last_day = legacy_tools.query_metrics("2026-06-30", "2026-06-30")
    assert last_day["orders"] > 0, "6 月 30 日本身应该有数据"
    assert whole["orders"] == without_last["orders"] + last_day["orders"], (
        "整月订单数 %s ≠ 前 29 天 %s + 末日 %s——末日被区间排除了"
        % (whole["orders"], without_last["orders"], last_day["orders"]))


def test_daily_includes_both_ends(legacy_tools):
    """daily 也要闭区间：`start..end` 的条数应等于天数，且首尾两天都在。"""
    result = legacy_tools.daily_metrics("2026-06-08", "2026-06-12", "S03")
    dates = [day["date"] for day in result["days"]]
    assert dates == ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"], (
        "daily 返回的日期序列是 %s，应为闭区间内连续 5 天" % dates)
    # M06 的期望值：前 4 天为 0，只有 6/12 有 998.00 / 27 单
    by_date = {day["date"]: day for day in result["days"]}
    assert by_date["2026-06-12"]["net_revenue"] == pytest.approx(998.00, abs=0.01)
    assert by_date["2026-06-12"]["orders"] == 27
    assert by_date["2026-06-11"]["net_revenue"] == 0
