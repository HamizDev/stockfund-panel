"""Opt-in main/backup reads over installed, normalized A-share providers.

Only equivalent datasets switch. Full-market minutes, depth, corporate actions
and exchange execution deliberately retain their independent contracts.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import OrderedDict
from datetime import timedelta

import polars as pl

from app.data_providers.source_health import SourceHealth

logger = logging.getLogger(__name__)
HEALTH = SourceHealth()
_LOCK = threading.Lock()
_PROXIES: OrderedDict = OrderedDict()
_LAST: OrderedDict = OrderedDict()
_SYMBOL = re.compile(r"\d{6}\.(SH|SZ|BJ)")


def enabled() -> bool:
    from app.services import preferences

    return preferences.load().get('data_source_failover_enabled') is True


def _raw(name):
    from app.data_providers.custom import loader

    # Resolve the already installed source, without recursively wrapping it.
    return loader.get_provider(name, use_failover=False)


def _remember(dataset, primary, actual):
    key = (dataset, primary)
    with _LOCK:
        changed = _LAST.get(key) != actual
        _LAST[key] = actual
        _LAST.move_to_end(key)
        while len(_LAST) > 64:
            _LAST.popitem(last=False)
    if changed:
        logger.info('data source route %s: %s -> %s', dataset, primary, actual)


def status() -> dict:
    from app.services import preferences

    with _LOCK:
        current = [{'dataset': key[0], 'primary': key[1], 'active': value}
                   for key, value in _LAST.items()]
    return {'enabled': enabled(), 'active': current, 'health': HEALTH.snapshot(),
            'selected': {'daily': preferences.get_daily_data_provider(),
                         'realtime': preferences.get_realtime_data_provider(),
                         'minute': preferences.get_minute_data_provider(),
                         'full_minute': preferences.get_full_minute_data_provider()},
            'policies': {'realtime': ['stocksdk', 'fuyao'],
                         'daily': ['stocksdk', 'tickflow'],
                         'minute_1m': ['txquote', 'stocksdk'],
                         'global_chart_hosts': ['query1.finance.yahoo.com', 'query2.finance.yahoo.com']}}


def wrap(provider):
    name = getattr(provider, 'name', None)
    if not enabled() or name not in {'stocksdk', 'fuyao', 'txquote'}:
        return provider
    with _LOCK:
        entry = _PROXIES.get(name)
        if entry is None or entry[0] is not provider:
            entry = (provider, _Provider(provider))
            _PROXIES[name] = entry
        _PROXIES.move_to_end(name)
        while len(_PROXIES) > 64:
            _PROXIES.popitem(last=False)
        return entry[1]


def _finite(value, *, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def _quotes(rows, wanted=None, *, source=None):
    if not isinstance(rows, list):
        return []
    selected = set(wanted) if wanted is not None else None
    now_ms = None
    if source == 'stocksdk':
        from app.market_time import cn_now, in_continuous_session
        from app.services import trading_calendar

        now = cn_now()
        if (in_continuous_session(now)
                and trading_calendar.is_trading_day(now.date(), wait_seconds=0) is True):
            now_ms = now.timestamp() * 1000
    seen, clean = set(), []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('symbol'), str)
                or not _SYMBOL.fullmatch(row['symbol']) or row['symbol'] in seen
                or (selected is not None and row['symbol'] not in selected)
                or not _finite(row.get('last_price'), positive=True)):
            continue
        stamp = row.get('timestamp')
        if now_ms is not None and (not _finite(stamp, positive=True) or not -5000 <= now_ms - stamp <= 120_000):
            continue
        seen.add(row['symbol'])
        clean.append(dict(row))
    return clean


def _daily(frame, symbols, start, end):
    from app.data_providers.normalizer import normalize_daily

    if not isinstance(frame, pl.DataFrame) or frame.is_empty():
        return pl.DataFrame()
    frame = normalize_daily(frame)
    required = {'symbol', 'date', 'open', 'high', 'low', 'close', 'volume'}
    if not required <= set(frame.columns):
        return pl.DataFrame()
    frame = frame.filter(
        pl.col('symbol').is_in(symbols)
        & pl.all_horizontal([pl.col(c).is_finite() & (pl.col(c) > 0)
                             for c in ('open', 'high', 'low', 'close')])
        & (pl.col('high') >= pl.max_horizontal('open', 'close'))
        & (pl.col('low') <= pl.min_horizontal('open', 'close'))
        & pl.col('volume').is_finite() & (pl.col('volume') >= 0))
    if start is not None:
        frame = frame.filter(pl.col('date') >= start.date())
    if end is not None:
        frame = frame.filter(pl.col('date') <= end.date())
    return frame.unique(['symbol', 'date'], keep='last').sort(['symbol', 'date'])


def _daily_session(end):
    from app.market_time import CN_TZ, cn_now
    from app.services import trading_calendar

    if end is None:
        return None
    now = cn_now()
    requested = end.astimezone(CN_TZ).date() if end.tzinfo else end.date()
    target = min(requested, now.date())
    # Historical daily endpoints need not contain the current unfinished bar.
    # That bar remains subject to the separate current-minute aggregation rules.
    boundary = target + timedelta(days=1)
    if target == now.date() and now.hour < 16:
        boundary = target
    return trading_calendar.previous_trading_day(boundary, wait_seconds=0)


class _Provider:
    def __init__(self, primary):
        self._primary = primary

    def __getattr__(self, name):
        return getattr(self._primary, name)

    def get_daily(self, symbols, start_time=None, end_time=None, asset_type='stock', on_chunk_done=None):
        if self.name != 'stocksdk' or not enabled():
            return self._primary.get_daily(symbols, start_time=start_time, end_time=end_time,
                asset_type=asset_type, on_chunk_done=on_chunk_done)
        if not symbols:
            return pl.DataFrame()
        partial = []
        missing = list(dict.fromkeys(symbols))
        expected = _daily_session(end_time)
        for source in ('stocksdk', 'tickflow'):
            requested = list(missing)
            fetched = []

            def fetch(source=source, requested=requested, fetched=fetched):
                if source == 'tickflow':
                    from app.data_providers.tickflow_provider import TickFlowProvider

                    frame = TickFlowProvider().get_daily(requested, start_time, end_time, asset_type)
                else:
                    frame = self._primary.get_daily(requested, start_time=start_time,
                        end_time=end_time, asset_type=asset_type, on_chunk_done=on_chunk_done)
                clean = _daily(frame, requested, start_time, end_time)
                fetched.append(clean)
                return clean

            result = HEALTH.attempt(('daily', source), fetch, lambda value: not value.is_empty()
                and (expected is None or value['date'].max() >= expected))
            frame = fetched[0] if fetched else None
            if frame is not None and not frame.is_empty():
                partial.append(frame.with_columns(pl.col('date').max().over('symbol').alias('__latest')))
                recent = frame.filter(pl.col('date') >= expected) if expected is not None else frame
                received = set(recent['symbol'].to_list())
                missing = [symbol for symbol in missing if symbol not in received]
                if result.success:
                    _remember('daily', self.name, source)
                if not missing:
                    break
        if not partial:
            return pl.DataFrame()
        combined = pl.concat(partial, how='diagonal_relaxed')
        if expected is not None:
            # A stale source can support an old chart only through the caller's
            # explicitly stale/local cache; it must not report a successful sync.
            combined = combined.filter(pl.col('date').max().over('symbol') >= expected)
        return (combined.sort(['symbol', 'date', '__latest']).unique(['symbol', 'date'], keep='last')
                .drop('__latest').sort(['symbol', 'date']))

    def _realtime(self, method, symbols=None):
        if self.name not in {'stocksdk', 'fuyao'} or not enabled():
            fn = getattr(self._primary, method)
            return fn() if symbols is None else fn(symbols)
        if symbols == []:
            return []
        missing = list(dict.fromkeys(symbols)) if symbols is not None else None
        merged = []
        candidates = [self.name, 'fuyao' if self.name == 'stocksdk' else 'stocksdk']
        for source in candidates:
            requested = list(missing) if missing is not None else None

            def fetch(source=source, requested=requested):
                provider = self._primary if source == self.name else _raw(source)
                fn = getattr(provider, method)
                return _quotes(fn() if requested is None else fn(requested), requested, source=source)

            # Full-market failure must not publish a one-stock response as a
            # valid universe. Explicit index/ETF requests use their own scope.
            minimum = 1000 if symbols is None else 1
            result = HEALTH.attempt((method, source), fetch, lambda value, minimum=minimum: len(value) >= minimum)
            if result.success:
                _remember(method, self.name, source)
                # Fuyao's timestamp is a receipt/server timestamp, not the last
                # trade time. Keep its price usable for display without letting
                # it masquerade as fresh exchange evidence or trigger orders.
                rows = [dict(row, _source_timestamp_verified=source == 'stocksdk') for row in result.value]
                if source != 'stocksdk':
                    rows = [dict(row, timestamp=None) for row in rows]
                merged.extend(rows)
                if missing is None:
                    return merged
                received = {row['symbol'] for row in rows}
                missing = [symbol for symbol in missing if symbol not in received]
                if not missing:
                    return merged
        return merged if merged or symbols is None else None

    def get_realtime(self):
        return self._realtime('get_realtime')

    def get_realtime_indices(self, symbols):
        return self._realtime('get_realtime_indices', symbols)

    def get_realtime_etfs(self, symbols):
        return self._realtime('get_realtime_etfs', symbols)

    def get_minute(self, symbols, start_time=None, end_time=None, asset_type='stock',
                   freq='1m', on_chunk_done=None):
        kwargs = dict(start_time=start_time, end_time=end_time, asset_type=asset_type,
                      freq=freq, on_chunk_done=on_chunk_done)
        # The verified StockSDK 1m path is raw trends data. Its 5m+ price basis
        # differs, so those intervals and strict research sources never switch.
        if (self.name != 'txquote' or freq != '1m' or asset_type not in {'stock', 'etf'}
                or not enabled()):
            return self._primary.get_minute(symbols, **kwargs)
        if not symbols:
            return pl.DataFrame()
        from app.market_time import CN_TZ
        from app.services.kline_sync import _enforce_minute_beijing_wallclock

        def wall(value):
            return value.astimezone(CN_TZ).replace(tzinfo=None) if value and value.tzinfo else value

        start, end = wall(start_time), wall(end_time)
        missing = list(dict.fromkeys(symbols))
        frames = []
        for source in ('txquote', 'stocksdk'):
            requested = list(missing)

            def fetch(source=source, requested=requested):
                provider = self._primary if source == self.name else _raw(source)
                frame = provider.get_minute(requested, **kwargs)
                if not isinstance(frame, pl.DataFrame) or frame.is_empty():
                    return pl.DataFrame()
                frame = _enforce_minute_beijing_wallclock(frame, source=source)
                if not {'symbol', 'datetime', 'close'} <= set(frame.columns):
                    return pl.DataFrame()
                frame = frame.filter(pl.col('symbol').is_in(requested)
                    & pl.col('close').is_finite() & (pl.col('close') > 0))
                if start:
                    frame = frame.filter(pl.col('datetime') >= start)
                if end:
                    frame = frame.filter(pl.col('datetime') <= end)
                return frame.unique(['symbol', 'datetime'], keep='last').sort(['symbol', 'datetime'])

            result = HEALTH.attempt(('minute', source, asset_type, freq), fetch,
                                    lambda value: not value.is_empty())
            if result.success:
                _remember('minute_1m_' + asset_type, self.name, source)
                frames.append(result.value)
                received = set(result.value['symbol'].to_list())
                missing = [symbol for symbol in missing if symbol not in received]
                if not missing:
                    break
        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how='diagonal_relaxed').unique(
            ['symbol', 'datetime'], keep='first').sort(['symbol', 'datetime'])
