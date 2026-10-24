"""Small, fail-closed reader for the SZSE official A-share trading calendar."""
from __future__ import annotations

import calendar
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date
from urllib.error import URLError
from urllib.request import Request, urlopen

_URL = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
_REQUEST_TIMEOUT_SECONDS = 1.8
_DEFAULT_WAIT_SECONDS = 2.0
_SUCCESS_TTL_SECONDS = 6 * 60 * 60
_FAILURE_TTL_SECONDS = 30
_MAX_CACHE_ENTRIES = 24
_MAX_PENDING = 2
_MAX_RESPONSE_BYTES = 64 * 1024

_CACHE: OrderedDict[tuple[int, int], tuple[float, frozenset[date] | None]] = OrderedDict()
_PENDING = {}
_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="szse-calendar")


def _parse_month(payload: object, year: int, month: int) -> frozenset[date]:
    """Validate a complete official month; malformed or partial data is unknown."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("unexpected calendar response")
    rows = payload["data"]
    expected = {
        date(year, month, day)
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
    }
    seen: set[date] = set()
    open_days: set[date] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid calendar row")
        date_text = row.get("jyrq")
        flag = row.get("jybz")
        if not isinstance(date_text, str) or not isinstance(flag, str) or flag not in {"0", "1"}:
            raise ValueError("invalid calendar fields")
        try:
            day = date.fromisoformat(date_text)
        except ValueError as exc:
            raise ValueError("invalid calendar date") from exc
        if day.isoformat() != date_text or day.year != year or day.month != month or day in seen:
            raise ValueError("calendar dates are duplicated or out of month")
        seen.add(day)
        if flag == "1":
            open_days.add(day)
    if seen != expected:
        raise ValueError("calendar month is incomplete")
    if not open_days:
        raise ValueError("calendar month has no open sessions")
    return frozenset(open_days)


def _fetch_month(year: int, month: int) -> frozenset[date]:
    request = Request(
        f"{_URL}?month={year:04d}-{month:02d}",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.szse.cn/",
            "User-Agent": "Mozilla/5.0 stockfund-panel calendar reader",
        },
    )
    with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ValueError("calendar response too large")
    return _parse_month(json.loads(body), year, month)


def _fetch_and_cache(key: tuple[int, int]) -> frozenset[date] | None:
    year, month = key
    try:
        value = _fetch_month(year, month)
    except Exception:  # Network, decode, and schema errors all mean unknown.
        value = None
    with _LOCK:
        ttl = _SUCCESS_TTL_SECONDS if value is not None else _FAILURE_TTL_SECONDS
        _CACHE[key] = (time.monotonic() + ttl, value)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)
        _PENDING.pop(key, None)
    return value


def month_open_days(year: int, month: int, *, wait_seconds: float = _DEFAULT_WAIT_SECONDS) -> frozenset[date] | None:
    """Return open dates for a strictly validated month, or None when unknown.

    Requests for the same month share one bounded in-flight fetch. No calendar
    data is persisted to disk; successful and failed results have short bounded
    in-memory lifetimes.
    """
    if type(year) is not int or type(month) is not int or not 1 <= month <= 12:
        return None
    try:
        date(year, month, 1)
    except ValueError:
        return None
    key = (year, month)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            expires_at, value = cached
            if time.monotonic() < expires_at:
                _CACHE.move_to_end(key)
                return value
            _CACHE.pop(key, None)
        future = _PENDING.get(key)
        if future is None:
            if len(_PENDING) >= _MAX_PENDING:
                return None
            future = _POOL.submit(_fetch_and_cache, key)
            _PENDING[key] = future
    try:
        return future.result(timeout=max(0.0, wait_seconds))
    except (TimeoutError, URLError, ValueError, OSError):
        return None
    except Exception:
        return None


def is_trading_day(day: date, *, wait_seconds: float = _DEFAULT_WAIT_SECONDS) -> bool | None:
    """Return official open/closed status; weekends are known closed, failures unknown."""
    if not isinstance(day, date):
        return None
    if day.weekday() >= 5:
        return False
    open_days = month_open_days(day.year, day.month, wait_seconds=wait_seconds)
    return None if open_days is None else day in open_days


def previous_trading_day(day: date, *, wait_seconds: float = _DEFAULT_WAIT_SECONDS) -> date | None:
    """Find the immediately preceding official session, or None if any month is unknown."""
    if not isinstance(day, date):
        return None
    deadline = time.monotonic() + max(0.0, wait_seconds)
    year, month = day.year, day.month
    # Every valid SZSE calendar month must contain an open session. A bound also
    # prevents unexpected upstream data from turning a lookup into a long loop.
    for _ in range(12):
        remaining = max(0.0, deadline - time.monotonic())
        open_days = month_open_days(year, month, wait_seconds=remaining)
        if open_days is None:
            return None
        candidates = [candidate for candidate in open_days if candidate < day]
        if candidates:
            return max(candidates)
        if month == 1:
            year, month = year - 1, 12
        else:
            month -= 1
    return None


def clear_cache() -> None:
    """Clear in-memory calendar results (primarily for deterministic tests)."""
    with _LOCK:
        _CACHE.clear()
