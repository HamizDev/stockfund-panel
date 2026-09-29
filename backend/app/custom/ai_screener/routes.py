"""Lightweight read-only candidates API; no upstream requests or data writes."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
import polars as pl

from app.custom.ai_screener.service import build_candidates
from app.services import preferences, strategy_cache
from app.strategy import config as strategy_config
from app.market_time import cn_today


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/custom/ai-screener", tags=["custom-ai-screener"])

    @router.get("/candidates")
    def candidates(request: Request, limit: int = Query(60, ge=1, le=200)) -> dict:
        data_dir = request.app.state.repo.store.data_dir
        cached = strategy_cache.read_cache(data_dir) or {"as_of": None, "results": {}, "updated_at": None}
        engine = getattr(request.app.state, "strategy_engine", None)
        strategies = []
        if engine is not None:
            for meta in engine.list_strategies():
                override = strategy_config.load_override(data_dir, meta["id"])
                strategies.append({**meta, "name": override.get("name") or meta.get("name"),
                                   "description": override.get("description") or meta.get("description")})
        quote_service = getattr(request.app.state, "quote_service", None)
        status = quote_service.status() if quote_service is not None else {}
        quotes = []
        if quote_service is not None and status.get("running"):
            frame, quote_date = quote_service.get_enriched_today()
            symbols = {
                str(row.get("symbol") or "").upper()
                for result in (cached.get("results") or {}).values()
                if isinstance(result, dict)
                for row in result.get("rows") or []
                if isinstance(row, dict)
            }
            if quote_date == cn_today() and not frame.is_empty() and symbols and "symbol" in frame.columns:
                fields = [key for key in ("symbol", "close", "change_pct", "amount", "volume", "turnover_rate")
                          if key in frame.columns]
                quotes = frame.filter(pl.col("symbol").is_in(symbols)).select(fields).to_dicts()
        return build_candidates(
            cached, strategies, quotes, status,
            preferences.get_realtime_data_provider(), limit,
        )

    return router
