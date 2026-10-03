"""Build read-only strategy candidates for stocks and ETFs."""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import defaultdict
from copy import deepcopy
from typing import Any

import polars as pl

from app.services.screener import ScreenerService

logger = logging.getLogger(__name__)
_STOCK_SYMBOL = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_FIELDS = (
    "close", "raw_close", "change_pct", "amount", "volume", "turnover_rate",
    "pe_ttm", "pb", "ma5", "ma20", "ma60", "vol_ratio_5d",
    "macd_dif", "macd_dea", "macd_hist", "kdj_k", "kdj_d", "kdj_j",
)
_ETF_CANDIDATE_TTL = 15.0
_ETF_CANDIDATE_CACHE_LIMIT = 16
_ETF_CANDIDATE_CACHE: dict[tuple[Any, ...], tuple[float, dict]] = {}
_ETF_CANDIDATE_CACHE_LOCK = threading.Lock()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _eligible_metadata(strategies: list[dict], asset_type: str) -> dict[str, dict]:
    return {
        str(meta["id"]): meta
        for meta in strategies
        if meta.get("id")
        and not meta.get("research_only")
        and asset_type in meta.get("asset_types", ["stock"])
        and "1d" in meta.get("timeframes", ["1d"])
    }


def _empty_response(
    asset_type: str,
    *,
    as_of: str | None,
    total_strategies: int,
    updated_at: int | None = None,
) -> dict:
    return {
        "asset_type": asset_type,
        "as_of": as_of,
        "updated_at": updated_at,
        "cache_available": False,
        "coverage": {"computed": 0, "total": total_strategies},
        "quote": {"live": False, "provider": None, "last_fetch_ms": None, "age_ms": None},
        "total": 0,
        "items": [],
    }


def _aggregate_candidates(
    *,
    as_of: str | None,
    updated_at: int | None,
    results: dict,
    metadata: dict[str, dict],
    asset_type: str,
    quotes: list[dict],
    quote_status: dict,
    provider: str | None,
    limit: int,
    allowed_symbols: set[str] | None = None,
) -> dict:
    """Aggregate same-date strategy rows into a stable candidate response."""
    computed_ids = {
        sid
        for sid, result in results.items()
        if sid in metadata
        and isinstance(result, dict)
        and as_of is not None
        and str(result.get("as_of") or "") == as_of
    }
    if as_of is None:
        return _empty_response(asset_type, as_of=None, total_strategies=len(metadata), updated_at=updated_at)

    grouped: dict[str, dict] = defaultdict(lambda: {"hits": [], "row": {}})
    for sid, result in results.items():
        if sid not in computed_ids:
            continue
        for row in result.get("rows") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            if allowed_symbols is not None:
                if symbol not in allowed_symbols:
                    continue
            elif asset_type == "stock" and not _STOCK_SYMBOL.fullmatch(symbol):
                continue
            candidate = grouped[symbol]
            if sid not in candidate["hits"]:
                candidate["hits"].append(sid)
            if len(row) > len(candidate["row"]):
                candidate["row"] = row

    age = _finite(quote_status.get("quote_age_ms"))
    valid_quotes = {
        str(row.get("symbol") or "").upper(): row
        for row in quotes
        if isinstance(row, dict)
        and (_finite(row.get("close")) or 0) > 0
    }
    live = bool(
        asset_type == "stock"
        and quote_status.get("enabled") and quote_status.get("running")
        and quote_status.get("is_trading_hours") and age is not None
        and age <= 120_000 and quote_status.get("last_fetch_ms") and valid_quotes
    )
    quote_map = valid_quotes if live else {}

    candidates = []
    for symbol, item in grouped.items():
        base = item["row"]
        quote = quote_map.get(symbol)
        metrics = {key: _finite(base.get(key)) for key in _FIELDS}
        if quote:
            for key in ("close", "change_pct", "amount", "volume", "turnover_rate"):
                value = _finite(quote.get(key))
                if value is not None:
                    metrics[key] = value
        hits = sorted(item["hits"])
        candidates.append({
            "symbol": symbol,
            "name": str(base.get("name") or (quote or {}).get("name") or symbol),
            "strategies": [{"id": sid, "name": str(metadata[sid].get("name") or sid),
                            "description": str(metadata[sid].get("description") or "")} for sid in hits],
            "hit_count": len(hits),
            "metrics": metrics,
            "price_source": "live" if quote else "daily",
            # close is a raw live quote when present; enriched close and all
            # technical fields remain forward-adjusted at the strategy date.
            "price_basis": "raw" if quote else "qfq",
            "technical_as_of": as_of,
        })
    candidates.sort(key=lambda item: (-item["hit_count"], -abs(item["metrics"]["amount"] or 0), item["symbol"]))
    computed = len(computed_ids)
    return {
        "asset_type": asset_type,
        "as_of": as_of,
        "updated_at": updated_at,
        # True means at least one current-date result for this asset type can
        # answer the candidate query; it is false for a missing/stale source.
        "cache_available": computed > 0,
        "coverage": {"computed": computed, "total": len(metadata)},
        "quote": {"live": live, "provider": provider if live else None,
                  "last_fetch_ms": quote_status.get("last_fetch_ms") if live else None,
                  "age_ms": age if live else None},
        "total": len(candidates),
        "items": candidates[:limit],
    }


