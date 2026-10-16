"""Bounded ETF paper worker; network retrieval never runs on the quote thread."""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime

import polars as pl

from app.market_time import cn_now, in_continuous_session
from app.strategy import etf_shortterm, paper

logger = logging.getLogger(__name__)


class EtfShortTermService:
    def __init__(self, app_state):
        self.app_state = app_state
        self.data_dir = app_state.repo.store.data_dir
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._quotes = {}
        self._demand = []
        self._minute = pl.DataFrame()
        self._daily = {}
        self._daily_day = None
        self._minute_day = None
        self._last_fetch = 0.0
        self._status = {"status": "等待启用账户", "minute_provider": None, "minute_error": None}

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="etf-shortterm-paper", daemon=True)
        self._thread.start()

    def stop(self):
        # Synchronize with the order commit boundary. A running account finishes
        # before stop becomes visible; no later account can start committing.
        with paper.PAPER_LOCK:
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def update_quotes(self, records: list[dict], *, source_timestamps: bool) -> None:
        # Fuyao synthesizes a receipt timestamp. It cannot satisfy the source-time
        # execution contract; never upgrade it to a live exchange event.
        if not source_timestamps:
            return
        allowed = {s for mode in etf_shortterm.MODES.values() for s in mode["symbols"]}
        now_ms = cn_now().timestamp() * 1000
        with self._lock:
            for record in records:
                symbol = record.get("symbol")
                try:
                    ts = float(record["timestamp"])
                    price = float(record["last_price"])
                except (KeyError, TypeError, ValueError):
                    continue
                if (symbol in allowed and math.isfinite(ts) and math.isfinite(price) and price > 0
                        and 0 <= now_ms - ts <= 90_000
                        and ts > self._quotes.get(symbol, {}).get("timestamp", 0)):
                    self._quotes[symbol] = {"timestamp": ts, "last_price": price}

    def fresh_quotes(self, now=None):
        now_ms = (now or cn_now()).timestamp() * 1000
        with self._lock:
            return {s: dict(q) for s, q in self._quotes.items() if 0 <= now_ms - q["timestamp"] <= 90_000}

    def status(self):
        with self._lock:
            return dict(self._status)

    def realtime_symbols(self):
        with self._lock:
            return list(self._demand)

    def _accounts(self):
        return [a for aid in paper.list_account_ids(self.data_dir)
                if (a := paper.get_account(self.data_dir, aid)) and a.get("etf_shortterm")]

    def demand_symbols(self):
        symbols = set()
        for summary in self._accounts():
            acc = paper.get_account(self.data_dir, summary["id"])
            cfg = acc["etf_shortterm"]
            if cfg["enabled"] and acc["status"] == "active":
                symbols.update(etf_shortterm.MODES[cfg["mode"]]["symbols"])
            symbols.update(s for s, p in paper.load_positions(self.data_dir, acc["id"]).items() if p.get("qty", 0) > 0)
            symbols.update(o["symbol"] for o in paper.load_orders(self.data_dir, acc["id"]) if o["status"] == "pending")
        return sorted(symbols)[:15]

    def _load_previous_daily(self, now: datetime):
        if self._daily_day == now.date():
            return
        wanted = etf_shortterm.MODES["base_t"]["symbols"]
        self._daily = {s: {"trend": False, "reason": "等待前日ETF日线和均线数据"} for s in wanted}
        # Persisted enriched is a narrow price table, not an indicator snapshot.
        # Ask the repository to calculate means with a strictly completed-day end.
        paths = sorted((self.data_dir / "kline_etf_enriched").glob("date=*/*.parquet"), reverse=True)
        for path in paths:
            try:
                day = datetime.strptime(path.parent.name.removeprefix("date="), "%Y-%m-%d").date()
            except ValueError:
                continue
            if day >= now.date():
                continue
            if (now.date() - day).days > 10:
                self._daily = {s: {"date": day.isoformat(), "trend": False,
                                  "reason": "前日日线过期，等待同步"} for s in wanted}
                return
            complete = True
            for symbol in wanted:
                item = {"date": day.isoformat(), "trend": False, "reason": "前日日线缺失，等待同步"}
                self._daily[symbol] = item
                try:
                    frame = self.app_state.repo.get_etf_daily(
                        symbol, day, day, columns=["symbol", "date", "close", "ma20", "ma60", "amount"])
                    rows = (frame.filter((pl.col("symbol") == symbol) & (pl.col("date") == day)).to_dicts()
                            if {"symbol", "date"}.issubset(frame.columns) else [])
                except Exception:
                    logger.exception("ETF短线前日日线读取失败: %s", symbol)
                    item["reason"] = "前日日线读取失败，等待重试"
                    complete = False
                    continue
                if len(rows) != 1:
                    complete = False
                    continue
                row = rows[0]
                close, ma20, ma60, amount = (row.get(k) for k in ("close", "ma20", "ma60", "amount"))
                valid = all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (close, ma20, ma60, amount))
                item.update({"close": close, "ma20": ma20, "ma60": ma60, "amount": amount})
                if not valid:
                    item["reason"] = ("前日均线数据不足60个有效日" if ma20 is None or ma60 is None
                                      else "前日日线数值待核对")
                    complete = False
                    continue
                item["trend"] = close > ma20 > ma60 and amount >= 20_000_000
                item["reason"] = ("前日趋势数据就绪" if item["trend"] else
                                  "前日成交金额低于2000万元" if amount < 20_000_000 else
                                  f"前日趋势未满足：收盘{close:.4f} > MA20 {ma20:.4f} > MA60 {ma60:.4f}")
            # Missing history may be supplied later today. Do not cache a failed
            # first attempt for the rest of the session; retry on the next fetch.
            if complete:
                self._daily_day = now.date()
            return

    def run_once(self, now=None):
        from app.services import kline_sync, trading_day

        live_clock = now is None
        now = now or cn_now()
        if self._stop.is_set():
            return []
        accounts = self._accounts()
        if not accounts:
            return
        symbols = self.demand_symbols()
        minute_symbols = sorted({s for a in accounts if a["etf_shortterm"]["enabled"] and a["status"] == "active"
                                 for s in etf_shortterm.MODES[a["etf_shortterm"]["mode"]]["symbols"]})
        with self._lock:
            self._demand = symbols
        qs = getattr(self.app_state, "quote_service", None)
        def session_allowed(at):
            return (in_continuous_session(at) and trading_day.is_trading_day() is not False
                    and qs is not None and not qs.is_paused() and qs.status().get("enabled") is True)
        session_ok = session_allowed(now)
        if self._minute_day != now.date():
            self._minute = pl.DataFrame()
            self._minute_day = now.date()
        if session_ok and minute_symbols and time.monotonic() - self._last_fetch >= 60:
            self._last_fetch = time.monotonic()
            try:
                self._minute, provider, error = kline_sync.fetch_etf_paper_minutes(
                    minute_symbols, self.app_state.capabilities, now)
            except Exception as exc:  # preserve missing-data status without stopping risk exits
                self._minute, provider, error = pl.DataFrame(), None, str(exc)
                logger.warning("ETF短线分钟获取失败: %s", exc)
            # Network IO may outlive the trading session or shutdown. Its start
            # time must not become the order's signal/expiry time.
            if self._stop.is_set():
                return []
            if live_clock:
                now = cn_now()
                session_ok = session_allowed(now)
            with self._lock:
                self._status.update({"minute_provider": provider, "minute_error": error,
                                     "minute_rows": self._minute.height, "last_fetch": now.isoformat()})
            self._load_previous_daily(now)
        with self._lock:
            self._status["status"] = ("等待启用账户" if not minute_symbols else "自动观察中"
                                      if session_ok else "等待行情服务/交易时段")
        quotes = self.fresh_quotes(now) if session_ok else {}
        events = []
        for account in accounts:
            with paper.PAPER_LOCK:
                if self._stop.is_set():
                    break
                events.extend(etf_shortterm.run_account(self.data_dir, account["id"], quotes,
                                                       self._minute if session_ok else pl.DataFrame(), self._daily, now))
        if events and qs:
            # _fill_order already appends the authoritative alert; only broadcast.
            qs.push_alerts(events)
        return events

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("ETF短线模拟本轮失败")
            self._stop.wait(3)
