"""Deterministic OHLC research replay for one-times Bitget USDT-M contracts.

This module has no network or filesystem effects. It reuses the production
signal and in-memory paper-ledger rules, while making historical signal and
fill timestamps explicit.
"""
# ruff: noqa: RUF001 -- localized Chinese research messages use Chinese punctuation.
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from app.custom.crypto_paper import auto, ledger, rules

_INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000}
_SLIPPAGE_BPS = 5
_D = Decimal
_ONE = _D("1")
_HUNDRED = _D("100")
_MAX_PRICE = _D("1e100")


def _positive(value: object, field: str) -> Decimal:
    try:
        number = ledger._num(value, positive=True)
    except ValueError as exc:
        raise ValueError(f"{field}无效") from exc
    if number > _MAX_PRICE:
        raise ValueError(f"{field}超出研究范围")
    return number


def _config_values(config: dict, bars: list[dict], contract: dict) -> tuple[dict, list[dict], dict]:
    if not isinstance(config, dict) or not isinstance(contract, dict) or not isinstance(bars, list):
        raise ValueError("回测配置、合约参数或K线格式无效")
    interval = config.get("interval")
    period_ms = _INTERVAL_MS.get(interval)
    if period_ms is None:
        raise ValueError("回测周期仅支持1h或4h")
    if type(config.get("bars")) is not int or not 250 <= config["bars"] <= 1000:
        raise ValueError("回测K线数需为250-1000根")
    if not 250 <= len(bars) <= 1000 or config["bars"] > len(bars):
        raise ValueError("输入已收盘K线需为250-1000根，且不少于回测K线数")

    strategy_id = config.get("strategy_id")
    params = rules.parameters(strategy_id, config.get("strategy_params"))
    required = rules.minimum_bars(strategy_id, params)
    if required > 200:
        raise ValueError("策略所需历史超过每次信号最多200根的研究窗口")
    if config["bars"] < required:
        raise ValueError(f"回测K线不足，策略至少需要{required}根")

    symbol = config.get("symbol")
    if symbol not in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        raise ValueError("仅支持BTCUSDT、ETHUSDT或SOLUSDT")
    if config.get("market", "usdm") != "usdm":
        raise ValueError("研究回测仅支持Bitget USDT本位合约")

    initial_cash = _positive(config.get("initial_cash"), "初始资金")
    if not _D("100") <= initial_cash <= _D("10000000"):
        raise ValueError("初始虚拟资金范围100-10000000 USDT")
    values: dict[str, Decimal] = {"initial_cash": initial_cash}
    for field in ("allocation_pct", "stop_loss_pct", "take_profit_pct"):
        number = _positive(config.get(field), field)
        if number > _HUNDRED:
            raise ValueError(f"{field}范围需大于0且不超过100%")
        values[field] = number

    normalized: list[dict] = []
    previous_open: int | None = None
    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            raise ValueError("K线格式无效")
        opened, closed = bar.get("open_time_ms"), bar.get("close_time_ms")
        if (type(opened) is not int or type(closed) is not int or opened < 0
                or opened % period_ms or closed != opened + period_ms - 1):
            raise ValueError("K线开收盘时间戳与所选周期不匹配")
        if previous_open is not None and opened != previous_open + period_ms:
            raise ValueError("K线必须按周期连续且无重复")
        previous_open = opened
        row = {"open_time_ms": opened, "close_time_ms": closed}
        for field in ("open", "high", "low", "close"):
            row[field] = _positive(bar.get(field), f"第{index + 1}根K线{field}")
        if (row["high"] < max(row["open"], row["low"], row["close"])
                or row["low"] > min(row["open"], row["high"], row["close"])):
            raise ValueError("K线OHLC关系无效")
        normalized.append(row)

    parsed_contract = {
        "step_size": _positive(contract.get("step_size"), "合约数量步长"),
        "min_qty": _positive(contract.get("min_qty"), "合约最小数量"),
        "min_notional": _positive(contract.get("min_notional"), "合约最小名义金额"),
        "max_qty": _positive(contract.get("max_qty"), "合约最大数量"),
        "max_leverage": _positive(contract.get("max_leverage"), "合约最大杠杆"),
        "taker_fee_rate": ledger._num(contract.get("taker_fee_rate")),
    }
    if (parsed_contract["max_qty"] < parsed_contract["min_qty"]
            or parsed_contract["max_leverage"] < _ONE
            or not _D("0") <= parsed_contract["taker_fee_rate"] <= _D("0.01")):
        raise ValueError("公开合约规则或taker费率无效")
    return ({**config, "strategy_params": params, "_values": values,
             "_period_ms": period_ms, "_required_bars": required},
            normalized[-config["bars"]:], parsed_contract)


