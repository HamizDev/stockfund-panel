# ruff: noqa: RUF001 -- localized Chinese data freshness messages.
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
from app.market_time import CN_TZ, cn_now
from app.services import analysis_current_session, kline_sync, preferences, trading_calendar
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


def freshness_error(metadata: dict, now: datetime) -> str | None:
    """Keep old chart data readable, but do not spend AI quota on a stale request."""
    now = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    today, wall = now.date(), now.time().replace(tzinfo=None)
    active = wall >= time(9, 30) and trading_calendar.is_trading_day(today) is not False
    expected = today if active else trading_calendar.previous_trading_day(today)
    actual = metadata.get('as_of')
    if expected is None:
        return "暂时无法核验最近交易日，本次AI分析已停止；请稍后重试。"
    if active and actual == today.isoformat() and metadata.get('source') == 'local':
        return "仅取得本地当日日K，无法核验是否已更新到当前时刻；本次AI分析已停止，请稍后重试。"
    if expected and (not actual or actual < expected.isoformat()):
        return (f"行情仅截至 {actual or '未知日期'}，未取得 {expected.isoformat()} 的有效行情。"
                "本次AI分析已停止，避免使用旧行情生成最新分析；请稍后重试。")
    if active and metadata.get('is_provisional'):
        target = analysis_current_session.session_cutoff(now).replace(tzinfo=CN_TZ)
        try:
            stamp = datetime.fromisoformat(metadata['intraday_as_of'])
            stamp = stamp.replace(tzinfo=CN_TZ) if stamp.tzinfo is None else stamp.astimezone(CN_TZ)
            tolerance = timedelta(0) if metadata.get('require_current_minute') else timedelta(minutes=3)
            if stamp.date() == today and stamp <= target and target - stamp <= tolerance:
                return None
        except (KeyError, TypeError, ValueError):
            pass
        return ("当日盘中行情缺失或已滞后，无法确认最新K线。"
                "本次AI分析已停止，避免按旧价格分析；请稍后重试。")
    return None


