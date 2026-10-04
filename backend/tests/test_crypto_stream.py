"""Bitget public stream safety and read-only market API contracts."""
from __future__ import annotations

import copy
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.crypto_paper import bitget, client, market, public_stream, routes

NOW = 1_800_000_000_000


def message(**updates):
    row = {"symbol": "ETHUSDT", "instId": "ETHUSDT", "bidPr": "100", "askPr": "101",
           "markPrice": "100.5", "lastPr": "100.7", "ts": str(NOW)}
    row.update(updates)
    return {"arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "ETHUSDT"},
            "action": "snapshot", "data": [row]}


@pytest.fixture
def feed(monkeypatch):
    monkeypatch.setattr(public_stream.time, "time", lambda: NOW / 1000)
    value = public_stream.PublicStream()
    value._connected = True
    return value


def test_valid_ticker_copy_and_source_timestamp(feed):
    feed.accept(message())
    ticker = feed.ticker("ETHUSDT")
    assert ticker == {"symbol": "ETHUSDT", "bid": "100", "ask": "101", "mark": "100.5",
                      "last": "100.7", "asof_ms": NOW}
    ticker["bid"] = "999"
    assert feed.ticker("ETHUSDT")["bid"] == "100"
    assert feed.status("ETHUSDT")["fresh"] is True


@pytest.mark.parametrize("updates", [
    {"bidPr": "NaN"}, {"askPr": "Infinity"}, {"markPrice": "0"}, {"lastPr": None},
    {"bidPr": "102"}, {"symbol": "BTCUSDT"}, {"instId": "SOLUSDT"},
    {"ts": str(NOW - 15_001)}, {"ts": str(NOW + 5001)}, {"ts": True},
])
def test_invalid_snapshot_invalidates_symbol_instead_of_reusing_price(feed, updates):
    feed.accept(message())
    with pytest.raises(ValueError):
        feed.accept(message(**updates))
    assert feed.ticker("ETHUSDT") is None


def test_source_and_monotonic_age_both_required(feed, monkeypatch):
    monkeypatch.setattr(public_stream.time, "monotonic", lambda: 10)
    feed.accept(message())
    monkeypatch.setattr(public_stream.time, "monotonic", lambda: 26)
    assert feed.ticker("ETHUSDT") is None
    monkeypatch.setattr(public_stream.time, "monotonic", lambda: 10)
    monkeypatch.setattr(public_stream.time, "time", lambda: (NOW + 15_001) / 1000)
    assert feed.ticker("ETHUSDT") is None


def test_old_snapshot_does_not_regress_price(feed):
    feed.accept(message())
    feed.accept(message(ts=str(NOW - 1000), bidPr="90"))
    assert feed.ticker("ETHUSDT")["bid"] == "100"
    feed._connected = False
    assert feed.ticker("ETHUSDT") is None


def test_foreign_channel_and_subscription_error_not_accepted(feed):
    payload = message()
    payload["arg"]["instType"] = "SPOT"
    with pytest.raises(ValueError):
        feed.accept(payload)
    with pytest.raises(ValueError):
        feed.accept({"event": "error", "code": "60012"})
    assert feed.ticker("ETHUSDT") is None


def test_single_public_connection_subscribes_no_keys_and_shuts_down(monkeypatch):
    monkeypatch.setattr(public_stream.time, "time", lambda: NOW / 1000)
    sent = []
    observations = []
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            observations.append(feed.ticker("ETHUSDT"))
        def send(self, value):
            sent.append(value)
        def recv(self, timeout):
            if len(observations) == 0:
                observations.append("received")
                return json.dumps(message())
            feed._stop.set()
            return "pong"
        def close(self):
            pass
    def connector(url, **kwargs):
        assert url == "wss://ws.bitget.com/v2/ws/public"
        assert kwargs["open_timeout"] == 6
        return Socket()
    feed = public_stream.PublicStream(connector)
    feed.start()
    feed._thread.join(timeout=2)
    feed.stop()
    subscription = json.loads(sent[0])
    assert subscription["op"] == "subscribe"
    assert all(arg["instType"] == "USDT-FUTURES" and arg["channel"] == "ticker" for arg in subscription["args"])
    assert {arg["instId"] for arg in subscription["args"]} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert observations[-1]["bid"] == "100"
    assert feed.status("ETHUSDT")["connected"] is False
    assert feed._thread is None


def test_adapter_uses_ws_prices_with_rest_contract_metadata(feed, monkeypatch):
    feed.accept(message())
    monkeypatch.setattr(bitget, "stream", feed)
    monkeypatch.setattr(bitget, "_WS_FUNDING", {})
    monkeypatch.setattr(bitget, "_contract_rules", lambda _: {"step_size": "0.01", "taker_fee_rate": "0.0006"})
    monkeypatch.setattr(bitget, "_current_funding", lambda _: {"rate": "0.0001", "next_update_ms": NOW + 10000, "interval_hours": 8})
    monkeypatch.setattr(bitget, "_request", lambda *_: pytest.fail("fresh WS must avoid ticker REST request"))
    quote = bitget.quote("usdm", "ETHUSDT")
    assert quote["source"] == "bitget_public_websocket"
    assert quote["asof_ms"] == NOW
    assert quote["bid"] == "100" and quote["market"] == "usdm" and quote["exchange"] == "bitget"


