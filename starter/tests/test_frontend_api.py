"""P4 前端配套接口与静态托管的契约测试（先红后绿）。

对应 `docs/phases/P4-前端与调试面板.md` §4：

* /api/* 永不 3xx（评测脚本不跟随跳转）；
* SPA fallback 给前端让路，但不吞 API 的 404；
* `/api/metrics/top_products`、`/api/meta/options` 是看板的数据口；
* `/api/data_quality` 的守恒等式是数据质量面板的根基；
* `/api/trace/{id}` 必须含调试面板要渲染的全部要素（契约 §6）。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def real_client(tmp_var):
    """真实 Service 的 TestClient（不替换检索），每个测试独立 VAR_DIR。"""
    import os

    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)
    from fastapi.testclient import TestClient

    from kbqa import server

    server._service = None  # 别让上一个测试的 Service 串过来
    try:
        yield TestClient(server.app)
    finally:
        server._service = None


API_PATHS = [
    ("GET", "/api/health"),
    ("GET", "/api/metrics/summary?start=2026-06-01&end=2026-06-30"),
    ("GET", "/api/metrics/daily?start=2026-06-01&end=2026-06-30"),
    ("GET", "/api/metrics/top_products?start=2026-06-01&end=2026-06-30"),
    ("GET", "/api/meta/options"),
    ("GET", "/api/data_quality"),
    ("POST", "/api/retrieve"),
    ("POST", "/api/chat"),
    ("GET", "/api/trace/no-such-trace"),
    ("GET", "/api/nope"),
]


def test_api_never_redirects(real_client):
    """全部 /api/* 路径响应码 ∈ {200,400,404,405,422}，绝不出现 3xx。

    评测脚本不跟随跳转；SPA 静态托管一旦把 /api/* 重定向到 index.html，
    评测会直接判挂。这是静态托管与 API 共存时最容易踩的坑。
    """
    for method, path in API_PATHS:
        response = real_client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code not in range(300, 400), (
            "%s %s 返回了 %d（重定向）" % (method, path, response.status_code))


def test_spa_fallback(real_client):
    """GET / 与非 API 路径 → 200 text/html；不存在的 /api/* → 404 JSON。"""
    for path in ("/", "/chat", "/dashboard"):
        response = real_client.get(path)
        assert response.status_code == 200, "%s -> %d" % (path, response.status_code)
        assert "text/html" in response.headers.get("content-type", ""), (
            "%s 不是 HTML：%s" % (path, response.headers.get("content-type")))
    missing = real_client.get("/api/nope")
    assert missing.status_code == 404
    assert missing.headers.get("content-type", "").startswith("application/json"), (
        "API 的 404 不能返回 HTML（SPA fallback 把 API 404 吞了）")


def test_top_products_shape(real_client):
    """看板排行榜接口：products 数组、按净营业额降序、字段齐。"""
    response = real_client.get(
        "/api/metrics/top_products?start=2026-06-01&end=2026-06-30")
    assert response.status_code == 200
    payload = response.json()
    products = payload.get("products")
    assert isinstance(products, list) and products, "products 为空"
    for item in products:
        for key in ("product_id", "product_name", "net_revenue", "orders", "qty"):
            assert key in item, "缺字段 %s" % key
    revenues = [item["net_revenue"] for item in products]
    assert revenues == sorted(revenues, reverse=True), "没有按净营业额降序"


def test_meta_options_shape(real_client):
    """筛选栏接口：门店/商品清单 + 数据区间。"""
    response = real_client.get("/api/meta/options")
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("data_period", {}).get("start"), "缺 data_period"
    assert payload.get("stores") and payload.get("products"), "缺门店或商品清单"
    assert {"store_id", "store_name"} <= set(payload["stores"][0])
    assert {"product_id", "product_name"} <= set(payload["products"][0])


def test_data_quality_conservation(real_client):
    """守恒：原始行数 == 保留行数 + 六项剔除之和（数据质量面板的第一行）。"""
    payload = real_client.get("/api/data_quality").json()
    report = payload["cleaning_report"]
    removed = sum(
        count for key, count in report["removed"].items() if not key.startswith("note_")
    )
    assert report["raw_rows"] == report["kept_rows"] + removed, (
        "守恒不成立：%d != %d + %d" % (report["raw_rows"], report["kept_rows"], removed))


def test_trace_full_elements(real_client):
    """调试面板的数据源：trace 必须含契约 §6 的全部要素。"""
    answer = real_client.post(
        "/api/chat", json={"session_id": "p4-trace", "question": "6 月的净营业额是多少？"})
    assert answer.status_code == 200
    trace_id = answer.json()["trace_id"]
    trace = real_client.get("/api/trace/%s" % trace_id).json()
    steps = {step.get("step") for step in trace.get("steps", [])}
    assert {"plan", "response"} <= steps, "trace 缺步骤：%s" % steps
    for key in ("trace_id", "question", "steps", "errors"):
        assert key in trace, "trace 缺字段 %s" % key
    # 每一步都要有耗时（契约 §6：每一步的耗时）
    for step in trace["steps"]:
        assert "took_ms" in step and "at_ms" in step, (
            "步骤 %s 没有耗时字段" % step.get("step"))
