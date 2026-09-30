"""Crypto research ledger: no real exchange or user account calls."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.crypto_paper import client, ledger
from app.custom.crypto_paper.routes import build_router


def _quote(market: str = "spot", bid: str = "100", ask: str = "101") -> dict:
    return {
        "market": market, "symbol": "BTCUSDT", "bid": bid, "ask": ask,
        "step_size": "0.001", "min_qty": "0.001", "min_notional": "5",
    }


def _trade(data: Path, *, market: str = "spot", action: str = "buy", qty: str = "1",
           request: str = "r1", quote: dict | None = None, leverage: int = 1):
    return ledger.trade(data, market=market, symbol="BTCUSDT", action=action,
                        quantity=qty, leverage=leverage, quote=quote or _quote(market),
                        request_id=request)


def test_spot_buy_sell_isolated_from_futures(tmp_path: Path):
    order, account = _trade(tmp_path)
    assert order["price"] == "101"
    assert account["spot"]["positions"]["BTCUSDT"]["qty"] == "1"
    assert account["usdm"]["cash"] == "10000"
    sold, account = _trade(tmp_path, action="sell", qty="0.5", request="r2")
    assert sold["price"] == "100"
    assert account["spot"]["positions"]["BTCUSDT"]["qty"] == "0.5"
    assert float(sold["realized_pnl"]) < 0


def test_rejected_order_does_not_write_or_mutate(tmp_path: Path):
    with pytest.raises(ValueError, match="余额不足"):
        _trade(tmp_path, qty="100")
    assert not (tmp_path / "user_data" / "crypto_paper.json").exists()
    with pytest.raises(ValueError, match="整数倍"):
        _trade(tmp_path, qty="0.0011")
    assert ledger.load(tmp_path)["spot"]["cash"] == "10000"


def test_futures_open_close_and_short_profit(tmp_path: Path):
    _, account = _trade(tmp_path, market="usdm", action="open_short", request="short",
                        quote=_quote("usdm"), leverage=2)
    assert account["usdm"]["positions"]["BTCUSDT"]["margin"] == "50"
    assert float(account["usdm"]["realized_pnl"]) < 0  # entry fee
    closed, account = _trade(tmp_path, market="usdm", action="close_short", request="cover",
                             quote=_quote("usdm", bid="90", ask="91"), leverage=2)
    assert account["usdm"]["positions"] == {}
    assert float(closed["realized_pnl"]) > 0
    assert float(account["usdm"]["cash"]) > 10000
    assert account["spot"]["cash"] == "10000"


def test_equity_marks_spot_bid_and_futures_mark(tmp_path: Path):
    _trade(tmp_path)
    _trade(tmp_path, market="usdm", action="open_long", request="long",
           quote=_quote("usdm"), leverage=2)
    state = ledger.load(tmp_path)
    values = ledger.value_account(state, {
        ("spot", "BTCUSDT"): {"bid": "110"},
        ("usdm", "BTCUSDT"): {"mark": "111"},
    })
    assert float(values["spot"]["equity"]) > 10000
    assert float(values["usdm"]["unrealized_pnl"]) == 10
    assert float(values["usdm"]["equity"]) > 10000


def test_idempotent_retry_and_conflict(tmp_path: Path):
    first, _ = _trade(tmp_path)
    repeated, account = _trade(tmp_path)
    assert repeated == first
    assert len(account["trades"]) == 1
    with pytest.raises(ValueError, match="另一笔订单"):
        _trade(tmp_path, qty="2")
    _trade(tmp_path, market="usdm", action="open_long", request="futures", leverage=2,
           quote=_quote("usdm"))
    with pytest.raises(ValueError, match="另一笔订单"):
        _trade(tmp_path, market="usdm", action="open_long", request="futures", leverage=3,
               quote=_quote("usdm"))


def test_corrupt_ledger_is_preserved(tmp_path: Path):
    path = tmp_path / "user_data" / "crypto_paper.json"
    path.parent.mkdir()
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="原文件已保留"):
        _trade(tmp_path)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_public_quote_only_uses_allowlisted_endpoint(monkeypatch):
    calls = []

    def fake_get(market, path, symbol):
        calls.append((market, path, symbol))
        if path == "ticker/bookTicker":
            return {"symbol": symbol, "bidPrice": "100", "askPrice": "101"}
        if path == "premiumIndex":
            return {"symbol": symbol, "markPrice": "100.5", "lastFundingRate": "0.0001", "nextFundingTime": 123}
        return {"symbols": [{"symbol": symbol, "status": "TRADING", "filters": [
            {"filterType": "MARKET_LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]}]}

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.quote("usdm", "BTCUSDT")
    assert result["mark"] == "100.5"
    assert result["last_funding_rate"] == "0.0001"
    assert {item[1] for item in calls} == {"ticker/bookTicker", "premiumIndex", "exchangeInfo"}
    with pytest.raises(ValueError):
        client.quote("usdm", "BTCUSDT/../order")
    assert len(calls) == 3


def test_spot_zero_market_lot_uses_conservative_lot_size(monkeypatch):
    monkeypatch.setattr(client, "_RULES", {})

    def fake_get(_market, path, symbol):
        if path == "ticker/bookTicker":
            return {"symbol": symbol, "bidPrice": "100", "askPrice": "101"}
        return {"symbols": [{"symbol": symbol, "status": "TRADING", "filters": [
            {"filterType": "MARKET_LOT_SIZE", "minQty": "0", "stepSize": "0"},
            {"filterType": "LOT_SIZE", "minQty": "0.00001", "stepSize": "0.00001"},
        ]}]}

    monkeypatch.setattr(client, "_get", fake_get)
    assert client.quote("spot", "BTCUSDT")["step_size"] == "0.00001"


def test_replay_survives_quote_outage_and_requires_request_id(tmp_path: Path, monkeypatch):
    app = FastAPI()
    app.include_router(build_router())
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(client, "quote", lambda market, symbol: _quote(market))
    api = TestClient(app)
    body = {"market": "spot", "symbol": "BTCUSDT", "action": "buy",
            "quantity": "1", "leverage": 1, "request_id": "once"}
    first = api.post("/api/custom/crypto-paper/orders", json=body)
    assert first.status_code == 200
    monkeypatch.setattr(client, "quote", lambda market, symbol: (_ for _ in ()).throw(RuntimeError("offline")))
    retried = api.post("/api/custom/crypto-paper/orders", json=body)
    assert retried.status_code == 200
    assert retried.json()["order"] == first.json()["order"]
    assert api.post("/api/custom/crypto-paper/orders", json={k: v for k, v in body.items() if k != "request_id"}).status_code == 422
    assert len(ledger.load(tmp_path)["trades"]) == 1
    assert api.get("/api/custom/crypto-paper/quote/spot/NOTREAL").status_code == 400


def test_twenty_x_gap_liquidation_does_not_spend_free_cash(tmp_path):
    _, book = _trade(tmp_path, market="usdm", action="open_long", leverage=20,
                     quote=_quote("usdm", "100", "100"))
    free_cash = book["usdm"]["cash"]
    assert ledger.value_account(book, {("usdm", "BTCUSDT"): {"mark": "1"}})["usdm"]["equity"] == free_cash
    assert ledger.liquidate(book, "BTCUSDT", "1", 2000)
    assert book["usdm"]["cash"] == free_cash
    assert book["spot"]["cash"] == "10000"
    assert book["trades"][-1]["realized_pnl"] == "-5"
    assert not ledger.liquidate(book, "BTCUSDT", "1", 2000)


@pytest.mark.parametrize("side, rate", [("long", "0.0001"), ("short", "-0.0001")])
def test_funding_is_debit_once_and_isolated(side, rate):
    book = ledger._empty()
    ledger.execute(book, market="usdm", symbol="BTCUSDT", action=f"open_{side}", quantity="1",
                   leverage=20, quote=_quote("usdm", "100", "100"), request_id="opening", at_ms=1000)
    cash = book["usdm"]["cash"]
    events = [{"funding_time_ms": 2000, "rate": rate, "mark_price": "100"}]
    ledger.settle_funding(book, "BTCUSDT", events, 2000)
    ledger.settle_funding(book, "BTCUSDT", events, 3000)
    assert book["usdm"]["cash"] == cash
    assert book["usdm"]["positions"]["BTCUSDT"]["margin"] == "4.9900"
    assert len([item for item in book["trades"] if item["kind"] == "funding"]) == 1


def test_funding_credit_and_debit_cap_do_not_create_cross_wallet_losses():
    book = ledger._empty()
    ledger.execute(book, market="usdm", symbol="BTCUSDT", action="open_long", quantity="1",
                   leverage=20, quote=_quote("usdm", "100", "100"), request_id="opening", at_ms=1000)
    cash = book["usdm"]["cash"]
    ledger.settle_funding(book, "BTCUSDT", [{"funding_time_ms": 2000, "rate": "-0.01", "mark_price": "100"}], 2000)
    assert book["usdm"]["positions"]["BTCUSDT"]["margin"] == "6.00"
    ledger.settle_funding(book, "BTCUSDT", [{"funding_time_ms": 3000, "rate": "1", "mark_price": "100"}], 3000)
    assert book["usdm"]["cash"] == cash
    assert book["usdm"]["positions"] == {}
    event = book["trades"][-2]
    assert Decimal(event["funding_amount"]) == 6
    assert Decimal(event["scheduled_funding_amount"]) == 100


def test_strategy_api_creates_paused_comparison_and_checks_missing_account(tmp_path):
    app = FastAPI()
    app.include_router(build_router())
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    api = TestClient(app)
    body = {"name": "compare", "market": "usdm", "symbol": "BTCUSDT", "strategy_id": "ema_trend",
            "request_id": "compare"}
    response = api.post("/api/custom/crypto-paper/strategy-accounts/compare", json=body)
    assert response.status_code == 200
    rows = response.json()["accounts"]
    assert [row["leverage"] for row in rows] == [1, 5, 10, 20]
    assert all(row["enabled"] is False for row in rows)
    assert api.get("/api/custom/crypto-paper/strategy-accounts").json()["runtime"]["running"] is False
    assert api.post(f"/api/custom/crypto-paper/strategy-accounts/{rows[0]['id']}/enabled", json={"enabled": "true"}).status_code == 422
    assert api.get("/api/custom/crypto-paper/strategy-accounts/absent").status_code == 404
