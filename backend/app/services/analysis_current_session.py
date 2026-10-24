"""Selected-instrument, memory-only current daily candles from complete minutes."""
from __future__ import annotations

import math
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from pathlib import Path

import polars as pl

from app.market_time import CN_TZ
from app.services import kline_sync, minute_response_cache, preferences, trading_calendar
from app.tickflow.capabilities import Cap

_CACHE = minute_response_cache.MinuteResponseCache(
    ttl_seconds=20, max_entries=16, max_rows=4_096, max_rows_per_entry=300,
    max_inflight=2, auto_expire=True,
)
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='analysis-current')
_PENDING: dict = {}
_LOCK = threading.Lock()
_WAIT_SECONDS = 3


def _preference_identity(data_dir):
    path = Path(data_dir) / 'user_data/preferences.json'
    try:
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return ('missing',)


def _source_context(data_dir, capset, asset_type, provider_name=None):
    identity = _preference_identity(data_dir)
    selected = provider_name or preferences.get_minute_data_provider()
    provider, fallback, error = kline_sync._resolve_minute_provider(selected, asset_type=asset_type)
    native = bool(capset is not None and capset.has(Cap.KLINE_MINUTE_BY_SYMBOL))
    if identity != _preference_identity(data_dir) or error:
        return None, None
    if fallback:
        if selected != 'tickflow' or not native:
            return None, None
        resolved = 'tickflow'
    else:
        resolved = str(getattr(provider, 'name', selected))
        aggregation = getattr(provider, 'minute_daily_aggregation', True)
        if aggregation is False or (isinstance(aggregation, tuple) and asset_type not in aggregation):
            return None, None
        # StockSDK 1m uses raw trends2, independently of its adjusted 5m+ data.
        if resolved != 'stocksdk' and getattr(provider, 'minute_price_basis', None) != 'raw':
            return None, None
    return (selected, resolved, native, identity, id(provider) if not fallback else None), provider


def session_cutoff(now: datetime) -> datetime:
    """Requested minute label, clamped to sessions; not a bar-finality claim."""
    wall = now.astimezone(CN_TZ).replace(tzinfo=None) if now.tzinfo else now
    target = min(wall.replace(second=0, microsecond=0), datetime.combine(wall.date(), time(15)))
    if time(11, 30) <= target.time() <= time(13):
        target = datetime.combine(wall.date(), time(11, 30))
    return target


def _session_frame(frame: pl.DataFrame, symbol: str, now: datetime) -> pl.DataFrame:
    cutoff = session_cutoff(now)
    return frame.filter((pl.col('symbol') == symbol)
                        & (pl.col('datetime').dt.date() == cutoff.date())
                        & (pl.col('datetime') <= cutoff)
                        & ((pl.col('datetime').dt.time().is_between(time(9, 30), time(11, 30)))
                           | (pl.col('datetime').dt.time().is_between(time(13), time(15)))))\
        .unique(['symbol', 'datetime'], keep='last').sort('datetime')


def _aggregate(frame: pl.DataFrame, symbol: str, now: datetime, *,
               require_latest: bool = False) -> tuple[dict | None, str | None]:
    required = {'symbol', 'datetime', 'open', 'high', 'low', 'close', 'volume'}
    if frame.is_empty() or not required.issubset(frame.columns):
        return None, None
    frame = _session_frame(frame, symbol, now)
    if frame.is_empty():
        return None, None
    first, last = frame['datetime'][0], frame['datetime'][-1]
    if first.time() not in (time(9, 30), time(9, 31)):
        return None, None
    # A lunch/post-close request expects the last completed session, not an
    # invented lunchtime/overnight bar. Reject materially stale session tails.
    target = session_cutoff(now)
    if (require_latest and last != target) or target - last > timedelta(minutes=3):
        return None, None
    expected = set()
    for hour, minute in ((9, 31), (13, 1)):
        start = datetime.combine(now.date(), time(hour, minute))
        end = min(last, datetime.combine(now.date(), time(11, 30) if hour == 9 else time(15)))
        while start <= end:
            expected.add(start)
            start += timedelta(minutes=1)
    if not expected.issubset(set(frame['datetime'])):
        return None, None
    for row in frame.select('open', 'high', 'low', 'close', 'volume').iter_rows(named=True):
        prices = [row[key] for key in ('open', 'high', 'low', 'close')]
        if not all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0 for value in prices):
            return None, None
        if (row['high'] < max(row['open'], row['close'])
                or row['low'] > min(row['open'], row['close']) or row['high'] < row['low']):
            return None, None
        volume = row['volume']
        if not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0:
            return None, None
    amounts = frame['amount'].to_list() if 'amount' in frame.columns else []
    amount = sum(amounts) if amounts and all(
        isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 for value in amounts
    ) else None
    bar = {'symbol': symbol, 'date': now.date(), 'open': float(frame['open'][0]),
           'high': float(frame['high'].max()), 'low': float(frame['low'].min()),
           'close': float(frame['close'][-1]), 'volume': float(frame['volume'].sum()), 'amount': amount}
    return bar, last.replace(tzinfo=CN_TZ).isoformat()


