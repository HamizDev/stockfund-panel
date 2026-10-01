"""Persisted, opt-in forward paper strategies using only public market data."""
# ruff: noqa: RUF001 -- localized Chinese UI messages use Chinese punctuation.
from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import httpx

from app.custom.crypto_paper import bitget, client, ledger
from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)
POLL_SECONDS = 30
STRATEGIES = [
    {"id": "ema_trend", "name": "均线趋势", "description": "已收盘 K 线 EMA20/60 趋势；现货只做多，合约可多空。"},
    {"id": "channel_breakout", "name": "通道突破", "description": "收盘突破此前 20 根高低区间；区间内保持持仓，止盈止损退出。"},
]
MODEL = {"maintenance_margin_rate": str(ledger.MAINTENANCE_RATE),
         "liquidation_fee_rate": str(ledger.LIQUIDATION_FEE), "slippage_bps": 5,
         "poll_seconds": POLL_SECONDS, "max_notional": str(ledger.MAX_NOTIONAL)}
_STORE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_RUNTIMES: dict[str, Runner] = {}
_RUNTIME_LOCK = threading.RLock()
_BARS: dict[tuple[str, str, str, str], tuple[float, list[dict]]] = {}


def _exchange(account: dict) -> str:
    exchange = account.get("exchange", "binance")
    if exchange not in ("binance", "bitget"):
        raise ValueError("不支持的模拟行情源")
    if exchange == "bitget" and account.get("market") != "usdm":
        raise ValueError("Bitget 模拟目前仅支持 USDT 本位合约")
    return exchange


def _source(exchange: str):
    if exchange == "binance":
        return client
    if exchange == "bitget":
        return bitget
    raise ValueError("不支持的模拟行情源")


def _connection_error(exc: httpx.HTTPError, exchange: str, dataset: str) -> str:
    label = "Bitget" if exchange == "bitget" else "币安"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (403, 451):
            return f"{label}公开{dataset}被接口拒绝访问（HTTP {status}），本轮没有生成策略订单"
        return f"{label}公开{dataset}请求失败（HTTP {status}），本轮没有生成策略订单"
    return f"{label}公开{dataset}连接失败，本轮没有生成策略订单"


def _path(data_dir: Path) -> Path:
    return data_dir / "user_data" / "crypto_strategy_accounts.json"


def _load(data_dir: Path) -> dict:
    path = _path(data_dir)
    if not path.exists():
        return {"schema": 1, "accounts": {}, "requests": {}}
    try:
        if path.stat().st_size > 50_000_000:
            raise ValueError("策略模拟账本过大，请先归档；原文件已保留")
        state = json.loads(path.read_text(encoding="utf-8"))
        if (state.get("schema") != 1 or not isinstance(state.get("accounts"), dict)
                or not isinstance(state.get("requests"), dict)):
            raise ValueError("schema")
        for account in state["accounts"].values():
            if not isinstance(account, dict) or not isinstance(account.get("ledger"), dict):
                raise ValueError("account")
            _exchange(account)
        return state
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError("策略模拟账本无法读取；原文件已保留") from exc


def _save(data_dir: Path, state: dict) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, separators=(",", ":")))


