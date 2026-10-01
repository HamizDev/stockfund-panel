"""Small, isolated USDT paper ledger for spot and one-way USD-M positions.

This is a research simulator: top-of-book fills and fixed example fees.
Funding and isolated liquidation use a disclosed flat maintenance model, not
Binance's account-specific maintenance tiers. It cannot place live orders.
"""
from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.services.fs_utils import atomic_write_text

_LOCK = threading.RLock()
INITIAL_USDT = Decimal("10000")
SPOT_FEE = Decimal("0.001")
FUTURES_FEE = Decimal("0.0005")
MAINTENANCE_RATE = Decimal("0.005")
LIQUIDATION_FEE = Decimal("0.005")
MAX_NOTIONAL = Decimal("100000")


def _num(value: object, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("数量或金额无效") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("数量或金额无效")
    return result


def _path(data_dir: Path) -> Path:
    return data_dir / "user_data" / "crypto_paper.json"


def _empty() -> dict:
    return {
        "schema": 1,
        "spot": {"cash": str(INITIAL_USDT), "positions": {}, "realized_pnl": "0"},
        "usdm": {"cash": str(INITIAL_USDT), "positions": {}, "realized_pnl": "0"},
        "trades": [],
    }


def load(data_dir: Path) -> dict:
    path = _path(data_dir)
    if not path.exists():
        return _empty()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("虚拟币模拟账本无法读取; 原文件已保留, 请先检查") from exc
    if (not isinstance(state, dict) or state.get("schema") != 1
            or not isinstance(state.get("trades"), list)
            or not all(isinstance(state.get(market), dict)
                       and isinstance(state[market].get("positions"), dict)
                       for market in ("spot", "usdm"))):
        raise ValueError("虚拟币模拟账本版本不受支持; 原文件已保留")
    return state


def _save(data_dir: Path, state: dict) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2))


def _validate_qty(qty: Decimal, quote: dict) -> None:
    step = _num(quote["step_size"], positive=True)
    minimum = _num(quote["min_qty"], positive=True)
    if qty < minimum or qty % step:
        raise ValueError(f"数量须至少 {minimum}, 且为 {step} 的整数倍")


def fee_rate(quote: dict, market: str) -> Decimal:
    if quote.get("exchange", "binance") == "bitget":
        rate = _num(quote.get("taker_fee_rate"))
        if not Decimal("0") <= rate <= Decimal("0.01"):
            raise ValueError("公开手续费率无效")
        return rate
    return SPOT_FEE if market == "spot" else FUTURES_FEE


def replay(data_dir: Path, *, market: str, symbol: str, action: str,
           quantity: object, leverage: int, request_id: str) -> tuple[dict, dict] | None:
    """Read back a committed request before any new external quote is needed."""
    with _LOCK:
        state = load(data_dir)
        old = next((item for item in state["trades"] if item.get("request_id") == request_id), None)
        if old is None:
            return None
        qty = _num(quantity, positive=True)
        if (any(old.get(k) != v for k, v in (("market", market), ("symbol", symbol),
                                             ("action", action), ("quantity", str(qty))))
                or (market == "usdm" and old.get("leverage") != leverage)):
            raise ValueError("请求标识已用于另一笔订单")
        return old, state