def load_current(repo, capset, symbol: str, asset_type: str, now: datetime, *,
                 wait_seconds: float = _WAIT_SECONDS,
                 provider_name: str | None = None,
                 require_latest: bool = False,
                 minute_limit: int = 60,
                 on_minutes: Callable[[pl.DataFrame], None] | None = None) -> tuple[dict | None, str | None]:
    if asset_type not in {'stock', 'etf'} or now.weekday() >= 5 or now.time().replace(tzinfo=None) < time(9, 30):
        return None, None
    if trading_calendar.is_trading_day(now.date(), wait_seconds=min(2.0, max(0.0, wait_seconds))) is False:
        return None, None
    try:
        data_dir = repo.store.data_dir
        def source_context():
            return _source_context(data_dir, capset, asset_type, provider_name)

        context, provider = source_context()
        if context is None:
            return None, None
        key = minute_response_cache.MinuteFetchKey(
            data_dir=data_dir, provider=str(context), asset_type=asset_type, symbol=symbol,
            trade_date=now.date(), frequency='1m', raw_basis=True,
            local_partition_context=('analysis-current',
                                     session_cutoff(now).isoformat() if require_latest else None),
        )

        def fetch():
            if provider is not None:
                # Explicit selected provider only: no unlabelled silent fallback.
                frame = provider.get_minute([symbol],
                    start_time=now.replace(hour=9, minute=25, second=0, microsecond=0),
                    end_time=now, asset_type=asset_type, freq='1m')
                frame = kline_sync._enforce_minute_beijing_wallclock(frame, source=context[1])
            else:
                frame = kline_sync.fetch_minute_single(symbol, now.date(), asset_type=asset_type,
                                                       capset=capset, raw_basis=True)
            if context != source_context()[0]:
                return pl.DataFrame()
            return frame

        def unchanged(_frame):
            return context == source_context()[0]

        def cacheable(frame):
            # A lagged response must not prevent a fresh retry in this minute.
            return unchanged(frame) and (not require_latest or _aggregate(
                frame, symbol, now, require_latest=True)[0] is not None)

        with _LOCK:
            future = _PENDING.get(key)
            created = future is None
            if created:
                if len(_PENDING) >= 2:
                    return None, None
                future = _POOL.submit(_CACHE.get_or_fetch, key, fetch, cache_if=cacheable)
                _PENDING[key] = future
        if created:
            def complete(done):
                with _LOCK:
                    if _PENDING.get(key) is done:
                        _PENDING.pop(key, None)
            future.add_done_callback(complete)
        frame = future.result(timeout=min(15, max(0, wait_seconds)))
        if not unchanged(frame):
            return None, None
        bar, stamp = _aggregate(frame, symbol, now, require_latest=require_latest)
        if bar is not None and on_minutes is not None:
            on_minutes(_session_frame(frame, symbol, now).tail(max(1, min(300, minute_limit))))
        return bar, stamp
    except Exception:
        # Preserve the previous daily snapshot; never expose provider settings.
        return None, None
