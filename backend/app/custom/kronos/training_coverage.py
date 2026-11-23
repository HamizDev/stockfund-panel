"""Read-only coverage of locally stored, complete A-share minute sessions.

This reports data coverage only. It does not assess training eligibility, train a
model, or write to the repository.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta

import polars as pl

from app.custom.kronos.data import _rows
from app.market_time import CN_TZ, cn_now

_SYMBOL = re.compile(r'^\d{6}\.(SH|SZ|BJ)$')
_TARGET_DAYS = 20
_LOOKBACK_DAYS = 60
_SESSION_BARS = 240


def _bounds(now: datetime) -> tuple[date, date]:
    if now.tzinfo is None:
        raise ValueError('当前时间必须包含北京时间时区。')
    wall = now.astimezone(CN_TZ)
    today = wall.date()
    through = today if wall.time().replace(tzinfo=None) >= time(15, 15) else today - timedelta(days=1)
    return through - timedelta(days=_LOOKBACK_DAYS - 1), through


def _expected_times(day: date) -> list[datetime]:
    return [datetime.combine(day, start) + timedelta(minutes=offset)
            for start, count in ((time(9, 31), 120), (time(13, 1), 120))
            for offset in range(count)]


def summarize(repo, symbol: str, asset_type: str, *, now: datetime | None = None) -> dict:
    """Count up to 20 complete sessions from the repository's bounded date range."""
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
        raise ValueError('标的代码格式无效。')
    if asset_type not in {'stock', 'etf'}:
        raise ValueError('标的类型仅支持stock或etf。')

    start, through = _bounds(now or cn_now())
    listed = repo.list_minute_dates(start, through, asset_type)
    dates = sorted({day for day in listed
                    if isinstance(day, date) and not isinstance(day, datetime)
                    and start <= day <= through and day.weekday() < 5})
    data = repo.get_minute_by_dates([symbol], dates, asset_type) if dates else pl.DataFrame()

    complete: list[date] = []
    required = {'symbol', 'datetime', 'open', 'high', 'low', 'close', 'volume'}
    if isinstance(data, pl.DataFrame) and not data.is_empty() and required.issubset(data.columns):
        for day in dates:
            try:
                frame = data.filter(
                    (pl.col('symbol') == symbol)
                    & (pl.col('datetime').dt.date() == day)
                ).filter(
                    pl.col('datetime').dt.time().is_between(time(9, 31), time(11, 30))
                    | pl.col('datetime').dt.time().is_between(time(13, 1), time(15))
                ).sort('datetime')
                # Compare raw datetimes before _rows formats timestamps to minutes.
                if frame['datetime'].to_list() != _expected_times(day):
                    continue
                # _rows validates strict ordering, duplicates and OHLC relations.
                rows = _rows(frame, symbol, 'datetime', frame.height)
            except (ValueError, TypeError, pl.exceptions.PolarsError):
                continue
            if len(rows) != _SESSION_BARS:
                continue
            try:
                volumes = [row['volume'] for row in rows]
            except (KeyError, TypeError):
                continue
            if (any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                    for value in volumes)
                    or not any(value > 0 for value in volumes)):
                continue
            complete.append(day)

    selected = complete[-_TARGET_DAYS:]
    return {
        'symbol': symbol,
        'asset_type': asset_type,
        'target_days': _TARGET_DAYS,
        'complete_days': len(selected),
        'from_date': start.isoformat(),
        'through_date': through.isoformat(),
        'dates': [day.isoformat() for day in selected],
    }