def value_account(state: dict, quotes: dict[tuple[str, str], dict]) -> dict:
    """Mark held assets using current bid (spot) and mark price (USD-M)."""
    spot = state["spot"]
    spot_value = sum(
        (_num(position["qty"]) * _num(quotes[("spot", symbol)]["bid"]))
        for symbol, position in spot["positions"].items()
    )
    futures = state["usdm"]
    futures_margin = sum((_num(position["margin"]) for position in futures["positions"].values()), Decimal("0"))
    futures_unrealized = sum((
        (_num(quotes[("usdm", symbol)]["mark"], positive=True) - _num(position["entry"], positive=True))
        * _num(position["qty"], positive=True)
        * (1 if position["side"] == "long" else -1)
        for symbol, position in futures["positions"].items()
    ), Decimal("0"))
    spot_equity = _num(spot["cash"]) + spot_value
    futures_equity = _num(futures["cash"]) + sum((max(Decimal("0"),
        _num(position["margin"]) +
        (_num(quotes[("usdm", symbol)]["mark"], positive=True) - _num(position["entry"], positive=True))
        * _num(position["qty"], positive=True) * (1 if position["side"] == "long" else -1))
        for symbol, position in futures["positions"].items()), Decimal("0"))
    return {
        "spot": {"equity": str(spot_equity), "holdings_value": str(spot_value),
                 "total_pnl": str(spot_equity - INITIAL_USDT)},
        "usdm": {"equity": str(futures_equity), "margin": str(futures_margin),
                 "unrealized_pnl": str(futures_unrealized),
                 "total_pnl": str(futures_equity - INITIAL_USDT)},
    }


def _trade_spot(wallet: dict, symbol: str, action: str, qty: Decimal, price: Decimal) -> dict:
    if action not in ("buy", "sell"):
        raise ValueError("现货方向无效")
    cash = _num(wallet["cash"])
    positions = wallet["positions"]
    current = positions.get(symbol, {"qty": "0", "cost": "0"})
    old_qty = _num(current["qty"])
    old_cost = _num(current["cost"])
    notional = qty * price
    fee = notional * SPOT_FEE
    realized = Decimal("0")
    if action == "buy":
        if notional + fee > cash:
            raise ValueError("现货虚拟余额不足")
        cash -= notional + fee
        positions[symbol] = {"qty": str(old_qty + qty), "cost": str(old_cost + notional + fee)}
    else:
        if qty > old_qty:
            raise ValueError("现货持仓不足")
        cost_out = old_cost * qty / old_qty
        realized = notional - fee - cost_out
        cash += notional - fee
        remain = old_qty - qty
        if remain:
            positions[symbol] = {"qty": str(remain), "cost": str(old_cost - cost_out)}
        else:
            positions.pop(symbol, None)
    wallet["cash"] = str(cash)
    wallet["realized_pnl"] = str(_num(wallet["realized_pnl"]) + realized)
    return {"fee": str(fee), "realized_pnl": str(realized)}


def _trade_usdm(wallet: dict, symbol: str, action: str, qty: Decimal,
                price: Decimal, leverage: int, rate: Decimal = FUTURES_FEE) -> dict:
    if action not in ("open_long", "open_short", "close_long", "close_short"):
        raise ValueError("合约方向无效")
    if isinstance(leverage, bool) or not isinstance(leverage, int) or not 1 <= leverage <= 20:
        raise ValueError("研究模拟仅支持 1 至 20 倍杠杆")
    cash = _num(wallet["cash"])
    positions = wallet["positions"]
    current = positions.get(symbol)
    fee = qty * price * rate
    realized = Decimal("0")
    if action.startswith("open_"):
        if current:
            raise ValueError("该合约已有持仓; 请先平仓")
        if qty * price > MAX_NOTIONAL:
            raise ValueError("单仓名义金额超过研究模型上限 100000 USDT")
        margin = qty * price / leverage
        if margin + fee > cash:
            raise ValueError("合约虚拟保证金不足")
        cash -= margin + fee
        realized = -fee
        positions[symbol] = {
            "side": action.removeprefix("open_"), "qty": str(qty),
            "entry": str(price), "margin": str(margin), "leverage": leverage,
        }
    else:
        side = action.removeprefix("close_")
        if not current or current["side"] != side:
            raise ValueError("没有可平的该方向持仓")
        old_qty = _num(current["qty"], positive=True)
        if qty > old_qty:
            raise ValueError("平仓数量超过持仓")
        entry = _num(current["entry"], positive=True)
        margin_out = _num(current["margin"]) * qty / old_qty
        gross = (price - entry) * qty * (1 if side == "long" else -1)
        # Isolated research positions cannot spend unrelated free wallet cash
        # when a quote gaps beyond their allocated collateral.
        proceeds = max(Decimal("0"), margin_out + gross - fee)
        realized = proceeds - margin_out
        cash += proceeds
        remain = old_qty - qty
        if remain:
            current["qty"] = str(remain)
            current["margin"] = str(_num(current["margin"]) - margin_out)
        else:
            positions.pop(symbol, None)
    wallet["cash"] = str(cash)
    wallet["realized_pnl"] = str(_num(wallet["realized_pnl"]) + realized)
    return {"fee": str(fee), "realized_pnl": str(realized)}


