"""Read-only, selected-instrument daily research snapshots.

Never append raw prices to an already adjusted local series. All indicators and
levels are derived from the same frame; online snapshots explicitly state their
unadjusted basis when no verified event factors are available.
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta

import polars as pl

from app.data_providers import custom
from app.data_providers.normalizer import normalize_daily
from app.indicators.pipeline import compute_enriched
from app.market_time import cn_now
from app.services import kline_sync, preferences
from app.tickflow.capabilities import Cap


def _baostock_daily(symbol: str, start, now, asset_type: str) -> pl.DataFrame:
    """Optional selected-instrument fallback; never changes the global source."""
    from app.plugins.baostock.provider import BaoStockProvider, availability

    if asset_type not in ('stock', 'etf') or not symbol.endswith(('.SH', '.SZ')):
        return pl.DataFrame()
    available, _ = availability()
    if not available:
        return pl.DataFrame()
    return BaoStockProvider().get_daily([symbol], start_time=datetime.combine(start, time.min),
        end_time=now, asset_type=asset_type)


def _clean(frame: pl.DataFrame, symbol: str, start, end) -> pl.DataFrame:
    frame = normalize_daily(frame, default_symbol=symbol)
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pl.DataFrame()
    prices = [pl.col(c).is_finite() & (pl.col(c) > 0) for c in ("open", "high", "low", "close")]
    return frame.filter(
        (pl.col("symbol") == symbol) & pl.col("date").is_between(start, end)
        & pl.all_horizontal(prices)
        & (pl.col("high") >= pl.max_horizontal("open", "close"))
        & (pl.col("low") <= pl.min_horizontal("open", "close"))
        & pl.col("volume").is_finite() & (pl.col("volume") >= 0)
    ).unique(subset=["symbol", "date"], keep="last").sort("date")


def _json_rows(frame: pl.DataFrame) -> list[dict]:
    # Some computed indicators are undefined on short histories (NaN/inf).
    return [{key: None if isinstance(value, float) and not math.isfinite(value) else value
             for key, value in row.items()} for row in frame.to_dicts()]


def load_snapshot(repo, capset, symbol: str, days: int) -> tuple[pl.DataFrame, dict]:
    now = cn_now()
    end = now.date()
    start = end - timedelta(days=days * 2 + 180)
    asset_type = repo.resolve_asset_type(symbol)
    provider_name = preferences.get_daily_data_provider()
    requested_provider = provider_name
    warning = None
    frame = pl.DataFrame()
    try:
        if provider_name == 'baostock':
            raw = _baostock_daily(symbol, start, now, asset_type)
        elif provider_name == "tickflow":
            if capset is None or not capset.has(Cap.KLINE_DAILY_BATCH):
                raise ValueError("daily capability unavailable")
            raw = kline_sync.sync_daily_batch([symbol], count=days * 2 + 180,
                start_time=datetime.combine(start, time.min), end_time=now)
        else:
            if not custom.provider_has_dataset(provider_name, "daily"):
                raise ValueError("daily capability unavailable")
            provider = custom.get_provider(provider_name)
            raw = provider.get_daily([symbol], start_time=datetime.combine(start, time.min),
                end_time=now, asset_type=asset_type)
        raw = _clean(raw, symbol, start, end)
        if raw.is_empty():
            raise ValueError("empty daily response")
        frame = compute_enriched(raw).tail(days)
    except Exception:  # Provider failures must not expose configuration or credentials.
        warning = "在线日线查询失败，显示本地已有数据；请检查数据源后重试。"  # noqa: RUF001

    if frame.is_empty() and provider_name != 'baostock':
        try:
            fallback = _clean(_baostock_daily(symbol, start, now, asset_type), symbol, start, end)
            if not fallback.is_empty():
                frame = compute_enriched(fallback).tail(days)
                provider_name = 'baostock'
                warning = '主日线源暂不可用，改用免费 BaoStock 原始日线备选；日期和复权口径见图表。'  # noqa: RUF001
        except Exception:  # Optional SDK/network errors retain the existing local fallback.
            pass

    source, basis = "online", "unadjusted"
    # The repository also contributes a newer valid local frame if the upstream
    # omits recent sessions. Never replace a newer chart with older provider rows.
    try:
        local = repo.get_daily_asset(asset_type, symbol, start, end).sort("date").tail(days)
    except Exception:
        local = pl.DataFrame()
    if not local.is_empty() and (frame.is_empty() or local["date"][-1] > frame["date"][-1]):
        if not frame.is_empty():
            warning = "在线日线早于本地数据，保留日期更新的本地快照。"  # noqa: RUF001
        frame, source, basis = local, "local", "local_enriched"
    metadata = {
        "symbol": symbol, "asset_type": asset_type, "source": source,
        "provider": provider_name, "requested_provider": requested_provider, "price_basis": basis,
        "as_of": str(frame["date"][-1]) if not frame.is_empty() else None,
        "checked_at": now.isoformat(), "warning": warning,
    }
    return frame, {**metadata, "rows": _json_rows(frame)}
