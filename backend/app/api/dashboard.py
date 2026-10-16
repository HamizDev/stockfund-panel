"""Dashboard 首屏聚合 API.

一次返回 Dashboard 首屏数据,减少前端往返次数。
首页使用独立日期摘要,完整 data/status 仍由数据页读取。
未指定轻量参数的旧调用方继续得到完整聚合响应。
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

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


def _directory_dates(directory: Path) -> dict | None:
    """Read partition dates only; no Parquet scan or table aggregate."""
    dates = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.name.startswith("date=") or not entry.is_dir():
                    continue
                text = entry.name[5:]
                try:
                    parsed = date.fromisoformat(text)
                except ValueError:
                    continue
                if parsed.isoformat() == text:
                    dates.append(text)
    except FileNotFoundError:
        return None
    if not dates:
        return None
    return {"trading_days": len(dates), "earliest_date": min(dates), "latest_date": max(dates)}


def dashboard_data_summary(request: Request) -> dict:
    """A separate homepage contract, never a partial full data/status result."""
    data_dir = request.app.state.repo.store.data_dir
    try:
        daily = _directory_dates(data_dir / "kline_daily")
        enriched = _directory_dates(data_dir / "kline_daily_enriched")
    except OSError:
        return {"known": False, "daily": None, "enriched": None}
    return {"known": True, "daily": daily, "enriched": enriched}


@router.get("/init")
def dashboard_init(request: Request, as_of: date | None = None, include_data_status: bool = True) -> dict:
    """Dashboard 聚合:overview + capabilities + settings + preferences
    + data-sources + 日期摘要 + alerts(最近7天10条), 可选完整 data/status.

    as_of: 总览日期 (YYYY-MM-DD),不传为最新交易日.
    include_data_status=False: 首页只取独立日期摘要, 不等待完整容量/表统计.
    默认保持原有完整聚合响应, 兼容已有调用方.
    """
    # 延迟导入:避免模块加载时的循环依赖风险,保持与现有 handler 一致的调用路径
    from app.api import alerts as alerts_api
    from app.api import data as data_api
    from app.api import overview as overview_api
    from app.api import routes as routes_api
    from app.api import settings as settings_api

    result = {
        "overview": overview_api.market_overview(request, as_of),
        "capabilities": routes_api.capabilities(),
        "settings": settings_api.get_settings(),
        "preferences": settings_api.get_preferences(),
        "data_sources": settings_api.list_data_sources(),
        "data_summary": dashboard_data_summary(request),
    }
    if include_data_status:
        result["data_status"] = data_api.status(request)
    result["alerts"] = alerts_api.list_alerts(request, days=7, limit=10)
    return result
