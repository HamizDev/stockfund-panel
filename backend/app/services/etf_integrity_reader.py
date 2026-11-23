# ruff: noqa: RUF001 -- Read-only integrity labels.
"""Small ETF storage snapshots for the pure gap detector; never repairs data."""
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from app.market_time import cn_now
from app.services import trading_calendar
from app.services.etf_history_integrity import MAX_SYMBOLS, MAX_WINDOW_DAYS, detect_etf_history_gaps


def report(repo, symbols, start=None, end=None):
    end = end or cn_now().date()
    start = start or end - timedelta(days=90)
    if (not 1 <= len(symbols) <= MAX_SYMBOLS or len(set(symbols)) != len(symbols)
            or start > end or end > cn_now().date() or (end - start).days + 1 > MAX_WINDOW_DAYS):
        raise ValueError('ETF缺口检测最多8只、550个自然日，日期须有序且代码不重复')
    if any(repo.resolve_asset_type(symbol) != 'etf' for symbol in symbols):
        raise ValueError('请只选择已识别的ETF')
    root = Path(repo.store.data_dir)
    snapshots = []
    for table in ('kline_etf_daily', 'kline_etf_enriched'):
        dates = {symbol: [] for symbol in symbols}
        files = []
        for partition in (root / table).glob('date=*'):
            try:
                day = date.fromisoformat(partition.name[5:])
            except ValueError:
                continue
            if start <= day <= end:
                files.extend((day, p) for p in partition.glob('*.parquet') if p.is_file())
        if len(files) > (MAX_WINDOW_DAYS + 1) * 4:
            raise ValueError('分区文件超出检测预算')
        for day, path in files:
            frame = pl.read_parquet(path, columns=['symbol'])
            present = set(frame['symbol'].filter(frame['symbol'].is_in(symbols)).to_list())
            for symbol in present:
                dates[symbol].append(day)
        snapshots.append({symbol: sorted(set(days)) for symbol, days in dates.items()})
    months, cursor = {}, start.replace(day=1)
    while cursor <= end:
        months[(cursor.year, cursor.month)] = trading_calendar.month_open_days(cursor.year, cursor.month)
        cursor = date(cursor.year + int(cursor.month == 12), cursor.month % 12 + 1, 1)
    rows = detect_etf_history_gaps(symbols=symbols, raw_dates_by_symbol=snapshots[0],
        enriched_dates_by_symbol=snapshots[1], official_open_days_by_month=months,
        window_start=start, window_end=end)
    return dict(symbols=[asdict(row) for row in rows], checked_at=cn_now().isoformat(),
                mode='read_only', repaired=0,
                note='仅检查已观测首末日期之间的内部缺口；待核验缺口可能是停牌或缺数，未自动写入。')
