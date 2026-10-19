"""Bounded, in-memory fallback search over Nasdaq Trader's public symbol files."""
from __future__ import annotations

import csv
import io
import re
import threading
import time
import unicodedata

import httpx

from .catalog import MarketDataError, normalize_symbol

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_ROWS = 30_000
MAX_RESULTS = 20
CACHE_SECONDS = 60 * 60
FAILURE_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 4

_NASDAQ_HEADER = (
    "Symbol", "Security Name", "Market Category", "Test Issue", "Financial Status",
    "Round Lot Size", "ETF", "NextShares",
)
_OTHER_HEADER = (
    "ACT Symbol", "Security Name", "Exchange", "CQS Symbol", "ETF",
    "Round Lot Size", "Test Issue", "NASDAQ Symbol",
)
_EXCHANGES = {"A", "N", "P", "V", "Z"}
_FINANCIAL_STATUSES = {"D", "E", "G", "H", "J", "K", "N", "Q"}
_LOCK = threading.RLock()
_CACHE_ROWS: tuple[tuple[str, str, str], ...] | None = None
_CACHE_EXPIRES = 0.0
_FAILURE_UNTIL = 0.0


def _trailer_is_valid(value: str) -> bool:
    return bool(re.fullmatch(r"File Creation Time:\s+\d{10}:\d{2}", value.strip()))


def _normalize_ticker(value: str) -> str | None:
    try:
        return normalize_symbol("us", value)
    except ValueError:
        return None


def _parse_file(text: str, header: tuple[str, ...], *, nasdaq_listed: bool) -> list[tuple[str, str, str]]:
    lines = text.lstrip("\ufeff").splitlines()
    if len(lines) < 102 or tuple(lines[0].split("|")) != header:
        raise ValueError("证券目录表头或行数无效")
    trailer = lines[-1].split("|", 1)[0]
    if not _trailer_is_valid(trailer):
        raise ValueError("证券目录缺少有效更新时间")

    records = csv.DictReader(io.StringIO("\n".join(lines[:-1])), delimiter="|")
    rows: list[tuple[str, str, str]] = []
    for row in records:
        if None in row.values() or row.get("Test Issue") != "N":
            continue
        if nasdaq_listed:
            if row.get("Financial Status") not in _FINANCIAL_STATUSES:
                continue
            raw_symbol = row.get("Symbol", "")
        else:
            if row.get("Exchange") not in _EXCHANGES:
                continue
            raw_symbol = row.get("NASDAQ Symbol", "")
        if row.get("ETF") not in {"Y", "N"}:
            continue
        symbol = _normalize_ticker(raw_symbol)
        if not symbol and not nasdaq_listed:
            # CQS symbols use a dot-suffix form for some listed issues; keep
            # them only when the application's US ticker validator accepts it.
            symbol = _normalize_ticker(row.get("CQS Symbol", ""))
        name = row.get("Security Name", "").strip()
        if not symbol or not name:
            continue
        rows.append((symbol, name[:255], "etf" if row["ETF"] == "Y" else "stock"))
        if len(rows) > MAX_ROWS:
            raise ValueError("证券目录超过内存记录上限")
    if len(rows) < 100:
        raise ValueError("证券目录有效记录不足")
    return rows


def _read_text(client: httpx.Client, url: str) -> str:
    with client.stream("GET", url, headers={"Accept": "text/plain"}) as response:
        if response.status_code != 200:
            raise ValueError("证券目录请求失败")
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError("证券目录响应超过大小上限")
    return content.decode("utf-8-sig")


def _download_rows() -> tuple[tuple[str, str, str], ...]:
    # Exactly two bounded HTTPS requests per refresh. Both files must validate;
    # a partial directory is not cached or shown as a complete fallback.
    with httpx.Client(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=REQUEST_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as client:
        nasdaq_text = _read_text(client, NASDAQ_LISTED_URL)
        other_text = _read_text(client, OTHER_LISTED_URL)
    nasdaq_rows = _parse_file(nasdaq_text, _NASDAQ_HEADER, nasdaq_listed=True)
    other_rows = _parse_file(other_text, _OTHER_HEADER, nasdaq_listed=False)
    by_symbol = {symbol: (symbol, name, kind) for symbol, name, kind in nasdaq_rows}
    for symbol, name, kind in other_rows:
        by_symbol.setdefault(symbol, (symbol, name, kind))
        if len(by_symbol) > MAX_ROWS:
            raise ValueError("证券目录合并后超过内存记录上限")
    return tuple(by_symbol.values())


def _rows() -> tuple[tuple[str, str, str], ...]:
    global _CACHE_ROWS, _CACHE_EXPIRES, _FAILURE_UNTIL
    now = time.monotonic()
    with _LOCK:
        if _CACHE_ROWS is not None and now < _CACHE_EXPIRES:
            return _CACHE_ROWS
        if now < _FAILURE_UNTIL:
            raise MarketDataError("Nasdaq Trader证券目录暂不可用")
        try:
            rows = _download_rows()
        except (httpx.HTTPError, UnicodeError, ValueError, csv.Error) as error:
            _CACHE_ROWS = None
            _FAILURE_UNTIL = time.monotonic() + FAILURE_SECONDS
            raise MarketDataError("Nasdaq Trader证券目录暂不可用") from error
        _CACHE_ROWS = rows[:MAX_ROWS]
        _CACHE_EXPIRES = time.monotonic() + CACHE_SECONDS
        _FAILURE_UNTIL = 0.0
        return _CACHE_ROWS


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if char.isalnum())


def search(query: str) -> list[dict]:
    """Search the public listing directory after the primary name search misses."""
    query = query.strip()
    key = _key(query)
    if not key:
        return []
    rows = _rows()
    matches: list[tuple[int, str, str, str]] = []
    for symbol, name, kind in rows:
        symbol_key, name_key = _key(symbol), _key(name)
        if key == symbol_key:
            score = 0
        elif symbol_key.startswith(key):
            score = 1
        elif name_key.startswith(key):
            score = 2
        elif len(key) >= 2 and (key in symbol_key or key in name_key):
            score = 3
        else:
            continue
        matches.append((score, symbol, name, kind))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [
        {"symbol": symbol, "name": name, "market": "us", "kind": kind}
        for _, symbol, name, kind in matches[:MAX_RESULTS]
    ]