def execute(state: dict, *, market: str, symbol: str, action: str,
            quantity: object, leverage: int, quote: dict, request_id: str,
            at_ms: int | None = None, slippage_bps: int = 0) -> dict:
    """Mutate an isolated candidate ledger; the caller owns locking/commit."""
    if market not in ("spot", "usdm") or symbol not in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        raise ValueError("不支持的市场或交易对")
    if quote.get("market") != market or quote.get("symbol") != symbol:
        raise ValueError("行情与订单不匹配")
    exchange = state.get("exchange", "binance")
    if exchange not in ("binance", "bitget") or quote.get("exchange", "binance") != exchange:
        raise ValueError("行情源与模拟账本不匹配")
    if exchange == "bitget" and market != "usdm":
        raise ValueError("Bitget 模拟目前仅支持 USDT 本位合约")
    rate = fee_rate(quote, market)
    if exchange == "bitget" and leverage > _num(quote.get("max_leverage"), positive=True):
        raise ValueError("杠杆超过交易对上限")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80 or not all(
        c.isalnum() or c in "_-" for c in request_id
    ):
        raise ValueError("请求标识无效")
    qty = _num(quantity, positive=True)
    _validate_qty(qty, quote)
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not 0 <= slippage_bps <= 100:
        raise ValueError("滑点参数无效")
    old = next((item for item in state["trades"] if item.get("request_id") == request_id), None)
    if old:
        if (any(old.get(k) != v for k, v in (("market", market), ("symbol", symbol),
                                           ("action", action), ("quantity", str(qty))))
                or (market == "usdm" and old.get("leverage") != leverage)):
            raise ValueError("请求标识已用于另一笔订单")
        return old
    buying = action in ("buy", "open_long", "close_short")
    price = _num(quote["ask" if buying else "bid"], positive=True)
    price *= Decimal("1") + Decimal(slippage_bps) / 10000 * (1 if buying else -1)
    minimum = _num(quote["min_notional"])
    maximum = _num(quote.get("max_qty") or "1e30", positive=True)
    if qty > maximum:
        raise ValueError("数量超过交易对模拟上限")
    if qty * price < minimum and not (market == "usdm" and action.startswith("close_")):
        raise ValueError(f"订单名义金额低于 {minimum} USDT")
    wallet = state[market]
    result = (_trade_spot(wallet, symbol, action, qty, price) if market == "spot"
              else _trade_usdm(wallet, symbol, action, qty, price, leverage, rate))
    now_ms = at_ms if at_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
    if market == "usdm" and action.startswith("open_"):
        wallet["positions"][symbol].update(opened_ms=now_ms, funding_cursor_ms=now_ms)
    record = {
        "id": f"cp_{uuid.uuid4().hex[:16]}", "request_id": request_id,
        "kind": "trade", "at": datetime.fromtimestamp(now_ms / 1000, UTC).isoformat(),
        "market": market, "symbol": symbol, "exchange": exchange,
        "action": action, "quantity": str(qty), "fee_rate": str(rate),
        "price": str(price), "leverage": leverage if market == "usdm" else None,
        **result,
    }
    state["trades"].append(record)
    return record


def trade(data_dir: Path, *, market: str, symbol: str, action: str,
          quantity: object, leverage: int, quote: dict, request_id: str) -> tuple[dict, dict]:
    with _LOCK:
        state = copy.deepcopy(load(data_dir))
        record = execute(state, market=market, symbol=symbol, action=action,
                         quantity=quantity, leverage=leverage, quote=quote, request_id=request_id)
        _save(data_dir, state)
        return record, state


