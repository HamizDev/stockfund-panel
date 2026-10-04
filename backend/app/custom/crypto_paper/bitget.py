"""Bitget public USDT-margined perpetual market data only."""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from decimal import Decimal
from itertools import pairwise
from types import MappingProxyType

import httpx

from . import client as common
from .public_stream import stream

SYMBOLS = common.SYMBOLS
MARKETS = ("usdm",)
PRODUCT_TYPE = "USDT-FUTURES"
KLINE_INTERVALS = {
    "1m": (60 * 1000, "1m"),
    "5m": (5 * 60 * 1000, "5m"),
    "15m": (15 * 60 * 1000, "15m"),
    "1h": (60 * 60 * 1000, "1H"),
    "4h": (4 * 60 * 60 * 1000, "4H"),
}

_BASE_URL = "https://api.bitget.com"
_ENDPOINTS = {
    "time": "/api/v2/public/time",
    "ticker": "/api/v2/mix/market/ticker",
    "contracts": "/api/v2/mix/market/contracts",
    "current-fund-rate": "/api/v2/mix/market/current-fund-rate",
    "candles": "/api/v2/mix/market/candles",
    "history-fund-rate": "/api/v2/mix/market/history-fund-rate",
    "history-mark-candles": "/api/v2/mix/market/history-mark-candles",
}
_MAX_FUNDING_SPAN_MS = 30 * 24 * 60 * 60 * 1000
_MAX_FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000
_MAX_FUNDING_PAGES = 10
_RULES_TTL_SECONDS = 600
_MARK_CACHE_SIZE = 512
_RULES: dict[tuple[str, str], tuple[float, Mapping[str, object]]] = {}
_WS_FUNDING: dict[str, tuple[float, dict]] = {}
_MARKS: OrderedDict[tuple[str, int], str] = OrderedDict()
_LOCK = threading.RLock()


def _validate(market: str, symbol: str) -> None:
    if market not in MARKETS:
        raise ValueError("Bitget仅支持USDT本位合约市场")
    if symbol not in SYMBOLS:
        raise ValueError("不支持的交易对")


def _timestamp_ms(value: object, name: str, *, allow_zero: bool = True) -> int:
    if isinstance(value, str):
        if not value or not value.isascii() or not value.isdecimal():
            raise ValueError(f"{name}格式无效")
        value = int(value)
    return common._timestamp(value, name, allow_zero=allow_zero)


