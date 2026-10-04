"""Anonymous zzshare minute HTTP client.

The public endpoint is intentionally called without authentication. Request
throttling is process-wide so provider reloads cannot multiply its anonymous
rate limit. HTTP 429 responses are surfaced immediately without retries.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.zizizaizai.com/v3/market/kline/minute"
_MIN_REQUEST_INTERVAL_S = 2.1
_SYMBOL_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_BEIJING = ZoneInfo("Asia/Shanghai")

_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_STARTED: float | None = None


class ZZShareError(Exception):
    """zzshare HTTP or response-contract error, without request URLs or secrets."""


def _beijing_wallclock(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(_BEIJING).replace(tzinfo=None)


class ZZShareClient:
    """Fetch one stock's minute bars from the anonymous zzshare endpoint."""

    def __init__(self, timeout: float = 20.0) -> None:
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _get(self, symbol: str, params: dict[str, str]) -> httpx.Response:
        global _LAST_REQUEST_STARTED

        url = f"{_BASE_URL}/{symbol}"
        # Keep the lock for both pacing and I/O. This serializes requests from
        # all ZZShareClient instances and records the attempt before the call,
        # so a failed request consumes the same anonymous rate-limit interval.
        with _REQUEST_LOCK:
            now = time.monotonic()
            if _LAST_REQUEST_STARTED is not None:
                delay = _MIN_REQUEST_INTERVAL_S - (now - _LAST_REQUEST_STARTED)
                if delay > 0:
                    time.sleep(delay)
            _LAST_REQUEST_STARTED = time.monotonic()
            try:
                return self._http.get(url, params=params)
            except httpx.HTTPError as exc:
                raise ZZShareError(
                    f"zzshare minute 网络请求失败 ({type(exc).__name__})"
                ) from None

    def fetch_minute(
        self,
        symbol: str,
        start_time: datetime | None,
        end_time: datetime | None,
        *,
        trade_time: str | None = None,
    ) -> list[dict]:
        """Return raw rows for a range or a single YYYYMMDD trading date."""
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError("zzshare 证券代码格式无效")

        params: dict[str, str] = {"freq": "1min"}
        if (start_time is None) != (end_time is None):
            raise ValueError("zzshare 区间查询要求同时提供 start_time 和 end_time")
        if start_time is not None and end_time is not None:
            start = _beijing_wallclock(start_time)
            end = _beijing_wallclock(end_time)
            if start > end:
                raise ValueError("zzshare start_time 不能晚于 end_time")
            params["start_time"] = start.strftime("%Y%m%d %H:%M:%S")
            params["end_time"] = end.strftime("%Y%m%d %H:%M:%S")
        else:
            day = trade_time or datetime.now(_BEIJING).strftime("%Y%m%d")
            if not re.fullmatch(r"\d{8}", day):
                raise ValueError("zzshare trade_time 必须使用 YYYYMMDD")
            try:
                datetime.strptime(day, "%Y%m%d")
            except ValueError as exc:
                raise ValueError("zzshare trade_time 必须是有效日期 YYYYMMDD") from exc
            params["trade_time"] = day

        response = self._get(symbol, params)
        if response.status_code == 429:
            raise ZZShareError("zzshare minute HTTP 429: rate limited; no automatic retry")
        if response.status_code != 200:
            raise ZZShareError(f"zzshare minute HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise ZZShareError("zzshare minute 响应不是有效 JSON") from None
        if not isinstance(payload, dict) or payload.get("code") not in (200, "200"):
            raise ZZShareError("zzshare minute 响应 code 无效")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ZZShareError("zzshare minute 响应缺少 data 对象")
        rows = data.get("list")
        if not isinstance(rows, list):
            raise ZZShareError("zzshare minute 响应 list 字段不是数组")
        if any(not isinstance(row, dict) for row in rows):
            raise ZZShareError("zzshare minute 响应包含非对象行")
        return rows
