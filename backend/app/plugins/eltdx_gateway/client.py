"""Bounded RPC reader; ELTDX itself is not imported or bundled by the panel."""
from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_URL = "http://127.0.0.1:3022"
GATEWAY_ENV = "STOCKFUND_ELTDX_GATEWAY_URL"
MAX_PAGES = 6
PAGE_SIZE = 800
TOTAL_TIMEOUT = 25.0
_SYMBOL = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")


class EltdxGatewayError(RuntimeError):
    """Public errors deliberately exclude upstream bodies and URL credentials."""


def gateway_url(value: str | None = None) -> str:
    value = value or os.getenv(GATEWAY_ENV) or DEFAULT_URL
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.username is None and parsed.password is None
            and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
            and (parsed.port is None or 0 < parsed.port <= 65535)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise EltdxGatewayError("ELTDX网关须为无凭据、无路径参数的本机回环地址")
    return value.rstrip("/")


def wallclock(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise EltdxGatewayError("分钟区间必须使用有效日期时间")
    return value.astimezone(CN_TZ).replace(tzinfo=None) if value.tzinfo else value


def bar_datetime(value: object) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return wallclock(parsed)
    except (ValueError, TypeError, OverflowError):
        raise EltdxGatewayError("ELTDX分钟时间格式无效") from None


def wire_symbol(symbol: str) -> tuple[str, str, str]:
    match = _SYMBOL.fullmatch(symbol) if isinstance(symbol, str) else None
    if match is None:
        raise EltdxGatewayError("分钟查询需要带交易所后缀的6位证券代码")
    code, market = match.groups()
    return market.lower() + code, market.lower(), code


def time_window(start_time, end_time) -> tuple[datetime | None, datetime | None]:
    start, end = wallclock(start_time), wallclock(end_time)
    if (start is None) != (end is None) or (start is not None and end < start):
        raise EltdxGatewayError("分钟查询需要完整且顺序正确的日期区间")
    if start is not None and end - start > timedelta(days=30):
        raise EltdxGatewayError("研究分钟查询区间最多30个自然日; 历史覆盖以实际返回为准")
    return start, end


class EltdxGatewayClient:
    def __init__(self, base_url: str | None = None, transport=None) -> None:
        self._http = httpx.Client(
            base_url=gateway_url(base_url), transport=transport,
            timeout=10, trust_env=False, follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, *, deadline: float, body=None) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EltdxGatewayError("ELTDX分钟查询超时, 未发布截断数据")
        try:
            response = self._http.request(method, path, json=body, timeout=min(10, remaining))
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError:
            raise EltdxGatewayError("ELTDX本机网关请求失败, 请检查网关和上游连接") from None
        except (ValueError, TypeError):
            raise EltdxGatewayError("ELTDX网关返回了无效JSON") from None
        if not isinstance(payload, dict):
            raise EltdxGatewayError("ELTDX网关响应结构无效")
        if time.monotonic() >= deadline:
            raise EltdxGatewayError("ELTDX分钟查询超时, 未发布截断数据")
        return payload

    def fetch_minute(self, symbol: str, start_time, end_time) -> list[dict]:
        code, market, bare_code = wire_symbol(symbol)
        start, _ = time_window(start_time, end_time)
        deadline = time.monotonic() + TOTAL_TIMEOUT
        health = self._request("GET", "/health", deadline=deadline)
        if health.get("ok") is not True or health.get("service") != "eltdx" or health.get("version") != "3.2.2":
            raise EltdxGatewayError("需要兼容且已启动的ELTDX 3.2.2本机网关")
        offset, records = 0, []
        count = 240 if start is None else PAGE_SIZE
        for page in range(MAX_PAGES):
            request_id = uuid.uuid4().hex
            payload = self._request("POST", "/rpc", deadline=deadline, body={
                "id": request_id,
                "method": "bars.get",
                "params": {"code": code, "period": "1m", "start": offset, "count": count, "adjust": "none"},
            })
            if payload.get("ok") is not True or payload.get("id") != request_id:
                raise EltdxGatewayError("ELTDX分钟RPC失败或请求标识不匹配")
            result = payload.get("result")
            if (
                not isinstance(result, dict)
                or result.get("exchange") != market or result.get("code") != bare_code
                or result.get("adjust_mode") != "none" or result.get("period_name") != "1m"
            ):
                raise EltdxGatewayError("ELTDX返回证券、1分钟周期或不复权口径不匹配")
            bars = result.get("bars")
            if not isinstance(bars, list) or len(bars) > count or any(not isinstance(r, dict) for r in bars):
                raise EltdxGatewayError("ELTDX分钟页结构或数量无效")
            if not bars:
                return records
            earliest = min(bar_datetime(row.get("time")) for row in bars)
            records.extend(bars)
            if start is None or earliest <= start:
                return records
            offset += len(bars)
            if page + 1 < MAX_PAGES:
                time.sleep(0.2)
        raise EltdxGatewayError("分钟区间超出6页查询预算, 未发布截断数据; 请缩短区间")
