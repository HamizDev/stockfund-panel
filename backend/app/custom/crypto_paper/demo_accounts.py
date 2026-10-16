# ruff: noqa: RUF001 -- localized Chinese UI punctuation.
"""Normalize read-only official demo snapshots without merging local paper ledgers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from .demo_client import DemoAPIError, get


def number(value) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise InvalidOperation
        return str(parsed)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DemoAPIError("官方账户数值格式无效，未更新账户快照") from exc


def text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int)):
        raise DemoAPIError("官方账户字段格式无效")
    return str(value)[:160]


def timestamp(value) -> int | None:
    parsed = number(value)
    if parsed is None:
        return None
    value = int(Decimal(parsed))
    if value <= 0:
        return None
    return value


def rows(value, key=None) -> list[dict]:
    if key is not None and isinstance(value, dict):
        value = value.get(key)
    if not isinstance(value, list) or len(value) > 1000 or any(not isinstance(r, dict) for r in value):
        raise DemoAPIError("官方账户列表格式无效，未更新账户快照")
    return value


def _okx(payloads: list) -> dict:
    account_rows = rows(payloads[0])
    if len(account_rows) != 1:
        raise DemoAPIError("官方模拟余额响应缺少账户总览")
    account = account_rows[0]
    balances = [{"currency": text(r.get("ccy")), "equity": number(r.get("eq")),
                 "available": number(r.get("availBal")), "unrealized_pnl": number(r.get("upl"))}
                for r in rows(account.get("details"))]
    positions = []
    for r in rows(payloads[1]):
        quantity = number(r.get("pos"))
        if quantity is None:
            raise DemoAPIError("官方持仓缺少数量")
        if Decimal(quantity) == 0:
            continue
        side = text(r.get("posSide"))
        if side == "net":
            side = "short" if Decimal(quantity) < 0 else "long"
        positions.append({"symbol": text(r.get("instId")), "side": side, "quantity": str(abs(Decimal(quantity))),
                          "quantity_unit": "张", "entry_price": number(r.get("avgPx")), "mark_price": number(r.get("markPx")),
                          "unrealized_pnl": number(r.get("upl")), "leverage": number(r.get("lever")), "margin_mode": text(r.get("mgnMode"))})
    orders = [{"order_id": text(r.get("ordId")), "symbol": text(r.get("instId")), "side": text(r.get("side")),
               "order_type": text(r.get("ordType")), "quantity": number(r.get("sz")), "quantity_unit": "张",
               "price": number(r.get("px")), "filled_quantity": number(r.get("accFillSz")),
               "status": text(r.get("state")), "created_at_ms": timestamp(r.get("cTime"))} for r in rows(payloads[2])]
    fills = [{"fill_id": text(r.get("tradeId")), "order_id": text(r.get("ordId")), "symbol": text(r.get("instId")),
              "side": text(r.get("side")), "price": number(r.get("fillPx")), "quantity": number(r.get("fillSz")),
              "quantity_unit": "张", "fee": number(r.get("fee")), "fee_currency": text(r.get("feeCcy")),
              "ts_ms": timestamp(r.get("ts"))} for r in rows(payloads[3])]
    return {"total_equity": number(account.get("totalEq")), "equity_ccy": "USD", "balances": balances,
            "positions": positions, "orders": orders, "fills": fills}


def _fee(detail, field: str = "fee") -> tuple[str | None, str]:
    if detail is None:
        return None, ""
    items = rows(detail)
    currencies = {text(r.get("feeCoin")) for r in items}
    if not items:
        return None, ""
    if len(currencies) != 1:
        return None, "多币种费用，请在交易所查看"
    values = [number(r.get(field)) for r in items]
    if any(v is None for v in values):
        return None, next(iter(currencies))
    return str(sum((Decimal(v) for v in values), Decimal(0))), next(iter(currencies))


def _bitget_uta(payloads: list) -> dict:
    account = payloads[0]
    if not isinstance(account, dict):
        raise DemoAPIError("官方模拟余额响应缺少账户总览")
    balances = [{"currency": text(r.get("coin")), "equity": number(r.get("equity")),
                 "available": number(r.get("available")), "unrealized_pnl": None}
                for r in rows(account.get("assets"))]
    positions = []
    for r in rows(payloads[1], "list"):
        quantity = number(r.get("total"))
        if quantity is None:
            raise DemoAPIError("官方持仓缺少数量")
        if Decimal(quantity) == 0:
            continue
        positions.append({"symbol": text(r.get("symbol")), "side": text(r.get("posSide")), "quantity": quantity,
                          "quantity_unit": "交易所原始数量", "entry_price": number(r.get("avgPrice")),
                          "mark_price": number(r.get("markPrice")), "unrealized_pnl": number(r.get("unrealisedPnl")),
                          "leverage": number(r.get("leverage")), "margin_mode": text(r.get("marginMode"))})
    orders = [{"order_id": text(r.get("orderId")), "symbol": text(r.get("symbol")), "side": text(r.get("side")),
               "order_type": text(r.get("orderType")), "quantity": number(r.get("qty")),
               "quantity_unit": text(r.get("symbol")).removesuffix("USDT"), "price": number(r.get("price")),
               "filled_quantity": number(r.get("cumExecQty")), "status": text(r.get("orderStatus")),
               "created_at_ms": timestamp(r.get("createdTime"))} for r in rows(payloads[2], "list")]
    fills = []
    for r in rows(payloads[3], "list"):
        fee, currency = _fee(r.get("feeDetail"))
        fills.append({"fill_id": text(r.get("execId")), "order_id": text(r.get("orderId")), "symbol": text(r.get("symbol")),
                      "side": text(r.get("side")), "price": number(r.get("execPrice")), "quantity": number(r.get("execQty")),
                      "quantity_unit": text(r.get("symbol")).removesuffix("USDT"), "fee": fee, "fee_currency": currency,
                      "ts_ms": timestamp(r.get("createdTime"))})
    return {"total_equity": number(account.get("accountEquity")), "equity_ccy": "USD", "balances": balances,
            "positions": positions, "orders": orders, "fills": fills}


def _bitget(payloads: list, mode: str) -> dict:
    if mode == "uta":
        return _bitget_uta(payloads)
    balances = [{"currency": text(r.get("marginCoin")), "equity": number(r.get("accountEquity")),
                 "available": number(r.get("available")), "unrealized_pnl": number(r.get("unrealizedPL"))}
                for r in rows(payloads[0])]
    usd = next((r for r in balances if r["currency"] == "USDT"), None)
    positions = []
    for r in rows(payloads[1]):
        quantity = number(r.get("total"))
        if quantity is None:
            raise DemoAPIError("官方持仓缺少数量")
        if Decimal(quantity) == 0:
            continue
        symbol = text(r.get("symbol"))
        positions.append({"symbol": symbol, "side": text(r.get("holdSide")), "quantity": quantity,
                          "quantity_unit": symbol.removesuffix("USDT"), "entry_price": number(r.get("openPriceAvg")),
                          "mark_price": number(r.get("markPrice")), "unrealized_pnl": number(r.get("unrealizedPL")),
                          "leverage": number(r.get("leverage")), "margin_mode": text(r.get("marginMode"))})
    orders = [{"order_id": text(r.get("orderId")), "symbol": text(r.get("symbol")), "side": text(r.get("side")),
               "order_type": text(r.get("orderType")), "quantity": number(r.get("size")),
               "quantity_unit": text(r.get("symbol")).removesuffix("USDT"), "price": number(r.get("price")),
               "filled_quantity": number(r.get("baseVolume")), "status": text(r.get("status")),
               "created_at_ms": timestamp(r.get("cTime"))} for r in rows(payloads[2], "entrustedList")]
    fills = []
    for r in rows(payloads[3], "fillList"):
        fee, currency = _fee(r.get("feeDetail"), "totalFee")
        fills.append({"fill_id": text(r.get("tradeId")), "order_id": text(r.get("orderId")), "symbol": text(r.get("symbol")),
                      "side": text(r.get("side")), "price": number(r.get("price")), "quantity": number(r.get("baseVolume")),
                      "quantity_unit": text(r.get("symbol")).removesuffix("USDT"), "fee": fee, "fee_currency": currency,
                      "ts_ms": timestamp(r.get("cTime"))})
    return {"total_equity": usd["equity"] if usd else None, "equity_ccy": "USDT", "balances": balances,
            "positions": positions, "orders": orders, "fills": fills}


def snapshot(credentials: dict) -> dict:
    exchange, mode = credentials["exchange"], credentials["account_mode"]
    if exchange == "okx":
        endpoints = [("/api/v5/account/balance", {}), ("/api/v5/account/positions", {"instType": "SWAP"}),
                     ("/api/v5/trade/orders-pending", {"instType": "SWAP", "limit": "100"}),
                     ("/api/v5/trade/fills", {"instType": "SWAP", "limit": "20"})]
    elif mode == "classic":
        p = {"productType": "USDT-FUTURES"}
        endpoints = [("/api/v2/mix/account/accounts", p), ("/api/v2/mix/position/all-position", p),
                     ("/api/v2/mix/order/orders-pending", {**p, "limit": "100"}),
                     ("/api/v2/mix/order/fills", {**p, "limit": "20"})]
    else:
        p = {"category": "USDT-FUTURES"}
        endpoints = [("/api/v3/account/assets", {}), ("/api/v3/position/current-position", p),
                     ("/api/v3/trade/unfilled-orders", {**p, "limit": "100"}),
                     ("/api/v3/trade/fills", {**p, "limit": "20"})]
    with ThreadPoolExecutor(max_workers=4) as pool:
        payloads = list(pool.map(lambda pair: get(credentials, *pair), endpoints))
    normalized = _okx(payloads) if exchange == "okx" else _bitget(payloads, mode)
    return {"exchange": exchange, "site": credentials["site"], "account_mode": mode,
            "environment": "demo", "readonly": True, "observed_at": datetime.now(UTC).isoformat(),
            "stale": False, **normalized}
