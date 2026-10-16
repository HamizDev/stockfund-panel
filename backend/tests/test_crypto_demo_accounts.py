"""Contract fixtures are invented; tests never contact authenticated exchanges."""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.crypto_paper import demo_accounts as accounts
from app.custom.crypto_paper import demo_market as market
from app.custom.crypto_paper import demo_service as service
from app.custom.crypto_paper.demo_client import DemoAPIError
from app.custom.crypto_paper.demo_routes import build_router
from app.custom.crypto_paper.demo_store import DemoStoreError


def credentials(exchange="okx", mode="classic"):
    return {"exchange": exchange, "account_mode": mode, "site": "global", "api_key": "fake-key-value",
            "secret_key": "fake-secret-value", "passphrase": "fake-passphrase"}


def body(**kwargs):
    c = credentials()
    return service.Connect(**{k: c[k] for k in ("api_key", "secret_key", "passphrase")}, **kwargs)


@pytest.fixture(autouse=True)
def clean_runtime():
    service._SESSIONS.clear()
    service._GENERATIONS.clear()
    service._FETCH_LOCKS.clear()
    service._DISCONNECTED.clear()
    market._CACHE.clear()
    market._FETCH_LOCKS.clear()


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(build_router())
    return TestClient(app)


def test_okx_units_missing_fields_and_net_short():
    result = accounts._okx([
        [{"totalEq": "999.1", "details": [{"ccy": "USDT", "eq": "998", "availBal": "900"}]}],
        [{"instId": "ETH-USDT-SWAP", "pos": "-2", "posSide": "net", "avgPx": "3000"}],
        [{"ordId": "o", "instId": "ETH-USDT-SWAP", "sz": "3", "accFillSz": "1", "cTime": "1750000000000"}],
        [{"tradeId": "t", "fillSz": "1", "fee": "-0.02", "feeCcy": "USDT"}],
    ])
    assert result["equity_ccy"] == "USD"
    assert result["positions"][0]["side"] == "short"
    assert result["positions"][0]["quantity"] == "2"
    assert result["positions"][0]["quantity_unit"] == "张"
    assert result["positions"][0]["mark_price"] is None
    assert result["balances"][0]["unrealized_pnl"] is None
    assert result["fills"][0]["fee"] == "-0.02"


def test_bitget_classic_currency_units_and_fees():
    result = accounts._bitget([
        [{"marginCoin": "USDT", "accountEquity": "100", "available": "80", "unrealizedPL": "2"}],
        [{"symbol": "ETHUSDT", "total": "0.03", "holdSide": "long", "openPriceAvg": "3000"}],
        {"entrustedList": [{"symbol": "ETHUSDT", "size": "0.01", "baseVolume": "0.005"}]},
        {"fillList": [{"symbol": "ETHUSDT", "baseVolume": "0.005", "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.01", "totalDeductionFee": "0", "deduction": "no"}]}]},
    ], "classic")
    assert result["total_equity"] == "100"
    assert result["equity_ccy"] == "USDT"
    assert result["positions"][0]["quantity_unit"] == "ETH"
    assert result["orders"][0]["filled_quantity"] == "0.005"
    assert result["fills"][0]["fee_currency"] == "USDT"
    assert result["fills"][0]["fee"] == "-0.01"


def test_uta_different_response_fields_and_unknown_position_unit():
    result = accounts._bitget([
        {"accountEquity": "100.5", "assets": [{"coin": "USDT", "equity": "100.4", "available": "80"}]},
        {"list": [{"symbol": "ETHUSDT", "total": "0.01", "avgPrice": "3000", "unrealisedPnl": "0.5", "posSide": "long"}]},
        {"list": [{"symbol": "ETHUSDT", "qty": "0.02", "cumExecQty": "0.01", "orderStatus": "partially_filled", "createdTime": "1750000000000"}]},
        {"list": [{"symbol": "ETHUSDT", "execId": "u1", "execPrice": "3010", "execQty": "0.01", "createdTime": "1750000000000",
                   "feeDetail": [{"feeCoin": "USDT", "fee": "-0.01"}, {"feeCoin": "USDT", "fee": "-0.02"}]}]},
    ], "uta")
    assert result["equity_ccy"] == "USD"
    assert result["positions"][0]["quantity_unit"] == "交易所原始数量"
    assert result["positions"][0]["unrealized_pnl"] == "0.5"
    assert result["balances"][0]["unrealized_pnl"] is None
    assert result["orders"][0]["filled_quantity"] == "0.01"
    assert result["fills"][0]["fill_id"] == "u1"
    assert result["fills"][0]["fee"] == "-0.03"


@pytest.mark.parametrize("value", ["NaN", "Infinity", [], {}])
def test_nonfinite_or_untyped_financials_rejected(value):
    with pytest.raises(DemoAPIError):
        accounts.number(value)


def test_distinct_fee_currencies_never_added():
    assert accounts._fee([{"feeCoin": "ETH", "fee": "1"}, {"feeCoin": "USDT", "fee": "2"}])[0] is None


