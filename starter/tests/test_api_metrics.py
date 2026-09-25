"""`/api/metrics/*` 与 `/api/health` 的契约测试（走真实 Service，不起 HTTP）。

这些测试用**真实的清洗表与索引**（`VAR_DIR` 指到临时目录），
这样接口层与数据层之间的接线错误能被抓到——比 TestClient + 假检索更接近评测。
"""

from __future__ import annotations

import pytest

from conftest import EXPECTED


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    """一份真实 Service：重建清洗表 + 索引，全部落在临时目录。"""
    import os

    var = tmp_path_factory.mktemp("var")
    os.environ["VAR_DIR"] = str(var)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)

    from kbqa.config import load_settings
    from kbqa.service import Service

    settings = load_settings()
    return Service(settings)


def test_health_valid_sales_rows(service):
    """N01 的一半：`valid_sales_rows` 必须是 18290。"""
    body = service.health()
    assert body["valid_sales_rows"] == EXPECTED["valid_sales_rows"], (
        "valid_sales_rows 是 %s，应为 %s" % (body["valid_sales_rows"],
                                          EXPECTED["valid_sales_rows"]))


def test_health_status_and_mode(service):
    body = service.health()
    assert body["status"] == "ok"
    assert body["llm_mode"] == "mock", "没有 Key 时必须降级为 mock，且服务照常可用"


def test_health_data_period_is_clean(service):
    """`data_period` 是 P3 区间闸的判据来源，必须是干净 ISO 日期。"""
    body = service.health()
    assert body["data_period"] == EXPECTED["data_period"]


def test_health_cleaning_report_breakdown(service):
    """数据质量面板：六项剔除原因分布直接可消费。"""
    report = service.health()["cleaning_report"]
    assert report["removed"]["1_unparseable_date"] == 8
    assert report["removed"]["2_empty_amount"] == 150
    assert report["removed"]["3_qty_le_zero"] == 30
    assert report["removed"]["4_store_not_in_stores"] == 10
    assert report["removed"]["5_product_not_in_products"] == 40
    assert report["removed"]["6_duplicate_row"] == 100


@pytest.mark.parametrize("case_id,params,expect", [
    ("M01", dict(start="2026-06-01", end="2026-06-30"),
     dict(net_revenue=156757.00, refund_amount=953.00, orders=4311, aov=36.36, qty=6496)),
    ("M02", dict(start="2026-07-01", end="2026-07-31", store_id="S02"),
     dict(net_revenue=41740.00, refund_amount=107.00, orders=875, aov=47.70, qty=1395)),
    ("M03", dict(start="2026-08-01", end="2026-08-31", product_id="P21"),
     dict(net_revenue=11024.00, refund_amount=16.00, orders=461, aov=23.91, qty=689)),
    ("M04", dict(start="2026-06-18", end="2026-06-18", store_id="S02", product_id="P06"),
     dict(net_revenue=3625.00, refund_amount=0.00, orders=53, aov=68.40, qty=125)),
    ("M05", dict(start="2026-09-01", end="2026-09-30"),
     dict(net_revenue=0.00, refund_amount=0.00, orders=0, aov=None, qty=0)),
], ids=["M01", "M02", "M03", "M04", "M05"])
def test_metrics_summary_matches_question_bank(service, case_id, params, expect):
    """M01–M05：五指标逐一命中公开题库的期望值。"""
    got = service.metrics_summary(**params)
    problems = []
    for field, want in expect.items():
        actual = got.get(field)
        if want is None:
            if actual is not None:
                problems.append("%s：期望 null，实际 %s" % (field, actual))
            continue
        tol = 0.005 if isinstance(want, float) else 0
        if actual is None or abs(float(actual) - float(want)) > tol:
            problems.append("%s：期望 %s，实际 %s" % (field, want, actual))
    assert not problems, "%s 对不上：%s" % (case_id, "；".join(problems))


def test_metrics_daily_m06(service):
    """M06：S03 在 6/8–6/12 的逐日数据，前四天为 0，只有 6/12 有营业额。"""
    got = service.metrics_daily(start="2026-06-08", end="2026-06-12", store_id="S03")
    days = got["days"]
    assert len(days) == 5
    expected = [
        ("2026-06-08", 0.0, 0, None),
        ("2026-06-09", 0.0, 0, None),
        ("2026-06-10", 0.0, 0, None),
        ("2026-06-11", 0.0, 0, None),
        ("2026-06-12", 998.0, 27, 36.96),
    ]
    for day, (want_date, net, orders, aov) in zip(days, expected):
        assert day["date"] == want_date
        assert day["net_revenue"] == pytest.approx(net, abs=0.005), want_date
        assert day["orders"] == orders, want_date
        if aov is None:
            assert day["aov"] is None, want_date
        else:
            assert day["aov"] == pytest.approx(aov, abs=0.005), want_date


def test_data_quality_endpoint_shape(service):
    """`/api/data_quality` 给 P4 前端的数据质量面板供数。"""
    from kbqa.server import data_quality

    body = data_quality()
    assert "cleaning_report" in body
    assert "data_period" in body
    assert body["cleaning_report"]["removed"]["6_duplicate_row"] == 100


def test_metrics_unchanged_after_reads(service):
    """反复查指标不该改变任何东西（只读连接的基本保证）。"""
    before = service.metrics_summary("2026-06-01", "2026-06-30")
    for _ in range(3):
        service.metrics_summary("2026-06-01", "2026-06-30")
        service.metrics_daily("2026-06-01", "2026-06-30")
    assert service.metrics_summary("2026-06-01", "2026-06-30") == before