def _quote(symbol: str, contract: dict, price: Decimal) -> dict:
    value = str(price)
    return {
        "exchange": "bitget", "market": "usdm", "symbol": symbol,
        "bid": value, "ask": value, "mark": value,
        **{key: str(contract[key]) for key in (
            "step_size", "min_qty", "min_notional", "max_qty",
            "max_leverage", "taker_fee_rate")},
    }


def _event(record: dict, signal_time_ms: int, fill_time_ms: int, reason: str) -> dict:
    return {
        "signal_time_ms": signal_time_ms,
        "fill_time_ms": fill_time_ms,
        "action": record["action"],
        "price": float(ledger._num(record["price"])),
        "quantity": float(ledger._num(record["quantity"])),
        "fee": float(ledger._num(record.get("fee", "0"))),
        "realized_pnl": float(ledger._num(record.get("realized_pnl", "0"))),
        "reason": reason,
    }


def _thresholds(position: dict, stop_pct: Decimal, take_pct: Decimal) -> tuple[Decimal, Decimal]:
    entry = ledger._num(position["entry"], positive=True)
    if position["side"] == "long":
        return entry * (_ONE - stop_pct / _HUNDRED), entry * (_ONE + take_pct / _HUNDRED)
    return entry * (_ONE + stop_pct / _HUNDRED), entry * (_ONE - take_pct / _HUNDRED)


def _open_quantity(cash: Decimal, target: str, raw_price: Decimal,
                   allocation_pct: Decimal, contract: dict) -> Decimal:
    direction = _ONE if target == "long" else -_ONE
    fill_price = raw_price * (_ONE + direction * _D(_SLIPPAGE_BPS) / _D("10000"))
    fee_rate = contract["taker_fee_rate"]
    budget = cash * allocation_pct / _HUNDRED
    quantity = budget / (fill_price * (_ONE + fee_rate))
    maximum = min(contract["max_qty"], ledger.MAX_NOTIONAL / fill_price)
    step = contract["step_size"]
    return (min(quantity, maximum) / step).to_integral_value(rounding=ROUND_DOWN) * step