def build_candidates(
    cached: dict | None,
    strategies: list[dict],
    quotes: list[dict],
    quote_status: dict,
    provider: str | None,
    limit: int = 60,
    *,
    asset_type: str = "stock",
    allowed_symbols: set[str] | None = None,
) -> dict:
    """Aggregate the existing stock strategy result cache only.

    The stock path deliberately remains read-only over StrategyResultCache. If a
    stock instrument directory is available, allowed_symbols prevents rows for
    other assets from leaking into this view.
    """
    cached = cached or {}
    metadata = _eligible_metadata(strategies, asset_type)
    as_of = str(cached.get("as_of")) if cached.get("as_of") else None
    results = cached.get("results") or {}
    current_results = {
        sid: result
        for sid, result in results.items()
        if isinstance(result, dict) and as_of is not None and str(result.get("as_of") or "") == as_of
    }
    return _aggregate_candidates(
        as_of=as_of,
        updated_at=cached.get("updated_at"),
        results=current_results,
        metadata=metadata,
        asset_type=asset_type,
        quotes=quotes,
        quote_status=quote_status,
        provider=provider,
        limit=limit,
        allowed_symbols=allowed_symbols,
    )


def _etf_cache_key(repo: Any, engine: Any, strategy_ids: tuple[str, ...], limit: int) -> tuple[Any, ...]:
    # Object identity scopes results to this running app without persisting data
    # or inspecting data-directory contents.
    return (id(repo), id(engine), strategy_ids, limit)


def _strategy_fingerprint(metadata: dict[str, dict]) -> tuple[tuple[str, str], ...]:
    """Capture available in-memory strategy configuration for cache invalidation."""
    return tuple(
        (sid, json.dumps(meta, ensure_ascii=False, sort_keys=True, default=str))
        for sid, meta in metadata.items()
    )


def _data_generation(repo: Any) -> str | None:
    getter = getattr(repo, "get_matrix_data_generation", None)
    if not callable(getter):
        return None
    try:
        return str(getter("etf"))
    except Exception:
        # The candidate path remains read-only if an adapter has no generation
        # token; the short TTL bounds how long same-date rows can be stale.
        return None


def _read_etf_cache(key: tuple[Any, ...]) -> dict | None:
    now = time.monotonic()
    with _ETF_CANDIDATE_CACHE_LOCK:
        entry = _ETF_CANDIDATE_CACHE.get(key)
        if entry is None:
            return None
        created_at, response = entry
        if now - created_at >= _ETF_CANDIDATE_TTL:
            _ETF_CANDIDATE_CACHE.pop(key, None)
            return None
        return deepcopy(response)


