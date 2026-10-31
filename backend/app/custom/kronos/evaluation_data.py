
"""Selected raw actual prices only; no source settings or market files are changed."""
from datetime import date, datetime, time

import polars as pl

from app.data_providers import custom
from app.market_time import CN_TZ
from app.services import (
    kline_sync,
    minute_adjust,
    preferences,
    trading_calendar,
)

from .data import _raw_daily, _rows, closed_daily_date, online_raw_daily
from .evaluation import valid_price


def minute_closes(frame, symbol, day, now):
    required = {'symbol', 'datetime', 'close'}
    if frame.is_empty() or not required.issubset(frame.columns):
        return {}
    # The fetch layer converts timezone-aware values to Beijing wall time.
    if getattr(frame.schema['datetime'], 'time_zone', None) is not None:
        return {}
    safe = now.astimezone(CN_TZ).replace(tzinfo=None, second=0, microsecond=0)
    selected = frame.filter((pl.col('symbol') == symbol) & (pl.col('datetime').dt.date() == day)
                            & (pl.col('datetime') < safe))
    duplicate = set(selected.group_by('datetime').len().filter(pl.col('len') > 1)['datetime'].to_list())
    result = {}
    for row in selected.iter_rows(named=True):
        stamp = row['datetime']
        value = row['close']
        if stamp.second or stamp.microsecond or stamp in duplicate or not (time(9, 31) <= stamp.time() <= time(11, 30)
                                     or time(13, 1) <= stamp.time() <= time(15)):
            continue
        if valid_price(value):
            result[stamp.isoformat(timespec='minutes')] = float(value)
    return result


def supported_history(provider, day, today):
    depth = getattr(provider, 'minute_history_days', None)
    if depth is None:
        return True
    if type(depth) is not int or not 1 <= depth <= 60:
        return False
    latest = today if trading_calendar.is_trading_day(today) is True else trading_calendar.previous_trading_day(today)
    if latest is None:
        return False
    earliest = latest
    for _ in range(depth - 1):
        earliest = trading_calendar.previous_trading_day(earliest)
        if earliest is None:
            return False
    return earliest <= day <= latest


def _local_minute(repo, symbol, asset_type, day, now):
    if not minute_adjust.minute_basis_is_raw(repo.store.data_dir):
        return {}
    folder = repo.store.data_dir / ('kline_etf_minute' if asset_type == 'etf' else 'kline_minute') / f'date={day}'
    paths = [str(path) for path in folder.glob('*.parquet') if path.is_file() and not path.is_symlink()]
    if not paths:
        return {}
    frame = (pl.scan_parquet(paths, hive_partitioning=False, missing_columns='insert', extra_columns='ignore')
             .select('symbol', 'datetime', 'close').filter(pl.col('symbol') == symbol).collect())
    return minute_closes(frame, symbol, day, now)


def actual_prices(repo, capset, symbol, asset_type, period, day_text, now):
    del capset  # Sources are explicit raw providers; no unlabelled fallback.
    now = now.astimezone(CN_TZ)
    day = date.fromisoformat(day_text)
    if day > now.date() or period not in {'daily', '1m'}:
        return {}
    if trading_calendar.is_trading_day(day) is not True:
        return {}
    if period == 'daily':
        if day > closed_daily_date(now):
            return {}
        frame = _raw_daily(repo, symbol, asset_type, 1, day, start_day=day)
        if frame.is_empty():
            frame = online_raw_daily(symbol, asset_type, day, day, strict=True)
        if not {'symbol', 'date'}.issubset(frame.columns):
            return {}
        frame = frame.filter((pl.col('symbol') == symbol) & (pl.col('date') == day))
        if frame.height != 1:
            return {}
        rows = _rows(frame, symbol, 'date', 1)
        return {day_text: rows[-1]['close']} if rows[-1]['time'] == day_text else {}
    local = {}
    try:
        local = _local_minute(repo, symbol, asset_type, day, now)
        if len(local) == 240:
            return local
    except (OSError, ValueError, pl.exceptions.PolarsError):
        pass
    selected = preferences.get_minute_data_provider()
    names = [selected]
    if selected == 'stocksdk' and asset_type == 'etf' and custom.provider_has_dataset('txquote', 'minute'):
        names.append('txquote')
    for name in names:
        if name == 'tickflow':
            continue
        try:
            provider = custom.get_provider(name, use_failover=False)
            if name != 'stocksdk' and getattr(provider, 'minute_price_basis', None) != 'raw':
                continue
            if asset_type not in getattr(provider, 'minute_asset_types', ()) or not supported_history(provider, day, now.date()):
                continue
            def fetch(source=provider, source_name=name):
                frame = source.get_minute([symbol], start_time=datetime.combine(day, time(9, 25)),
                    end_time=datetime.combine(day, time(15, 5)), asset_type=asset_type, freq='1m')
                return kline_sync._enforce_minute_beijing_wallclock(frame, source=source_name)
            # EvaluationManager already shares one bounded quote dictionary per
            # symbol/day in each scoring cycle. Do not retain another frame cache.
            frame = fetch()
            if preferences.get_minute_data_provider() != selected:
                return {}
            actual = minute_closes(frame, symbol, day, now)
            if actual:
                return {**local, **actual}
        except Exception:
            continue
    return local
