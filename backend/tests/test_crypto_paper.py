"""Crypto research ledger: no real exchange or user account calls."""
from __future__ import annotations

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