def run(config: dict, bars: list[dict], contract: dict) -> dict:
    """Replay closed candles without touching the network or persistent ledger."""
    settings, history, market = _config_values(config, bars, contract)
    symbol = settings["symbol"]
    values = settings["_values"]
    state = ledger._empty()
    state["exchange"] = "bitget"
    state["usdm"]["cash"] = str(values["initial_cash"])
    output_trades: list[dict] = []
    warnings = [
        "资金费未计入；结果口径为手续费后、资金费前，不是完整费后实盘净收益。",
        "逐仓强平沿用ledger固定维持保证金和强平费模型，不等同交易所分档规则；OHLC无法复原真实盘中路径或退出先后，同根双触发时止损优先。",
        "此结果逐根使用已收盘K线并在下一根开盘回放；实时前向轮询则按轮询时公开行情成交，二者的可观察时点和成交价不同。",
        "最大回撤基于逐根收盘估值权益曲线，不代表未观测到的盘中最大回撤。",
    ]
    round_pnl = _D("0")
    closed_pnls: list[Decimal] = []
    request_number = 0

    def execute(action: str, quantity: object, price: Decimal, signal_ms: int,
                fill_ms: int, reason: str) -> dict:
        nonlocal request_number
        request_number += 1
        record = ledger.execute(
            state, market="usdm", symbol=symbol, action=action, quantity=quantity,
            leverage=1, quote=_quote(symbol, market, price),
            request_id=f"research_{request_number}", at_ms=fill_ms,
            slippage_bps=_SLIPPAGE_BPS,
        )
        output_trades.append(_event(record, signal_ms, fill_ms, reason))
        return record

    def close_position(price: Decimal, signal_ms: int, fill_ms: int, reason: str) -> dict:
        nonlocal round_pnl
        position = state["usdm"]["positions"].get(symbol)
        if not position:
            raise ValueError("研究账本缺少待平仓位")
        action = f"close_{position['side']}"
        record = execute(action, position["qty"], price, signal_ms, fill_ms, reason)
        round_pnl += ledger._num(record["realized_pnl"])
        closed_pnls.append(round_pnl)
        round_pnl = _D("0")
        return record

    def open_position(target: str, price: Decimal, signal_ms: int,
                      fill_ms: int) -> bool:
        nonlocal round_pnl
        cash = ledger._num(state["usdm"]["cash"])
        quantity = _open_quantity(cash, target, price, values["allocation_pct"], market)
        if (quantity < market["min_qty"]
                or quantity * price * (_ONE + _D(_SLIPPAGE_BPS) / _D("10000")
                                        * (_ONE if target == "long" else -_ONE))
                < market["min_notional"]):
            warnings.append("开仓信号因可用资金不足以满足当前合约最小数量或名义金额而跳过。")
            return False
        try:
            record = execute(f"open_{target}", quantity, price, signal_ms, fill_ms, "strategy_signal")
        except ValueError as exc:
            if "余额不足" in str(exc) or "保证金不足" in str(exc):
                warnings.append("开仓信号因可用资金不足而跳过。")
                return False
            raise
        round_pnl = ledger._num(record["realized_pnl"])
        return True

    def liquidate_at(price: Decimal, signal_ms: int, fill_ms: int) -> bool:
        nonlocal round_pnl
        if not ledger.liquidate(state, symbol, price, fill_ms):
            return False
        record = state["trades"][-1]
        output_trades.append(_event(record, signal_ms, fill_ms, "isolated_liquidation"))
        round_pnl += ledger._num(record["realized_pnl"])
        closed_pnls.append(round_pnl)
        round_pnl = _D("0")
        return True

    def equity_at(price: Decimal) -> Decimal:
        quote = _quote(symbol, market, price)
        value = ledger.value_account(state, {("usdm", symbol): quote})
        return ledger._num(value["usdm"]["equity"])

    first = history[0]
    nav = [{"at_ms": first["open_time_ms"], "equity": float(values["initial_cash"])}]
    peak_equity = values["initial_cash"]
    max_drawdown_pct = _D("0")
    pending: tuple[str, int] | None = None

    for index, bar in enumerate(history):
        opened, closed = bar["open_time_ms"], bar["close_time_ms"]
        open_price = bar["open"]
        risk_exit = False

        position = state["usdm"]["positions"].get(symbol)
        if position and liquidate_at(open_price, opened, opened):
            risk_exit = True
        position = state["usdm"]["positions"].get(symbol)
        if position and not risk_exit:
            stop, take = _thresholds(position, values["stop_loss_pct"], values["take_profit_pct"])
            side = position["side"]
            gap_exit: tuple[str, Decimal] | None = None
            if side == "long":
                if open_price <= stop:
                    gap_exit = ("stop_loss_gap", open_price)
                elif open_price >= take:
                    gap_exit = ("take_profit_gap", take)
            elif open_price >= stop:
                gap_exit = ("stop_loss_gap", open_price)
            elif open_price <= take:
                gap_exit = ("take_profit_gap", take)
            if gap_exit:
                close_position(gap_exit[1], opened, opened, gap_exit[0])
                risk_exit = True

        if pending is not None:
            target, signal_ms = pending
            pending = None
            if not risk_exit:
                position = state["usdm"]["positions"].get(symbol)
                current = position["side"] if position else "flat"
                if target not in ("hold", current):
                    if position:
                        close_position(open_price, signal_ms, opened,
                                       "signal_flat" if target == "flat" else "signal_reversal")
                    if target in ("long", "short"):
                        open_position(target, open_price, signal_ms, opened)

        position = state["usdm"]["positions"].get(symbol)
        if position and not risk_exit and liquidate_at(open_price, opened, opened):
            risk_exit = True

        position = state["usdm"]["positions"].get(symbol)
        if position and not risk_exit:
            stop, take = _thresholds(position, values["stop_loss_pct"], values["take_profit_pct"])
            side = position["side"]
            if side == "long":
                stop_hit, take_hit = bar["low"] <= stop, bar["high"] >= take
            else:
                stop_hit, take_hit = bar["high"] >= stop, bar["low"] <= take

            if stop_hit:
                if not liquidate_at(stop, closed, closed):
                    close_position(stop, closed, closed, "stop_loss")
                risk_exit = True
            elif take_hit:
                close_position(take, closed, closed, "take_profit")
                risk_exit = True
            else:
                adverse_price = bar["low"] if side == "long" else bar["high"]
                if liquidate_at(adverse_price, closed, closed):
                    risk_exit = True

        final_bar = index == len(history) - 1
        if not risk_exit and index >= settings["_required_bars"] - 1:
            signal_bars = history[max(0, index - 199):index + 1]
            # auto.signal receives only this closed prefix and only OHLC/time fields.
            target = auto.signal(
                settings["strategy_id"], signal_bars, "usdm", settings["strategy_params"])
            if target not in ("long", "short", "flat", "hold"):
                raise ValueError("策略返回了未知信号")
            if final_bar:
                position = state["usdm"]["positions"].get(symbol)
                current = position["side"] if position else "flat"
                if target not in ("hold", current):
                    warnings.append("末根收盘信号没有下一根开盘可供成交，未执行该信号。")
            else:
                pending = (target, closed)

        if final_bar:
            position = state["usdm"]["positions"].get(symbol)
            if position:
                close_position(bar["close"], closed, closed, "end_of_test_estimate")
                warnings.append("末根仍持有的合约已按收盘价作期末估算平仓，计入平仓滑点与手续费。")

        equity = equity_at(bar["close"])
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown_pct = max(
                max_drawdown_pct, (peak_equity - equity) / peak_equity * _HUNDRED)
        nav.append({"at_ms": closed, "equity": float(equity)})

    final_cash = ledger._num(state["usdm"]["cash"])
    fee_total = sum((ledger._num(item.get("fee", "0")) for item in state["trades"]), _D("0"))
    closed_count = len(closed_pnls)
    wins = sum(pnl > 0 for pnl in closed_pnls)
    initial_cash = values["initial_cash"]
    return {
        "metrics": {
            "return_pct": float((final_cash / initial_cash - _ONE) * _HUNDRED),
            "max_drawdown_pct": float(max_drawdown_pct),
            "closed_trades": closed_count,
            "win_rate_pct": float(_D(wins) / closed_count * _HUNDRED) if closed_count else None,
            "fees_paid": float(fee_total),
            "ending_equity": float(final_cash),
        },
        "nav": nav,
        "trades": output_trades,
        "warnings": warnings,
        "method": (
            "Bitget USDT本位1倍逐仓研究回测；只用截至当前收盘的最多200根已收盘OHLC生成信号，"
            "下一根开盘执行，固定5bps不利滑点并使用传入公开taker费率；"
            "止盈止损按持仓bar的open与OHLC估算，跳空按较差开盘处理，同根止损优先；"
            "末根未平仓按收盘价估算平仓并计双边成本，最大回撤按收盘权益计算。"
            "指标为手续费后、资金费前；"
            "实时前向轮询按轮询时公开行情成交，观察和成交时点不同。"
        ),
    }
