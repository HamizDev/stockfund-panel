"""One public Bitget ticker connection; no keys, orders, or persisted prices."""
# ruff: noqa: RUF001 -- localized Chinese UI messages use Chinese punctuation.
from __future__ import annotations

import copy
import json
import threading
import time
from collections.abc import Callable
from contextlib import suppress

from . import client as common

PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"
MAX_AGE_SECONDS = 15


class PublicStream:
    def __init__(self, connector: Callable | None = None) -> None:
        self._connector = connector
        self._lifecycle = threading.Lock()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket = None
        self._connected = False
        self._tickers: dict[str, tuple[float, dict]] = {}
        self._error: str | None = None

    def start(self) -> None:
        with self._lifecycle, self._lock:
            if not self._thread or not self._thread.is_alive():
                self._stop.clear()
                self._tickers.clear()
                self._thread = threading.Thread(target=self._run, name="bitget-public", daemon=True)
                self._thread.start()

    def stop(self) -> None:
        with self._lifecycle:
            with self._lock:
                self._stop.set()
                sock, thread = self._socket, self._thread
                self._connected = False
                self._tickers.clear()
            if sock is not None:
                # A broken connection must not prevent the stop/join path.
                with suppress(Exception):
                    sock.close()
            if thread is not None:
                # Connection opening and closing also have bounded timeouts.
                thread.join(timeout=10)
            with self._lock:
                if not thread or not thread.is_alive():
                    self._thread = None

    def ticker(self, symbol: str) -> dict | None:
        if symbol not in common.SYMBOLS:
            raise ValueError("不支持的交易对")
        with self._lock:
            record = self._tickers.get(symbol)
            if not self._connected or record is None:
                return None
            received, value = record
            age_ms = int(time.time() * 1000) - value["asof_ms"]
            if time.monotonic() - received > MAX_AGE_SECONDS or not -5000 <= age_ms <= MAX_AGE_SECONDS * 1000:
                return None
            return copy.deepcopy(value)

    def status(self, symbol: str) -> dict:
        ticker = self.ticker(symbol)
        with self._lock:
            return {"connected": self._connected, "fresh": ticker is not None,
                    "asof_ms": ticker["asof_ms"] if ticker else None,
                    "error": self._error, "source": "bitget_public_websocket"}

    def accept(self, payload: object) -> None:
        if not isinstance(payload, dict):
            raise ValueError("推送响应格式无效")
        if payload.get("event") == "error":
            raise ValueError("公开推送订阅被拒绝")
        if payload.get("event") == "subscribe":
            return
        arg = payload.get("arg")
        if not isinstance(arg, dict):
            raise ValueError("推送频道缺失")
        symbol = arg.get("instId")
        if symbol not in common.SYMBOLS or arg.get("instType") != "USDT-FUTURES" or arg.get("channel") != "ticker":
            raise ValueError("推送频道不匹配")
        try:
            data = payload.get("data")
            if payload.get("action") != "snapshot" or not isinstance(data, list) or len(data) != 1:
                raise ValueError("推送快照格式无效")
            row = data[0]
            if not isinstance(row, dict) or row.get("symbol") != symbol or row.get("instId", symbol) != symbol:
                raise ValueError("推送交易对不匹配")
            stamp = row.get("ts")
            if isinstance(stamp, str) and stamp.isascii() and stamp.isdecimal():
                stamp = int(stamp)
            stamp = common._timestamp(stamp, "推送时间", allow_zero=False)
            if not -5000 <= int(time.time() * 1000) - stamp <= MAX_AGE_SECONDS * 1000:
                raise ValueError("推送行情过期")
            bid, ask = common._positive(row.get("bidPr")), common._positive(row.get("askPr"))
            if bid > ask:
                raise ValueError("推送买卖价异常")
            value = {"symbol": symbol, "bid": str(bid), "ask": str(ask),
                     "mark": str(common._positive(row.get("markPrice"))),
                     "last": str(common._positive(row.get("lastPr"))), "asof_ms": stamp}
            with self._lock:
                prior = self._tickers.get(symbol)
                if prior and prior[1]["asof_ms"] > stamp:
                    return
                self._tickers[symbol] = (time.monotonic(), value)
        except (ValueError, TypeError, KeyError):
            with self._lock:
                self._tickers.pop(symbol, None)
            raise

    def _run(self) -> None:
        delay = 2
        while not self._stop.is_set():
            try:
                connector = self._connector
                if connector is None:
                    from websockets.sync.client import connect
                    connector = connect
                with connector(PUBLIC_URL, open_timeout=6, close_timeout=2,
                               ping_interval=None, max_size=65536, max_queue=16) as sock:
                    if self._stop.is_set():
                        break
                    with self._lock:
                        self._socket = sock
                        self._tickers.clear()
                        self._connected = True
                        self._error = None
                    sock.send(json.dumps({"op": "subscribe", "args": [
                        {"instType": "USDT-FUTURES", "channel": "ticker", "instId": symbol}
                        for symbol in common.SYMBOLS]}))
                    ping_at, pong_at = time.monotonic(), time.monotonic()
                    delay = 2
                    while not self._stop.is_set():
                        now = time.monotonic()
                        if now - pong_at > 45:
                            raise TimeoutError("公开推送心跳超时")
                        if now - ping_at >= 20:
                            sock.send("ping")
                            ping_at = now
                        try:
                            message = sock.recv(timeout=1)
                        except TimeoutError:
                            continue
                        if message == "pong":
                            pong_at = time.monotonic()
                        else:
                            self.accept(json.loads(message))
            except Exception as exc:
                # Never expose socket/proxy URLs or provider payloads in the UI.
                with self._lock:
                    self._error = "Bitget 公开推送暂不可用，改用已校验的 REST 行情"
                if isinstance(exc, ImportError):
                    with self._lock:
                        self._error = "未安装 websockets，当前使用 REST 行情"
                    return
            finally:
                with self._lock:
                    self._socket = None
                    self._connected = False
                    self._tickers.clear()
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 60)


stream = PublicStream()
