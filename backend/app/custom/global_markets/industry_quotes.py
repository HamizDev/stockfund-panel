# ruff: noqa: RUF001 -- localized Chinese labels and punctuation.
"""Bounded, read-only Tencent quotes for a fixed set of known US tickers."""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_URL = "https://qt.gtimg.cn/q="
MAX_BATCH_SIZE = 60
MAX_SYMBOLS = 100
MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_TIMEOUT_SECONDS = 5
TTL_SECONDS = 300
RETRY_DELAY_SECONDS = 30

DISCLOSURE = (
    "来源报价时间未声明时区，延迟未知；数据不可称为实时或暗盘行情。"
)
REFRESH_ERROR = "行情刷新失败，保留上一份可用快照。"
_REQUEST_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_RECORD_RE = re.compile(
    r'^v_us([A-Z][A-Z0-9.-]{0,9})="([^"\r\n]*)";?$', re.MULTILINE
)
_WALL_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class QuoteFetchError(RuntimeError):
    """A bounded quote request or response could not be used safely."""


@dataclass(frozen=True)
class _Snapshot:
    quotes: dict[str, dict[str, Any]]
    fetched_at_ms: int
    completed_monotonic: float


_lock = threading.Lock()
_cache: dict[tuple[str, ...], _Snapshot] = {}
_last_error: dict[tuple[str, ...], str] = {}
_retry_after: dict[tuple[str, ...], float] = {}
_active_key: tuple[str, ...] | None = None
_MAX_CACHED_UNIVERSES = 8


def _normalize_symbols(symbols: set[str]) -> tuple[str, ...]:
    if not isinstance(symbols, (set, frozenset)):
        raise ValueError("symbols must be a set of US tickers")
    if not symbols or len(symbols) > MAX_SYMBOLS:
        raise ValueError(f"symbols must contain 1 to {MAX_SYMBOLS} tickers")
    if any(not isinstance(symbol, str) or not _REQUEST_SYMBOL_RE.fullmatch(symbol)
           for symbol in symbols):
        raise ValueError("symbols must contain canonical uppercase US tickers")
    return tuple(sorted(symbols))


def _source_symbol_matches(requested: str, returned: str) -> bool:
    # The selected US code is followed by a provider venue suffix such as .OQ
    # or .N. Require the whole pre-suffix symbol to match the request.
    return bool(re.fullmatch(re.escape(requested) + r"(?:\.[A-Z]{1,4})?", returned))


