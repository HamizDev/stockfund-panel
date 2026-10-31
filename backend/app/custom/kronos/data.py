# ruff: noqa: RUF001 -- Chinese local research status messages.
"""Selected A-share/ETF observations and official future trading timestamps."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from pathlib import Path

import polars as pl

from app.data_providers import custom, registry
from app.data_providers.normalizer import normalize_daily
from app.market_time import CN_TZ
from app.services import analysis_current_session, kline_sync, preferences, trading_calendar


def _open_day(day: date) -> bool:
    value = trading_calendar.is_trading_day(day)
    if value is None:
        raise ValueError('交易日历暂不可核验，预测未启动，请稍后重试。')
    return value


def closed_daily_date(now: datetime) -> date:
    if now.time().replace(tzinfo=None) >= time(15, 30) and _open_day(now.date()):
        return now.date()
    previous = trading_calendar.previous_trading_day(now.date())
    if previous is None:
        raise ValueError('无法确认最近已收盘交易日，预测未启动。')
    return previous


def future_times(last: str, period: str, horizon: int) -> list[str]:
    """Enumerate actual exchange sessions; unknown holidays never become weekdays."""
    cutoff = datetime.fromisoformat(last)
    day = cutoff.date()
    result = []
    months = {}
    for _ in range(120):
        key = (day.year, day.month)
        if key not in months:
            months[key] = trading_calendar.month_open_days(*key)
        open_days = months[key]
        if open_days is None:
            raise ValueError('未来交易日历暂不可核验，预测未启动。')
        if day in open_days:
            if period == 'daily':
                if day > cutoff.date():
                    result.append(day.isoformat())
            else:
                for start, end in ((time(9, 31), time(11, 30)), (time(13, 1), time(15))):
                    stamp = datetime.combine(day, start)
                    while stamp.time() <= end:
                        if stamp > cutoff:
                            result.append(stamp.isoformat(timespec='minutes'))
                            if len(result) == horizon:
                                return result
                        stamp += timedelta(minutes=1)
            if len(result) >= horizon:
                return result[:horizon]
        day += timedelta(days=1)
    raise ValueError('无法构造完整预测交易时间窗口。')


def _rows(frame: pl.DataFrame, symbol: str, field: str, limit: int) -> list[dict]:
    needed = {'symbol', field, 'open', 'high', 'low', 'close'}
    if frame.is_empty() or not needed.issubset(frame.columns):
        raise ValueError('未取得完整的真实开高低收数据，预测未启动。')
    frame = frame.filter(pl.col('symbol') == symbol).sort(field).tail(limit)
    if frame.height < limit:
        raise ValueError(f'只有{frame.height}根可用K线，所选窗口需要{limit}根；请缩短窗口或更新行情。')
    rows, previous = [], ''
    for row in frame.iter_rows(named=True):
        stamp = row[field]
        if not isinstance(stamp, date) or (isinstance(stamp, datetime) and stamp.tzinfo is not None):
            raise ValueError('行情日期或北京时间口径不可核验。')
        text = stamp.isoformat(timespec='minutes') if isinstance(stamp, datetime) else stamp.isoformat()
        if text <= previous:
            raise ValueError('K线日期重复或顺序异常，预测未启动。')
        previous = text
        values = [row[key] for key in ('open', 'high', 'low', 'close')]
        if not all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in values):
            raise ValueError('K线含无效价格，预测未启动。')
        op, hi, lo, cl = values
        if hi < max(op, cl) or lo > min(op, cl) or hi < lo:
            raise ValueError('K线高低价关系异常，预测未启动。')
        item = {'time': text, **dict(zip(('open', 'high', 'low', 'close'), values, strict=True))}
        for key in ('volume', 'amount'):
            value = row.get(key)
            item[key] = value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        rows.append(item)
    return rows


def _raw_daily(repo, symbol: str, asset_type: str, count: int, cutoff: date, *,
               start_day: date | None = None) -> pl.DataFrame:
    """Read only raw daily partitions; enriched prices have a different basis."""
    data_dir = getattr(getattr(repo, 'store', None), 'data_dir', None)
    if data_dir is None:
        return pl.DataFrame()
    folder = Path(data_dir) / ('kline_etf_daily' if asset_type == 'etf' else 'kline_daily')
    first = start_day or cutoff - timedelta(days=count * 2 + 180)
    files = []
    for partition in folder.glob('date=*'):
        try:
            day = date.fromisoformat(partition.name.removeprefix('date='))
        except ValueError:
            continue
        if first <= day <= cutoff:
            files.extend(str(path) for path in partition.glob('*.parquet') if path.is_file())
    if not files:
        return pl.DataFrame()
    schema = {'symbol': pl.String, 'date': pl.Date,
              **dict.fromkeys(('open', 'high', 'low', 'close', 'volume', 'amount'), pl.Float64)}
    lazy = pl.scan_parquet(sorted(files), hive_partitioning=False, schema=schema,
                           missing_columns='insert', extra_columns='ignore',
                           cast_options=pl.ScanCastOptions(integer_cast='allow-float'))
    frame = (lazy
            .filter((pl.col('symbol') == symbol) & (pl.col('date') <= cutoff)
                    & (pl.col('date') >= first))
            .sort('date').tail(count + 1).collect())
    if frame['date'].n_unique() != frame.height:
        raise ValueError('原始日线出现重复日期。')
    return frame.tail(count)


def online_raw_daily(symbol: str, asset_type: str, first: date, cutoff: date, *, strict=False) -> pl.DataFrame:
    """Use the same selected, verified raw daily route for input and evaluation."""
    name = preferences.get_daily_data_provider()
    provider = (registry.get_provider(name) if name == 'tickflow' else
                custom.get_provider(name, use_failover=False) if strict else custom.get_provider(name))
    if name not in {'tickflow', 'stocksdk', 'baostock'} and getattr(provider, 'daily_price_basis', None) != 'raw':
        raise ValueError('当前日线源未明确原始价格口径，无法用于本地预测。')
    frame = provider.get_daily([symbol], start_time=datetime.combine(first, time.min),
                               end_time=datetime.combine(cutoff, time(23, 59)), asset_type=asset_type)
    return normalize_daily(frame, default_symbol=symbol)


def _daily(repo, symbol: str, asset_type: str, count: int, now: datetime):
    cutoff = closed_daily_date(now)
    warning = '只使用已收盘日K；原始未复权价格遇除权或份额折算可能产生跳变。'
    try:
        local = _rows(_raw_daily(repo, symbol, asset_type, count, cutoff), symbol, 'date', count)
        if local[-1]['time'] == cutoff.isoformat():
            return local, [warning, '已核验本地原始日线覆盖最近已收盘交易日，未使用复权指标表。']
    except (OSError, ValueError, pl.exceptions.PolarsError):
        pass
    frame = online_raw_daily(symbol, asset_type, cutoff - timedelta(days=count * 2 + 180), cutoff)
    if 'date' in frame.columns:
        frame = frame.filter(pl.col('date') <= cutoff)
    rows = _rows(frame, symbol, 'date', count)
    if rows[-1]['time'] != cutoff.isoformat():
        raise ValueError(f'日K仅截至{rows[-1]["time"]}，缺少最近已收盘交易日{cutoff}，预测未启动。')
    return rows, [warning, '本地原始日线未满足窗口要求，本次已在线重新取数核验。']


def current_minute_frame(repo, capset, symbol: str, asset_type: str, now: datetime, *,
                         source_notes: list[str] | None = None) -> pl.DataFrame:
    if now.time().replace(tzinfo=None) < time(9, 31) or not _open_day(now.date()):
        raise ValueError('1分钟预测需要当日已开始的有效交易行情，请在交易日开盘后使用。')
    captured = []
    selected = preferences.get_minute_data_provider()
    providers = [None]
    if asset_type == 'etf' and selected == 'stocksdk' and custom.provider_has_dataset('txquote', 'minute'):
        providers.append('txquote')
    if asset_type == 'stock' and selected != 'stocksdk' and custom.provider_has_dataset('stocksdk', 'minute'):
        providers.append('stocksdk')
    for provider_name in providers:
        captured.clear()
        _, _ = analysis_current_session.load_current(repo, capset, symbol, asset_type, now,
            require_latest=True, minute_limit=300, wait_seconds=12, provider_name=provider_name,
            cache_result=False,
            on_minutes=lambda frame: captured.append(frame))
        if captured:
            if provider_name is not None and source_notes is not None:
                source_notes.append(f'所选分钟源{selected}未提供合格原始股票分钟K，本次显式使用{provider_name}备用；未修改全局数据源设置。')
            break
    if not captured:
        raise ValueError('当前分钟源未返回连续、有效的当日OHLCV；价格点或缺字段不能用于预测。')
    # Sources lack a finality flag. Exclude the wall-clock minute in progress;
    # during lunch/after close the completed session endpoint remains usable.
    safe = now.astimezone(CN_TZ).replace(tzinfo=None, second=0, microsecond=0)
    frame = captured[0].filter((pl.col('datetime') < safe) & (
        pl.col('datetime').dt.time().is_between(time(9, 31), time(11, 30))
        | pl.col('datetime').dt.time().is_between(time(13, 1), time(15))))
    return frame


def historical_minute_tail(repo, capset, symbol: str, asset_type: str, day: date,
                           count: int, *, source_notes: list[str] | None = None) -> pl.DataFrame:
    """One prior session, no disk/cache writes; validate exactly the needed suffix."""
    selected = preferences.get_minute_data_provider()
    if type(count) is not int or not 1 <= count <= 240:
        raise ValueError('历史分钟尾部根数需为1至240。')
    sources = [selected]
    # ETF mkline is native raw OHLC; stock day/query contains price points only.
    if asset_type == 'etf' and selected == 'stocksdk':
        sources.append('txquote')
    if selected != 'eltdx_gateway':
        sources.append('eltdx_gateway')
    expected = []
    for hour, minute, size in ((9, 31, 120), (13, 1, 120)):
        start = datetime.combine(day, time(hour, minute))
        expected.extend(start + timedelta(minutes=i) for i in range(size))
    expected = expected[-count:]
    for name in sources:
        # StockSDK only documents genuine per-minute opens for the latest
        # session. A numerically plausible older open is still not verified.
        if name == 'stocksdk':
            continue
        try:
            if name == 'tickflow':
                if capset is None:
                    continue
                frame = kline_sync.fetch_minute_single(symbol, day, asset_type=asset_type,
                                                       capset=capset, raw_basis=True)
            else:
                if not custom.provider_has_dataset(name, 'minute'):
                    continue
                provider = custom.get_provider(name, use_failover=False)
                aggregation = getattr(provider, 'minute_daily_aggregation', True)
                if (aggregation is False or (isinstance(aggregation, tuple) and asset_type not in aggregation)
                        or getattr(provider, 'minute_price_basis', None) != 'raw'):
                    continue
                frame = provider.get_minute([symbol],
                    start_time=datetime.combine(day, time(9, 25), tzinfo=CN_TZ),
                    end_time=datetime.combine(day, time(15, 5), tzinfo=CN_TZ),
                    asset_type=asset_type, freq='1m')
                frame = kline_sync._enforce_minute_beijing_wallclock(frame, source=name)
            if not {'symbol', 'datetime', 'volume'}.issubset(frame.columns):
                continue
            frame = frame.filter((pl.col('symbol') == symbol)
                                 & pl.col('datetime').is_in(expected)).sort('datetime')
            # Filtering to the requested suffix also rejects missing/duplicate bars;
            # raw OHLC and volume are verified before any model sees this frame.
            if frame['datetime'].to_list() != expected:
                continue
            values = _rows(frame, symbol, 'datetime', count)
            if any(value['volume'] is None for value in values):
                continue
            return frame.select('symbol', 'datetime', 'open', 'high', 'low', 'close', 'volume',
                                *(['amount'] if 'amount' in frame.columns else []))
        except Exception:
            # A missing/invalid source is never converted into synthetic OHLC.
            continue
    raise ValueError(f'当日分钟不足，已尝试补取前一交易日{day}，但未取得所需{count}根完整原始分钟K；预测未启动。')


def _minutes(repo, capset, symbol: str, asset_type: str, count: int, now: datetime,
             chart_context: dict | None = None):
    if count > 240:
        raise ValueError('1分钟模式最多240根，请缩短历史窗口。')
    source_notes = []
    frame = (current_minute_frame(repo, capset, symbol, asset_type, now, source_notes=source_notes)
             if asset_type == 'stock' else current_minute_frame(repo, capset, symbol, asset_type, now))
    if frame.is_empty():
        raise ValueError('当日尚无已完成的真实分钟K，请在首根分钟收盘后重试。')
    current = frame
    warnings = ['剔除当前仍可能变化的一分钟；仅使用已观测分钟，不补造缺失K线。',
                '行情按需临时取用，完成后释放取数缓存；保留有限预测输入和结果供事后核对。']
    warnings.extend(source_notes)
    if frame.height < count:
        day = trading_calendar.previous_trading_day(now.date())
        if day is None:
            raise ValueError('交易日历暂不可核验，无法补取前一交易日分钟K。')
        missing = count - frame.height
        historical = (historical_minute_tail(repo, capset, symbol, asset_type, day, missing, source_notes=warnings)
                      if asset_type == 'stock' else historical_minute_tail(repo, capset, symbol, asset_type, day, missing))
        frame = pl.concat([historical, frame], how='diagonal_relaxed').sort('datetime')
        warnings.append(f'当日已完成{current.height}根，按需补取前一交易日{day}的{missing}根真实分钟K。')
    rows = _rows(frame, symbol, 'datetime', count)
    if chart_context is not None:
        chart_context['chart_context'] = _rows(current, symbol, 'datetime', min(300, current.height))
    return rows, warnings


def previous_close(repo, symbol: str, asset_type: str, session: date, *, online=False) -> dict:
    """Optional chart reference, exact prior exchange date and original basis."""
    missing = {'prev_close': None, 'prev_close_date': None}
    if repo is None:
        return missing
    prior = trading_calendar.previous_trading_day(session)
    if prior is None:
        return missing
    try:
        frame = _raw_daily(repo, symbol, asset_type, 1, prior, start_day=prior)
        if frame.is_empty() and online:
            frame = online_raw_daily(symbol, asset_type, prior, prior, strict=True)
        if not {'symbol', 'date'}.issubset(frame.columns):
            return missing
        frame = frame.filter((pl.col('symbol') == symbol) & (pl.col('date') == prior))
        if frame.height != 1:
            return missing
        rows = _rows(frame, symbol, 'date', 1)
        if rows[-1]['time'] != prior.isoformat():
            return missing
        return {'prev_close': rows[-1]['close'], 'prev_close_date': prior.isoformat()}
    except Exception:
        return missing


def enrich_chart_reference(job: dict, repo) -> dict:
    """Old saved forecasts remain immutable; enrich only their display response."""
    result = job.get('result')
    if result and result.get('period') == '1m' and 'prev_close' not in result:
        result.update(previous_close(repo, result['symbol'], result['asset_type'],
                                     date.fromisoformat(result['input_as_of'][:10])))
    return job


def load_input(repo, capset, request: dict, now: datetime) -> dict:
    asset_type = repo.resolve_asset_type(request['symbol'])
    if asset_type not in {'stock', 'etf'}:
        raise ValueError('本机预测入口目前支持A股股票和场内ETF。')
    symbol, period, count = request['symbol'], request['period'], request['lookback']
    extra = {}
    if period == 'daily':
        rows, warnings = _daily(repo, symbol, asset_type, count, now)
    else:
        rows, warnings = _minutes(repo, capset, symbol, asset_type, count, now, extra)
        extra.update(previous_close(repo, symbol, asset_type, now.date(), online=True))
    targets = future_times(rows[-1]['time'], period, request['horizon'])
    return {'symbol': symbol, 'name': request['name'], 'model': request['model'], 'period': period,
            'asset_type': asset_type, 'history': rows, 'future_times': targets,
            'samples': request['samples'], 'input_as_of': rows[-1]['time'],
            'requested_at': now.isoformat(), 'price_basis': '原始价格（未复权）', 'warnings': warnings, **extra}