@pytest.mark.parametrize("exchange,mode,expected", [
    ("okx", "classic", "/api/v5/"), ("bitget", "classic", "/api/v2/"), ("bitget", "uta", "/api/v3/"),
])
def test_snapshot_selects_correct_readonly_endpoints(monkeypatch, exchange, mode, expected):
    called = []
    def fake(c, path, params):
        called.append((path, params))
        if exchange == "okx":
            return [{"totalEq": "0", "details": []}] if path.endswith("balance") else []
        if mode == "uta":
            return {"assets": [], "accountEquity": "0"} if path.endswith("assets") else {"list": []}
        if path.endswith("accounts") or "position" in path:
            return []
        return {"entrustedList": []} if path.endswith("orders-pending") else {"fillList": []}
    monkeypatch.setattr(accounts, "get", fake)
    result = accounts.snapshot(credentials(exchange, mode))
    assert len(called) == 4 and all(p.startswith(expected) for p, _ in called)
    assert result["environment"] == "demo" and result["readonly"] is True
    assert all("fill-history" not in p for p, _ in called)


@pytest.mark.parametrize("kwargs,expected_mode", [({}, "uta"), ({"account_mode": "classic"}, "classic")])
def test_bitget_connect_defaults_to_uta_and_keeps_classic_manual(monkeypatch, tmp_path, kwargs, expected_mode):
    received = {}

    def fake_snapshot(credentials):
        received.update(credentials)
        return {"exchange": "bitget", "site": "global", "account_mode": credentials["account_mode"],
                "environment": "demo", "readonly": True}

    monkeypatch.setattr(accounts, "snapshot", fake_snapshot)

    result = service.connect(tmp_path, "bitget", body(**kwargs))

    assert received["account_mode"] == expected_mode
    assert received["site"] == "global"
    assert result["account"]["account_mode"] == expected_mode


def test_failed_connection_preserves_previous_and_no_secret_in_status(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "snapshot", lambda _: {"exchange": "okx", "positions": []})
    result = service.connect(tmp_path, "okx", body())
    assert "fake-secret" not in str(result)
    assert "fake-key-value" not in str(service.accounts(tmp_path))
    monkeypatch.setattr(accounts, "snapshot", lambda _: (_ for _ in ()).throw(DemoAPIError("暂不可用")))
    with pytest.raises(DemoAPIError):
        service.connect(tmp_path, "okx", body())
    stale = service.snapshot(tmp_path, "okx", refresh=True)
    assert stale["stale"] and stale["error"] == "暂不可用"
    assert service.snapshot(tmp_path, "okx")["stale"] is True
    assert service.accounts(tmp_path)["accounts"][0]["configured"]


def test_saved_cold_start_failure_remains_saved_not_connected(monkeypatch, tmp_path):
    saved_credentials = credentials("bitget", "uta")
    saved_profile = {"site": "global", "account_mode": "uta", "masked_key": "bitget-****"}
    monkeypatch.setattr(service.demo_store, "profiles", lambda _: {"bitget": saved_profile})
    monkeypatch.setattr(service.demo_store, "read", lambda *_: saved_credentials)

    def fail_snapshot(_):
        raise DemoAPIError("transport unavailable", category="network")

    monkeypatch.setattr(accounts, "snapshot", fail_snapshot)

    with pytest.raises(DemoAPIError):
        service.snapshot(tmp_path, "bitget", refresh=True)

    status = service.accounts(tmp_path)["accounts"][1]
    assert status["state"] == "saved"
    assert status["configured"] is True and status["saved"] is True
    assert status["account_mode"] == "uta"
    assert service._key(tmp_path, "bitget") not in service._SESSIONS


def test_disconnect_during_inflight_connection_cannot_restore_key(monkeypatch, tmp_path):
    started, finish = threading.Event(), threading.Event()
    def delayed(_):
        started.set()
        assert finish.wait(5)
        return {"positions": []}
    monkeypatch.setattr(accounts, "snapshot", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.connect, tmp_path, "okx", body())
        assert started.wait(5)
        service.disconnect(tmp_path, "okx")
        finish.set()
        with pytest.raises(DemoAPIError, match="配置已更新"):
            pending.result(5)
    assert not service.accounts(tmp_path)["accounts"][0]["configured"]


def test_disconnect_clears_session_even_if_persisted_profile_cannot_be_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "snapshot", lambda _: {"positions": []})
    service.connect(tmp_path, "okx", body())
    key = service._key(tmp_path, "okx")
    old_generation = service._GENERATIONS[key]
    def inaccessible(*_):
        raise DemoStoreError("不可读的配置")
    monkeypatch.setattr(service.demo_store, "remove", inaccessible)
    with pytest.raises(DemoStoreError, match="内存会话已断开"):
        service.disconnect(tmp_path, "okx")
    assert key not in service._SESSIONS
    assert service._GENERATIONS[key] > old_generation
    # Even if a persistent credential can be read again later, this runtime's
    # explicit disconnect must prevent implicitly restoring that connection.
    monkeypatch.setattr(service.demo_store, "read", lambda *_: pytest.fail("must not restore revoked profile"))
    with pytest.raises(DemoAPIError, match="请先连接"):
        service.snapshot(tmp_path, "okx", refresh=True)


