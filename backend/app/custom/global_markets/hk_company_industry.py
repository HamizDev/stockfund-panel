"""On-demand, bounded Eastmoney F10 company-industry lookup for HK shares."""
from __future__ import annotations

import copy
import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import Future

import httpx

from .catalog import MarketDataError, normalize_symbol

SOURCE = "eastmoney_hk_f10"
SYSTEM = "eastmoney_belong_industry"
URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
PARAMS = {
    "reportName": "RPT_HKF10_INFO_ORGPROFILE",
    "columns": "SECUCODE,SECURITY_CODE,ORG_NAME,BELONG_INDUSTRY",
    "quoteColumns": "",
    "pageNumber": "1",
    "pageSize": "1",
    "sortTypes": "",
    "sortColumns": "",
    "source": "F10",
    "client": "PC",
}
MAX_RESPONSE_BYTES = 64 * 1024
MAX_INDUSTRY_LENGTH = 120
MAX_COMPANY_NAME_LENGTH = 256
MAX_SECONDS = 10
CACHE_SECONDS = 24 * 60 * 60
FAILURE_SECONDS = 15
MAX_CACHE = 64
MAX_PENDING = 4
TIMEOUT = httpx.Timeout(6.0, connect=4.0)
_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 stockfund-panel"}
_LOCK = threading.RLock()
_CACHE: OrderedDict[str, tuple[float, dict | None]] = OrderedDict()
_PENDING: dict[str, Future] = {}
_FAILURES: OrderedDict[str, float] = OrderedDict()


def _clean_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (not text or len(text) > limit
            or any(unicodedata.category(char) == "Cc" for char in text)):
        return None
    return text


def _source_code(value: object, expected: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return normalize_symbol("hk", value) == expected
    except (TypeError, ValueError):
        return False


def _security_code(value: object, expected: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return False
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,5}", text):
        return False
    return text.zfill(5) == expected.removesuffix(".HK")


def parse_payload(payload: object, symbol: str) -> dict | None:
    """Validate one exact F10 row. Empty or unclassified results stay absent."""
    expected = normalize_symbol("hk", symbol)
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise MarketDataError("Eastmoney港股公司资料响应结构无效")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise MarketDataError("Eastmoney港股公司资料响应结构无效")
    rows = result.get("data")
    if not isinstance(rows, list) or len(rows) > 1:
        raise MarketDataError("Eastmoney港股公司资料记录数量或结构无效")
    if not rows:
        return None
    row = rows[0]
    if not isinstance(row, dict):
        raise MarketDataError("Eastmoney港股公司资料记录结构无效")
    if (not _source_code(row.get("SECUCODE"), expected)
            or not _security_code(row.get("SECURITY_CODE"), expected)):
        raise MarketDataError("Eastmoney港股公司资料证券代码不匹配")
    if _clean_text(row.get("ORG_NAME"), limit=MAX_COMPANY_NAME_LENGTH) is None:
        raise MarketDataError("Eastmoney港股公司资料公司名称无效")
    industry = _clean_text(row.get("BELONG_INDUSTRY"), limit=MAX_INDUSTRY_LENGTH)
    if industry is None or industry in {"-", "--", "N/A", "NA"}:
        return None
    return {
        "source": SOURCE,
        "system": SYSTEM,
        "industry": industry,
        "fetched_at_ms": round(time.time() * 1000),
    }


def fetch(symbol: str) -> dict | None:
    """Read a single anonymous F10 row with strict time and byte bounds."""
    symbol = normalize_symbol("hk", symbol)
    params = PARAMS | {"filter": f'(SECUCODE="{symbol}")'}
    started = time.monotonic()
    try:
        with (
            httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers=_HEADERS) as client,
            client.stream("GET", URL, params=params) as response,
        ):
            if response.status_code != 200:
                raise MarketDataError(f"Eastmoney港股公司资料读取失败 (HTTP {response.status_code})")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise MarketDataError("Eastmoney港股公司资料响应超过读取上限")
                if time.monotonic() - started > MAX_SECONDS:
                    raise MarketDataError("Eastmoney港股公司资料读取超时")
    except MarketDataError:
        raise
    except httpx.HTTPError as error:
        raise MarketDataError("Eastmoney港股公司资料网络读取失败") from error
    if time.monotonic() - started > MAX_SECONDS:
        raise MarketDataError("Eastmoney港股公司资料读取超时")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as error:
        raise MarketDataError("Eastmoney港股公司资料响应不是有效 JSON") from error
    return parse_payload(payload, symbol)


def cached(symbol: str) -> tuple[bool, dict | None]:
    """Return a fresh optional result without starting network work."""
    symbol = normalize_symbol("hk", symbol)
    with _LOCK:
        return _cached_locked(symbol)


def _cached_locked(symbol: str) -> tuple[bool, dict | None]:
    row = _CACHE.get(symbol)
    if row is not None:
        if time.monotonic() - row[0] < CACHE_SECONDS:
            _CACHE.move_to_end(symbol)
            return True, copy.deepcopy(row[1])
        _CACHE.pop(symbol, None)
    failure = _FAILURES.get(symbol)
    if failure is not None and time.monotonic() - failure < FAILURE_SECONDS:
        _FAILURES.move_to_end(symbol)
        return True, None
    return False, None


def _remember(symbol: str, value: dict | None) -> None:
    with _LOCK:
        _CACHE[symbol] = (time.monotonic(), copy.deepcopy(value))
        _CACHE.move_to_end(symbol)
        while len(_CACHE) > MAX_CACHE:
            _CACHE.popitem(last=False)
        _FAILURES.pop(symbol, None)


def get(symbol: str) -> dict | None:
    """Use a separate bounded TTL/singleflight cache; source failures are optional."""
    symbol = normalize_symbol("hk", symbol)
    with _LOCK:
        hit, value = _cached_locked(symbol)
        if hit:
            return value
        pending = _PENDING.get(symbol)
        owner = pending is None
        if owner:
            if len(_PENDING) >= MAX_PENDING:
                return None
            pending = Future()
            _PENDING[symbol] = pending
    if not owner:
        try:
            return copy.deepcopy(pending.result(timeout=MAX_SECONDS + 1))
        except Exception:
            return None
    try:
        value = fetch(symbol)
        _remember(symbol, value)
    except Exception:
        value = None
        with _LOCK:
            _FAILURES[symbol] = time.monotonic()
            _FAILURES.move_to_end(symbol)
            while len(_FAILURES) > MAX_CACHE:
                _FAILURES.popitem(last=False)
    finally:
        with _LOCK:
            _PENDING.pop(symbol, None)
        pending.set_result(copy.deepcopy(value))
    return value
