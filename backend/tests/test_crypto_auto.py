"""Crypto paper strategy accounts: isolated, opt-in, public-data-only tests."""
from __future__ import annotations

import copy
import threading
from decimal import Decimal

import httpx
import pytest

from app.custom.crypto_paper import auto, ledger

_HOUR_MS = 60 * 60 * 1000


@pytest.fixture
def clock(monkeypatch):
    value = [1_800_000_000_000]
    monkeypatch.setattr(auto.time, "time", lambda: value[0] / 1000)
    monkeypatch.setattr(auto, "_BARS", {})
    return value


def _body(
    request_id: str = "auto-1",
    *,
    market: str = "usdm",
    symbol: str = "BTCUSDT",
    leverage: int = 10,
    initial_cash: str = "10000",
    allocation_pct: float = 10,
    stop_loss_pct: float = 90,
    take_profit_pct: float = 90,
) -> dict:
    return {
        "name": "trend study",
        "market": market,
        "symbol": symbol,
        "strategy_id": "ema_trend",
        "interval": "1h",
        "leverage": leverage,
        "initial_cash": initial_cash,
        "allocation_pct": allocation_pct,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "request_id": request_id,
    }


def _quote(market: str, now_ms: int, *, mark: str = "100", min_qty: str = "0.001") -> dict:
    return {
        "market": market,
        "symbol": "BTCUSDT",
        "bid": mark,
        "ask": str(float(mark) + 0.1),
        "mark": mark if market == "usdm" else None,
        "asof_ms": now_ms,
        "step_size": "0.001",
        "min_qty": min_qty,
        "max_qty": "1000",
        "min_notional": "5",
    }


def _bars(now_ms: int, *, trend: str = "up", interval: str = "1h") -> list[dict]:
    period = _HOUR_MS if interval == "1h" else 4 * _HOUR_MS
    last_open = now_ms // period * period - period
    result = []
    for index in range(61):
        opening = last_open - (60 - index) * period
        close = 100 + index if trend == "up" else 200 - index
        result.append({
            "open_time_ms": opening,
            "close_time_ms": opening + period - 1,
            "open": str(close),
            "high": str(close + 1),
            "low": str(close - 1),
            "close": str(close),
            "volume": "1",
        })
    return result


