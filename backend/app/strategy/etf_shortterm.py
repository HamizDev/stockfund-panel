"""Forward-only ETF paper rules. Orders/fills always use the existing paper ledger.

Minute close times are treated conservatively: ignore the current and preceding
wall-clock minute. This also accommodates providers that label the bar's start.
No historical signals are replayed into accounts, and no broker is connected.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

import polars as pl

from app.market_time import CN_TZ, in_continuous_session
from app.services.fs_utils import atomic_write_text
from app.strategy import paper
from app.strategy.etf_presets import POOL

MODES = {
    "t0_reversion": {
        "name": "ETF T+0 日内回归",
        "description": "仅已核对T+0品种；30根完整分钟的量加权均价偏离、回升确认和费用门槛，14:45后尝试退出。",
        "symbols": [s for s, _, _, t0, _ in POOL if t0],
    },
    "base_t": {
        "name": "股票ETF底仓做T",
        "description": "先按前日趋势建立真实模拟底仓；可卖后只卖部分，再低价回补。新买份额遵守T+1，不做空。",
        "symbols": [s for s, _, _, t0, _ in POOL if not t0],
    },
}
MAX_POSITIONS = {5000: 2, 10000: 2, 20000: 3, 100000: 3}
RULES = [
    "候选池人工预定，是否交易由数据和规则决定；不保证每天成交或盈利。",
    "总持仓成本最多80%初始本金，每只预算按最多持仓数分配；保留现金，不使用杠杆。",
    "佣金万5、每笔最低5元、滑点5bps；预计回归幅度须覆盖双边费用与额外0.2%缓冲。",
    "每只每天最多一次新波段/做T；股票ETF先有隔夜可卖底仓，保留至少100份。",
    "仅当日完整闭合分钟与推进的新鲜来源行情；缺口、过期、涨跌停、资金不足均等待或拒单。",
    "订单只在后续有效报价模拟；90秒未成交过期，盘后不得用收盘价补造日内成交。",
    "T+0尾盘退出、做T尾盘回补均只是尝试，断网或价格变化可能保留隔夜持仓/减仓。",
]


def plan() -> dict:
    return {"budgets": list(MAX_POSITIONS), "commission_pct": .0005,
            "min_commission": 5, "slippage_bps": 5,
            "modes": [{"id": key, **value} for key, value in MODES.items()],
            "max_positions_by_budget": MAX_POSITIONS, "rules": RULES}


def create_account(data_dir: Path, mode: str, initial_cash: int, max_positions: int) -> tuple[dict, bool]:
    if mode not in MODES or initial_cash not in MAX_POSITIONS:
        raise ValueError("短线模式或本金不支持")
    if not 1 <= max_positions <= MAX_POSITIONS[initial_cash]:
        raise ValueError("最多持仓数超出这档本金的范围")
    aid = f"etf_{'t0' if mode == 't0_reversion' else 'bt'}_{initial_cash}_{max_positions}"
    with paper.PAPER_LOCK:
        old = paper.get_account(data_dir, aid)
        if old:
            cfg = old.get("etf_shortterm") or {}
            expected = {"initial_cash": initial_cash, "asset_type": "etf", "strategy_id": f"etf_shortterm_{mode}",
                        "commission_pct": .0005, "stamp_tax_pct": 0, "slippage_bps": 5}
            if ((cfg.get("mode"), cfg.get("max_positions")) != (mode, max_positions)
                    or any(old.get(k) != v for k, v in expected.items())):
                raise ValueError("同名账户配置不一致，请核对")
            return old, False
        acc = paper.create_account(data_dir, initial_cash, account_id=aid,
                                   name=f"{MODES[mode]['name']} · {initial_cash:,} · 最多{max_positions}只",
                                   commission_pct=.0005, stamp_tax_pct=0, slippage_bps=5,
                                   asset_type="etf", strategy_id=f"etf_shortterm_{mode}")
        acc["etf_shortterm"] = {"mode": mode, "max_positions": max_positions, "enabled": False}
        paper.save_account(data_dir, acc, aid)
        return acc, True


def state_path(data_dir: Path, aid: str) -> Path:
    paper.validate_account_id(aid)
    return data_dir / "paper" / "accounts" / aid / "shortterm_state.json"


def read_state(data_dir: Path, aid: str) -> dict:
    path = state_path(data_dir, aid)
    if not path.exists():
        return {"symbols": {}, "t_rounds": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("symbols"), dict) or not isinstance(state.get("t_rounds", []), list):
            raise ValueError("Invalid short-term state shape")
        return state
    except (OSError, UnicodeError, ValueError):
        # Preserve the corrupt projection. Rebuilding trading cycles requires a
        # separate ledger recovery; never treat unreadable state as an empty book.
        return {"symbols": {}, "t_rounds": [], "state_error": True,
                "status": "状态文件无法读取 · 本账户停止模拟 · 原文件和账本保留"}


def save_state(data_dir: Path, aid: str, state: dict) -> None:
    if state.get("state_error"):
        raise ValueError("Refuse to overwrite an unreadable short-term state")
    atomic_write_text(state_path(data_dir, aid), json.dumps(state, ensure_ascii=False, indent=2))


def buy_guard(data_dir: Path, acc: dict, symbol: str, qty: int, raw_price: float,
              *, exclude_order: str | None = None) -> str | None:
    """Shared order creation/fill cap; same-symbol replenishment is allowed."""
    cfg = acc["etf_shortterm"]
    if symbol not in MODES[cfg["mode"]]["symbols"]:
        return "标的不在此短线模式已核对的候选池"
    positions = paper.load_positions(data_dir, acc["id"])
    held = {s: p for s, p in positions.items() if p.get("qty", 0) > 0}
    pending = [o for o in paper.load_orders(data_dir, acc["id"])
               if o["status"] == "pending" and o["side"] == "buy" and o["id"] != exclude_order]
    occupied = set(held) | {o["symbol"] for o in pending}
    if symbol not in occupied and len(occupied) >= cfg["max_positions"]:
        return "短线账户已达最多持仓数（含待买单）"
    if any(not o.get("ref_price") for o in pending):
        return "待买单缺少参考价，资金待核对"
    def cost(n, p):
        price = paper.apply_slippage(p, "buy", acc["slippage_bps"])
        return n * price + paper.buy_fee(n, price, acc["commission_pct"])
    new = cost(qty, raw_price)
    reserved = [(o["symbol"], cost(o["qty"], o["ref_price"])) for o in pending]
    total = sum(p["qty"] * p["avg_cost"] for p in held.values()) + sum(v for _, v in reserved) + new
    cap = acc["initial_cash"] * .8
    if total > cap + 1e-6:
        return "买入后持仓成本和待买预算超过80%本金"
    symbol_cost = held.get(symbol, {}).get("qty", 0) * held.get(symbol, {}).get("avg_cost", 0)
    symbol_cost += sum(v for s, v in reserved if s == symbol) + new
    if symbol_cost > cap / cfg["max_positions"] + 1e-6:
        return "买入后超过单只ETF预算"
    if new + sum(v for _, v in reserved) > acc["cash"] + 1e-6:
        return "可用现金已被待买单占用"
    return None


def features(frame: pl.DataFrame, symbol: str, now: datetime) -> tuple[dict | None, str]:
    required = {"symbol", "datetime", "open", "high", "low", "close", "volume", "amount"}
    if not required.issubset(frame.columns):
        return None, "等待当日原始分钟数据"
    boundary = now.replace(tzinfo=None, second=0, microsecond=0) - timedelta(minutes=1)
    rows = frame.filter((pl.col("symbol") == symbol) &
                        (pl.col("datetime").dt.date() == now.date()) &
                        (pl.col("datetime") < boundary)).unique("datetime").sort("datetime").tail(30).to_dicts()
    if len(rows) < 30:
        return None, "等待30根闭合分钟"
    def ordinal(dt):
        minutes = dt.hour * 60 + dt.minute
        if 571 <= minutes <= 690:
            return minutes - 571
        if 781 <= minutes <= 900:
            return minutes - 781 + 120
        return None
    ticks = [ordinal(r["datetime"]) for r in rows]
    if any(t is None for t in ticks) or any(b != a + 1 for a, b in pairwise(ticks)):
        return None, "分钟存在缺口或时标不支持"
    if (now.replace(tzinfo=None) - rows[-1]["datetime"]).total_seconds() > 240:
        return None, "闭合分钟过期"
    for row in rows:
        vals = [row[k] for k in ("open", "high", "low", "close", "volume", "amount")]
        if any(v is None or not math.isfinite(v) for v in vals):
            return None, "分钟字段缺失"
        if min(vals[:4]) <= 0 or min(vals[4:]) < 0 or row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
            return None, "分钟价格待核对"
    volume = sum(r["volume"] for r in rows)
    if volume <= 0 or sum(r["amount"] for r in rows) < 2_000_000:
        return None, "近30分钟成交金额不足200万元"
    mean = sum((r["high"] + r["low"] + r["close"]) / 3 * r["volume"] for r in rows) / volume
    last = rows[-1]
    return {"mean": mean, "close": last["close"], "previous": rows[-2]["close"],
            "range": (max(r["high"] for r in rows) - min(r["low"] for r in rows)) / mean,
            "ts": last["datetime"].isoformat()}, "数据就绪"


def cost_floor(acc: dict, qty: int, price: float) -> float:
    gross = qty * price
    if gross <= 0:
        return math.inf
    return 2 * max(5, gross * acc["commission_pct"]) / gross + 2 * acc["slippage_bps"] / 10_000 + .002


def _qty(acc: dict, price: float) -> int:
    budget = min(acc["cash"], acc["initial_cash"] * .8 / acc["etf_shortterm"]["max_positions"])
    qty = int(budget / price / 100) * 100
    while qty > 0:
        slipped = paper.apply_slippage(price, "buy", acc["slippage_bps"])
        if qty * slipped + paper.buy_fee(qty, slipped, acc["commission_pct"]) <= budget:
            return qty
        qty -= 100
    return 0


def _observe_reason(item: dict, reason: str, now: datetime) -> None:
    """Clear metrics when their inputs are unavailable; timestamp the actual evaluation."""
    item.update(reason=reason, checked_at=now.replace(second=0, microsecond=0).isoformat())
    item.pop("diagnostic", None)


def _diagnostic(acc: dict, qty: int, price: float, feature: dict,
                held: int, available: int) -> dict:
    floor = cost_floor(acc, qty, price)
    return {"quote_price": price, "mean_30m": feature["mean"], "range_ratio": feature["range"],
            "reversion_ratio": (feature["mean"] - price) / price,
            "spread_ratio": (price - feature["mean"]) / feature["mean"],
            "required_ratio": floor if math.isfinite(floor) else None,
            "roundtrip_fee": 2 * max(5, qty * price * acc["commission_pct"]) if qty > 0 else None,
            "slippage_ratio": 2 * acc["slippage_bps"] / 10_000, "buffer_ratio": .002,
            "qty": qty, "held_qty": held, "available_qty": available, "bar_time": feature["ts"]}


def _reconcile(state: dict, orders: dict, now: datetime) -> None:
    # State is a projection. Rebuild inventory cycles from durable orders after a
    # crash between order/fill/state writes; never sell the remaining base twice.
    cycles, rounds = {}, []
    for item in state["symbols"].values():
        item.pop("cycle", None)
    for order in sorted(orders.values(), key=paper._order_sort_key):
        if not order.get("source", "").startswith("etf_shortterm:") or order["status"] != "filled":
            continue
        action = order["source"].split(":", 1)[1]
        symbol = order["symbol"]
        item = state["symbols"].setdefault(symbol, {})
        day = datetime.fromisoformat(order["filled_at"]).astimezone(CN_TZ).date().isoformat()
        if action == "t_sell":
            cycles[symbol] = {"sell_order": order["id"], "qty": order["qty"],
                              "price": order["fill_price"], "fee": order["fees"], "day": day}
        elif action == "t_buy" and symbol in cycles and order.get("cycle_sell_order") == cycles[symbol]["sell_order"]:
            cycle = cycles.pop(symbol)
            if cycle["qty"] == order["qty"]:
                rounds.append({"symbol": symbol, "sell_order": cycle["sell_order"], "buy_order": order["id"],
                               "date": day, "net_spread": round(cycle["qty"] * (cycle["price"] - order["fill_price"])
                                                                - cycle["fee"] - order["fees"], 2)})
                item["traded_day"] = day
        elif action == "t0_exit":
            item["traded_day"] = day
        if item.get("order_id") == order["id"]:
            item["action"] = None
    for symbol, cycle in cycles.items():
        state["symbols"][symbol]["cycle"] = cycle
    state["t_rounds"] = rounds


def run_account(data_dir: Path, aid: str, quotes: dict, minute: pl.DataFrame,
                daily: dict, now: datetime) -> list[dict]:
    """One live observation. Persistent decisions suppress restart/quote duplicates."""
    with paper.PAPER_LOCK:
        acc = paper.get_account(data_dir, aid)
        if not acc or not acc.get("etf_shortterm"):
            return []
        state = read_state(data_dir, aid)
        if state.get("state_error"):
            return []
        events = paper.evaluate_etf_intraday(data_dir, quotes, now, aid)
        acc = paper.get_account(data_dir, aid)
        previous_state = json.dumps(state, sort_keys=True)
        orders = {o["id"]: o for o in paper.load_orders(data_dir, aid)}
        _reconcile(state, orders, now)
        cfg = acc["etf_shortterm"]
        state["last_check"] = now.replace(second=0, microsecond=0).isoformat()
        if not cfg["enabled"] or acc["status"] != "active" or not in_continuous_session(now):
            state["status"] = acc.get("ledger_error") or ("暂停新信号" if not cfg["enabled"]
                                                       else "账户已冻结" if acc["status"] != "active" else "等待交易时段")
            if json.dumps(state, sort_keys=True) != previous_state:
                save_state(data_dir, aid, state)
            return events
        positions = paper.load_positions(data_dir, aid)
        acc = paper.get_account(data_dir, aid)  # cash after fills
        for symbol in MODES[cfg["mode"]]["symbols"]:
            item = state["symbols"].setdefault(symbol, {})
            quote = quotes.get(symbol) or {}
            try:
                ts, price = float(quote["timestamp"]), float(quote["last_price"])
            except (KeyError, TypeError, ValueError):
                _observe_reason(item, "等待带来源时间的行情", now)
                continue
            age = now.timestamp() * 1000 - ts
            if not math.isfinite(price) or price <= 0 or not 0 <= age <= 90_000:
                _observe_reason(item, "实时行情过期或日期待核对", now)
                continue
            pos = positions.get(symbol, {})
            held = int(pos.get("qty", 0))
            avail = paper._available_of(pos, now.date().isoformat()) if held else 0
            if any(o["status"] == "pending" and o["symbol"] == symbol for o in orders.values()):
                _observe_reason(item, "等待后续有效行情撮合", now)
                continue
            feature, reason = features(minute, symbol, now)
            late = now.hour * 60 + now.minute >= 14 * 60 + 45
            action, side, qty = None, None, 0
            cycle = item.get("cycle")
            # Risk/time exits use a fresh quote even when minute retrieval failed.
            if cfg["mode"] == "t0_reversion" and held and avail:
                target_met = feature and price >= feature["mean"] and price >= pos["avg_cost"] * (1 + cost_floor(acc, held, pos["avg_cost"]) - .002)
                if late or price <= pos["avg_cost"] * .992 or target_met:
                    action, side, qty = "t0_exit", "sell", avail // 100 * 100
            elif cfg["mode"] == "base_t" and cycle and (late or (cycle["price"] - price) / price >= cost_floor(acc, cycle["qty"], price)):
                # A sold inventory leg can survive restart/date change. Replenish at
                # a cheaper quote or after 14:45; losses are recorded, never hidden.
                action, side, qty = "t_buy", "buy", cycle["qty"]
            if not action:
                if not feature:
                    _observe_reason(item, reason, now)
                    continue
                if item.get("bar") == feature["ts"]:
                    continue
                item["bar"] = feature["ts"]
                item["checked_at"] = now.replace(second=0, microsecond=0).isoformat()
                item.pop("diagnostic", None)
                if late or item.get("traded_day") == now.date().isoformat():
                    item["reason"] = "今日已完成波段或停止新开仓"
                    continue
                qty = _qty(acc, price) if not held else min(avail // 200 * 100, max(0, held - 100))
                floor = cost_floor(acc, qty, price)
                item["diagnostic"] = _diagnostic(acc, qty, price, feature, held, avail)
                if qty <= 0:
                    item["reason"] = ("一手预算不足" if not held else "没有可卖的部分底仓，等待可卖份额")
                    continue
                if cfg["mode"] == "t0_reversion" and not held:
                    room = (feature["mean"] - price) / price
                    if room < floor:
                        item["reason"] = f"回归空间{room:.2%}不足费用门槛{floor:.2%}"
                        continue
                    if feature["range"] < floor * 1.5:
                        item["reason"] = f"近30分钟振幅{feature['range']:.2%}不足入场门槛{floor * 1.5:.2%}"
                        continue
                    if feature["close"] <= feature["previous"] or price < feature["close"]:
                        item["reason"] = "等待闭合分钟回升及实时行情确认"
                        continue
                    action, side = "t0_entry", "buy"
                elif cfg["mode"] == "base_t" and not held and not cycle:
                    prev = daily.get(symbol, {})
                    if not prev.get("trend"):
                        item["reason"] = prev.get("reason") or "等待前日趋势数据和均线条件"
                        continue
                    if feature["close"] <= feature["previous"]:
                        item["reason"] = "前日趋势已满足，等待闭合分钟回升"
                        continue
                    if price < feature["mean"]:
                        item["reason"] = "前日趋势已满足，实时价低于30分钟量均价"
                        continue
                    action, side = "seed", "buy"
                elif cfg["mode"] == "base_t" and held and not cycle:
                    spread = (price - feature["mean"]) / feature["mean"]
                    if spread < floor:
                        item["reason"] = f"上涨偏离{spread:.2%}不足做T费用门槛{floor:.2%}"
                        continue
                    if feature["close"] >= feature["previous"]:
                        item["reason"] = "做T偏离已满足，等待闭合分钟回落"
                        continue
                    action, side = "t_sell", "sell"
                if not action:
                    item["reason"] = "等待趋势/回归信号和双边费用门槛"
                    continue
            if side == "buy":
                error = buy_guard(data_dir, acc, symbol, qty, price)
                if error:
                    _observe_reason(item, error, now)
                    if feature:
                        item["diagnostic"] = _diagnostic(acc, qty, price, feature, held, avail)
                    continue
            # Creation time and source time must both precede the execution quote.
            order, error = paper.create_order(data_dir, symbol, side, account_id=aid, qty=qty,
                                              asset_type="etf", order_type="market", ref_price=price,
                                              source=f"etf_shortterm:{action}")
            if error:
                _observe_reason(item, error, now)
                if feature:
                    item["diagnostic"] = _diagnostic(acc, qty, price, feature, held, avail)
                continue
            order.update({"signal_quote_ts": ts, "signal_at_ms": now.timestamp() * 1000,
                          "expires_at_ms": (now + timedelta(seconds=90)).timestamp() * 1000})
            if action == "t_buy":
                order["cycle_sell_order"] = cycle["sell_order"]
            paper.save_order(data_dir, order, aid)
            orders[order["id"]] = order
            _observe_reason(item, "已建单，等待下一笔有效行情", now)
            if feature:
                item["diagnostic"] = _diagnostic(acc, qty, price, feature, held, avail)
            item.update({"action": action, "order_id": order["id"]})
        state["status"] = "自动短线观察中"
        if json.dumps(state, sort_keys=True) != previous_state:
            save_state(data_dir, aid, state)
        return events
