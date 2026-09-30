"""自动跟单规则域 — 监控事件触发模拟盘自动下单 (V2)。

规则匹配: 事件 source=="strategy" 且 strategy_id 相等 (跟策略), 或 rule_id 相等
(跟任意监控规则, 含信号/价格规则)。仓位: 固定金额 或 账户权益百分比。
冷却: 同规则同 symbol 最近一次自动下单后 N 个交易日内不再触发 (按订单
created_at 日期判断, 无独立状态文件 — 可由订单列表重放推导)。

规则按账户存放 (accounts/{account_id}/auto_rules/), 账户间隔离;
on_rule_events 由钩子对每个账户各调一次。

触发链路: quote_service._evaluate_monitors 产出的 rule_events →
paper_auto.on_rule_events → paper.create_order(source=f"auto:{rule_id}")。
所有下单走 paper 域的同一把锁与校验, 账户冻结/资金/涨跌停等约束自动生效。
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import date as _date
from datetime import datetime
from pathlib import Path

from app.market_time import cn_now
from app.services.fs_utils import atomic_write_text
from app.strategy import paper

logger = logging.getLogger(__name__)


def _dir(data_dir: Path, account_id: str) -> Path:
    d = paper._root(data_dir, account_id) / "auto_rules"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_iso() -> str:
    return cn_now().isoformat()


def _new_id() -> str:
    return f"arule_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"


def validate_rule(rule: dict) -> None:
    """校验规则字段, 非法抛 ValueError (中文信息)。"""
    if (rule.get("name") or "").strip() == "":
        raise ValueError("规则名称不能为空")
    if rule.get("match_kind") not in ("strategy", "rule"):
        raise ValueError(f"match_kind 非法: {rule.get('match_kind')!r} (应为 strategy / rule)")
    if not (rule.get("match_id") or "").strip():
        raise ValueError("match_id 不能为空")
    if rule.get("side") not in ("buy", "sell"):
        raise ValueError(f"side 非法: {rule.get('side')!r}")
    if rule.get("size_mode") not in ("fixed_amount", "pct_equity", "full_position"):
        raise ValueError(f"size_mode 非法: {rule.get('size_mode')!r}")
    if rule.get("size_mode") == "full_position" and rule.get("side") != "sell":
        raise ValueError("full_position 仅支持卖出")
    value = rule.get("size_value")
    if (rule.get("size_mode") != "full_position"
            and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0)):
        raise ValueError("size_value 必须是正数")
    if rule.get("size_mode") == "pct_equity" and value > 100:
        raise ValueError("pct_equity 的 size_value 不能超过 100 (%)")
    if rule.get("order_type") not in ("market", "next_open", "close"):
        raise ValueError(f"order_type 非法: {rule.get('order_type')!r}")
    cooldown = rule.get("cooldown_days", 0)
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown < 0:
        raise ValueError("cooldown_days 必须是非负整数")


def normalize_rule(rule: dict) -> dict:
    d = dict(rule)
    d["name"] = (d.get("name") or "").strip()
    d["match_id"] = (d.get("match_id") or "").strip()
    d.setdefault("side", "buy")
    d.setdefault("size_mode", "fixed_amount")
    d.setdefault("order_type", "next_open")
    d.setdefault("cooldown_days", 5)
    d.setdefault("enabled", True)
    d.setdefault("created_at", _now_iso())
    return d


def load_auto_rules(data_dir: Path, account_id: str = paper.DEFAULT_ACCOUNT_ID, enabled_only: bool = False) -> list[dict]:
    out: list[dict] = []
    for f in sorted(_dir(data_dir, account_id).glob("arule_*.json")):
        try:
            r = normalize_rule(json.loads(f.read_text(encoding="utf-8")))
            if enabled_only and not r.get("enabled"):
                continue
            out.append(r)
        except Exception as e:
            logger.warning("paper auto rule load failed %s: %s", f.name, e)
    return out


def save_auto_rule(data_dir: Path, rule: dict, account_id: str = paper.DEFAULT_ACCOUNT_ID) -> dict:
    validate_rule(rule)
    atomic_write_text(_dir(data_dir, account_id) / f"{rule['id']}.json", json.dumps(rule, ensure_ascii=False, indent=2))
    return rule


def create_auto_rule(data_dir: Path, rule: dict, account_id: str = paper.DEFAULT_ACCOUNT_ID) -> dict:
    rule = normalize_rule({**rule, "id": _new_id()})
    validate_rule(rule)
    return save_auto_rule(data_dir, rule, account_id)


def delete_auto_rule(data_dir: Path, rule_id: str, account_id: str = paper.DEFAULT_ACCOUNT_ID) -> bool:
    p = _dir(data_dir, account_id) / f"{rule_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False


def set_enabled(data_dir: Path, rule_id: str, enabled: bool, account_id: str = paper.DEFAULT_ACCOUNT_ID) -> dict | None:
    p = _dir(data_dir, account_id) / f"{rule_id}.json"
    if not p.exists():
        return None
    rule = normalize_rule(json.loads(p.read_text(encoding="utf-8")))
    rule["enabled"] = enabled
    return save_auto_rule(data_dir, rule, account_id)


def _matches(rule: dict, ev: dict) -> bool:
    if rule["match_kind"] == "strategy":
        if ev.get("source") != "strategy" or ev.get("strategy_id") != rule["match_id"]:
            return False
        # Strategy notifications include both entry and exit events. A rule's
        # side must agree with the event, otherwise an exit can open a buy.
        return ev.get("type") in (
            {"buy_signal", "pool_entry"} if rule["side"] == "buy"
            else {"sell_signal", "pool_exit"}
        )
    return ev.get("rule_id") == rule["match_id"]


def prepare_rule_events(events: list[dict], raw_prices: dict[str, float]) -> tuple[list[dict], list[dict]]:
    """Keep batched notifications compact while giving paper orders raw-price symbols.

    The private batch list is removed before notifications are stored or sent.
    Missing raw prices fail closed for strategy orders; adjusted prices are not
    valid order references.
    """
    public_events: list[dict] = []
    paper_events: list[dict] = []
    for event in events:
        public = {key: value for key, value in event.items() if key != "_paper_items"}
        public_events.append(public)
        items = event.get("_paper_items")
        if items:
            for item in items:
                symbol = item.get("symbol")
                if symbol:
                    paper_events.append({**public, "symbol": symbol, "price": raw_prices.get(symbol)})
        elif event.get("source") == "strategy":
            paper_events.append({**public, "price": raw_prices.get(event.get("symbol", ""))})
        else:
            paper_events.append(public)
    return public_events, paper_events


def _in_cooldown(data_dir: Path, rule: dict, symbol: str, cooldown_days: int, account_id: str) -> bool:
    """同规则同 symbol 最近一次自动下单是否仍在冷却期 (按日历日, 含当日)。"""
    if cooldown_days <= 0:
        return False
    prefix = f"auto:{rule['id']}"
    today = cn_now().date()
    for order in paper.load_orders(data_dir, account_id):
        if order.get("source") != prefix or order.get("symbol") != symbol:
            continue
        try:
            created = _date.fromisoformat(order["created_at"][:10])
        except (KeyError, ValueError):
            continue
        if (today - created).days < cooldown_days:
            return True
    return False


def _sizing_qty(data_dir: Path, rule: dict, symbol: str, ref_price: float, account_id: str) -> int:
    if ref_price <= 0:
        return 0
    if rule["size_mode"] == "full_position":
        position = paper.load_positions(data_dir, account_id).get(symbol)
        if position is None:
            return 0
        available = paper._available_of(position, cn_now().date().isoformat())
        return available // paper.LOT_SIZE * paper.LOT_SIZE
    if rule["size_mode"] == "fixed_amount":
        amount = float(rule["size_value"])
    else:  # pct_equity: 按账户总权益 (现金 + 最新定版持仓市值)
        acc = paper.get_account(data_dir, account_id)
        if acc is None:
            return 0
        nav_rows = paper.load_nav(data_dir, account_id)
        equity = nav_rows[-1]["nav"] if nav_rows else float(acc["cash"])
        amount = equity * float(rule["size_value"]) / 100.0
    return paper.qty_from_amount(amount, ref_price)


def _estimated_buy_cost(qty: int, price: float, account: dict) -> float:
    slip = float(account["slippage_bps"]) / 10000
    commission = float(account["commission_pct"])
    return qty * price * (1 + slip) + paper.buy_fee(qty, price, commission)


def _cash_after_pending_buys(data_dir: Path, account_id: str, account: dict) -> float:
    reserved = 0.0
    for order in paper.load_orders(data_dir, account_id):
        if order.get("status") != "pending" or order.get("side") != "buy":
            continue
        ref = order.get("ref_price")
        if not isinstance(ref, (int, float)) or not math.isfinite(ref) or ref <= 0:
            return 0.0
        reserved += _estimated_buy_cost(int(order["qty"]), float(ref), account)
    return max(0.0, float(account["cash"]) - reserved)


def on_rule_events(data_dir: Path, events: list[dict], account_id: str = paper.DEFAULT_ACCOUNT_ID) -> list[dict]:
    """监控事件 → 自动下单。返回本次创建的订单列表 (被拒订单只记日志)。

    触发条件: 事件带 symbol 与价格、有规则匹配、未冷却; 下单复用 paper.create_order
    (冻结/资金/T+1/涨跌停等校验自动生效), ref_price 用事件价格 (当前快照价)。
    """
    created: list[dict] = []
    if not events:
        return created
    rules = load_auto_rules(data_dir, account_id, enabled_only=True)
    if not rules:
        return created
    with paper.PAPER_LOCK:
        account = paper.get_account(data_dir, account_id)
        if account is None:
            return created
        buy_cash = _cash_after_pending_buys(data_dir, account_id, account)
        for ev in events:
            symbol = (ev.get("symbol") or "").strip()
            try:
                price = float(ev.get("price"))
            except (TypeError, ValueError):
                continue
            if not symbol or not math.isfinite(price) or price <= 0:
                continue
            for rule in rules:
                if not _matches(rule, ev):
                    continue
                if _in_cooldown(data_dir, rule, symbol, int(rule.get("cooldown_days", 0)), account_id):
                    continue
                qty = _sizing_qty(data_dir, rule, symbol, price, account_id)
                if qty <= 0:
                    logger.info("paper auto %s: %s 金额不足以一手 (价 %s)", rule["name"], symbol, price)
                    continue
                buy_cost = _estimated_buy_cost(qty, price, account) if rule["side"] == "buy" else 0.0
                if rule["side"] == "buy" and buy_cost > buy_cash:
                    logger.info("paper auto %s: 待成交买单已占用可用资金, 跳过 %s", rule["name"], symbol)
                    continue
                order, err = paper.create_order(
                    data_dir, symbol, rule["side"],
                    account_id=account_id,
                    qty=qty,
                    order_type=rule["order_type"],
                    ref_price=price,
                    source=f"auto:{rule['id']}",
                )
                if err:
                    logger.info("paper auto %s: %s 下单被拒: %s", rule["name"], symbol, err)
                    continue
                if rule["side"] == "buy":
                    buy_cash -= buy_cost
                created.append(order)
                logger.info("paper auto %s: %s 触发 %s %d 股 (%s)", rule["name"], symbol, rule["side"], qty, order["id"])
    return created


_ORDER_TYPE_LABEL = {"market": "即时", "next_open": "次日开盘", "close": "当日收盘"}


def auto_order_events(created: list[dict], account_id: str = paper.DEFAULT_ACCOUNT_ID) -> list[dict]:
    """自动跟单创建的订单 → 推送事件。与成交事件同构 (source=paper 对齐
    AlertEvent), 走同一推送通道; rule_id 从 order.source (auto:{rule_id}) 还原。
    """
    events: list[dict] = []
    for o in created:
        source = str(o.get("source", ""))
        rule_id = source.removeprefix("auto:") if source.startswith("auto:") else ""
        side_label = "买入" if o["side"] == "buy" else "卖出"
        type_label = _ORDER_TYPE_LABEL.get(o["order_type"], o["order_type"])
        events.append({
            "source": "paper",
            "type": "auto_order",
            "severity": "info",
            "ts": int(cn_now().timestamp() * 1000),
            "symbol": o["symbol"],
            "rule_id": rule_id,
            "side": o["side"],
            "qty": o["qty"],
            "account_id": account_id,
            "message": f"自动跟单触发{side_label}下单 ({type_label}) {o['qty']}股",
        })
    return events
