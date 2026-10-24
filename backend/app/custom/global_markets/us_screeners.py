
"""Bounded Yahoo predefined-screen union for fallback US research data.

This is a capped screener pool, not a full US market universe. It makes public
GET requests without cookies or API credentials and never paginates screens.
"""
from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from .catalog import MarketDataError, normalize_symbol

SCREEN_IDS = ("day_gainers", "day_losers", "most_actives")
MAX_PER_SCREEN = 250
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT = httpx.Timeout(8.0, connect=4.0)
URL = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
_HEADERS = {"User-Agent": "Mozilla/5.0 stockfund-panel"}
_US_ZONE = ZoneInfo("America/New_York")


def _number(value: object, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if (positive and value <= 0) or (nonnegative and value < 0):
        return None
    return float(value)


def _read_screen(client: httpx.Client, screen_id: str) -> tuple[int, list[dict]]:
    if screen_id not in SCREEN_IDS:
        raise ValueError("Unsupported Yahoo predefined screen")
    params = {
        "formatted": "false",
        "lang": "en-US",
        "region": "US",
        "scrIds": screen_id,
        "count": MAX_PER_SCREEN,
        "corsDomain": "finance.yahoo.com",
    }
    try:
        with client.stream("GET", URL, params=params) as response:
            if response.status_code != 200:
                raise MarketDataError("Yahoo 预设筛选读取失败或受到限流")
            body = bytearray()
            for block in response.iter_bytes():
                body.extend(block)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise MarketDataError("Yahoo 预设筛选响应超过读取上限")
    except httpx.HTTPError as error:
        raise MarketDataError("Yahoo 预设筛选网络读取失败") from error

    try:
        payload = json.loads(body)
        finance = payload.get("finance") if isinstance(payload, dict) else None
        if not isinstance(finance, dict) or finance.get("error"):
            raise MarketDataError("Yahoo 预设筛选响应结构无效")
        results = finance.get("result")
        if not isinstance(results, list) or len(results) != 1:
            raise MarketDataError("Yahoo 预设筛选响应结构无效")
        result = results[0]
        if not isinstance(result, dict):
            raise MarketDataError("Yahoo 预设筛选响应结构无效")
        quotes = result.get("quotes")
        reported = result.get("count")
        if (not isinstance(quotes, list) or len(quotes) > MAX_PER_SCREEN
                or isinstance(reported, bool) or not isinstance(reported, int)
                or reported < len(quotes) or reported < 0):
            raise MarketDataError("Yahoo 预设筛选记录无效或超过数量上限")
        if any(not isinstance(row, dict) for row in quotes):
            raise MarketDataError("Yahoo 预设筛选记录结构无效")
        return reported, quotes
    except (TypeError, ValueError, RecursionError) as error:
        raise MarketDataError("Yahoo 预设筛选响应不是有效 JSON") from error


def _normalize_quote(row: dict, screen_id: str, now: float) -> tuple[dict | None, str | None]:
    try:
        symbol = normalize_symbol("us", row.get("symbol"))
    except (TypeError, ValueError):
        return None, "invalid"

    currency = row.get("currency")
    if not isinstance(currency, str) or currency.strip().upper() != "USD":
        return None, "non_usd"

    stamp = row.get("regularMarketTime")
    if (isinstance(stamp, bool) or not isinstance(stamp, int)
            or stamp <= 0 or stamp > now + 120):
        return None, "invalid"
    price = _number(row.get("regularMarketPrice"), positive=True)
    change_percent = _number(row.get("regularMarketChangePercent"))
    if price is None or change_percent is None:
        return None, "invalid"

    raw_volume = row.get("regularMarketVolume")
    volume = _number(raw_volume, nonnegative=True)
    name = row.get("shortName") or row.get("longName")
    if not isinstance(name, str) or not name.strip():
        name = symbol
    name = name.strip()[:160]
    quote_date = datetime.fromtimestamp(stamp, UTC).astimezone(_US_ZONE).date().isoformat()

    return {
        "market": "us",
        "symbol": symbol,
        "name": name,
        "kind": "etf" if row.get("quoteType") == "ETF" else "security",
        "currency": "USD",
        "price": price,
        "change_pct": change_percent / 100,
        "volume": volume,
        "market_cap": _number(row.get("marketCap"), positive=True),
        # Yahoo's screener payload has no traded amount, turnover, or industry.
        "amount": None,
        "turnover_rate": None,
        "industry": None,
        "quote_at_ms": stamp * 1000,
        "quote_date": quote_date,
        "fetched_at_ms": None,
        "source": "yahoo_screener",
        "scope": "us_screener_pool",
        "status": "ok",
        "stale": False,
        "error": None,
        # Keep source-native values for callers that need the original units.
        "regularMarketTime": stamp,
        "regularMarketPrice": price,
        "regularMarketChangePercent": change_percent,
        "regularMarketVolume": volume,
        "_screen_ids": {screen_id},
    }, None


def _collect(client: httpx.Client, *, now: float | None = None) -> dict:
    checked_at = time.time() if now is None else now
    union: dict[str, dict] = {}
    per_screen = []
    invalid_rows = 0
    currency_filtered = 0
    valid_occurrences = 0

    for screen_id in SCREEN_IDS:
        reported, source_rows = _read_screen(client, screen_id)
        screen_invalid = 0
        screen_currency_filtered = 0
        screen_valid = 0
        for source_row in source_rows:
            row, reason = _normalize_quote(source_row, screen_id, checked_at)
            if row is None:
                if reason == "non_usd":
                    currency_filtered += 1
                    screen_currency_filtered += 1
                else:
                    invalid_rows += 1
                    screen_invalid += 1
                continue

            valid_occurrences += 1
            screen_valid += 1
            prior = union.get(row["symbol"])
            if prior is None:
                union[row["symbol"]] = row
            else:
                screens = prior["_screen_ids"] | row["_screen_ids"]
                # Retain the most recent source quote while preserving every
                # screen in which the symbol appeared.
                if row["quote_at_ms"] > prior["quote_at_ms"]:
                    row["_screen_ids"] = screens
                    union[row["symbol"]] = row
                else:
                    prior["_screen_ids"] = screens

        per_screen.append({
            "screen_id": screen_id,
            "source_count": reported,
            "received": len(source_rows),
            "accepted_usd": screen_valid,
            "filtered_non_usd": screen_currency_filtered,
            "invalid_rows": screen_invalid,
        })

    if not union:
        raise MarketDataError("Yahoo 预设筛选没有可用的 USD 行情")

    fetched_at_ms = round(time.time() * 1000)
    for row in union.values():
        row["fetched_at_ms"] = fetched_at_ms
        memberships = row.pop("_screen_ids")
        row["screen_ids"] = [screen for screen in SCREEN_IDS if screen in memberships]

    quotes = sorted(union.values(), key=lambda row: row["symbol"])
    pool = {
        "market": "us",
        "source": "yahoo_screener",
        "scope": "us_screener_pool",
        "currency": "USD",
        "universe_label": "Yahoo predefined-screen union (capped sample, not the full US market)",
        "screen_ids": list(SCREEN_IDS),
        "limit_per_screen": MAX_PER_SCREEN,
        "max_candidates": MAX_PER_SCREEN * len(SCREEN_IDS),
        "all_requested_screens_received": True,
        "screen_results": per_screen,
        "received_rows": sum(item["received"] for item in per_screen),
        "accepted_occurrences": valid_occurrences,
        "unique_symbols": len(quotes),
        "duplicates_removed": valid_occurrences - len(quotes),
        "filtered_non_usd": currency_filtered,
        "invalid_rows": invalid_rows,
        "quote_dates": sorted({row["quote_date"] for row in quotes}),
        "fetched_at_ms": fetched_at_ms,
    }
    return {"quotes": quotes, "pool": pool}


def fetch_us_screener_pool() -> dict:
    """Read and union the three capped Yahoo USD screens or fail as one pool."""
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers=_HEADERS) as client:
            return _collect(client)
    except MarketDataError:
        raise
    except httpx.HTTPError as error:
        raise MarketDataError("Yahoo 预设筛选网络读取失败") from error
