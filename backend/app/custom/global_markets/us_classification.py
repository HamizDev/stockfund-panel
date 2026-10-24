"""Bounded Nasdaq screener classification enrichment for US quote rows."""
from __future__ import annotations

import json
import time
import unicodedata

import httpx

from .catalog import MarketDataError, normalize_symbol

SOURCE = "nasdaq_screener"
URL = "https://api.nasdaq.com/api/screener/stocks"
PARAMS = {
    "tableonly": "true",
    "limit": "5000",
    "offset": "0",
    "exchange": "all",
    "download": "true",
}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_ROWS = 20_000
MAX_CLASSIFICATION_LENGTH = 120
MAX_AS_OF_LENGTH = 80
CACHE_SECONDS = 24 * 60 * 60
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
    "User-Agent": "Mozilla/5.0 stockfund-panel",
}


def _classification_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text in {"-", "--", "N/A", "NA"} or len(text) > MAX_CLASSIFICATION_LENGTH:
        return None
    if any(unicodedata.category(char) == "Cc" for char in text):
        return None
    return text


def _source_as_of(value: object) -> str | None:
    if value is None:
        return None
    if (not isinstance(value, str) or len(value) > MAX_AS_OF_LENGTH
            or any(unicodedata.category(char) == "Cc" for char in value)):
        raise MarketDataError("Nasdaq证券分类更新时间格式无效")
    return value


def _merge_classification(
    previous: dict[str, str | None], current: dict[str, str | None]
) -> dict[str, str | None] | None:
    merged = dict(previous)
    for field in ("sector", "industry"):
        left, right = merged[field], current[field]
        if left is not None and right is not None and left != right:
            return None
        if left is None:
            merged[field] = right
    return merged


def _parse_payload(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise MarketDataError("Nasdaq证券分类响应结构无效")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MarketDataError("Nasdaq证券分类响应结构无效")
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_SOURCE_ROWS:
        raise MarketDataError("Nasdaq证券分类记录数量或结构无效")

    by_symbol: dict[str, dict[str, str | None]] = {}
    conflicts: set[str] = set()
    invalid_symbols = 0
    for row in rows:
        if (not isinstance(row, dict)
                or not {"symbol", "sector", "industry"}.issubset(row)):
            raise MarketDataError("Nasdaq证券分类记录结构无效")
        raw_symbol = row.get("symbol")
        try:
            symbol = normalize_symbol("us", raw_symbol)
        except (TypeError, ValueError):
            invalid_symbols += 1
            continue

        item = {
            "sector": _classification_text(row.get("sector")),
            "industry": _classification_text(row.get("industry")),
        }
        if item["sector"] is None and item["industry"] is None:
            continue
        if symbol in conflicts:
            continue
        previous = by_symbol.get(symbol)
        if previous is None:
            by_symbol[symbol] = item
            continue
        merged = _merge_classification(previous, item)
        if merged is None:
            del by_symbol[symbol]
            conflicts.add(symbol)
        else:
            by_symbol[symbol] = merged

    if not by_symbol:
        raise MarketDataError("Nasdaq证券分类没有可用记录")

    sectors = {item["sector"] for item in by_symbol.values() if item["sector"] is not None}
    industries = {item["industry"] for item in by_symbol.values() if item["industry"] is not None}
    return {
        "source": SOURCE,
        "source_as_of": _source_as_of(data.get("asOf")),
        "fetched_at_ms": round(time.time() * 1000),
        "row_count": len(rows),
        "covered": len(by_symbol),
        "sector_count": sum(item["sector"] is not None for item in by_symbol.values()),
        "industry_count": sum(item["industry"] is not None for item in by_symbol.values()),
        "distinct_sector_count": len(sectors),
        "distinct_industry_count": len(industries),
        "conflict_count": len(conflicts),
        "invalid_symbol_count": invalid_symbols,
        "items": by_symbol,
    }


def fetch() -> dict:
    """Fetch and validate one bounded anonymous Nasdaq screener response."""
    try:
        with (
            httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers=_HEADERS) as client,
            client.stream("GET", URL, params=PARAMS) as response,
        ):
            if response.status_code != 200:
                raise MarketDataError(f"Nasdaq证券分类读取失败 (HTTP {response.status_code})")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise MarketDataError("Nasdaq证券分类响应超过读取上限")
    except MarketDataError:
        raise
    except httpx.HTTPError as error:
        raise MarketDataError("Nasdaq证券分类网络读取失败") from error

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as error:
        raise MarketDataError("Nasdaq证券分类响应不是有效 JSON") from error
    return _parse_payload(payload)


def _empty_metadata(error: str) -> dict:
    return {
        "source": SOURCE,
        "source_as_of": None,
        "fetched_at_ms": None,
        "row_count": 0,
        "covered": 0,
        "matched": 0,
        "enriched": 0,
        "sector_count": 0,
        "industry_count": 0,
        "distinct_sector_count": 0,
        "distinct_industry_count": 0,
        "conflict_count": 0,
        "error": error,
    }


def enrich(rows: list[dict]) -> dict:
    """Fill missing US sector/industry values in place; leave quote fields alone."""
    if not isinstance(rows, list):
        return _empty_metadata("美股行业分类输入无效")

    # Delayed import avoids a service -> optional enrichment -> service cycle.
    from . import service

    try:
        source = service._cached(("us-classification",), CACHE_SECONDS, fetch)
    except MarketDataError:
        return _empty_metadata("Nasdaq证券分类暂不可用")
    except Exception:
        return _empty_metadata("Nasdaq证券分类暂不可用")

    matched = 0
    enriched = 0
    items = source.get("items") if isinstance(source, dict) else None
    if not isinstance(items, dict):
        return _empty_metadata("Nasdaq证券分类数据无效")
    for row in rows:
        if not isinstance(row, dict) or row.get("market") != "us":
            continue
        try:
            symbol = normalize_symbol("us", row.get("symbol"))
        except (TypeError, ValueError):
            continue
        classification = items.get(symbol)
        if not isinstance(classification, dict):
            continue
        matched += 1
        changed = False
        industry_added = False
        for field in ("sector", "industry"):
            current = row.get(field)
            missing = current is None or (isinstance(current, str) and not current.strip())
            value = classification.get(field)
            if missing and isinstance(value, str) and value:
                row[field] = value
                changed = True
                industry_added = industry_added or field == "industry"
        if changed:
            enriched += 1
        if industry_added and (
            row.get("industry_source") is None
            or (
                isinstance(row.get("industry_source"), str)
                and not row["industry_source"].strip()
            )
        ):
            row["industry_source"] = SOURCE

    return {
        key: value for key, value in source.items() if key != "items"
    } | {"matched": matched, "enriched": enriched, "error": None}
