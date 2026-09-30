"""Binance public market data only. No account endpoint, key, or trade call."""
from __future__ import annotations

import threading
import time
from decimal import Decimal, InvalidOperation

import httpx

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
MARKETS = ("spot", "usdm")
KLINE_INTERVALS = {"1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000}
_MAX_FUNDING_SPAN_MS = 90 * 24 * 60 * 60 * 1000
_BASE = {"spot": "https://data-api.binance.vision/api/v3", "usdm": "https://fapi.binance.com/fapi/v1"}
_RULES: dict[tuple[str, str], tuple[float, dict]] = {}
_LOCK = threading.RLock()


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("行情数值无效") from exc
    if not result.is_finite():
        raise ValueError("行情数值无效")
    return result


def _positive(value: object) -> Decimal:
    result = _decimal(value)
    if result <= 0:
        raise ValueError("行情数值无效")
    return result


def _nonnegative(value: object) -> Decimal:
    result = _decimal(value)
    if result < 0:
        raise ValueError("行情数值无效")
    return result


def _request(market: str, path: str, params: dict) -> object:
    if market not in MARKETS:
        raise ValueError("不支持的市场")
    with httpx.Client(timeout=6.0, follow_redirects=False) as client:
        response = client.get(f"{_BASE[market]}/{path}", params=params)
        response.raise_for_status()
        return response.json()


def _get(market: str, path: str, symbol: str) -> dict:
    data = _request(market, path, {"symbol": symbol})
    if not isinstance(data, dict):
        raise ValueError("行情响应格式无效")
    return data


def _validate_symbol(symbol: str) -> None:
    if symbol not in SYMBOLS:
        raise ValueError("不支持的交易对")


def _timestamp(value: object, name: str, *, allow_zero: bool = True) -> int:
    if type(value) is not int or value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"{name}格式无效")
    return value


def _server_time_ms(market: str) -> int:
    data = _request(market, "time", {})
    if not isinstance(data, dict):
        raise ValueError("服务器时间格式无效")
    return _timestamp(data.get("serverTime"), "服务器时间", allow_zero=False)


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
    # Older exchangeInfo fixtures omit maxQty. Keep those callers working while
    # exposing the exchange bound whenever Binance publishes it.
    rules["max_qty"] = None
    for candidate in (lot, filters.get("LOT_SIZE")):
        try:
            rules["max_qty"] = str(_positive(candidate["maxQty"]))
            break
        except (KeyError, TypeError, ValueError):
            continue
    with _LOCK:
        _RULES[key] = (time.monotonic(), rules)
    return rules


def quote(market: str, symbol: str) -> dict:
    if market not in MARKETS or symbol not in SYMBOLS:
        raise ValueError("不支持的市场或交易对")
    observed_ms = int(time.time() * 1000)
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
        "asof_ms": observed_ms, **_rules(market, symbol),
    }


def klines(
    market: str,
    symbol: str,
    interval: str = "1h",
    limit: int = 200,
    end_ms: int | None = None,
) -> list[dict]:
    """Fetch validated, closed public spot or USDⓈ-M kline bars."""
    if market not in MARKETS:
        raise ValueError("不支持的市场")
    _validate_symbol(symbol)
    if not isinstance(interval, str):
        raise ValueError("不支持的K线周期")
    interval_ms = KLINE_INTERVALS.get(interval)
    if interval_ms is None:
        raise ValueError("不支持的K线周期")
    if type(limit) is not int or not 2 <= limit <= 1000:
        raise ValueError("K线数量必须在2到1000之间")
    if end_ms is not None:
        _timestamp(end_ms, "end_ms")

    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_ms is not None:
        params["endTime"] = end_ms
    # Anchor before fetching bars: a request spanning a boundary must not turn
    # a snapshot of a still-open candle into a supposedly closed candle.
    server_time_ms = _server_time_ms(market)
    raw_rows = _request(market, "klines", params)
    if not isinstance(raw_rows, list):
        raise ValueError("K线响应格式无效")

    bars = []
    previous_open_ms = None
    for raw in raw_rows:
        if not isinstance(raw, (list, tuple)) or len(raw) != 12:
            raise ValueError("K线响应格式无效")
        open_time_ms = _timestamp(raw[0], "K线开盘时间")
        close_time_ms = _timestamp(raw[6], "K线收盘时间")
        if previous_open_ms is not None and open_time_ms <= previous_open_ms:
            raise ValueError("K线时间必须严格升序")
        if previous_open_ms is not None and open_time_ms - previous_open_ms != interval_ms:
            raise ValueError("K线时间存在缺口")
        previous_open_ms = open_time_ms
        if open_time_ms % interval_ms or close_time_ms != open_time_ms + interval_ms - 1:
            raise ValueError("K线周期或完整性无效")

        open_price = _positive(raw[1])
        high_price = _positive(raw[2])
        low_price = _positive(raw[3])
        close_price = _positive(raw[4])
        volume = _nonnegative(raw[5])
        if high_price < max(open_price, low_price, close_price) or low_price > min(open_price, high_price, close_price):
            raise ValueError("K线OHLC关系无效")

        if close_time_ms < server_time_ms:
            bars.append({
                "open_time_ms": open_time_ms,
                "close_time_ms": close_time_ms,
                "open": str(open_price),
                "high": str(high_price),
                "low": str(low_price),
                "close": str(close_price),
                "volume": str(volume),
            })
    return bars


def funding_history(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch public USDⓈ-M funding records over an inclusive bounded window."""
    _validate_symbol(symbol)
    start_ms = _timestamp(start_ms, "start_ms")
    end_ms = _timestamp(end_ms, "end_ms")
    if start_ms > end_ms:
        raise ValueError("资金费率时间范围无效")
    if end_ms - start_ms > _MAX_FUNDING_SPAN_MS:
        raise ValueError("资金费率查询范围不能超过90天")

    records_by_time: dict[int, dict] = {}
    cursor = start_ms
    while cursor <= end_ms:
        page = _request("usdm", "fundingRate", {
            "symbol": symbol,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        })
        if not isinstance(page, list):
            raise ValueError("资金费率响应格式无效")
        if len(page) > 1000:
            raise ValueError("资金费率响应超过分页上限")
        if not page:
            break

        page_times = []
        for raw in page:
            if not isinstance(raw, dict) or raw.get("symbol") != symbol:
                raise ValueError("资金费率响应格式无效")
            funding_time_ms = _timestamp(raw.get("fundingTime"), "资金费率时间")
            rate = _decimal(raw.get("fundingRate"))
            mark_price = _positive(raw.get("markPrice"))
            page_times.append(funding_time_ms)
            if start_ms <= funding_time_ms <= end_ms:
                records_by_time.setdefault(funding_time_ms, {
                    "funding_time_ms": funding_time_ms,
                    "rate": str(rate),
                    "mark_price": str(mark_price),
                })

        last_time_ms = max(page_times)
        if len(page) < 1000 or last_time_ms >= end_ms:
            break
        if last_time_ms < cursor:
            raise ValueError("资金费率分页没有前进")
        next_cursor = last_time_ms + 1
        if next_cursor <= cursor:
            raise ValueError("资金费率分页没有前进")
        cursor = next_cursor

    return [records_by_time[timestamp] for timestamp in sorted(records_by_time)]
