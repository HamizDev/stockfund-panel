"""Read-only repair of missing session references; bounded in-memory caching."""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import suppress
from datetime import date
from datetime import time as day_time
from pathlib import Path

import polars as pl

from app.market_time import cn_today
from app.services import analysis_snapshot, minute_adjust, preferences

_CACHE: OrderedDict = OrderedDict()
_LOCK = threading.Lock()
_PENDING: dict = {}
_MAX_ENTRIES = 16
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='minute-reference')
_WAIT_SECONDS = 4


def prefetch(repo, capset, symbol: str) -> None:
    """Overlap a missing optional baseline with the minute fetch, without waiting.

    Uses the same two-worker queue and cache as resolve(); no separate request
    pool, price inference, writes or background work for already valid baselines.
    """
    # Optional warm-up must not prevent the primary minute response. Resolve
    # retains its existing bounded lookup and source/basis checks afterward.
    with suppress(Exception):
        _online_daily(repo, capset, symbol, wait=False)


def _preference_revision():
    """Non-creating file identity; atomic A->B->A edits remain distinguishable."""
    from app.config import settings

    path = Path(settings.data_dir) / 'user_data' / 'preferences.json'
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return (str(path), 'missing')
    except OSError:
        return None


def _source_context():
    revision = _preference_revision()
    provider = preferences.get_daily_data_provider()
    return (provider, revision) if revision is not None and revision == _preference_revision() else None


def _online_daily(repo, capset, symbol, *, wait=True):
    context = _source_context()
    if context is None:
        return None
    key = (str(repo.store.data_dir), context, symbol)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and time.monotonic() < hit[0]:
            _CACHE.move_to_end(key)
            return hit[1]
        pending = _PENDING.get(key)
        if pending is None:
            if len(_PENDING) >= 2:
                return None
            pending = _POOL.submit(_fetch_daily, key, repo, capset, symbol)
            _PENDING[key] = pending
    if not wait:
        return None
    try:
        return pending.result(timeout=_WAIT_SECONDS)
    except TimeoutError:
        # Quotes remain usable while this optional reference warms in the
        # background. Two total workers cap slow upstream calls across symbols.
        return None


def _fetch_daily(key, repo, capset, symbol):
    value = None
    try:
        if _source_context() != key[1]:
            return None
        frame, metadata = analysis_snapshot.load_snapshot(repo, capset, symbol, 30)
        if (_source_context() == key[1]
                and metadata.get('requested_provider', metadata.get('provider')) == key[1][0]
                and metadata.get('source') == 'online' and metadata.get('price_basis') == 'unadjusted'):
            value = frame.select('symbol', 'date', 'close')
    except Exception:
        # Provider error text can contain private configuration. Never return it.
        pass
    finally:
        with _LOCK:
            _PENDING.pop(key, None)
            if _source_context() == key[1]:
                _CACHE[key] = (time.monotonic() + (300 if value is not None else 15), value)
                _CACHE.move_to_end(key)
                while len(_CACHE) > _MAX_ENTRIES:
                    _CACHE.popitem(last=False)
    return value


def resolve(repo, capset, symbol: str, trade_date: date, asset_type: str,
            minute: pl.DataFrame, local_close: float | None) -> dict:
    missing = {'prev_close': None, 'prev_close_source': 'none',
               'prev_close_warning': '缺少所选交易日的有效昨收，暂不显示涨跌幅基准。'}  # noqa: RUF001
    if minute.is_empty():
        return missing
    closing = minute.filter(pl.col('datetime').dt.time() == day_time(15, 0))
    if local_close is not None:
        if closing.is_empty() and trade_date == cn_today():
            return {'prev_close': local_close, 'prev_close_source': 'local_daily'}
        if not closing.is_empty():
            try:
                local_session = repo.get_daily_asset(asset_type, symbol, trade_date, trade_date,
                                                     columns=['date', 'close'])
                local_session = local_session.filter(pl.col('date') == trade_date)
                if not local_session.is_empty() and math.isclose(
                    float(closing['close'][-1]), float(local_session['close'][-1]),
                    rel_tol=1e-8, abs_tol=1e-8,
                ):
                    return {'prev_close': local_close, 'prev_close_source': 'local_daily'}
            except Exception:
                pass
    frame = _online_daily(repo, capset, symbol)
    if frame is None or frame.is_empty():
        return missing
    session = frame.filter(pl.col('date') == trade_date)
    before = frame.filter(pl.col('date') < trade_date).sort('date')
    if session.is_empty() or before.is_empty():
        return missing

    # Raw minute partitions are projected by the shared factor layer on reads.
    # Keep the reference in that very same basis. Legacy stock-sdk reads are raw.
    provider = preferences.get_minute_data_provider()
    if minute_adjust.minute_basis_is_raw(repo.store.data_dir) or provider != 'stocksdk':
        projected = frame.with_columns(pl.col('date').cast(pl.Datetime).alias('datetime'))
        projected = minute_adjust.apply_minute_adjustment(projected, repo.store.data_dir, asset_type)
        session = projected.filter(pl.col('date') == trade_date)
        before = projected.filter(pl.col('date') < trade_date).sort('date')

    # Historical minute and daily closes must agree before borrowing a baseline.
    # Do not infer a conversion ratio from prices or substitute the first minute.
    if not closing.is_empty():
        if not math.isclose(float(closing['close'][-1]), float(session['close'][-1]), rel_tol=1e-8, abs_tol=1e-8):
            return missing
    elif trade_date != cn_today() or provider != 'stocksdk':
        return missing
    value = float(before['close'][-1])
    if not math.isfinite(value) or value <= 0:
        return missing
    return {'prev_close': value, 'prev_close_source': 'online_daily',
            'prev_close_date': before['date'][-1].isoformat()}