def test_stop_clears_cache_even_if_socket_close_fails(feed):
    feed.accept(message())
    class BrokenSocket:
        def close(self):
            raise OSError("already disconnected")
    feed._socket = BrokenSocket()
    feed.stop()
    assert feed.ticker("ETHUSDT") is None
    assert feed.status("ETHUSDT")["connected"] is False
    assert feed._thread is None


def test_restart_waits_for_overlapping_stop_to_finish(monkeypatch):
    feed = public_stream.PublicStream()
    joined, release, restarted = threading.Event(), threading.Event(), threading.Event()
    class OldWorker:
        alive = True
        def is_alive(self):
            return self.alive
        def join(self, timeout):
            joined.set()
            assert release.wait(timeout=1)
            self.alive = False
    old = OldWorker()
    feed._thread = old
    monkeypatch.setattr(feed, "_run", lambda: feed._stop.wait())
    stopper = threading.Thread(target=feed.stop)
    starter = threading.Thread(target=lambda: (feed.start(), restarted.set()))
    stopper.start()
    assert joined.wait(timeout=1)
    starter.start()
    assert not restarted.wait(timeout=0.02)
    release.set()
    stopper.join(timeout=1)
    starter.join(timeout=1)
    assert restarted.is_set() and feed._thread is not old and feed._thread.is_alive()
    feed.stop()
    assert feed._thread is None


@pytest.mark.parametrize("fresh_schedule", [True, False])
def test_ws_funding_cache_expires_at_settlement_boundary(feed, monkeypatch, fresh_schedule):
    feed.accept(message())
    monkeypatch.setattr(bitget, "stream", feed)
    monkeypatch.setattr(bitget, "_WS_FUNDING", {"ETHUSDT": (
        public_stream.time.monotonic(), {"next_update_ms": NOW, "rate": "0.0001", "interval_hours": 8})})
    calls = []
    def funding(symbol):
        calls.append(symbol)
        return {"next_update_ms": NOW + (10000 if fresh_schedule else 0), "rate": "0.0001", "interval_hours": 8}
    monkeypatch.setattr(bitget, "_current_funding", funding)
    monkeypatch.setattr(bitget, "_contract_rules", lambda _: {})
    if fresh_schedule:
        assert bitget.quote("usdm", "ETHUSDT")["next_funding_time"] == NOW + 10000
    else:
        with pytest.raises(ValueError, match="结算时间待更新"):
            bitget.quote("usdm", "ETHUSDT")
    assert calls == ["ETHUSDT"]


def test_chart_cache_reuses_validated_data_and_copies(monkeypatch):
    monkeypatch.setattr(market, "_CACHE", {})
    calls = []
    def candles(*args, **kwargs):
        calls.append(args)
        return [{"open_time_ms": 1, "close": "100"}]
    monkeypatch.setattr(bitget, "klines", candles)
    first = market.candles("ETHUSDT", "1m")
    first["bars"][0]["close"] = "999"
    second = market.candles("ETHUSDT", "1m")
    assert second["bars"][0]["close"] == "100"
    assert len(calls) == 1 and second["closed_only"] is True
    with pytest.raises(ValueError):
        market.candles("NOTREAL", "1m")


def test_retired_manual_routes_no_quote_and_no_writes(tmp_path, monkeypatch):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(routes.build_router())
    monkeypatch.setattr(client, "quote", lambda *_: pytest.fail("Binance must not be called"))
    api = TestClient(app)
    assert api.get("/api/custom/crypto-paper/quote/spot/ETHUSDT").status_code == 410
    assert api.get("/api/custom/crypto-paper/valuation").status_code == 410
    response = api.post("/api/custom/crypto-paper/orders", json={"market": "usdm", "symbol": "ETHUSDT", "action": "open_long", "quantity": "1", "leverage": 10, "request_id": "legacy1"})
    assert response.status_code == 410
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize("interval", ["1m", "5m", "15m", "1h", "4h"])
def test_chart_period_adapter_filters_unclosed_bars(monkeypatch, interval):
    period, granularity = bitget.KLINE_INTERVALS[interval]
    rows = [[str(0), "100", "101", "99", "100", "1", "100"],
            [str(period), "100", "101", "99", "100", "1", "100"]]
    monkeypatch.setattr(bitget, "_server_time_ms", lambda: period)
    def request(endpoint, params):
        assert endpoint == "candles" and params["granularity"] == granularity
        return {"code": "00000", "data": copy.deepcopy(rows)}
    monkeypatch.setattr(bitget, "_request", request)
    bars = bitget.klines("usdm", "ETHUSDT", interval, 2)
    assert len(bars) == 1 and bars[0]["close_time_ms"] == period - 1