def _request(endpoint: str, params: dict) -> object:
    """Make an unauthenticated GET to one fixed Bitget public endpoint."""
    path = _ENDPOINTS.get(endpoint)
    if path is None:
        raise ValueError("Bitget公开接口无效")
    with httpx.Client(timeout=6.0, follow_redirects=False) as client:
        response = client.get(f"{_BASE_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()


def _data(payload: object, expected_type: type, endpoint: str) -> object:
    if not isinstance(payload, dict):
        raise ValueError("Bitget响应格式无效")
    if payload.get("code") != "00000":
        raw_code = payload.get("code")
        code = str(raw_code) if raw_code is not None else "unknown"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", code):
            code = "unknown"
        raise ValueError(f"Bitget公开接口失败(code={code})")
    data = payload.get("data")
    if not isinstance(data, expected_type):
        raise ValueError(f"Bitget {endpoint}响应格式无效")
    return data


def _one_symbol(rows: list, symbol: str, endpoint: str) -> dict:
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Bitget {endpoint}响应格式无效")
    matches = [row for row in rows if row.get("symbol") == symbol]
    if len(matches) != 1:
        raise ValueError(f"Bitget {endpoint}交易对响应无效")
    return matches[0]


def _current_funding(symbol: str) -> dict:
    rows = _data(_request("current-fund-rate", {
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
    }), list, "当前资金费率")
    row = _one_symbol(rows, symbol, "当前资金费率")
    rate = common._decimal(row.get("fundingRate"))
    next_update_ms = _timestamp_ms(row.get("nextUpdate"), "下次资金费时间", allow_zero=False)
    interval_raw = row.get("fundingRateInterval")
    interval = common._positive(interval_raw)
    if interval != interval.to_integral_value() or int(interval) not in (1, 2, 4, 8):
        raise ValueError("Bitget资金费间隔无效")
    interval_hours = int(interval)
    if next_update_ms <= interval_hours * 60 * 60 * 1000:
        raise ValueError("Bitget下次资金费时间无效")
    return {
        "rate": rate,
        "next_update_ms": next_update_ms,
        "interval_hours": interval_hours,
    }


def _contract_rules(symbol: str) -> dict:
    key = ("usdm", symbol)
    now = time.monotonic()
    with _LOCK:
        cached = _RULES.get(key)
        if cached and now - cached[0] < _RULES_TTL_SECONDS:
            return dict(cached[1])

    rows = _data(_request("contracts", {
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
    }), list, "合约规则")
    row = _one_symbol(rows, symbol, "合约规则")
    support_margin_coins = row.get("supportMarginCoins")
    if (row.get("symbolStatus") != "normal"
            or row.get("symbolType") != "perpetual"
            or row.get("quoteCoin") != "USDT"
            or not isinstance(support_margin_coins, list)
            or "USDT" not in support_margin_coins):
        raise ValueError("Bitget合约暂不支持模拟")

    minimum_qty = common._positive(row.get("minTradeNum"))
    step_size = common._positive(row.get("sizeMultiplier"))
    maximum_qty = common._positive(row.get("maxMarketOrderQty"))
    minimum_notional = common._positive(row.get("minTradeUSDT"))
    minimum_leverage = common._positive(row.get("minLever", "1"))
    maximum_leverage = common._positive(row.get("maxLever"))
    taker_fee_rate = common._decimal(row.get("takerFeeRate"))
    if (minimum_qty % step_size or maximum_qty < minimum_qty
            or minimum_leverage != minimum_leverage.to_integral_value()
            or maximum_leverage != maximum_leverage.to_integral_value()
            or minimum_leverage > maximum_leverage or taker_fee_rate < 0):
        raise ValueError("Bitget合约规则数值无效")

    rules = MappingProxyType({
        "step_size": str(step_size),
        "min_qty": str(minimum_qty),
        "max_qty": str(maximum_qty),
        "min_notional": str(minimum_notional),
        "max_leverage": int(maximum_leverage),
        "taker_fee_rate": str(taker_fee_rate),
    })
    with _LOCK:
        _RULES[key] = (time.monotonic(), rules)
    return dict(rules)


def quote(market: str, symbol: str) -> dict:
    _validate(market, symbol)
    pushed = stream.ticker(symbol)
    if pushed is not None:
        with _LOCK:
            cached_funding = _WS_FUNDING.get(symbol)
        if (cached_funding and time.monotonic() - cached_funding[0] < 20
                and cached_funding[1]["next_update_ms"] > int(time.time() * 1000)):
            funding = dict(cached_funding[1])
        else:
            funding = _current_funding(symbol)
            with _LOCK:
                _WS_FUNDING[symbol] = (time.monotonic(), dict(funding))
        rules = _contract_rules(symbol)
        # REST metadata may take several seconds. Re-read the freshest ticker
        # afterwards, rather than using a pre-fetch price to create an order.
        pushed = stream.ticker(symbol)
        if pushed is not None:
            if funding["next_update_ms"] <= max(pushed["asof_ms"], int(time.time() * 1000)):
                raise ValueError("Bitget资金费结算时间待更新")
            return {"market": market, "exchange": "bitget", **pushed,
                    "last_funding_rate": str(funding["rate"]),
                    "next_funding_time": funding["next_update_ms"],
                    "funding_interval_hours": funding["interval_hours"],
                    "source": "bitget_public_websocket", **rules}
    ticker_rows = _data(_request("ticker", {
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
    }), list, "行情")
    ticker = _one_symbol(ticker_rows, symbol, "行情")
    bid = common._positive(ticker.get("bidPr"))
    ask = common._positive(ticker.get("askPr"))
    mark = common._positive(ticker.get("markPrice"))
    # Validate the ticker's rate too; the current-rate endpoint supplies the
    # rate paired with Bitget's nextUpdate schedule below.
    common._decimal(ticker.get("fundingRate"))
    asof_ms = _timestamp_ms(ticker.get("ts"), "行情时间", allow_zero=False)
    if bid > ask:
        raise ValueError("买卖价异常")

    funding = _current_funding(symbol)
    rules = _contract_rules(symbol)
    if funding["next_update_ms"] <= max(asof_ms, int(time.time() * 1000)):
        raise ValueError("Bitget资金费结算时间待更新")
    return {
        "market": market,
        "exchange": "bitget",
        "symbol": symbol,
        "bid": str(bid),
        "ask": str(ask),
        "mark": str(mark),
        "last_funding_rate": str(funding["rate"]),
        "next_funding_time": funding["next_update_ms"],
        "funding_interval_hours": funding["interval_hours"],
        "asof_ms": asof_ms,
        **rules,
    }


def _server_time_ms() -> int:
    data = _data(_request("time", {}), dict, "服务器时间")
    return _timestamp_ms(data.get("serverTime"), "服务器时间", allow_zero=False)


def klines(
    market: str,
    symbol: str,
    interval: str = "1h",
    limit: int = 200,
    end_ms: int | None = None,
) -> list[dict]:
    _validate(market, symbol)
    if not isinstance(interval, str) or interval not in KLINE_INTERVALS:
        raise ValueError("不支持的K线周期")
    interval_ms, granularity = KLINE_INTERVALS[interval]
    if type(limit) is not int or not 2 <= limit <= 1000:
        raise ValueError("K线数量必须在2到1000之间")
    if end_ms is not None:
        common._timestamp(end_ms, "end_ms")

    params = {
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
        "granularity": granularity,
        "limit": str(limit),
    }
    if end_ms is not None:
        params["endTime"] = str(end_ms)

    # Anchor before fetching: the last returned candle can still be open.
    server_time_ms = _server_time_ms()
    raw_rows = _data(_request("candles", params), list, "K线")
    if len(raw_rows) > limit:
        raise ValueError("Bitget K线响应超过请求数量")

    parsed = []
    for raw in raw_rows:
        if not isinstance(raw, (list, tuple)) or len(raw) != 7:
            raise ValueError("K线响应格式无效")
        open_time_ms = _timestamp_ms(raw[0], "K线开盘时间")
        if open_time_ms % interval_ms:
            raise ValueError("K线周期或完整性无效")
        open_price = common._positive(raw[1])
        high_price = common._positive(raw[2])
        low_price = common._positive(raw[3])
        close_price = common._positive(raw[4])
        volume = common._nonnegative(raw[5])
        # The seventh field is quote-volume; validate the supplier value even
        # though the shared Binance-shaped contract exposes base volume only.
        common._nonnegative(raw[6])
        if high_price < max(open_price, low_price, close_price) or low_price > min(open_price, high_price, close_price):
            raise ValueError("K线OHLC关系无效")
        close_time_ms = open_time_ms + interval_ms - 1
        parsed.append({
            "open_time_ms": open_time_ms,
            "close_time_ms": close_time_ms,
            "open": str(open_price),
            "high": str(high_price),
            "low": str(low_price),
            "close": str(close_price),
            "volume": str(volume),
        })

    parsed.sort(key=lambda bar: bar["open_time_ms"])
    bars = []
    previous_open_ms = None
    for bar in parsed:
        open_time_ms = bar["open_time_ms"]
        if previous_open_ms is not None:
            if open_time_ms == previous_open_ms:
                raise ValueError("K线开盘时间重复")
            if open_time_ms - previous_open_ms != interval_ms:
                raise ValueError("K线时间存在缺口")
        previous_open_ms = open_time_ms
        if bar["close_time_ms"] >= server_time_ms:
            continue
        if end_ms is not None and bar["close_time_ms"] > end_ms:
            continue
        bars.append(bar)
    return bars


def _mark_open(symbol: str, funding_time_ms: int) -> str:
    key = (symbol, funding_time_ms)
    with _LOCK:
        cached = _MARKS.get(key)
        if cached is not None:
            _MARKS.move_to_end(key)
            return cached

    rows = _data(_request("history-mark-candles", {
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
        "granularity": "1m",
        "startTime": str(funding_time_ms),
        "endTime": str(funding_time_ms + 60_000),
        "limit": "2",
    }), list, "历史标记价格K线")
    matched = []
    for raw in rows:
        if not isinstance(raw, (list, tuple)) or len(raw) != 7:
            raise ValueError("历史标记价格K线响应格式无效")
        stamp = _timestamp_ms(raw[0], "历史标记价格K线时间")
        open_price = common._positive(raw[1])
        high_price = common._positive(raw[2])
        low_price = common._positive(raw[3])
        close_price = common._positive(raw[4])
        common._nonnegative(raw[5])
        common._nonnegative(raw[6])
        if high_price < max(open_price, low_price, close_price) or low_price > min(open_price, high_price, close_price):
            raise ValueError("历史标记价格K线OHLC关系无效")
        if stamp == funding_time_ms:
            matched.append(str(open_price))
    if len(matched) != 1:
        raise ValueError("Bitget资金费对应的历史标记价格不可用")

    with _LOCK:
        _MARKS[key] = matched[0]
        _MARKS.move_to_end(key)
        while len(_MARKS) > _MARK_CACHE_SIZE:
            _MARKS.popitem(last=False)
    return matched[0]


def _validate_funding_coverage(
    start_ms: int,
    end_ms: int,
    funding_times: list[int],
    *,
    latest_boundary_ms: int,
) -> None:
    if funding_times:
        if funding_times[0] - start_ms > _MAX_FUNDING_INTERVAL_MS:
            raise ValueError("Bitget资金费历史起始覆盖不足")
        if end_ms - funding_times[-1] > _MAX_FUNDING_INTERVAL_MS:
            raise ValueError("Bitget资金费历史结束覆盖不足")
        if any(b - a > _MAX_FUNDING_INTERVAL_MS for a, b in pairwise(funding_times)):
            raise ValueError("Bitget资金费历史存在超过8小时的缺口")
    elif end_ms - start_ms > _MAX_FUNDING_INTERVAL_MS:
        raise ValueError("Bitget资金费历史覆盖不足")

    if start_ms < latest_boundary_ms <= end_ms and latest_boundary_ms not in funding_times:
        raise ValueError("Bitget最新结算资金费尚未发布; 覆盖不足")


def funding_history(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch bounded public funding history with explicitly estimated marks."""
    _validate("usdm", symbol)
    start_ms = common._timestamp(start_ms, "start_ms")
    end_ms = common._timestamp(end_ms, "end_ms")
    if start_ms > end_ms:
        raise ValueError("资金费率时间范围无效")
    if end_ms - start_ms > _MAX_FUNDING_SPAN_MS:
        raise ValueError("资金费率查询范围不能超过30天")

    funding = _current_funding(symbol)
    latest_boundary_ms = funding["next_update_ms"] - funding["interval_hours"] * 60 * 60 * 1000
    records_by_time: dict[int, Decimal] = {}
    previous_oldest_ms = None
    history_complete = False

    for page_no in range(1, _MAX_FUNDING_PAGES + 1):
        page = _data(_request("history-fund-rate", {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "pageNo": str(page_no),
            "pageSize": "100",
        }), list, "资金费率历史")
        if len(page) > 100:
            raise ValueError("Bitget资金费率页超过分页上限")
        if not all(isinstance(row, dict) for row in page):
            raise ValueError("资金费率响应格式无效")
        if not page:
            history_complete = True
            break

        times = []
        parsed_page = []
        for raw in page:
            if raw.get("symbol") != symbol:
                raise ValueError("资金费率响应交易对不匹配")
            stamp = _timestamp_ms(raw.get("fundingTime"), "资金费率时间")
            rate = common._decimal(raw.get("fundingRate"))
            times.append(stamp)
            parsed_page.append((stamp, rate))
        if any(later > earlier for earlier, later in pairwise(times)):
            raise ValueError("Bitget资金费分页必须按时间倒序")

        oldest_ms = times[-1]
        if previous_oldest_ms is not None and oldest_ms >= previous_oldest_ms:
            raise ValueError("Bitget资金费分页没有前进")
        previous_oldest_ms = oldest_ms
        for stamp, rate in parsed_page:
            if start_ms <= stamp <= end_ms:
                previous_rate = records_by_time.setdefault(stamp, rate)
                if previous_rate != rate:
                    raise ValueError("Bitget资金费重复记录不一致")

        if oldest_ms <= start_ms or len(page) < 100:
            history_complete = True
            break
    if not history_complete:
        raise ValueError("Bitget资金费分页超过安全上限")

    funding_times = sorted(records_by_time)
    _validate_funding_coverage(
        start_ms,
        end_ms,
        funding_times,
        latest_boundary_ms=latest_boundary_ms,
    )

    return [{
        "funding_time_ms": stamp,
        "rate": str(records_by_time[stamp]),
        "mark_price": _mark_open(symbol, stamp),
        "mark_price_basis": "bitget_1m_mark_open_estimate",
    } for stamp in funding_times]
