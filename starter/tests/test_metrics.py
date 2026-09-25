"""口径引擎回归测试（P1 新增，非缺陷复现）。

`tests/defects/` 那批是"先红后绿"的缺陷证据；这一批是**期望长期保持绿**的
规格测试——契约 §2/§3 的边界行为、口径的舍入、v2/v3 分歧。

期望值一律写死（fixture 写死合法）；代码逻辑里没有任何数字。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import EXPECTED, WORKSPACE


@pytest.fixture(scope="module")
def engine(tmp_path_factory, workspace: Path):
    """一份独立的 clean.db + 口径引擎，整个模块共用。"""
    from kbqa.core.cleaning import build_clean_db
    from kbqa.core.metrics import MetricsEngine

    target = tmp_path_factory.mktemp("metrics") / "clean.db"
    build_clean_db(workspace / "data" / "pos.db", target)
    return MetricsEngine(target)


# --------------------------------------------------------------------- 契约边界

def test_empty_range_returns_zero(engine):
    """契约 §2：区间内没有数据时数值字段返回 0、`aov` 为 `null`，不报错。"""
    got = engine.summary("2026-09-01", "2026-09-30")
    assert got["net_revenue"] == 0
    assert got["refund_amount"] == 0
    assert got["orders"] == 0
    assert got["qty"] == 0
    assert got["aov"] is None, "空区间的 aov 必须是 null，不是 0"


def test_range_before_data_returns_zero(engine):
    """数据区间之前（2026-01）同样返回 0/null。"""
    got = engine.summary("2026-01-01", "2026-01-31")
    assert got["orders"] == 0 and got["aov"] is None


def test_daily_pads_missing_days(engine):
    """契约 §3：区间内每一天都要有一条记录，没有营业额的日期也要出现且为 0。"""
    result = engine.daily("2026-06-08", "2026-06-12", "S03")
    dates = [day["date"] for day in result["days"]]
    assert dates == ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"]
    for day in result["days"][:4]:
        assert day["net_revenue"] == 0
        assert day["orders"] == 0
        assert day["aov"] is None


def test_daily_covers_full_month(engine):
    """整月 daily 的条数等于当月天数（6 月 30 天）。"""
    result = engine.daily("2026-06-01", "2026-06-30")
    assert len(result["days"]) == 30
    assert result["days"][0]["date"] == "2026-06-01"
    assert result["days"][-1]["date"] == "2026-06-30"


def test_daily_single_day(engine):
    result = engine.daily("2026-06-18", "2026-06-18", "S02", "P06")
    assert len(result["days"]) == 1
    assert result["days"][0]["net_revenue"] == pytest.approx(3625.00, abs=0.01)


def test_summary_contract_shape(engine):
    """契约 §2 的字段必须齐全，字段名不能变。"""
    got = engine.summary("2026-06-01", "2026-06-30")
    for field in ("start", "end", "store_id", "product_id",
                  "net_revenue", "refund_amount", "orders", "aov", "qty"):
        assert field in got, "响应里缺少契约字段 %s" % field
    assert got["start"] == "2026-06-01" and got["end"] == "2026-06-30"
    assert got["store_id"] is None and got["product_id"] is None


def test_summary_echoes_filters(engine):
    """传了门店/商品就原样回显（契约示例里这两个字段就是这么用的）。"""
    got = engine.summary("2026-07-01", "2026-07-31", "S02", "P06")
    assert got["store_id"] == "S02" and got["product_id"] == "P06"


# --------------------------------------------------------------------- 舍入

def test_aov_rounding_is_half_up(engine):
    """客单价用 `ROUND_HALF_UP`，不是 Python 默认的银行家舍入。

    156757.00 ÷ 4311 = 36.3551… → 36.36
    银行家舍入在"第三位恰好是 5"时才与 HALF_UP 分道扬镳，所以这里用
    Decimal 直接验算 `round2` 本身，再用真实数据验一遍结果。
    """
    from kbqa.core.metrics import round2

    # 2.675 在二进制浮点里是 2.67499…，HALF_UP 的十进制实现应给出 2.68
    assert round2(Decimal("2.675")) == 2.68
    assert round2(Decimal("2.665")) == 2.67
    assert round2(Decimal("36.355")) == 36.36
    # Decimal 默认的 ROUND_HALF_EVEN 会把 2.675 变成 2.68、把 2.665 变成 2.66，
    # 两者在这里恰好相同，所以再挑一个能区分的：0.125 → HALF_UP 0.13 / EVEN 0.12
    assert round2(Decimal("0.125")) == 0.13, "用的是银行家舍入，不是 ROUND_HALF_UP"

    got = engine.summary("2026-06-01", "2026-06-30")
    assert got["aov"] == 36.36


def test_aov_two_decimals(engine):
    """客单价保留两位小数。"""
    got = engine.summary("2026-07-01", "2026-07-31", "S02")
    assert got["aov"] == 47.70
    assert round(got["aov"], 2) == got["aov"]


# --------------------------------------------------------------------- 口径分歧

def test_v2_v3_diverge_by_design(engine):
    """v2 与 v3 必须给出**不同**的结果，且各自符合自己的口径。

    差异三处：退款行是否剔除、空 amount 是否回填、客单价分母。
    """
    v3 = engine.summary("2026-06-01", "2026-06-30", caliber="v3")
    v2 = engine.summary("2026-06-01", "2026-06-30", caliber="v2")

    # v3：退款计入净额，所以净额 = 全部行之和；退款额单独表达
    assert v3["refund_amount"] == pytest.approx(953.00, abs=0.01)
    assert v3["net_revenue"] == pytest.approx(156757.00, abs=0.01)
    # v2：退款行被剔除，退款额恒为 0
    assert v2["refund_amount"] == 0.0
    assert v2["net_revenue"] != v3["net_revenue"]
    # v2 的回填会把 v3 剔掉的空金额行补回来，所以净额更高
    assert v2["net_revenue"] > v3["net_revenue"]


def test_v2_counts_backfilled_rows(engine):
    """v2 保留的行数 = v3 保留的 18290 − 94 退款行 + 150 回填行 = 18346。"""
    assert engine.valid_sales_rows("v3") == 18290
    assert engine.valid_sales_rows("v2") == 18290 - 94 + 150


def test_unknown_caliber_falls_back_to_v3(engine):
    """口径参数写错时退回 v3（现行），不能悄悄给出 v2 的数字。"""
    v3 = engine.summary("2026-06-01", "2026-06-30", caliber="v3")
    assert engine.summary("2026-06-01", "2026-06-30", caliber="nonsense") == v3


def test_qty_nets_refunds_v3(engine):
    """销量 = 销售 qty − 退款 qty（v3）。"""
    got = engine.summary("2026-06-01", "2026-06-30")
    assert got["qty"] == 6496


# --------------------------------------------------------------------- 数据质量

def test_cleaning_report_breakdown(engine):
    """数据质量面板要的六项剔除原因分布能直接取到。"""
    report = engine.cleaning_report()
    assert report["raw_rows"] == EXPECTED["raw_sales_rows"]
    assert report["kept_rows"] == EXPECTED["valid_sales_rows"]
    for rule, want in EXPECTED["removed"].items():
        assert report["removed"][rule] == want, "%s 应为 %s" % (rule, want)
    assert report["rejected_rows"] == 338


def test_valid_sales_rows_matches_report(engine):
    """`/api/health` 的 valid_sales_rows 必须与台账一致（不能一个数两个来源）。"""
    assert engine.valid_sales_rows() == engine.cleaning_report()["kept_rows"]


def test_data_period(engine):
    assert engine.data_period() == EXPECTED["data_period"]