def _finite_number(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_quote(fields: list[str], requested: str, today: date) -> dict[str, Any] | None:
    if len(fields) < 36 or fields[0] != "200":
        return None

    name = fields[1].strip()
    returned_symbol = fields[2].strip()
    if not name or not _source_symbol_matches(requested, returned_symbol):
        return None
    price = _finite_number(fields[3])
    if price is None or price <= 0 or fields[35].strip() != "USD":
        return None

    source_wall_time = fields[30].strip()
    if not _WALL_TIME_RE.fullmatch(source_wall_time):
        return None
    try:
        parsed_wall_time = datetime.strptime(source_wall_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    quote_date = parsed_wall_time.date()
    if quote_date > today:
        return None

    quote: dict[str, Any] = {
        "symbol": requested,
        "provider_symbol": returned_symbol,
        "name": name,
        "price": price,
        "currency": "USD",
        "source_wall_time": source_wall_time,
        "quote_date": quote_date.isoformat(),
        "basis": "source_reported_date",
        "disclosure": DISCLOSURE,
    }
    change_pct = _finite_number(fields[32])
    if change_pct is not None:
        quote["change_pct"] = change_pct / 100
    return quote


def _parse_response(
    body: str, requested_symbols: tuple[str, ...], *, today: date | None = None
) -> dict[str, dict[str, Any]]:
    today = today or datetime.now(UTC).date()
    requested = set(requested_symbols)
    seen: set[str] = set()
    matched_record = False
    quotes: dict[str, dict[str, Any]] = {}

    for match in _RECORD_RE.finditer(body):
        ticker, payload = match.groups()
        if ticker not in requested:
            continue
        matched_record = True
        if ticker in seen:
            raise QuoteFetchError("duplicate requested ticker record")
        seen.add(ticker)
        quote_row = _parse_quote(payload.split("~"), ticker, today)
        if quote_row is not None:
            quotes[ticker] = quote_row

    if not matched_record:
        raise QuoteFetchError("response contains no requested ticker records")
    return quotes


def _fetch_batch(tickers: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    if not tickers or len(tickers) > MAX_BATCH_SIZE:
        raise QuoteFetchError("invalid Tencent batch size")
    symbols = ",".join(f"us{ticker}" for ticker in tickers)
    url = BASE_URL + quote(symbols, safe=",")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise QuoteFetchError("Tencent returned a non-200 status")
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > MAX_RESPONSE_BYTES:
                raise QuoteFetchError("Tencent response exceeded the size limit")
            raw_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw_body) > MAX_RESPONSE_BYTES:
                raise QuoteFetchError("Tencent response exceeded the size limit")
            charset = response.headers.get_content_charset() or "gbk"
            body = raw_body.decode(charset, errors="strict")
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, QuoteFetchError):
            raise
        raise QuoteFetchError("Tencent quote request failed") from None
    quotes = _parse_response(body, tickers)
    if not quotes:
        raise QuoteFetchError("Tencent batch contained no valid quote rows")
    return quotes


def _fetch_complete_snapshot(symbols: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    quotes: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(symbols), MAX_BATCH_SIZE):
        batch = symbols[offset:offset + MAX_BATCH_SIZE]
        batch_quotes = _fetch_batch(batch)
        if any(ticker not in batch for ticker in batch_quotes):
            raise QuoteFetchError("Tencent returned a ticker outside the requested batch")
        quotes.update(batch_quotes)
    if not quotes:
        raise QuoteFetchError("Tencent returned no valid quote rows")
    return quotes


def _refresh_snapshot(key: tuple[str, ...]) -> None:
    global _active_key
    try:
        quotes = _fetch_complete_snapshot(key)
        snapshot = _Snapshot(
            quotes=quotes,
            fetched_at_ms=int(time.time() * 1000),
            completed_monotonic=time.monotonic(),
        )
        with _lock:
            _cache[key] = snapshot
            _last_error.pop(key, None)
            _retry_after.pop(key, None)
            if len(_cache) > _MAX_CACHED_UNIVERSES:
                oldest_key = min(_cache, key=lambda item: _cache[item].completed_monotonic)
                if oldest_key != key:
                    _cache.pop(oldest_key, None)
    except Exception:
        with _lock:
            _last_error[key] = REFRESH_ERROR
            _retry_after[key] = time.monotonic() + RETRY_DELAY_SECONDS
    finally:
        with _lock:
            if _active_key == key:
                _active_key = None


def _start_refresh(key: tuple[str, ...]) -> None:
    threading.Thread(target=_refresh_snapshot, args=(key,), daemon=True).start()


def _copy_quotes(quotes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {symbol: dict(quote_row) for symbol, quote_row in quotes.items()}


def _state_data(
    state: str,
    *,
    snapshot: _Snapshot | None,
    loading: bool,
    stale: bool,
    error: str | None,
) -> dict[str, Any]:
    return {
        "state": state,
        "data": {"fetched_at_ms": snapshot.fetched_at_ms} if snapshot else None,
        "loading": loading,
        "stale": stale,
        "error": error,
        "disclosure": DISCLOSURE,
    }


def get(symbols: set[str]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return cached quote state immediately and refresh it in one background task.

    A refresh may use up to two sequential GETs (60 + remainder for a 70-symbol
    set). Results replace the cache only after all required GETs complete; a
    failure keeps the previous complete snapshot intact.
    """
    global _active_key
    key = _normalize_symbols(symbols)
    now = time.monotonic()

    with _lock:
        snapshot = _cache.get(key)
        age = now - snapshot.completed_monotonic if snapshot else None
        fresh = snapshot is not None and age is not None and age < TTL_SECONDS
        if fresh:
            return (
                _state_data(
                    "data", snapshot=snapshot, loading=False, stale=False, error=None
                ),
                _copy_quotes(snapshot.quotes),
            )

        retry_at = _retry_after.get(key, 0.0)
        if _active_key is None and now >= retry_at:
            _active_key = key
            _last_error.pop(key, None)
            try:
                _start_refresh(key)
            except Exception:
                _active_key = None
                _last_error[key] = REFRESH_ERROR
                _retry_after[key] = now + RETRY_DELAY_SECONDS

        error = _last_error.get(key)
        if snapshot is not None:
            return (
                _state_data(
                    "stale",
                    snapshot=snapshot,
                    loading=_active_key == key,
                    stale=True,
                    error=error,
                ),
                _copy_quotes(snapshot.quotes),
            )
        if error and _active_key != key:
            return (
                _state_data(
                    "error", snapshot=None, loading=False, stale=False, error=error
                ),
                {},
            )
        return (
            _state_data(
                "loading",
                snapshot=None,
                loading=_active_key == key,
                stale=False,
                error=error,
            ),
            {},
        )
