# ruff: noqa: RUF001 -- localized Chinese UI punctuation.
"""Public perpetual chart data. No demo credentials enter market-data requests."""
from __future__ import annotations

import copy
import threading
import time
from itertools import pairwise

import httpx

from . import bitget, market
from . import client as common
from .demo_client import OKX_HOSTS, DemoAPIError

_LOCK = threading.RLock()
_CACHE: dict[tuple, tuple[float, dict]] = {}
_FETCH_LOCKS: dict[tuple, threading.Lock] = {}
_PATHS = {"time": "/api/v5/public/time", "candles": "/api/v5/market/candles",
          "ticker": "/api/v5/market/ticker", "mark": "/api/v5/public/mark-price"}


def _validate(exchange: str, symbol: str, site: str, interval: str = "1m") -> None:
    if exchange not in {"okx", "bitget"} or symbol not in bitget.SYMBOLS or site not in OKX_HOSTS:
        raise DemoAPIError("不支持的交易所、站点或交易对")
    if interval not in bitget.KLINE_INTERVALS:
        raise DemoAPIError("不支持的图表周期")


def _request(site: str, endpoint: str, params: dict) -> list:
    with httpx.Client(timeout=httpx.Timeout(10, connect=6), follow_redirects=False) as client:
        response = client.get(OKX_HOSTS[site] + _PATHS[endpoint], params=params)
        if response.status_code >= 300:
            raise DemoAPIError("欧易公开行情暂不可用，请检查网络和账户站点")
        payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("code")) != "0" or not isinstance(payload.get("data"), list):
        raise DemoAPIError("欧易公开行情响应无效")
    return payload["data"]


def _instrument(symbol: str) -> str:
    return symbol.removesuffix("USDT") + "-USDT-SWAP"


def _okx_candles(symbol: str, interval: str, site: str) -> dict:
    clock = _request(site, "time", {})
    if len(clock) != 1 or not isinstance(clock[0], dict):
        raise DemoAPIError("欧易服务器时间无效")
    cutoff = bitget._timestamp_ms(clock[0].get("ts"), "服务器时间", allow_zero=False)
    duration, bar = bitget.KLINE_INTERVALS[interval]
    raw = _request(site, "candles", {"instId": _instrument(symbol), "bar": bar, "limit": "300"})
    if len(raw) > 300:
        raise DemoAPIError("欧易K线超过读取上限")
    parsed = {}
    for row in raw:
        if not isinstance(row, list) or len(row) != 9 or row[8] not in ("0", "1"):
            raise DemoAPIError("欧易K线格式无效")
        stamp = bitget._timestamp_ms(row[0], "K线时间")
        if stamp % duration or stamp in parsed:
            raise DemoAPIError("欧易K线周期或重复记录无效")
        o, h, low, c = [common._positive(v) for v in row[1:5]]
        if h < max(o, c, low) or low > min(o, c, h):
            raise DemoAPIError("欧易K线OHLC关系无效")
        common._nonnegative(row[5])  # Contracts, not base coin volume.
        volume = common._nonnegative(row[6])  # volCcy is base currency for SWAP.
        common._nonnegative(row[7])
        end = stamp + duration - 1
        parsed[stamp] = None if row[8] != "1" or end >= cutoff else {
            "open_time_ms": stamp, "close_time_ms": end, "open": str(o), "high": str(h),
            "low": str(low), "close": str(c), "volume": str(volume)}
    bars = [parsed[t] for t in sorted(parsed) if parsed[t] is not None]
    if any(b["open_time_ms"] - a["open_time_ms"] != duration for a, b in pairwise(bars)):
        raise DemoAPIError("欧易K线时间存在缺口")
    return {"exchange": "okx", "market": "usdm", "symbol": symbol, "interval": interval,
            "bars": bars, "closed_only": True, "source": "okx_public_rest"}


def _cached(key: tuple, ttl: int, fetch) -> dict:
    with _LOCK:
        lock = _FETCH_LOCKS.setdefault(key, threading.Lock())
    # Bounded by validated 2 exchanges x 3 sites x 3 symbols x 6 resources.
    with lock:
        with _LOCK:
            cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < ttl:
            return copy.deepcopy(cached[1])
        result = fetch()
        with _LOCK:
            _CACHE[key] = (time.monotonic(), copy.deepcopy(result))
        return result


def candles(exchange: str, symbol: str, interval: str, site: str = "global") -> dict:
    _validate(exchange, symbol, site, interval)
    if exchange == "bitget":
        return market.candles(symbol, interval)
    return _cached((exchange, site, symbol, interval), 15, lambda: _okx_candles(symbol, interval, site))


def quote(exchange: str, symbol: str, site: str = "global") -> dict:
    _validate(exchange, symbol, site)

    def fetch() -> dict:
        if exchange == "bitget":
            q = bitget.quote("usdm", symbol)
            result = {"exchange": exchange, "symbol": symbol, "bid": q["bid"], "ask": q["ask"],
                      "mark": q["mark"], "asof_ms": q["asof_ms"]}
        else:
            instrument = _instrument(symbol)
            tickers = _request(site, "ticker", {"instId": instrument})
            marks = _request(site, "mark", {"instType": "SWAP", "instId": instrument})
            if (len(tickers) != 1 or len(marks) != 1 or not isinstance(tickers[0], dict)
                    or not isinstance(marks[0], dict) or tickers[0].get("instId") != instrument
                    or marks[0].get("instId") != instrument):
                raise DemoAPIError("欧易报价交易对无效")
            row, mark = tickers[0], marks[0]
            stamp = bitget._timestamp_ms(row.get("ts"), "报价时间", allow_zero=False)
            mark_stamp = bitget._timestamp_ms(mark.get("ts"), "标记价格时间", allow_zero=False)
            if not -5000 <= int(time.time() * 1000) - mark_stamp <= 60000:
                raise DemoAPIError("欧易标记价格已过期")
            result = {"exchange": exchange, "symbol": symbol, "bid": str(common._positive(row.get("bidPx"))),
                      "ask": str(common._positive(row.get("askPx"))), "mark": str(common._positive(mark.get("markPx"))),
                      "asof_ms": stamp}
        if common._positive(result["bid"]) > common._positive(result["ask"]):
            raise DemoAPIError("公开买卖报价异常")
        if not -5000 <= int(time.time() * 1000) - result["asof_ms"] <= 60000:
            raise DemoAPIError("公开报价已过期")
        return result

    return _cached((exchange, site, symbol, "quote"), 5, fetch)
