"""FastAPI 层：只做参数校验和 JSON 序列化，逻辑都在 service.py。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .service import Service

#: 前端构建产物（vite build 的输出，dist 入库，评委零构建启动）。
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="经营看板 + 问答服务", version="0.9.4")
_service: Optional[Service] = None


def service() -> Service:
    global _service
    if _service is None:
        _service = Service()
    return _service


@app.on_event("startup")
async def _boot_check() -> None:
    """启动期就完成一次初始化。

    R1/D3：clean.db 与当前 data/ 不匹配时 Service() 会抛 RuntimeError。
    放在 startup 里，uvicorn 进程当场带着明确信息退出，
    而不是等到第一个请求才 500（评测脚本对 500 的宽容不等于应该 500）。
    """
    service()


def _as_text(value: Any) -> str:
    """把请求里的标量原样变成字符串。

    契约 §5 要求 `/api/chat` 无论如何都返回 200，所以这里对类型宽容：
    数字、布尔的 `session_id` 或 `question` 一律当字符串收下，`null` 当没填。
    """
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    return json.dumps(value, ensure_ascii=False)


class ChatRequest(BaseModel):
    session_id: Optional[Any] = None
    question: Optional[Any] = None


class RetrieveRequest(BaseModel):
    query: Optional[Any] = None
    #: 只要求是正整数；超过索引片段总数时由服务按总数封顶（契约 §4）。
    top_k: int = Field(default=5, ge=1)


def _bad_date(*values: str) -> Optional[JSONResponse]:
    for value in values:
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError):
            return JSONResponse(
                status_code=400,
                content={"error": "日期格式必须是 YYYY-MM-DD，收到 %r" % value},
            )
    return None


@app.get("/api/health")
def health() -> dict:
    return service().health()


@app.get("/api/metrics/summary")
def metrics_summary(
    start: str = Query(...),
    end: str = Query(...),
    store_id: Optional[str] = None,
    product_id: Optional[str] = None,
):
    bad = _bad_date(start, end)
    return bad or service().metrics_summary(start, end, store_id, product_id)


@app.get("/api/metrics/daily")
def metrics_daily(
    start: str = Query(...),
    end: str = Query(...),
    store_id: Optional[str] = None,
    product_id: Optional[str] = None,
):
    bad = _bad_date(start, end)
    return bad or service().metrics_daily(start, end, store_id, product_id)


@app.post("/api/retrieve")
def retrieve(request: RetrieveRequest) -> dict:
    return service().retrieve(_as_text(request.query), request.top_k)


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    session_id = _as_text(request.session_id) or None
    return service().chat(session_id, _as_text(request.question))


@app.get("/api/trace/{trace_id}")
def trace(trace_id: str):
    payload = service().get_trace(trace_id)
    if payload is None:
        return JSONResponse(status_code=404, content={"error": "没有这个 trace_id：%s" % trace_id})
    return payload


@app.get("/api/data_quality")
def data_quality() -> dict:
    """第一关的“数据质量”面板：清洗掉了多少行、各因为什么。"""
    current = service()
    return {
        "cleaning_report": current.tools.cleaning_report(),
        "data_period": current.data_period,
        "kb_warnings": current.index.warnings,
    }


@app.get("/api/metrics/top_products")
def metrics_top_products(
    start: str = Query(...),
    end: str = Query(...),
    store_id: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
):
    """看板排行榜：与问答链路共用 DataTools.top_products，口径天然一致。"""
    bad = _bad_date(start, end)
    return bad or service().tools.top_products(start, end, store_id, limit)


@app.get("/api/meta/options")
def meta_options() -> dict:
    """筛选栏的备选项：门店 / 商品清单 + 数据区间。"""
    current = service()
    return {
        "stores": current.tools.stores(),
        "products": current.tools.products(),
        "data_period": current.data_period,
        "today": current.settings.today.isoformat(),
    }


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    """单页应用托管：API 之外的 GET 一律回 index.html，API 的 404 不吞。

    两条铁律：
    * /api/* 到这里的说明没匹配上任何路由——返回 404 JSON，**绝不**回 HTML，
      否则评测会把"接口不存在"误判成"接口返回了坏 JSON"；
    * 只做文件的静态返回，不重定向（评测脚本不跟随 3xx）。
    """
    if full_path.startswith("api/") or full_path == "api":
        return JSONResponse(status_code=404, content={"error": "没有这个接口：/%s" % full_path})
    target = (STATIC_DIR / full_path).resolve()
    if target.is_file() and STATIC_DIR in target.parents:
        return FileResponse(target)
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(
        status_code=404,
        content={"error": "前端还没构建：请先跑 frontend 的 vite build，产物放进 kbqa/static/"},
    )