def test_old_refresh_during_rekey_is_blocked_before_using_old_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "snapshot", lambda _: {"owner": "initial"})
    service.connect(tmp_path, "okx", body())
    new_started, finish_new = threading.Event(), threading.Event()
    old_started, finish_old = threading.Event(), threading.Event()
    def delayed(c):
        if c["api_key"] == "fake-new-key-value":
            new_started.set()
            assert finish_new.wait(5)
            return {"owner": "new"}
        old_started.set()
        assert finish_old.wait(5)
        return {"owner": "old-late"}
    monkeypatch.setattr(accounts, "snapshot", delayed)
    new_body = service.Connect(api_key="fake-new-key-value", secret_key="fake-new-secret", passphrase="fake-new-pass")
    with ThreadPoolExecutor(max_workers=2) as pool:
        new_connection = pool.submit(service.connect, tmp_path, "okx", new_body)
        assert new_started.wait(5)
        old_refresh = pool.submit(service.snapshot, tmp_path, "okx", refresh=True)
        with pytest.raises(DemoAPIError, match="正在验证"):
            old_refresh.result(5)
        assert not old_started.is_set()
        finish_new.set()
        assert new_connection.result(5)["snapshot"]["owner"] == "new"
    assert service.snapshot(tmp_path, "okx")["owner"] == "new"


def test_secret_validation_error_never_echoes_input(client):
    secret = "FAKE-PRIVATE-INPUT-ONLY-FOR-TEST"
    for payload in [
        {"api_key": secret, "secret_key": secret, "passphrase": secret, "site": secret},
        {"api_key": "bad\n" + secret, "secret_key": secret, "passphrase": secret},
        {"api_key": secret, "secret_key": secret, "passphrase": secret, "unexpected": secret},
    ]:
        response = client.post("/demo/accounts/okx/connect", json=payload)
        assert response.status_code == 422 and secret not in response.text
    assert client.post("/demo/accounts/okx/connect", content=b"[", headers={"content-type": "application/json"}).status_code == 422


def test_cross_origin_reads_and_mutations_rejected(client):
    assert client.get("/demo/accounts", headers={"origin": "https://unrelated.invalid"}).status_code == 403
    assert client.get("/demo/accounts", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/demo/accounts/okx/disconnect", headers={"origin": "https://unrelated.invalid"}).status_code == 403
    result = client.get("/demo/accounts", headers={"origin": "http://testserver"})
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"


def test_connect_no_trade_routes_or_data_writes_on_empty_read(client, tmp_path):
    assert client.get("/demo/accounts").json()["accounts"][0]["configured"] is False
    assert not list(tmp_path.rglob("*"))
    assert client.post("/demo/orders").status_code == 404
    assert client.post("/demo/accounts/okx/order").status_code == 404
    assert client.post("/demo/accounts/okx/connect", content=b"x" * 8200, headers={"content-type": "application/json"}).status_code == 413


def okx_row(stamp, confirm="1"):
    return [str(stamp), "10", "12", "9", "11", "9", "0.09", "0.99", confirm]


def test_okx_chart_uses_closed_candles_base_volume_and_separate_market_cache(monkeypatch):
    calls = []
    def fake(site, endpoint, params):
        calls.append((site, endpoint, params))
        if endpoint == "time":
            return [{"ts": "180000"}]
        return [okx_row(120000, "0"), okx_row(60000), okx_row(0)]
    monkeypatch.setattr(market, "_request", fake)
    first = market.candles("okx", "ETHUSDT", "1m", "global")
    assert [r["open_time_ms"] for r in first["bars"]] == [0, 60000]
    assert first["bars"][0]["volume"] == "0.09"
    first["bars"].clear()
    assert len(market.candles("okx", "ETHUSDT", "1m")["bars"]) == 2
    assert len(calls) == 2
    assert calls[1][2]["instId"] == "ETH-USDT-SWAP"
    market.candles("okx", "ETHUSDT", "1m", "eea")
    assert calls[-1][0] == "eea"


@pytest.mark.parametrize("bad", [
    [okx_row(0), okx_row(0)], [okx_row(0), okx_row(120000)],
    [["0", "10", "9", "8", "11", "1", "1", "1", "1"]],
    [["0", "10", "12", "8", "11", "1", "NaN", "1", "1"]],
])
def test_okx_rejects_duplicate_gapped_or_invalid_data(monkeypatch, bad):
    monkeypatch.setattr(market, "_request", lambda s, e, p: [{"ts": "900000"}] if e == "time" else copy.deepcopy(bad))
    with pytest.raises(ValueError):
        market.candles("okx", "ETHUSDT", "1m")