def _config(body: dict) -> dict:
    config = {key: body.get(key) for key in (
        "name", "market", "symbol", "strategy_id", "interval", "leverage",
        "initial_cash", "allocation_pct", "stop_loss_pct", "take_profit_pct")}
    config["exchange"] = _exchange(body)
    if (config["market"] not in client.MARKETS or config["symbol"] not in client.SYMBOLS
            or config["strategy_id"] not in {s["id"] for s in STRATEGIES}
            or config["interval"] not in ("1h", "4h")):
        raise ValueError("市场、交易对或策略参数无效")
    leverage = config["leverage"]
    if type(leverage) is not int or not 1 <= leverage <= 20 or (config["market"] == "spot" and leverage != 1):
        raise ValueError("现货只能 1 倍；合约研究杠杆范围 1–20 倍")
    cash = ledger._num(config["initial_cash"], positive=True)
    if not Decimal("100") <= cash <= Decimal("10000000"):
        raise ValueError("初始虚拟资金范围 100–10000000 USDT")
    config["initial_cash"] = str(cash)
    for key in ("allocation_pct", "stop_loss_pct", "take_profit_pct"):
        number = ledger._num(config[key], positive=True)
        if number > 100:
            raise ValueError("仓位和止盈止损比例范围为大于 0 至 100%")
        config[key] = float(number)
    name = config["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError("账户名称需为 1–80 个字符")
    config["name"] = name.strip()
    return config


def _row(account: dict) -> dict:
    market = account["market"]
    book = account["ledger"]
    records = book["trades"]
    equity = account.get("equity")
    initial = ledger._num(account["initial_cash"], positive=True)
    pnl = ledger._num(equity) - initial if equity is not None else None
    return {
        "exchange": _exchange(account), "taker_fee_rate": account.get("taker_fee_rate"),
        **{key: account.get(key) for key in (
            "id", "name", "market", "symbol", "strategy_id", "interval", "leverage",
            "initial_cash", "allocation_pct", "stop_loss_pct", "take_profit_pct", "enabled",
            "created_at", "status", "last_error", "last_check_ms", "last_signal", "last_bar_time_ms")},
        "cash": book[market]["cash"], "equity": equity,
        "total_pnl": str(pnl) if pnl is not None else None,
        "return_pct": float(pnl / initial * 100) if pnl is not None else None,
        "max_drawdown_pct": account.get("max_drawdown_pct", 0),
        "fee_total": str(sum((ledger._num(t.get("fee", "0")) for t in records), Decimal("0"))),
        # Positive funding_total is a cost; negative is a credit.
        "funding_total": str(sum((ledger._num(t.get("funding_amount", "0")) for t in records), Decimal("0"))),
        "liquidation_count": sum(t.get("kind") == "liquidation" for t in records),
        "trade_count": sum(t.get("kind", "trade") != "funding" for t in records),
        "positions": copy.deepcopy(book[market]["positions"]),
    }


def accounts(data_dir: Path) -> list[dict]:
    with _STORE_LOCK:
        return [_row(a) for a in _load(data_dir)["accounts"].values()]


def detail(data_dir: Path, account_id: str) -> dict:
    with _STORE_LOCK:
        account = _load(data_dir)["accounts"].get(account_id)
        if account is None:
            raise KeyError("策略模拟账户不存在")
        return {"account": _row(account), "trades": copy.deepcopy(account["ledger"]["trades"][-60:]),
                "nav": copy.deepcopy(account["nav"])}


def create(data_dir: Path, body: dict, leverage_list: list[int] | None = None) -> list[dict]:
    request = body.get("request_id")
    if not isinstance(request, str) or not 1 <= len(request) <= 80 or not all(c.isalnum() or c in "_-" for c in request):
        raise ValueError("请求标识无效")
    config = _config(body)
    levels = leverage_list if leverage_list is not None else [config["leverage"]]
    if (not levels or len(levels) > 4 or len(set(levels)) != len(levels)
            or any(type(level) is not int or not 1 <= level <= 20 for level in levels)
            or (leverage_list is not None and config["market"] != "usdm")):
        raise ValueError("合约对照需要 1–4 个不同杠杆")
    signature = {"config": config, "levels": levels}
    with _STORE_LOCK:
        state = _load(data_dir)
        previous = state["requests"].get(request)
        if previous:
            old_signature = copy.deepcopy(previous["signature"])
            old_signature["config"].setdefault("exchange", "binance")
            if old_signature != signature:
                raise ValueError("请求标识已用于其他账户")
            return [_row(state["accounts"][aid]) for aid in previous["ids"]]
        if len(state["accounts"]) + len(levels) > 24:
            raise ValueError("最多保留 24 个策略模拟账户")
        now = int(time.time() * 1000)
        ids = []
        for level in levels:
            aid = f"cps_{uuid.uuid4().hex[:16]}"
            book = ledger._empty()
            book["exchange"] = config["exchange"]
            book[config["market"]]["cash"] = config["initial_cash"]
            state["accounts"][aid] = {
                **config, "id": aid, "leverage": level,
                "name": f"{config['name']} · {level}x" if leverage_list is not None else config["name"],
                "enabled": False, "created_at": datetime.fromtimestamp(now / 1000, UTC).isoformat(),
                "status": "paused", "last_error": None, "last_check_ms": None,
                "last_signal": None, "last_bar_time_ms": None, "ledger": book,
                "equity": config["initial_cash"], "peak_equity": config["initial_cash"],
                "max_drawdown_pct": 0, "nav": [{"at_ms": now, "equity": config["initial_cash"]}],
            }
            ids.append(aid)
        state["requests"][request] = {"signature": signature, "ids": ids}
        _save(data_dir, state)
        return [_row(state["accounts"][aid]) for aid in ids]


def set_enabled(data_dir: Path, account_id: str, enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("启用状态无效")
    with _STORE_LOCK:
        state = _load(data_dir)
        account = state["accounts"].get(account_id)
        if account is None:
            raise KeyError("策略模拟账户不存在")
        account["enabled"] = enabled
        account["status"] = "waiting" if enabled else "paused"
        account["last_error"] = None
        _save(data_dir, state)
        return _row(account)


def signal(strategy_id: str, bars: list[dict], market: str) -> str:
    """Only caller-validated closed bars; never fill at a historical close."""
    if len(bars) < 61:
        raise ValueError("已收盘 K 线不足 61 根")
    closes = [ledger._num(bar["close"], positive=True) for bar in bars]
    if strategy_id == "ema_trend":
        def ema(period: int) -> Decimal:
            result = closes[0]
            alpha = Decimal(2) / (period + 1)
            for price in closes[1:]:
                result += alpha * (price - result)
            return result
        fast, slow = ema(20), ema(60)
        target = "long" if fast > slow and closes[-1] > slow else "short" if fast < slow and closes[-1] < slow else "flat"
    elif strategy_id == "channel_breakout":
        history = bars[-21:-1]
        upper = max(ledger._num(bar["high"], positive=True) for bar in history)
        lower = min(ledger._num(bar["low"], positive=True) for bar in history)
        target = "long" if closes[-1] > upper else "short" if closes[-1] < lower else "hold"
    else:
        raise ValueError("未知策略")
    return "flat" if market == "spot" and target == "short" else target


def _funding_start(book: dict, symbol: str, now_ms: int) -> int:
    position = book["usdm"]["positions"].get(symbol)
    if not position:
        return now_ms
    stamp = position.get("funding_cursor_ms") or position.get("opened_ms")
    if stamp is None:
        opening = next((r for r in reversed(book["trades"])
                        if r.get("market") == "usdm" and r.get("symbol") == symbol
                        and r.get("action") in ("open_long", "open_short")), None)
        if not opening:
            raise ValueError("旧合约缺少开仓时间")
        stamp = int(datetime.fromisoformat(opening["at"]).timestamp() * 1000)
    return stamp


def _funding_window(book: dict, symbol: str, now_ms: int, exchange: str = "binance") -> tuple[list[dict], int]:
    cursor = _funding_start(book, symbol, now_ms)
    if cursor > now_ms:
        raise ValueError("资金费游标晚于当前行情时间，请核对系统时钟")
    if cursor == now_ms:
        return [], now_ms
    if exchange == "bitget":
        # Published funding records and their completed one-minute mark candle
        # can lag the boundary. Keep the cursor behind it rather than skipping
        # a not-yet-published event forever.
        through = min(now_ms - 120_000, cursor + 30 * 86_400_000)
        if through <= cursor:
            return [], cursor
    else:
        through = min(now_ms, cursor + 90 * 86_400_000)
    return _source(exchange).funding_history(symbol, cursor + 1, through), through


def _close(account: dict, quote: dict, now_ms: int, suffix: str) -> None:
    market, symbol = account["market"], account["symbol"]
    position = account["ledger"][market]["positions"].get(symbol)
    if not position:
        return
    action = "sell" if market == "spot" else f"close_{position['side']}"
    ledger.execute(account["ledger"], market=market, symbol=symbol, action=action,
                   quantity=position["qty"], leverage=account["leverage"], quote=quote,
                   request_id=f"{account['id']}_{now_ms}_{suffix}", at_ms=now_ms, slippage_bps=5)


def _open(account: dict, quote: dict, target: str, now_ms: int) -> None:
    market = account["market"]
    cash = ledger._num(account["ledger"][market]["cash"])
    budget = cash * ledger._num(account["allocation_pct"]) / 100
    price = ledger._num(quote["ask" if target == "long" else "bid"], positive=True)
    price *= Decimal("1.0005") if target == "long" else Decimal("0.9995")
    leverage = account["leverage"]
    fee = ledger.fee_rate(quote, market)
    step = ledger._num(quote["step_size"], positive=True)
    qty = budget / (price / leverage + price * fee)
    maximum = ledger._num(quote.get("max_qty") or "1e30", positive=True)
    qty = min(qty, maximum, ledger.MAX_NOTIONAL / price)
    qty = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
    if qty < ledger._num(quote["min_qty"], positive=True) or qty * price < ledger._num(quote["min_notional"]):
        raise ValueError("虚拟资金不足以满足交易对最小数量或金额")
    ledger.execute(account["ledger"], market=market, symbol=account["symbol"],
                   action="buy" if market == "spot" else f"open_{target}", quantity=str(qty),
                   leverage=leverage, quote=quote, request_id=f"{account['id']}_{now_ms}_open",
                   at_ms=now_ms, slippage_bps=5)


def _mark(account: dict, quote: dict, now_ms: int) -> None:
    value = ledger.value_account(account["ledger"], {(account["market"], account["symbol"]): quote})
    equity = ledger._num(value[account["market"]]["equity"])
    peak = max(ledger._num(account["peak_equity"], positive=True), equity)
    drawdown = max(Decimal("0"), (peak - equity) / peak * 100)
    account.update(equity=str(equity), peak_equity=str(peak),
                   max_drawdown_pct=max(account["max_drawdown_pct"], float(drawdown)))
    nav = account["nav"]
    point = {"at_ms": now_ms, "equity": str(equity)}
    # Keep the initial value and at most 4999 rolling 15-minute observations.
    if len(nav) > 1 and nav[-1]["at_ms"] // 900_000 == now_ms // 900_000:
        nav[-1] = point
    else:
        nav.append(point)
    account["nav"] = nav[:1] + nav[-4999:] if len(nav) > 5000 else nav


def _evaluate(account: dict, quote: dict, bars: list[dict], funding: list[dict], now_ms: int,
              signal_error: str | None = None, funding_through_ms: int | None = None) -> None:
    market, symbol = account["market"], account["symbol"]
    book = account["ledger"]
    if (quote.get("exchange", "binance") != _exchange(account)
            or book.get("exchange", "binance") != _exchange(account)):
        raise ValueError("行情源与策略模拟账户不匹配")
    account["taker_fee_rate"] = str(ledger.fee_rate(quote, market))
    had_position = bool(book[market]["positions"].get(symbol))
    if market == "usdm":
        ledger.settle_funding(book, symbol, funding, funding_through_ms if funding_through_ms is not None else now_ms)
        lag = 120_000 if _exchange(account) == "bitget" else 0
        pending_boundary = False
        if lag and funding_through_ms is not None and book[market]["positions"].get(symbol):
            interval_ms = int(quote["funding_interval_hours"]) * 3_600_000
            last_boundary = int(quote["next_funding_time"]) - interval_ms
            pending_boundary = funding_through_ms < last_boundary <= now_ms
        if (funding_through_ms is not None and (funding_through_ms < now_ms - lag or pending_boundary)
                and book[market]["positions"].get(symbol)):
            account.update(equity=None, status="catching_up", last_check_ms=now_ms,
                           last_error="正在补齐历史资金费，本轮不生成策略订单或记录当前净值")
            return
        ledger.liquidate(book, symbol, quote["mark"], now_ms)
    position = book[market]["positions"].get(symbol)
    risk_exit = had_position and position is None
    if account["enabled"]:
        if position:
            entry = (ledger._num(position["cost"]) / ledger._num(position["qty"], positive=True)
                     if market == "spot" else ledger._num(position["entry"], positive=True))
            mark = ledger._num(quote["bid"] if market == "spot" else quote["mark"], positive=True)
            change = (mark / entry - 1) * 100 * (-1 if position.get("side") == "short" else 1)
            if change <= -ledger._num(account["stop_loss_pct"]) or change >= ledger._num(account["take_profit_pct"]):
                try:
                    _close(account, quote, now_ms, "risk")
                    risk_exit = True
                except (ValueError, KeyError, TypeError) as exc:
                    signal_error = str(exc)
        if len(book["trades"]) >= 50000:
            account["enabled"] = False
            signal_error = "成交记录达到上限，已暂停，请先归档"
        if not signal_error:
            # Only the strategy action is transactional. A rejected entry must
            # not undo funding or risk exits already settled above.
            strategy_book = copy.deepcopy(book)
            try:
                bar_time = bars[-1]["close_time_ms"]
                target = signal(account["strategy_id"], bars, market)
                account["last_signal"] = target
                if bar_time != account["last_bar_time_ms"]:
                    position = book[market]["positions"].get(symbol)
                    current = position.get("side", "long") if position else "flat"
                    if not risk_exit and target not in ("hold", current):
                        if position:
                            _close(account, quote, now_ms, "signal")
                        if target in ("long", "short"):
                            _open(account, quote, target, now_ms)
                    account["last_bar_time_ms"] = bar_time
                elif risk_exit:
                    account["last_bar_time_ms"] = bar_time
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                account["ledger"] = strategy_book
                signal_error = str(exc)
    _mark(account, quote, now_ms)
    account.update(status="error" if signal_error else "running" if account["enabled"] else "paused",
                   last_error=signal_error, last_check_ms=now_ms)


def _bars(market: str, symbol: str, interval: str, exchange: str = "binance") -> list[dict]:
    key = (exchange, market, symbol, interval)
    previous = _BARS.get(key)
    if previous and time.monotonic() - previous[0] < POLL_SECONDS:
        return previous[1]
    bars = _source(exchange).klines(market, symbol, interval, limit=200)
    _BARS[key] = (time.monotonic(), bars)
    return bars


def run_once(data_dir: Path, account_id: str | None = None, *, stop: threading.Event | None = None) -> dict:
    """Fetch outside the store lock, then reload settings before committing."""
    if not _RUN_LOCK.acquire(blocking=False):
        return {"busy": True}
    try:
        with _STORE_LOCK:
            snapshot = copy.deepcopy(_load(data_dir)["accounts"])
        if account_id is not None:
            if account_id not in snapshot:
                raise KeyError("策略模拟账户不存在")
            snapshot = {account_id: snapshot[account_id]}
        now_ms = int(time.time() * 1000)
        quotes: dict[tuple[str, str, str], dict] = {}
        results: dict[str, tuple[dict, list[dict], list[dict], int, int, str | None] | str] = {}
        for aid, account in snapshot.items():
            if stop and stop.is_set():
                return {"stopped": True}
            market, symbol = account["market"], account["symbol"]
            exchange = _exchange(account)
            if not account["enabled"] and not account["ledger"][market]["positions"]:
                continue
            try:
                key = (exchange, market, symbol)
                quote = quotes.get(key)
                if quote is None:
                    quote = _source(exchange).quote(market, symbol)
                    quotes[key] = quote
                observed_ms = quote["asof_ms"]
                if (quote.get("exchange", "binance") != exchange or quote.get("market") != market
                        or quote.get("symbol") != symbol or abs(int(time.time() * 1000) - observed_ms) > 60_000):
                    raise ValueError("公开行情过期或标的错误，已停止本轮策略下单")
                bars, signal_error = [], None
                if account["enabled"]:
                    try:
                        bars = _bars(market, symbol, account["interval"], exchange)
                        period_ms = 3_600_000 if account["interval"] == "1h" else 14_400_000
                        if bars and bars[-1]["close_time_ms"] >= observed_ms:
                            # Another account may have cached a quote from
                            # before the candle used by this new signal.
                            quote = _source(exchange).quote(market, symbol)
                            quotes[key] = quote
                            observed_ms = quote["asof_ms"]
                            if (quote.get("exchange", "binance") != exchange or quote.get("market") != market
                                    or quote.get("symbol") != symbol or abs(int(time.time() * 1000) - observed_ms) > 60_000):
                                raise ValueError("新信号后的公开行情过期或标的错误")
                        if len(bars) < 61 or not 0 <= observed_ms - bars[-1]["close_time_ms"] <= period_ms * 2:
                            raise ValueError("已收盘 K 线不足或过期，已停止本轮策略下单")
                    except httpx.HTTPError as exc:
                        signal_error = _connection_error(exc, exchange, " K 线")
                    except (ValueError, KeyError, TypeError, IndexError) as exc:
                        signal_error = str(exc)
                funding, through = (_funding_window(account["ledger"], symbol, observed_ms, exchange)
                                    if market == "usdm" and account["ledger"][market]["positions"] else ([], observed_ms))
                results[aid] = (quote, bars, funding, observed_ms, through, signal_error)
            except httpx.HTTPError as exc:
                results[aid] = _connection_error(exc, exchange, "行情")
            except (ValueError, KeyError, TypeError) as exc:
                results[aid] = str(exc)
        if stop and stop.is_set():
            return {"stopped": True}
        with _STORE_LOCK:
            if stop and stop.is_set():
                return {"stopped": True}
            state = _load(data_dir)
            for aid, outcome in results.items():
                current = state["accounts"].get(aid)
                if current is None:
                    continue
                if isinstance(outcome, str):
                    current.update(status="error", last_error=outcome, last_check_ms=now_ms)
                    if current["ledger"][current["market"]]["positions"]:
                        current["equity"] = None
                    continue
                candidate = copy.deepcopy(current)
                quote, bars, funding, observed_ms, through, signal_error = outcome
                # A paused account may have been enabled during the fetch; it
                # must wait for the next poll to obtain its strategy bars.
                if candidate["enabled"] and not bars:
                    signal_error = signal_error or "策略尚未取得已收盘 K 线，等待下一轮"
                try:
                    commit_ms = int(time.time() * 1000)
                    if commit_ms - quote["asof_ms"] > 60_000:
                        raise ValueError("提交时公开行情已过期，等待下一轮")
                    _evaluate(candidate, quote, bars, funding, observed_ms, signal_error, through)
                except (ValueError, KeyError, TypeError) as exc:
                    current.update(status="error", last_error=str(exc), last_check_ms=now_ms)
                    if current["ledger"][current["market"]]["positions"]:
                        current["equity"] = None
                else:
                    state["accounts"][aid] = candidate
            if results:
                if stop and stop.is_set():
                    return {"stopped": True}
                _save(data_dir, state)
        _maintain_manual(data_dir, quotes, now_ms, stop)
        return {"processed": len(results), "busy": False}
    finally:
        _RUN_LOCK.release()


def _maintain_manual(data_dir: Path, quotes: dict, now_ms: int, stop: threading.Event | None) -> None:
    # The legacy manual wallet remains separate; only funding/liquidation are
    # automatic. It never receives strategy orders or moves strategy funds.
    with ledger._LOCK:
        snapshot = copy.deepcopy(ledger.load(data_dir))
    if not snapshot["usdm"]["positions"]:
        if snapshot.get("maintenance_error") and not (stop and stop.is_set()):
            with ledger._LOCK:
                current = ledger.load(data_dir)
                if current == snapshot:
                    current["maintenance_error"] = None
                    ledger._save(data_dir, current)
        return
    try:
        histories = {}
        for symbol in snapshot["usdm"]["positions"]:
            if stop and stop.is_set():
                return
            key = ("usdm", symbol)
            if key not in quotes:
                quotes[key] = client.quote("usdm", symbol)
            quote = quotes[key]
            observed_ms = quote["asof_ms"]
            if quote.get("market") != "usdm" or quote.get("symbol") != symbol or abs(int(time.time() * 1000) - observed_ms) > 60_000:
                raise ValueError("手动合约公开行情过期或标的错误")
            events, through = _funding_window(snapshot, symbol, observed_ms)
            histories[symbol] = (events, through, observed_ms)
        if stop and stop.is_set():
            return
        with ledger._LOCK:
            if stop and stop.is_set():
                return
            current = ledger.load(data_dir)
            # Never apply a fetched funding window to a reopened position.
            if current != snapshot:
                return
            state = copy.deepcopy(current)
            pending = False
            for symbol, (events, through, observed_ms) in histories.items():
                ledger.settle_funding(state, symbol, events, through)
                if through < observed_ms and state["usdm"]["positions"].get(symbol):
                    pending = True
                else:
                    ledger.liquidate(state, symbol, quotes[("usdm", symbol)]["mark"], observed_ms)
            state["maintenance_error"] = "历史资金费补账中，风险记账与净值尚未完成" if pending else None
            if not (stop and stop.is_set()):
                ledger._save(data_dir, state)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        logger.warning("手动合约资金费/强平核对失败，保留原账本")
        with ledger._LOCK:
            current = ledger.load(data_dir)
            if current == snapshot and not (stop and stop.is_set()):
                current["maintenance_error"] = "资金费或行情不可用，风险记账与净值尚未完成"
                ledger._save(data_dir, current)


def manual_order(data_dir: Path, *, market: str, symbol: str, action: str, quantity: str,
                 leverage: int, quote: dict, request_id: str) -> tuple[dict, dict]:
    """Settle a held contract through its quote time before an atomic close."""
    if market != "usdm":
        return ledger.trade(data_dir, market=market, symbol=symbol, action=action, quantity=quantity,
                            leverage=leverage, quote=quote, request_id=request_id)
    fill_ms = quote["asof_ms"]
    with ledger._LOCK:
        snapshot = copy.deepcopy(ledger.load(data_dir))
    position = snapshot["usdm"]["positions"].get(symbol)
    events, through = _funding_window(snapshot, symbol, fill_ms) if position else ([], fill_ms)
    with ledger._LOCK:
        state = ledger.load(data_dir)
        if state != snapshot:
            raise ValueError("模拟账本已变化，请刷新后重试")
        state = copy.deepcopy(state)
        if position:
            ledger.settle_funding(state, symbol, events, through)
            if through < fill_ms and state["usdm"]["positions"].get(symbol):
                ledger._save(data_dir, state)
                raise ValueError("历史资金费仍在补账，本次没有创建订单，请稍后重试")
            if ledger.liquidate(state, symbol, quote["mark"], fill_ms) or symbol not in state["usdm"]["positions"]:
                if not state["usdm"]["positions"]:
                    state["maintenance_error"] = None
                ledger._save(data_dir, state)
                raise ValueError("原合约已按研究模型强平，本次没有创建订单")
        record = ledger.execute(state, market=market, symbol=symbol, action=action, quantity=quantity,
                                leverage=leverage, quote=quote, request_id=request_id, at_ms=fill_ms)
        if not state["usdm"]["positions"]:
            state["maintenance_error"] = None
        ledger._save(data_dir, state)
        return record, state


class Runner:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="crypto-paper", daemon=True)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                run_once(self.data_dir, stop=self.stop_event)
            except Exception:
                logger.exception("数字资产自动模拟本轮失败")
            self.stop_event.wait(POLL_SECONDS)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        # The process data lock must remain held until the worker has finished
        # any atomic commit. Public HTTP calls have bounded timeouts.
        self.thread.join()


def start(data_dir: Path) -> None:
    key = str(data_dir.resolve())
    with _RUNTIME_LOCK:
        existing = _RUNTIMES.get(key)
        if existing and existing.thread.is_alive():
            return
        runtime = Runner(data_dir)
        _RUNTIMES[key] = runtime
        runtime.start()


def shutdown(data_dir: Path) -> None:
    with _RUNTIME_LOCK:
        key = str(data_dir.resolve())
        runtime = _RUNTIMES.get(key)
        if runtime:
            runtime.stop()
            _RUNTIMES.pop(key, None)


def runtime_status(data_dir: Path) -> dict:
    with _RUNTIME_LOCK:
        runtime = _RUNTIMES.get(str(data_dir.resolve()))
        return {"running": bool(runtime and runtime.thread.is_alive()), "poll_seconds": POLL_SECONDS}
