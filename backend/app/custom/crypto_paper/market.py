"""Read-only chart snapshots, sharing the validated Bitget adapter."""
from __future__ import annotations

import copy
import threading
import time

from . import bitget

_LOCK = threading.RLock()
_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_FETCH_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def candles(symbol: str, interval: str) -> dict:
    bitget._validate("usdm", symbol)
    if interval not in bitget.KLINE_INTERVALS:
        raise ValueError("不支持的K线周期")
    key = (symbol, interval)
    with _LOCK:
        fetch_lock = _FETCH_LOCKS.setdefault(key, threading.Lock())
    # At most 15 possible keys: three explicit symbols, five chart periods.
    with fetch_lock:
        with _LOCK:
            cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 15:
            bars = copy.deepcopy(cached[1])
        else:
            bars = bitget.klines("usdm", symbol, interval, limit=300)
            with _LOCK:
                _CACHE[key] = (time.monotonic(), copy.deepcopy(bars))
        return {"exchange": "bitget", "market": "usdm", "symbol": symbol,
                "interval": interval, "bars": bars, "closed_only": True,
                "source": "bitget_public_rest"}