def _write_etf_cache(key: tuple[Any, ...], response: dict) -> None:
    now = time.monotonic()
    with _ETF_CANDIDATE_CACHE_LOCK:
        expired = [k for k, (created_at, _) in _ETF_CANDIDATE_CACHE.items()
                   if now - created_at >= _ETF_CANDIDATE_TTL]
        for expired_key in expired:
            _ETF_CANDIDATE_CACHE.pop(expired_key, None)
        if len(_ETF_CANDIDATE_CACHE) >= _ETF_CANDIDATE_CACHE_LIMIT and key not in _ETF_CANDIDATE_CACHE:
            oldest = min(_ETF_CANDIDATE_CACHE, key=lambda k: _ETF_CANDIDATE_CACHE[k][0])
            _ETF_CANDIDATE_CACHE.pop(oldest, None)
        _ETF_CANDIDATE_CACHE[key] = (now, deepcopy(response))


def _strategy_result_payload(result: Any) -> dict:
    as_of = getattr(result, "as_of", None)
    return {
        "as_of": str(as_of) if as_of is not None else None,
        "rows": getattr(result, "rows", []) or [],
    }


def build_etf_candidates(repo: Any, engine: Any, limit: int = 60) -> dict:
    """Compute ETF candidates from ETF enriched data without StrategyResultCache.

    The only cache here is a bounded in-process TTL snapshot scoped by
    repository, ETF data generation/date, and available in-memory strategy
    metadata. This path does not read or write a strategy cache or strategy
    override file and does not call an AI provider.
    """
    strategies = engine.list_strategies() if engine is not None else []
    metadata = _eligible_metadata(strategies, "etf")
    strategy_ids = tuple(metadata)
    if repo is None or engine is None or not strategy_ids:
        return _empty_response("etf", as_of=None, total_strategies=len(metadata))

    screener = ScreenerService(repo, asset_type="etf")
    try:
        latest = screener.latest_date()
    except Exception:
        logger.exception("ETF AI screener could not read the latest enriched date")
        response = _empty_response("etf", as_of=None, total_strategies=len(metadata))
        response["error"] = "ETF 候选数据读取失败"
        return response
    as_of = str(latest) if latest is not None else None
    base_key = _etf_cache_key(repo, engine, strategy_ids, limit)
    cache_key = (*base_key, as_of, _data_generation(repo), _strategy_fingerprint(metadata))
    cached_response = _read_etf_cache(cache_key)
    if cached_response is not None:
        return cached_response

    if latest is None:
        response = _empty_response("etf", as_of=None, total_strategies=len(metadata))
        _write_etf_cache(cache_key, response)
        return response

    try:
        context = screener.build_strategy_context(
            engine,
            latest,
            list(strategy_ids),
            timeframe="1d",
            params_map={},
            overrides_map={},
        )
    except Exception:
        logger.exception("ETF AI screener could not build the strategy context")
        response = _empty_response("etf", as_of=as_of, total_strategies=len(metadata))
        response["error"] = "ETF 候选计算失败"
        response["updated_at"] = int(time.time() * 1000)
        _write_etf_cache(cache_key, response)
        return response
    current = context.current
    if current is None or current.is_empty() or "symbol" not in current.columns:
        response = _empty_response("etf", as_of=as_of, total_strategies=len(metadata))
        _write_etf_cache(cache_key, response)
        return response

    try:
        results = engine.run_all(
            context,
            params_map={},
            overrides_map={},
            strategy_ids=list(strategy_ids),
        )
    except Exception:
        logger.exception("ETF AI screener strategy execution failed")
        response = _empty_response("etf", as_of=as_of, total_strategies=len(metadata))
        response["error"] = "ETF 候选策略计算失败"
        response["updated_at"] = int(time.time() * 1000)
        _write_etf_cache(cache_key, response)
        return response
    normalized_results = {sid: _strategy_result_payload(result) for sid, result in results.items()}
    universe = {
        str(symbol).upper()
        for symbol in current["symbol"].cast(pl.Utf8).unique().to_list()
        if symbol is not None
    }
    response = _aggregate_candidates(
        as_of=as_of,
        updated_at=int(time.time() * 1000),
        results=normalized_results,
        metadata=metadata,
        asset_type="etf",
        quotes=[],
        quote_status={},
        provider=None,
        limit=limit,
        allowed_symbols=universe,
    )
    _write_etf_cache(cache_key, response)
    return response