def load_snapshot(repo, capset, symbol: str, days: int, *,
                  include_current: bool = False,
                  current_wait_seconds: float = 8,
                  as_of_time: datetime | None = None,
                  require_current_minute: bool = False) -> tuple[pl.DataFrame, dict]:
    now = as_of_time or cn_now()
    now = now.astimezone(CN_TZ) if now.tzinfo else now.replace(tzinfo=CN_TZ)
    end = now.date()
    start = end - timedelta(days=days * 2 + 180)
    asset_type = repo.resolve_asset_type(symbol)
    provider_name = preferences.get_daily_data_provider()
    requested_provider = provider_name
    raw_daily_verified = provider_name in {'tickflow', 'baostock', 'stocksdk'}
    warning = None
    frame = pl.DataFrame()
    raw = pl.DataFrame()
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
            raw_daily_verified = raw_daily_verified or getattr(provider, 'daily_price_basis', None) == 'raw'
            raw = provider.get_daily([symbol], start_time=datetime.combine(start, time.min),
                end_time=now, asset_type=asset_type)
        raw = _clean(raw, symbol, start, end)
        if raw.is_empty():
            raise ValueError("empty daily response")
        frame = raw
    except Exception:  # Provider failures must not expose configuration or credentials.
        warning = "在线日线查询失败，显示本地已有数据；请检查数据源后重试。"

    if frame.is_empty() and provider_name != 'baostock':
        try:
            fallback = _clean(_baostock_daily(symbol, start, now, asset_type), symbol, start, end)
            if not fallback.is_empty():
                raw = fallback
                frame = raw
                provider_name = 'baostock'
                raw_daily_verified = True
                warning = '主日线源暂不可用，改用免费 BaoStock 原始日线备选；日期和复权口径见图表。'
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
            warning = "在线日线早于本地数据，保留日期更新的本地快照。"
        frame, source, basis = local, "local", "local_enriched"
    daily_as_of = str(frame["date"][-1]) if not frame.is_empty() else None
    intraday_as_of = None
    intraday_provider = None
    minute_rows = []
    is_provisional = (not frame.is_empty() and frame['date'][-1] == end
                      and now.time().replace(tzinfo=None) < time(15, 30))
    # Only join prices with the same verified raw basis. The default path is
    # daily-only, including callers that resolve minute-chart previous closes.
    if include_current and source == "online" and raw_daily_verified and not raw.is_empty():
        today_rows = raw.filter(pl.col("date") == end)
        official_today = not today_rows.is_empty() and now.time().replace(tzinfo=None) >= time(15, 30)
        candidate_rows = []
        def capture_minutes(minutes):
            candidate_rows[:] = [{key: value.isoformat() if isinstance(value, datetime) else value
                                  for key, value in row.items()} for row in _json_rows(minutes)]

        minute_options = ({'require_latest': True, 'on_minutes': capture_minutes}
                          if require_current_minute else {})
        bar, minute_time = (None, None) if official_today else analysis_current_session.load_current(
            repo, capset, symbol, asset_type, now, wait_seconds=current_wait_seconds, **minute_options)
        candidate_provider = preferences.get_minute_data_provider() if bar is not None else None
        fallback_note = None
        # StockSDK's Eastmoney 1m endpoint may return no ETF rows. This bounded,
        # labelled fallback never changes the selected source or feeds strategies.
        if (not official_today and bar is None and asset_type == 'etf'
                and preferences.get_minute_data_provider() == 'stocksdk'
                and custom.provider_has_dataset('txquote', 'minute')):
            bar, minute_time = analysis_current_session.load_current(
                repo, capset, symbol, asset_type, now, wait_seconds=min(8, current_wait_seconds),
                provider_name='txquote', **minute_options)
            if bar is not None:
                candidate_provider = 'txquote'
                fallback_note = ('原分钟源暂无有效当日行情，临时日K使用腾讯ETF原始分钟备选；'
                                 '该备选不提供成交额，成交额相关指标不可用。')
        is_provisional = not today_rows.is_empty() and not official_today
        if bar is not None and not official_today:
            # A daily provider may already carry a newer partial candle. Do not
            # replace it with an earlier minute response from another request.
            # An AI request is pinned to its submission minute. The online daily
            # response can advance while fetching; only the validated minute frame
            # is authoritative for that earlier, explicit cutoff.
            can_replace = require_current_minute or today_rows.is_empty() or (
                bar["volume"] >= float(today_rows["volume"][-1])
                and bar["high"] >= float(today_rows["high"][-1])
                and bar["low"] <= float(today_rows["low"][-1])
            )
            if can_replace:
                raw = pl.concat([raw.filter(pl.col("date") != end), pl.DataFrame([bar])],
                                how="diagonal_relaxed").sort("date")
                intraday_as_of = minute_time
                intraday_provider = candidate_provider
                minute_rows = candidate_rows
                is_provisional = True
                if fallback_note:
                    warning = f'{warning} {fallback_note}' if warning else fallback_note
        elif (today_rows.is_empty() and asset_type in {'stock', 'etf'} and now.weekday() < 5
              and now.time().replace(tzinfo=None) >= time(9, 30)):
            detail = f"尚无当日有效完整分钟行情, 仅使用截至 {daily_as_of} 的日线, 不含当日盘中变化。"
            warning = f"{warning} {detail}" if warning else detail
        frame = raw
    if source == "online" and not frame.is_empty():
        # Recompute on full history before cutting the displayed/prompt window.
        frame = compute_enriched(frame).tail(days)
    metadata = {
        "symbol": symbol, "asset_type": asset_type, "source": source,
        "provider": provider_name, "requested_provider": requested_provider, "price_basis": basis,
        "as_of": str(frame["date"][-1]) if not frame.is_empty() else None,
        "daily_as_of": daily_as_of, "intraday_as_of": intraday_as_of,
        "intraday_provider": intraday_provider,
        "requested_at": now.isoformat(), "require_current_minute": require_current_minute,
        "minute_rows": minute_rows,
        "is_provisional": is_provisional,
        "checked_at": cn_now().isoformat(), "warning": warning,
    }
    metadata['freshness_error'] = freshness_error(metadata, now) if include_current else None
    return frame, {**metadata, "rows": _json_rows(frame)}