def _install_client(monkeypatch, clock, *, trend: str = "up") -> dict:
    state = {"trend": trend, "mark": "100", "min_qty": "0.001", "bars": None,
             "funding": [], "calls": []}

    def fake_quote(market: str, symbol: str) -> dict:
        state["calls"].append(("quote", market, symbol))
        quote = _quote(market, clock[0], mark=state["mark"], min_qty=state["min_qty"])
        quote.update(
            symbol=symbol, exchange="bitget", taker_fee_rate="0.0006", max_leverage=20,
            funding_interval_hours=8,
            next_funding_time=(clock[0] // (8 * _HOUR_MS) + 1) * 8 * _HOUR_MS,
        )
        return quote

    def fake_klines(market: str, symbol: str, interval: str = "1h", limit: int = 200) -> list[dict]:
        state["calls"].append(("klines", market, symbol, interval, limit))
        if state["bars"] is not None:
            return copy.deepcopy(state["bars"])
        return _bars(clock[0], trend=state["trend"], interval=interval)

    def fake_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
        state["calls"].append(("funding", symbol, start_ms, end_ms))
        events = state["funding"]
        return copy.deepcopy(events(start_ms, end_ms) if callable(events) else events)

    monkeypatch.setattr(auto.bitget, "quote", fake_quote)
    monkeypatch.setattr(auto.bitget, "klines", fake_klines)
    monkeypatch.setattr(auto.bitget, "funding_history", fake_funding)
    return state


def _funding_event(symbol: str, funding_time_ms: int) -> dict:
    return {"funding_time_ms": funding_time_ms, "rate": "0.001", "mark_price": "100"}


def _open_usdm_position(data_dir, monkeypatch, clock) -> tuple[str, dict, dict]:
    account = auto.create(data_dir, _body(market="usdm", leverage=20))[0]
    account_id = account["id"]
    auto.set_enabled(data_dir, account_id, True)
    market = _install_client(monkeypatch, clock)
    result = auto.run_once(data_dir, account_id)
    assert result["processed"] == 1
    position = auto.detail(data_dir, account_id)["account"]["positions"]["BTCUSDT"]
    return account_id, market, position


def _install_bitget(monkeypatch, clock, *, trend="down"):
    calls = []

    def quote(market, symbol):
        calls.append(("quote", market, symbol))
        return {**_quote(market, clock[0], mark="110"), "symbol": symbol,
                "exchange": "bitget", "taker_fee_rate": "0.0006", "max_leverage": 20,
                "funding_interval_hours": 8,
                "next_funding_time": (clock[0] // (8 * _HOUR_MS) + 1) * 8 * _HOUR_MS}

    def klines(market, symbol, interval="1h", limit=200):
        calls.append(("klines", market, symbol))
        return _bars(clock[0], trend=trend, interval=interval)

    monkeypatch.setattr(auto.bitget, "quote", quote)
    monkeypatch.setattr(auto.bitget, "klines", klines)
    monkeypatch.setattr(auto.bitget, "funding_history", lambda *args: [])
    return calls


def _legacy_binance_account(tmp_path, clock, *, missing_exchange=True):
    body = _body("legacy-account")
    account = auto.create(tmp_path, body)[0]
    state = auto._load(tmp_path)
    saved = state["accounts"][account["id"]]
    book = saved["ledger"]
    book["exchange"] = "binance"
    quote = {**_quote("usdm", clock[0]), "exchange": "binance"}
    ledger.execute(book, market="usdm", symbol="BTCUSDT", action="open_long", quantity="1",
                   leverage=10, quote=quote, request_id="legacy-position", at_ms=clock[0])
    saved.update(enabled=True, status="running", ledger=book)
    if missing_exchange:
        saved.pop("exchange")
        saved["ledger"].pop("exchange")
        state["requests"][body["request_id"]]["signature"]["config"].pop("exchange")
    else:
        saved["exchange"] = "binance"
        state["requests"][body["request_id"]]["signature"]["config"]["exchange"] = "binance"
    auto._save(tmp_path, state)
    return account


def test_legacy_exchange_without_field_remains_readable_as_binance(tmp_path):
    original = _legacy_binance_account(tmp_path, [1_800_000_000_000])
    path = auto._path(tmp_path)
    before = path.read_bytes()
    row = auto.accounts(tmp_path)[0]
    assert row["exchange"] == "binance"
    assert row["effective_readonly"] is True
    assert row["readonly_reason"]
    assert row["enabled"] is False
    assert row["status"] == "readonly"
    assert auto.detail(tmp_path, original["id"])["account"] == row
    assert path.read_bytes() == before


@pytest.mark.parametrize("exchange", ["bitget", "bybit", "unknown"])
def test_invalid_spot_exchange_does_not_create_account(tmp_path, exchange):
    with pytest.raises(ValueError):
        auto.create(tmp_path, {**_body(market="spot", leverage=1), "exchange": exchange})
    assert not auto._path(tmp_path).exists()


def test_new_strategy_accounts_default_to_bitget_usdm(tmp_path, monkeypatch, clock):
    bitget_calls = _install_bitget(monkeypatch, clock, trend="down")
    accounts = [auto.create(tmp_path, _body("bitget-one"))[0],
                auto.create(tmp_path, _body("bitget-two"))[0]]
    for account in accounts:
        auto.set_enabled(tmp_path, account["id"], True)
    assert auto.run_once(tmp_path)["processed"] == 2
    first, second = [auto.detail(tmp_path, a["id"]) for a in accounts]
    assert all(row["account"]["exchange"] == "bitget" for row in (first, second))
    assert all(row["account"]["market"] == "usdm" for row in (first, second))
    assert second["account"]["last_signal"] == "short"
    assert first["trades"][0]["exchange"] == "bitget"
    trade = second["trades"][0]
    assert trade["exchange"] == "bitget"
    assert trade["fee_rate"] == "0.0006"
    assert Decimal(trade["fee"]) == Decimal(trade["quantity"]) * Decimal(trade["price"]) * Decimal("0.0006")
    assert second["account"]["taker_fee_rate"] == "0.0006"
    assert len([c for c in bitget_calls if c[0] == "klines"]) == 1
    auto.run_once(tmp_path)
    assert auto.detail(tmp_path, accounts[1]["id"])["trades"] == second["trades"]


def test_new_binance_strategy_is_rejected_without_changing_ledger(tmp_path, monkeypatch):
    auto.create(tmp_path, _body("existing"))
    path = auto._path(tmp_path)
    before = path.read_bytes()
    source_calls = []

    def unexpected_source(exchange):
        source_calls.append(exchange)
        raise AssertionError("account creation must not request market data")

    monkeypatch.setattr(auto, "_source", unexpected_source)

    with pytest.raises(ValueError, match="仅支持新建 Bitget"):
        auto.create(tmp_path, {**_body("new-binance"), "exchange": "binance"})

    assert source_calls == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("missing_exchange", [True, False], ids=["missing-exchange", "explicit-binance"])
def test_legacy_binance_strategy_cannot_enable_or_run_and_keeps_ledger_unchanged(
    tmp_path, monkeypatch, clock, missing_exchange
):
    account = _legacy_binance_account(tmp_path, clock, missing_exchange=missing_exchange)
    legacy_positions = auto.detail(tmp_path, account["id"])["account"]["positions"]
    assert legacy_positions["BTCUSDT"]

    manual_quote = _quote("usdm", clock[0])
    ledger.trade(tmp_path, market="usdm", symbol="ETHUSDT", action="open_long", quantity="1",
                 leverage=10, quote={**manual_quote, "symbol": "ETHUSDT"}, request_id="legacy-manual-position")
    manual = ledger.load(tmp_path)
    manual["maintenance_error"] = "旧账本待核对"
    ledger._save(tmp_path, manual)

    path = auto._path(tmp_path)
    before = path.read_bytes()
    manual_path = ledger._path(tmp_path)
    manual_before = manual_path.read_bytes()
    source_calls = []
    original_source = auto._source

    def tracked_source(exchange):
        source_calls.append(exchange)
        return original_source(exchange)

    monkeypatch.setattr(auto, "_source", tracked_source)

    def unexpected_binance_request(*_args, **_kwargs):
        raise AssertionError("legacy processing must not request Binance market data")

    monkeypatch.setattr(auto.client, "quote", unexpected_binance_request)
    monkeypatch.setattr(auto.client, "funding_history", unexpected_binance_request)
    with pytest.raises(ValueError, match="不能启用"):
        auto.set_enabled(tmp_path, account["id"], True)
    assert auto.run_once(tmp_path, account["id"]) == {"processed": 0, "busy": False}

    class IterationEvent(threading.Event):
        def __init__(self):
            super().__init__()
            self.iteration_finished = threading.Event()

        def wait(self, timeout=None):
            self.iteration_finished.set()
            return super().wait(timeout)

    runner = auto.Runner(tmp_path)
    runner.stop_event = IterationEvent()
    runner.start()
    try:
        assert runner.stop_event.iteration_finished.wait(timeout=1)
    finally:
        runner.stop()

    saved = auto.detail(tmp_path, account["id"])
    assert saved["account"]["effective_readonly"] is True
    assert saved["account"]["positions"] == legacy_positions
    assert source_calls == []
    assert path.read_bytes() == before
    assert manual_path.read_bytes() == manual_before


def test_active_source_rejects_binance_but_keeps_bitget():
    with pytest.raises(ValueError, match="不请求 Binance"):
        auto._source("binance")
    assert auto._source("bitget") is auto.bitget


def test_foreign_exchange_quote_cannot_open_position(tmp_path, monkeypatch, clock):
    account = auto.create(tmp_path, _body())[0]
    _install_bitget(monkeypatch, clock)
    monkeypatch.setattr(auto.bitget, "quote", lambda *args: _quote("usdm", clock[0]))
    auto.set_enabled(tmp_path, account["id"], True)
    auto.run_once(tmp_path)
    saved = auto.detail(tmp_path, account["id"])
    assert saved["trades"] == []
    assert saved["account"]["status"] == "error"


@pytest.mark.parametrize("offset", [-60_001, 5001])
def test_stale_or_future_quote_cannot_create_simulated_order(tmp_path, monkeypatch, clock, offset):
    account = auto.create(tmp_path, _body())[0]
    _install_bitget(monkeypatch, clock)
    source_quote = auto.bitget.quote
    def invalid_quote(market, symbol):
        quote = source_quote(market, symbol)
        quote["asof_ms"] = clock[0] + offset
        return quote
    monkeypatch.setattr(auto.bitget, "quote", invalid_quote)
    auto.set_enabled(tmp_path, account["id"], True)
    auto.run_once(tmp_path)
    saved = auto.detail(tmp_path, account["id"])
    assert saved["trades"] == []
    assert saved["account"]["status"] == "error"


@pytest.mark.parametrize("status", [403, 451, 429])
def test_http_failure_discloses_venue_and_status_without_order(tmp_path, monkeypatch, clock, status):
    account = auto.create(tmp_path, _body())[0]
    request = httpx.Request("GET", "https://api.bitget.com/api/v2/mix/market/ticker")

    def blocked(*args):
        raise httpx.HTTPStatusError("blocked", request=request,
                                    response=httpx.Response(status, request=request))

    monkeypatch.setattr(auto.bitget, "quote", blocked)
    auto.set_enabled(tmp_path, account["id"], True)
    auto.run_once(tmp_path)
    saved = auto.detail(tmp_path, account["id"])
    assert saved["trades"] == []
    assert "Bitget" in saved["account"]["last_error"]
    assert f"HTTP {status}" in saved["account"]["last_error"]


def test_bitget_pending_funding_boundary_blocks_exit_until_settled(tmp_path, monkeypatch, clock):
    boundary = clock[0] // (8 * _HOUR_MS) * 8 * _HOUR_MS
    clock[0] = boundary - 60_000
    account = auto.create(tmp_path, _body())[0]
    _install_bitget(monkeypatch, clock, trend="up")
    auto.set_enabled(tmp_path, account["id"], True)
    auto.run_once(tmp_path)
    assert auto.detail(tmp_path, account["id"])["account"]["positions"]
    clock[0] = boundary + 30_000
    auto._BARS.clear()
    _install_bitget(monkeypatch, clock, trend="down")
    auto.run_once(tmp_path)
    waiting = auto.detail(tmp_path, account["id"])
    assert waiting["account"]["status"] == "catching_up"
    assert len(waiting["trades"]) == 1
    assert waiting["account"]["positions"]["BTCUSDT"]["side"] == "long"
    clock[0] = boundary + 180_000
    event = {"funding_time_ms": boundary, "rate": "0.0001", "mark_price": "110",
             "mark_price_basis": "bitget_1m_mark_open_estimate"}
    monkeypatch.setattr(auto.bitget, "funding_history", lambda *args: [event])
    auto.run_once(tmp_path)
    after = auto.detail(tmp_path, account["id"])
    assert after["account"]["status"] == "running"
    assert after["account"]["positions"]["BTCUSDT"]["side"] == "short"
    assert [t["kind"] for t in after["trades"]] == ["trade", "funding", "trade", "trade"]
    assert after["trades"][1]["mark_price_basis"] == "bitget_1m_mark_open_estimate"


def test_empty_and_default_paused_accounts_do_not_fetch_market_data(tmp_path, monkeypatch, clock):
    def unexpected_network_call(*_args, **_kwargs):
        raise AssertionError("paused or empty strategy accounts must not fetch market data")

    monkeypatch.setattr(auto.bitget, "quote", unexpected_network_call)
    monkeypatch.setattr(auto.bitget, "klines", unexpected_network_call)
    monkeypatch.setattr(auto.bitget, "funding_history", unexpected_network_call)

    assert auto.accounts(tmp_path) == []
    assert auto.run_once(tmp_path) == {"processed": 0, "busy": False}
    assert not (tmp_path / "user_data" / "crypto_strategy_accounts.json").exists()

    account = auto.create(tmp_path, _body())[0]
    assert account["enabled"] is False
    assert account["status"] == "paused"
    assert auto.run_once(tmp_path, account["id"]) == {"processed": 0, "busy": False}


def test_compare_accounts_are_independent_and_create_is_idempotent(tmp_path):
    body = _body("compare-1", market="usdm", leverage=1)
    created = auto.create(tmp_path, body, leverage_list=[1, 2, 3])
    repeated = auto.create(tmp_path, body, leverage_list=[1, 2, 3])

    assert [row["id"] for row in repeated] == [row["id"] for row in created]
    assert len({row["id"] for row in created}) == 3
    assert [row["leverage"] for row in created] == [1, 2, 3]
    assert all(row["cash"] == "10000" and row["positions"] == {} for row in created)

    auto.set_enabled(tmp_path, created[0]["id"], True)
    assert auto.detail(tmp_path, created[0]["id"])["account"]["enabled"] is True
    assert auto.detail(tmp_path, created[1]["id"])["account"]["enabled"] is False
    assert auto.detail(tmp_path, created[2]["id"])["account"]["enabled"] is False

    changed = {**body, "name": "different signature"}
    with pytest.raises(ValueError, match="请求标识已用于其他账户"):
        auto.create(tmp_path, changed, leverage_list=[1, 2, 3])


@pytest.mark.parametrize(
    "updates",
    [
        {"market": "margin"},
        {"symbol": "NOTREAL"},
        {"interval": "15m"},
        {"market": "spot", "leverage": 1},
        {"initial_cash": "99"},
        {"allocation_pct": 101},
        {"request_id": "invalid/id"},
    ],
    ids=["market", "symbol", "interval", "spot-market", "cash", "allocation", "request-id"],
)
def test_create_rejects_invalid_parameters_without_persisting(tmp_path, updates):
    body = {**_body(), **updates}

    with pytest.raises(ValueError):
        auto.create(tmp_path, body)

    assert not (tmp_path / "user_data" / "crypto_strategy_accounts.json").exists()


def test_enable_runs_one_closed_signal_once_per_bar(tmp_path, monkeypatch, clock):
    account = auto.create(tmp_path, _body())[0]
    state = _install_client(monkeypatch, clock)

    enabled = auto.set_enabled(tmp_path, account["id"], True)
    assert enabled["enabled"] is True
    assert enabled["status"] == "waiting"

    first = auto.run_once(tmp_path, account["id"])
    after_first = auto.detail(tmp_path, account["id"])
    assert first["processed"] == 1
    assert after_first["account"]["status"] == "running"
    assert after_first["account"]["last_signal"] == "long"
    assert after_first["account"]["last_bar_time_ms"] == _bars(clock[0])[-1]["close_time_ms"]
    assert after_first["account"]["positions"]["BTCUSDT"]
    assert len(after_first["trades"]) == 1

    second = auto.run_once(tmp_path, account["id"])
    after_second = auto.detail(tmp_path, account["id"])
    assert second["processed"] == 1
    assert after_second["trades"] == after_first["trades"]
    assert len([call for call in state["calls"] if call[0] == "klines"]) == 1


def test_stop_event_during_fetch_does_not_persist_partial_state(tmp_path, monkeypatch, clock):
    account = auto.create(tmp_path, _body())[0]
    auto.set_enabled(tmp_path, account["id"], True)
    _install_client(monkeypatch, clock)
    stop = threading.Event()
    original_quote = auto.bitget.quote

    def quote_then_stop(market: str, symbol: str) -> dict:
        quote = original_quote(market, symbol)
        stop.set()
        return quote

    monkeypatch.setattr(auto.bitget, "quote", quote_then_stop)
    path = tmp_path / "user_data" / "crypto_strategy_accounts.json"
    before = path.read_bytes()

    assert auto.run_once(tmp_path, account["id"], stop=stop) == {"stopped": True}
    assert path.read_bytes() == before
    saved = auto.detail(tmp_path, account["id"])["account"]
    assert saved["status"] == "waiting"
    assert saved["last_check_ms"] is None


def test_paused_account_does_not_open_but_settles_funding_and_liquidates(
    tmp_path, monkeypatch, clock
):
    account_id, state, position = _open_usdm_position(tmp_path, monkeypatch, clock)
    auto.set_enabled(tmp_path, account_id, False)
    opened_ms = position["funding_cursor_ms"]
    clock[0] = opened_ms + 180_000
    state["mark"] = "5"
    state["funding"] = [_funding_event("BTCUSDT", opened_ms + 30_000)]
    calls_before_pause = len(state["calls"])

    auto.run_once(tmp_path, account_id)

    detail = auto.detail(tmp_path, account_id)
    assert detail["account"]["status"] == "paused"
    assert detail["account"]["enabled"] is False
    assert detail["account"]["positions"] == {}
    assert float(detail["account"]["funding_total"]) > 0
    assert detail["account"]["liquidation_count"] == 1
    assert [trade["kind"] for trade in detail["trades"]] == ["trade", "funding", "liquidation"]
    assert not any(call[0] == "klines" for call in state["calls"][calls_before_pause:])


def test_bad_kline_still_maintains_existing_position(tmp_path, monkeypatch, clock):
    account_id, state, position = _open_usdm_position(tmp_path, monkeypatch, clock)
    opened_ms = position["funding_cursor_ms"]
    clock[0] = opened_ms + 180_000
    state["mark"] = "5"
    state["bars"] = []
    state["funding"] = [_funding_event("BTCUSDT", opened_ms + 30_000)]
    auto._BARS.clear()

    auto.run_once(tmp_path, account_id)

    detail = auto.detail(tmp_path, account_id)
    assert detail["account"]["positions"] == {}
    assert float(detail["account"]["funding_total"]) > 0
    assert detail["account"]["liquidation_count"] == 1


def test_failed_strategy_action_keeps_funding_risk_record(tmp_path, monkeypatch, clock):
    account_id, state, position = _open_usdm_position(tmp_path, monkeypatch, clock)
    opened_ms = position["funding_cursor_ms"]
    clock[0] = opened_ms + _HOUR_MS + 180_000
    state["trend"] = "down"
    state["min_qty"] = position["qty"]
    state["funding"] = [_funding_event("BTCUSDT", opened_ms + 30_000)]
    auto._BARS.clear()

    auto.run_once(tmp_path, account_id)

    detail = auto.detail(tmp_path, account_id)
    assert detail["account"]["status"] == "error"
    assert "最小数量" in detail["account"]["last_error"] or "整数倍" in detail["account"]["last_error"]
    assert float(detail["account"]["funding_total"]) > 0
    assert any(trade["kind"] == "funding" for trade in detail["trades"])


def test_strategy_balance_isolated_from_manual_wallet(tmp_path, monkeypatch, clock):
    manual_quote = _quote("spot", clock[0])
    ledger.trade(tmp_path, market="spot", symbol="BTCUSDT", action="buy", quantity="1",
                 leverage=1, quote=manual_quote, request_id="manual-buy")
    manual_before = ledger.load(tmp_path)
    manual_spot = copy.deepcopy(manual_before["spot"])

    account = auto.create(tmp_path, _body(initial_cash="1000"))[0]
    auto.set_enabled(tmp_path, account["id"], True)
    _install_client(monkeypatch, clock)
    auto.run_once(tmp_path, account["id"])

    manual_after = ledger.load(tmp_path)
    strategy = auto.detail(tmp_path, account["id"])["account"]
    assert manual_after["spot"] == manual_spot
    assert strategy["initial_cash"] == "1000"
    assert strategy["positions"]["BTCUSDT"]
    assert float(strategy["cash"]) < 1000


def test_shared_quote_is_refetched_when_new_interval_bar_is_after_it(tmp_path, monkeypatch, clock):
    boundary = (clock[0] // _HOUR_MS + 1) * _HOUR_MS
    clock[0] = boundary + 30_000
    four_hour = auto.create(tmp_path, {**_body("four-hour"), "interval": "4h"})[0]
    one_hour = auto.create(tmp_path, _body("one-hour"))[0]
    auto.set_enabled(tmp_path, four_hour["id"], True)
    auto.set_enabled(tmp_path, one_hour["id"], True)

    quote_times = [boundary - 2, boundary + 500]
    quote_calls = []

    def fake_quote(market, symbol):
        asof_ms = quote_times[min(len(quote_calls), len(quote_times) - 1)]
        quote_calls.append((market, symbol, asof_ms))
        quote = _quote(market, asof_ms)
        quote.update(
            symbol=symbol, exchange="bitget", taker_fee_rate="0.0006", max_leverage=20,
            funding_interval_hours=8,
            next_funding_time=(asof_ms // (8 * _HOUR_MS) + 1) * 8 * _HOUR_MS,
        )
        return quote

    kline_calls = []

    def fake_klines(market, symbol, interval="1h", limit=200):
        kline_calls.append(interval)
        return _bars(clock[0], interval=interval)

    monkeypatch.setattr(auto.bitget, "quote", fake_quote)
    monkeypatch.setattr(auto.bitget, "klines", fake_klines)
    monkeypatch.setattr(auto.bitget, "funding_history", lambda *_args: [])

    result = auto.run_once(tmp_path)

    assert result["processed"] == 2
    assert kline_calls == ["4h", "1h"]
    assert [call[2] for call in quote_calls] == [boundary - 2, boundary + 500]
    assert auto.detail(tmp_path, four_hour["id"])["account"]["positions"]["BTCUSDT"]
    assert auto.detail(tmp_path, one_hour["id"])["account"]["positions"]["BTCUSDT"]


def test_funding_http_error_preserves_position_cursor_and_unknown_equity(
    tmp_path, monkeypatch, clock
):
    account_id, _market, position = _open_usdm_position(tmp_path, monkeypatch, clock)
    auto.set_enabled(tmp_path, account_id, False)
    before = auto.detail(tmp_path, account_id)
    clock[0] = position["opened_ms"] + 180_000

    def funding_unavailable(*_args):
        raise httpx.ReadTimeout("public funding history unavailable")

    monkeypatch.setattr(auto.bitget, "funding_history", funding_unavailable)

    result = auto.run_once(tmp_path, account_id)

    after = auto.detail(tmp_path, account_id)
    assert result["processed"] == 1
    assert after["account"]["status"] == "error"
    assert after["account"]["equity"] is None
    assert after["account"]["positions"]["BTCUSDT"] == before["account"]["positions"]["BTCUSDT"]
    assert after["account"]["positions"]["BTCUSDT"]["funding_cursor_ms"] == position["funding_cursor_ms"]
    assert after["nav"] == before["nav"]


def test_manual_close_without_pending_funding_uses_atomic_quote_fill(tmp_path, clock):
    open_quote = _quote("usdm", clock[0], mark="100")
    opened, book = ledger.trade(
        tmp_path, market="usdm", symbol="BTCUSDT", action="open_long", quantity="2",
        leverage=20, quote=open_quote, request_id="manual-two-coin-open",
    )
    opened_ms = book["usdm"]["positions"]["BTCUSDT"]["opened_ms"]
    book["maintenance_error"] = "资金费待核对"
    ledger._save(tmp_path, book)

    close_quote = _quote("usdm", opened_ms, mark="110")
    close_quote.update(bid="110", ask="110.1")

    closed_trade, closed = auto.manual_order(
        tmp_path, market="usdm", symbol="BTCUSDT", action="close_long", quantity="2",
        leverage=20, quote=close_quote, request_id="manual-two-coin-close",
    )

    open_trade, close_trade = closed["trades"]
    assert [record["kind"] for record in closed["trades"]] == ["trade", "trade"]
    assert opened["price"] == "100.1"
    assert closed_trade == close_trade
    assert close_trade["quantity"] == "2"
    assert close_trade["price"] == "110"
    assert Decimal(closed["usdm"]["cash"]) == Decimal("10000") + sum(
        (Decimal(record["realized_pnl"]) for record in (open_trade, close_trade)),
        Decimal("0"),
    )
    assert closed["usdm"]["positions"] == {}
    assert closed["maintenance_error"] is None


def test_runner_stop_waits_for_inflight_save_to_finish(tmp_path, monkeypatch):
    save_entered = threading.Event()
    release_save = threading.Event()
    save_exited = threading.Event()
    stop_returned = threading.Event()

    def blocked_save(_data_dir, _state):
        save_entered.set()
        release_save.wait()
        save_exited.set()

    monkeypatch.setattr(auto, "_save", blocked_save)
    monkeypatch.setattr(auto, "run_once", lambda data_dir, stop=None: auto._save(data_dir, {}))
    runner = auto.Runner(tmp_path)
    runner.start()

    stopper = threading.Thread(target=lambda: (runner.stop(), stop_returned.set()))
    try:
        assert save_entered.wait(timeout=1)
        stopper.start()
        assert runner.stop_event.wait(timeout=1)
        assert not stop_returned.wait(timeout=0.05)
        assert not save_exited.is_set()

        release_save.set()
        assert stop_returned.wait(timeout=1)
        assert save_exited.is_set()
        assert not runner.thread.is_alive()
    finally:
        release_save.set()
        if stopper.ident is not None:
            stopper.join(timeout=1)
        runner.stop()


def test_long_offline_funding_catches_up_in_bounded_windows(tmp_path, monkeypatch, clock):
    account_id, market, position = _open_usdm_position(tmp_path, monkeypatch, clock)
    clock[0] = position["opened_ms"] + 180 * 86_400_000 + 180_000
    auto.set_enabled(tmp_path, account_id, False)
    auto.run_once(tmp_path, account_id)
    saved = auto.detail(tmp_path, account_id)["account"]
    assert saved["status"] == "catching_up"
    assert saved["equity"] is None
    assert saved["positions"]["BTCUSDT"]["funding_cursor_ms"] == position["opened_ms"] + 30 * 86_400_000
    funding_call = [call for call in market["calls"] if call[0] == "funding"][-1]
    assert funding_call[-1] - funding_call[-2] <= 30 * 86_400_000
    for _ in range(6):
        auto.run_once(tmp_path, account_id)
    assert auto.detail(tmp_path, account_id)["account"]["status"] == "paused"


def test_delayed_funding_fetch_keeps_fill_and_cursor_at_same_observation(tmp_path, monkeypatch, clock):
    account_id, market, _position = _open_usdm_position(tmp_path, monkeypatch, clock)
    clock[0] += 180_000
    expected_fill = clock[0]
    market["mark"] = "99"
    state = auto._load(tmp_path)
    state["accounts"][account_id]["stop_loss_pct"] = 0.5
    auto._save(tmp_path, state)
    def delayed_funding(_symbol, _start, end):
        assert end == expected_fill - 120_000
        clock[0] += 2000
        return []
    monkeypatch.setattr(auto.bitget, "funding_history", delayed_funding)
    auto.run_once(tmp_path, account_id)
    trades = auto.detail(tmp_path, account_id)["trades"]
    assert trades[-1]["action"] == "close_long"
    assert int(auto.datetime.fromisoformat(trades[-1]["at"]).timestamp() * 1000) == expected_fill


def test_manual_close_with_pending_legacy_funding_fails_closed_without_write(
    tmp_path, clock
):
    quote = _quote("usdm", clock[0])
    ledger.trade(tmp_path, market="usdm", symbol="BTCUSDT", action="open_long", quantity="1",
                 leverage=20, quote=quote, request_id="manual-open")
    book = ledger.load(tmp_path)
    opened = book["usdm"]["positions"]["BTCUSDT"]["opened_ms"]
    quote["asof_ms"] = opened + 2_000
    path = ledger._path(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="不请求 Binance"):
        auto.manual_order(tmp_path, market="usdm", symbol="BTCUSDT", action="close_long",
                          quantity="1", leverage=20, quote=quote, request_id="manual-close")
    assert ledger.load(tmp_path) == book
    assert path.read_bytes() == before