def liquidate(state: dict, symbol: str, mark: object, at_ms: int) -> bool:
    """Flat-rate isolated liquidation, only at observed mark prices."""
    wallet = state["usdm"]
    position = wallet["positions"].get(symbol)
    if not position:
        return False
    price = _num(mark, positive=True)
    qty = _num(position["qty"], positive=True)
    margin = _num(position["margin"])
    gross = (price - _num(position["entry"], positive=True)) * qty * (1 if position["side"] == "long" else -1)
    collateral = margin + gross
    if collateral > qty * price * (MAINTENANCE_RATE + LIQUIDATION_FEE):
        return False
    fee = min(max(collateral, Decimal("0")), qty * price * LIQUIDATION_FEE)
    proceeds = max(Decimal("0"), collateral - fee)
    realized = proceeds - margin
    wallet["cash"] = str(_num(wallet["cash"]) + proceeds)
    wallet["realized_pnl"] = str(_num(wallet["realized_pnl"]) + realized)
    wallet["positions"].pop(symbol)
    state["trades"].append({
        "id": f"cp_{uuid.uuid4().hex[:16]}", "kind": "liquidation",
        "at": datetime.fromtimestamp(at_ms / 1000, UTC).isoformat(),
        "market": "usdm", "symbol": symbol, "action": "liquidation",
        "quantity": str(qty), "price": str(price), "fee": str(fee),
        "realized_pnl": str(realized), "leverage": position["leverage"],
    })
    return True


def settle_funding(state: dict, symbol: str, events: list[dict], through_ms: int) -> None:
    """Charge actual published funding events once against isolated margin."""
    wallet = state["usdm"]
    position = wallet["positions"].get(symbol)
    if not position:
        return
    opened = position.get("opened_ms")
    if opened is None:
        entry = next((item for item in reversed(state["trades"])
                      if item.get("market") == "usdm" and item.get("symbol") == symbol
                      and item.get("action") in ("open_long", "open_short")), None)
        if not entry:
            raise ValueError("旧合约缺少开仓时间; 无法核对资金费")
        opened = int(datetime.fromisoformat(entry["at"]).timestamp() * 1000)
        position["opened_ms"] = opened
    cursor = position.get("funding_cursor_ms", opened)
    previous = -1
    for event in events:
        stamp = event["funding_time_ms"]
        if not isinstance(stamp, int) or stamp <= previous:
            raise ValueError("资金费事件时间无效")
        previous = stamp
        if not cursor < stamp <= through_ms:
            continue
        rate = _num(event["rate"])
        price = _num(event["mark_price"], positive=True)
        # Historical observations are not an intrabar reconstruction. If the
        # observed mark already liquidates this isolated position, settle that
        # exit first rather than charging a wallet that no longer has a position.
        if liquidate(state, symbol, price, stamp):
            return
        qty = _num(position["qty"], positive=True)
        side = 1 if position["side"] == "long" else -1
        expected = qty * price * rate * side
        collateral = _num(position["margin"]) + qty * (price - _num(position["entry"])) * side
        amount = min(expected, max(Decimal("0"), collateral)) if expected > 0 else expected
        position["margin"] = str(_num(position["margin"]) - amount)
        wallet["realized_pnl"] = str(_num(wallet["realized_pnl"]) - amount)
        state["trades"].append({
            "id": f"cp_{uuid.uuid4().hex[:16]}", "kind": "funding",
            "at": datetime.fromtimestamp(stamp / 1000, UTC).isoformat(),
            "market": "usdm", "symbol": symbol, "action": "funding",
            "quantity": position["qty"], "price": str(price), "fee": "0",
            "funding_amount": str(amount), "realized_pnl": str(-amount),
            "scheduled_funding_amount": str(expected),
            "leverage": position["leverage"],
            "exchange": state.get("exchange", "binance"),
            "mark_price_basis": event.get("mark_price_basis", "published_settlement_mark"),
        })
        position["funding_cursor_ms"] = stamp
        if liquidate(state, symbol, price, stamp):
            return
    position["funding_cursor_ms"] = through_ms
