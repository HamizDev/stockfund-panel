# ruff: noqa: RUF001 -- localized Chinese UI punctuation.
"""Official demo-only authenticated GET transport. No trading endpoints or live mode."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from datetime import UTC, datetime

import httpx

OKX_HOSTS = {"global": "https://openapi.okx.com", "us": "https://us.okx.com", "eea": "https://eea.okx.com"}
BITGET_HOST = "https://api.bitget.com"
OKX_PATHS = {"/api/v5/account/balance", "/api/v5/account/positions", "/api/v5/trade/orders-pending", "/api/v5/trade/fills"}
BITGET_CLASSIC_PATHS = {"/api/v2/mix/account/accounts", "/api/v2/mix/position/all-position", "/api/v2/mix/order/orders-pending", "/api/v2/mix/order/fills"}
BITGET_UTA_PATHS = {"/api/v3/account/assets", "/api/v3/position/current-position", "/api/v3/trade/unfilled-orders", "/api/v3/trade/fills"}


class DemoAPIError(ValueError):
    _DIAGNOSTIC_CATEGORIES = frozenset({"exchange_code", "exchange_error", "http_status", "network", "invalid_response"})

    def __init__(self, message: str, *, category: str | None = None, code: str | int | None = None):
        if category not in self._DIAGNOSTIC_CATEGORIES:
            category = None
        safe_code = str(code) if code is not None else None
        if category == "exchange_code" and (safe_code is None or not re.fullmatch(r"[0-9]{1,8}", safe_code)):
            category, safe_code = "exchange_error", None
        if category == "http_status" and (safe_code is None or not re.fullmatch(r"[1-5][0-9]{2}", safe_code)):
            safe_code = None
        if category not in {"exchange_code", "http_status"}:
            safe_code = None
        self.diagnostic_category = category
        self.diagnostic_code = safe_code
        super().__init__(message)

    def __str__(self) -> str:
        message = super().__str__()
        if self.diagnostic_category is None:
            return message
        marker = f"[demo-error:{self.diagnostic_category}"
        if self.diagnostic_code is not None:
            marker += f":{self.diagnostic_code}"
        return f"{message} {marker}]"


def signature(secret: str, timestamp: str, request_path: str) -> str:
    message = (timestamp + "GET" + request_path).encode()
    return base64.b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode("ascii")


def headers(credentials: dict, request_path: str) -> dict:
    if credentials["exchange"] == "okx":
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return {"OK-ACCESS-KEY": credentials["api_key"], "OK-ACCESS-SIGN": signature(credentials["secret_key"], timestamp, request_path),
                "OK-ACCESS-TIMESTAMP": timestamp, "OK-ACCESS-PASSPHRASE": credentials["passphrase"], "x-simulated-trading": "1"}
    timestamp = str(time.time_ns() // 1_000_000)
    return {"ACCESS-KEY": credentials["api_key"], "ACCESS-SIGN": signature(credentials["secret_key"], timestamp, request_path),
            "ACCESS-TIMESTAMP": timestamp, "ACCESS-PASSPHRASE": credentials["passphrase"], "paptrading": "1", "locale": "en-US"}


def get(credentials: dict, path: str, params: dict | None = None) -> object:
    exchange = credentials["exchange"]
    if exchange == "okx":
        allowed, host, success = OKX_PATHS, OKX_HOSTS[credentials["site"]], "0"
    elif exchange == "bitget":
        allowed = BITGET_UTA_PATHS if credentials["account_mode"] == "uta" else BITGET_CLASSIC_PATHS
        host, success = BITGET_HOST, "00000"
    else:
        raise DemoAPIError("不支持的模拟交易所")
    if path not in allowed:
        raise DemoAPIError("官方模拟连接仅开放账户查询")
    request_path = path + ("?" + str(httpx.QueryParams(params)) if params else "")
    try:
        with httpx.Client(timeout=httpx.Timeout(12, connect=8), follow_redirects=False) as client:
            response = client.get(host + request_path, headers=headers(credentials, request_path))
            if response.status_code >= 300:
                status_code = response.status_code
                safe_code = str(status_code) if type(status_code) is int and 100 <= status_code <= 599 else "未知"
                raise DemoAPIError(f"官方模拟接口HTTP状态失败（代码 {safe_code}）", category="http_status", code=safe_code)
            if len(response.content) > 2_000_000:
                raise DemoAPIError("官方模拟响应超出读取上限", category="invalid_response")
            payload = response.json()
    except DemoAPIError:
        raise
    except httpx.HTTPError as exc:
        raise DemoAPIError("官方模拟连接失败，请检查网络和本机时间", category="network") from exc
    except ValueError as exc:
        raise DemoAPIError("官方模拟接口JSON格式无效", category="invalid_response") from exc
    if not isinstance(payload, dict):
        raise DemoAPIError("官方模拟接口响应格式无效", category="invalid_response")
    if str(payload.get("code")) != success:
        raw_code = str(payload.get("code", ""))
        code = raw_code if re.fullmatch(r"[0-9]{1,8}", raw_code) else "未知"
        category = "exchange_code" if code != "未知" else "exchange_error"
        raise DemoAPIError(f"官方模拟鉴权或权限检查失败（代码 {code}），请核对模拟盘专用密钥、账户类型和站点",
                           category=category, code=code if category == "exchange_code" else None)
    if "data" not in payload:
        raise DemoAPIError("官方模拟接口缺少账户数据", category="invalid_response")
    return payload["data"]
