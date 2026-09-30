"""Binance public market data only. No account endpoint, key, or trade call."""
from __future__ import annotations

import threading
import time
from decimal import Decimal, InvalidOperation

import httpx

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
MARKETS = ("spot", "usdm")
_BASE = {"spot": "https://data-api.binance.vision/api/v3", "usdm": "https://fapi.binance.com/fapi/v1"}
_RULES: dict[tuple[str, str], tuple[float, dict]] = {}
_LOCK = threading.RLock()


def _positive(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("行情数值无效") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("行情数值无效")
    return result


def _get(market: str, path: str, symbol: str) -> dict:
    with httpx.Client(timeout=6.0, follow_redirects=False) as client:
        response = client.get(f"{_BASE[market]}/{path}", params={"symbol": symbol})
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ValueError("行情响应格式无效")
    return data


def _rules(market: str, symbol: str) -> dict:
    key = (market, symbol)
    with _LOCK:
        cached = _RULES.get(key)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
    data = _get(market, "exchangeInfo", symbol)
    info = next((item for item in data.get("symbols", []) if item.get("symbol") == symbol), None)
    if not info or info.get("status") != "TRADING":
        raise ValueError("交易对目前不可模拟")
    filters = {item.get("filterType"): item for item in info.get("filters", [])}
    lot = filters.get("MARKET_LOT_SIZE")
    # Some spot pairs publish zero MARKET_LOT_SIZE bounds. In that case use
    # LOT_SIZE as a conservative paper-order grid, never a zero step.
    try:
        _positive(lot.get("stepSize"))
        _positive(lot.get("minQty"))
    except (AttributeError, ValueError):
        lot = filters.get("LOT_SIZE")
    if not lot:
        raise ValueError("缺少数量精度规则")
    minimum = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    rules = {
        "step_size": str(_positive(lot["stepSize"])),
        "min_qty": str(_positive(lot["minQty"])),
        "min_notional": str(minimum.get("minNotional") or minimum.get("notional") or "0"),
    }
    with _LOCK:
        _RULES[key] = (time.monotonic(), rules)
    return rules


def quote(market: str, symbol: str) -> dict:
    if market not in MARKETS or symbol not in SYMBOLS:
        raise ValueError("不支持的市场或交易对")
    book = _get(market, "ticker/bookTicker", symbol)
    if book.get("symbol") != symbol:
        raise ValueError("行情交易对不匹配")
    bid = _positive(book.get("bidPrice"))
    ask = _positive(book.get("askPrice"))
    if bid > ask:
        raise ValueError("买卖价异常")
    mark = None
    funding = None
    next_funding = None
    if market == "usdm":
        premium = _get(market, "premiumIndex", symbol)
        if premium.get("symbol") != symbol:
            raise ValueError("标记价格交易对不匹配")
        mark = str(_positive(premium.get("markPrice")))
        funding = premium.get("lastFundingRate")
        next_funding = premium.get("nextFundingTime")
    return {
        "market": market, "symbol": symbol, "bid": str(bid), "ask": str(ask),
        "mark": mark, "last_funding_rate": funding, "next_funding_time": next_funding,
        "asof_ms": int(time.time() * 1000), **_rules(market, symbol),
    }
