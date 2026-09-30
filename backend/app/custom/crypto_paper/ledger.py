"""Small, isolated USDT paper ledger for spot and one-way USD-M positions.

This is a research simulator: top-of-book fills, fixed example fees, no funding
settlement and no exchange-accurate liquidation. It cannot place live orders.
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
    futures_equity = _num(futures["cash"]) + futures_margin + futures_unrealized
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
                price: Decimal, leverage: int) -> dict:
    if action not in ("open_long", "open_short", "close_long", "close_short"):
        raise ValueError("合约方向无效")
    if isinstance(leverage, bool) or not isinstance(leverage, int) or not 1 <= leverage <= 5:
        raise ValueError("研究模拟仅支持 1 至 5 倍杠杆")
    cash = _num(wallet["cash"])
    positions = wallet["positions"]
    current = positions.get(symbol)
    fee = qty * price * FUTURES_FEE
    realized = Decimal("0")
    if action.startswith("open_"):
        if current:
            raise ValueError("该合约已有持仓; 请先平仓")
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
        realized = gross - fee
        cash += margin_out + realized
        remain = old_qty - qty
        if remain:
            current["qty"] = str(remain)
            current["margin"] = str(_num(current["margin"]) - margin_out)
        else:
            positions.pop(symbol, None)
    wallet["cash"] = str(cash)
    wallet["realized_pnl"] = str(_num(wallet["realized_pnl"]) + realized)
    return {"fee": str(fee), "realized_pnl": str(realized)}


def trade(data_dir: Path, *, market: str, symbol: str, action: str,
          quantity: object, leverage: int, quote: dict, request_id: str) -> tuple[dict, dict]:
    if market not in ("spot", "usdm") or symbol not in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        raise ValueError("不支持的市场或交易对")
    if quote.get("market") != market or quote.get("symbol") != symbol:
        raise ValueError("行情与订单不匹配")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80 or not all(
        c.isalnum() or c in "_-" for c in request_id
    ):
        raise ValueError("请求标识无效")
    qty = _num(quantity, positive=True)
    _validate_qty(qty, quote)
    with _LOCK:
        current = load(data_dir)
        old = next((item for item in current["trades"] if item.get("request_id") == request_id), None)
        if old:
            if (any(old[k] != v for k, v in (("market", market), ("symbol", symbol),
                                             ("action", action), ("quantity", str(qty))))
                    or (market == "usdm" and old.get("leverage") != leverage)):
                raise ValueError("请求标识已用于另一笔订单")
            return old, current
        # Work on a copy so a rejected order never mutates the in-memory state.
        state = copy.deepcopy(current)
        price_key = "ask" if action in ("buy", "open_long", "close_short") else "bid"
        price = _num(quote[price_key], positive=True)
        minimum = _num(quote["min_notional"])
        if qty * price < minimum:
            raise ValueError(f"订单名义金额低于 {minimum} USDT")
        wallet = state[market]
        result = (_trade_spot(wallet, symbol, action, qty, price) if market == "spot"
                  else _trade_usdm(wallet, symbol, action, qty, price, leverage))
        record = {
            "id": f"cp_{uuid.uuid4().hex[:16]}", "request_id": request_id,
            "at": datetime.now(UTC).isoformat(), "market": market,
            "symbol": symbol, "action": action, "quantity": str(qty),
            "price": str(price), "leverage": leverage if market == "usdm" else None,
            **result,
        }
        state["trades"].append(record)
        _save(data_dir, state)
        return record, state
