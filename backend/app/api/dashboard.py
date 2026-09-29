"""Dashboard 首屏聚合 API.

一次返回 Dashboard 首屏所需的全部数据,减少前端往返次数
(原 7 个并行请求 -> 1 个)。所有子数据复用现有 handler,
返回格式与单独调用各接口一致,仅做聚合不做转换。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/industry-kline")
def industry_kline(code: str, days: int = 60):
    """行业指数 K 线 (上证行业指数, fuyao 数据源)。

    code: fuyao 指数代码, 如 000032.SH
    days: 取最近 N 天
    """
    import time

    from app.plugins.fuyao.client import FuyaoClient
    from app.plugins.fuyao.provider import get_api_key

    client = FuyaoClient(get_api_key())
    try:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - days * 24 * 3600 * 1000
        rows = client.historical_kline(code, start_ms, end_ms)
        return {"code": code, "kline": rows}
    finally:
        client.close()


@router.get("/init")
def dashboard_init(request: Request, as_of: date | None = None) -> dict:
    """Dashboard 首屏聚合:overview + capabilities + settings + preferences
    + data-sources + data/status + alerts(最近7天10条).

    as_of: 总览日期 (YYYY-MM-DD),不传为最新交易日.
    """
    # 延迟导入:避免模块加载时的循环依赖风险,保持与现有 handler 一致的调用路径
    from app.api import alerts as alerts_api
    from app.api import data as data_api
    from app.api import overview as overview_api
    from app.api import routes as routes_api
    from app.api import settings as settings_api

    return {
        "overview": overview_api.market_overview(request, as_of),
        "capabilities": routes_api.capabilities(),
        "settings": settings_api.get_settings(),
        "preferences": settings_api.get_preferences(),
        "data_sources": settings_api.list_data_sources(),
        "data_status": data_api.status(request),
        "alerts": alerts_api.list_alerts(request, days=7, limit=10),
    }
