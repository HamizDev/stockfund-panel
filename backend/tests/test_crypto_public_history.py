"""Public Binance history client contract tests."""
from __future__ import annotations

import httpx
import pytest

from app.custom.crypto_paper import client

_HOUR_MS = 60 * 60 * 1000
_DAY_MS = 24 * _HOUR_MS


def _bar(open_time_ms: int, *, close_time_ms: int | None = None) -> list[object]:
    return [
        open_time_ms,
        "100.00",
        "103.00",
        "99.00",
        "101.00",
        "2.50",
        close_time_ms if close_time_ms is not None else open_time_ms + _HOUR_MS - 1,
        "252.50",
        12,
        "1.00",
        "101.00",
        "0",
    ]


def _funding(symbol: str, funding_time_ms: int, rate: str = "0.0001") -> dict:
    return {
        "symbol": symbol,
        "fundingTime": funding_time_ms,
        "fundingRate": rate,
        "markPrice": "100.25",
    }


def test_klines_use_public_params_and_filter_unclosed_bar(monkeypatch):
    calls = []

    def fake_request(market, path, params):
        calls.append((market, path, params))
        if path == "klines":
            return [_bar(0), _bar(_HOUR_MS), _bar(2 * _HOUR_MS)]
        if path == "time":
            return {"serverTime": 8_000_000}
        raise AssertionError(path)

    monkeypatch.setattr(client, "_request", fake_request)

    rows = client.klines("spot", "BTCUSDT", interval="1h", limit=20, end_ms=10_000_000)

    assert [row["open_time_ms"] for row in rows] == [0, _HOUR_MS]
    assert rows[0] == {
        "open_time_ms": 0,
        "close_time_ms": _HOUR_MS - 1,
        "open": "100.00",
        "high": "103.00",
        "low": "99.00",
        "close": "101.00",
        "volume": "2.50",
    }
    assert calls == [
        ("spot", "time", {}),
        ("spot", "klines", {"symbol": "BTCUSDT", "interval": "1h", "limit": 20, "endTime": 10_000_000}),
    ]


def test_klines_anchor_cutoff_across_hour_boundary_before_rows(monkeypatch):
    boundary = 24 * _HOUR_MS
    calls = []

    def fake_request(market, path, params):
        calls.append(path)
        if path == "time":
            return {"serverTime": boundary}
        if path == "klines":
            # The rows response contains the just-closed candle and a snapshot
            # of the new, still-open hour that was returned with it.
            return [_bar(boundary - _HOUR_MS), _bar(boundary)]
        raise AssertionError(path)

    monkeypatch.setattr(client, "_request", fake_request)

    rows = client.klines("spot", "BTCUSDT", interval="1h", limit=2)

    assert calls == ["time", "klines"]
    assert [row["open_time_ms"] for row in rows] == [boundary - _HOUR_MS]


def test_klines_accept_usdm_and_four_hour_interval(monkeypatch):
    calls = []

    def fake_request(market, path, params):
        calls.append((market, path, params))
        if path == "klines":
            return [_bar(0, close_time_ms=4 * _HOUR_MS - 1)]
        return {"serverTime": 4 * _HOUR_MS}

    monkeypatch.setattr(client, "_request", fake_request)

    rows = client.klines("usdm", "ETHUSDT", interval="4h", limit=2)

    assert len(rows) == 1
    assert calls[1] == (
        "usdm", "klines", {"symbol": "ETHUSDT", "interval": "4h", "limit": 2}
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda row: row[:6],
        lambda row: [*row[:2], "NaN", *row[3:]],
        lambda row: [*row[:5], "-1", *row[6:]],
        lambda row: [*row[:2], "98", *row[3:]],  # high below the open
        lambda row: [*row[:6], row[0] + _HOUR_MS - 2, *row[7:]],
    ],
    ids=["short-row", "nonfinite-price", "negative-volume", "invalid-ohlc", "partial-bar"],
)
def test_klines_reject_malformed_rows(monkeypatch, change):
    malformed = change(_bar(0))

    def fake_request(_market, path, _params):
        return [malformed] if path == "klines" else {"serverTime": 10 * _HOUR_MS}

    monkeypatch.setattr(client, "_request", fake_request)

    with pytest.raises(ValueError):
        client.klines("spot", "BTCUSDT", limit=2)


def test_klines_reject_nonascending_open_times(monkeypatch):
    def fake_request(_market, path, _params):
        return [_bar(_HOUR_MS), _bar(0)] if path == "klines" else {"serverTime": 10 * _HOUR_MS}

    monkeypatch.setattr(client, "_request", fake_request)

    with pytest.raises(ValueError, match="升序"):
        client.klines("spot", "BTCUSDT", limit=2)


@pytest.mark.parametrize(
    "args",
    [
        ("margin", "BTCUSDT", "1h", 2),
        ("spot", "NOTREAL", "1h", 2),
        ("spot", "BTCUSDT", "15m", 2),
        ("spot", "BTCUSDT", "1h", 1),
        ("spot", "BTCUSDT", "1h", 1001),
    ],
)
def test_klines_validate_allowlist_and_bounds(monkeypatch, args):
    def fail_request(*_args):
        raise AssertionError("validation must happen before HTTP")

    monkeypatch.setattr(client, "_request", fail_request)

    with pytest.raises(ValueError):
        client.klines(args[0], args[1], interval=args[2], limit=args[3])


def test_klines_propagate_network_failure(monkeypatch):
    def fail_request(*_args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(client, "_request", fail_request)

    with pytest.raises(httpx.ConnectError):
        client.klines("spot", "BTCUSDT", limit=2)


def test_funding_history_paginates_at_1000_and_deduplicates(monkeypatch):
    calls = []

    def fake_request(market, path, params):
        calls.append((market, path, params))
        if market != "usdm" or path != "fundingRate":
            raise AssertionError((market, path))
        if params["startTime"] == 0:
            return [_funding("BTCUSDT", timestamp) for timestamp in range(1000)]
        return [_funding("BTCUSDT", 999), _funding("BTCUSDT", 1000, "-0.0002")]

    monkeypatch.setattr(client, "_request", fake_request)

    rows = client.funding_history("BTCUSDT", 0, 1000)

    assert len(rows) == 1001
    assert rows[0] == {"funding_time_ms": 0, "rate": "0.0001", "mark_price": "100.25"}
    assert rows[-1] == {"funding_time_ms": 1000, "rate": "-0.0002", "mark_price": "100.25"}
    assert calls == [
        ("usdm", "fundingRate", {"symbol": "BTCUSDT", "startTime": 0, "endTime": 1000, "limit": 1000}),
        ("usdm", "fundingRate", {"symbol": "BTCUSDT", "startTime": 1000, "endTime": 1000, "limit": 1000}),
    ]


def test_funding_history_sorts_deduplicates_and_validates_values(monkeypatch):
    page = [
        _funding("SOLUSDT", 3000, "-0.0003"),
        _funding("SOLUSDT", 1000, "0"),
        _funding("SOLUSDT", 1000, "0"),
    ]
    monkeypatch.setattr(client, "_request", lambda *_args: page)

    rows = client.funding_history("SOLUSDT", 1000, 3000)

    assert [row["funding_time_ms"] for row in rows] == [1000, 3000]
    assert rows[0]["rate"] == "0"


@pytest.mark.parametrize(
    "record",
    [
        {"symbol": "BTCUSDT", "fundingTime": 1, "fundingRate": "NaN", "markPrice": "1"},
        {"symbol": "BTCUSDT", "fundingTime": 1, "fundingRate": "0.1", "markPrice": "0"},
        {"symbol": "ETHUSDT", "fundingTime": 1, "fundingRate": "0.1", "markPrice": "1"},
    ],
)
def test_funding_history_rejects_malformed_records(monkeypatch, record):
    monkeypatch.setattr(client, "_request", lambda *_args: [record])

    with pytest.raises(ValueError):
        client.funding_history("BTCUSDT", 0, 10)


def test_funding_history_propagates_network_failure(monkeypatch):
    def fail_request(*_args):
        raise httpx.ReadTimeout("offline")

    monkeypatch.setattr(client, "_request", fail_request)

    with pytest.raises(httpx.ReadTimeout):
        client.funding_history("BTCUSDT", 0, 1000)


@pytest.mark.parametrize(
    "symbol,start_ms,end_ms",
    [
        ("NOTREAL", 0, 1),
        ("BTCUSDT", -1, 1),
        ("BTCUSDT", 2, 1),
        ("BTCUSDT", 0, 90 * _DAY_MS + 1),
    ],
)
def test_funding_history_validates_symbol_and_range(monkeypatch, symbol, start_ms, end_ms):
    def fail_request(*_args):
        raise AssertionError("validation must happen before HTTP")

    monkeypatch.setattr(client, "_request", fail_request)

    with pytest.raises(ValueError):
        client.funding_history(symbol, start_ms, end_ms)


def test_quote_adds_max_qty_from_market_lot_rule(monkeypatch):
    def fake_get(market, path, symbol):
        if path == "ticker/bookTicker":
            return {"symbol": symbol, "bidPrice": "100", "askPrice": "101"}
        return {"symbols": [{"symbol": symbol, "status": "TRADING", "filters": [
            {"filterType": "MARKET_LOT_SIZE", "minQty": "0.001", "stepSize": "0.001", "maxQty": "10"},
        ]}]}

    monkeypatch.setattr(client, "_RULES", {})
    monkeypatch.setattr(client, "_get", fake_get)

    assert client.quote("spot", "BTCUSDT")["max_qty"] == "10"


def test_public_request_keeps_six_second_timeout_and_sends_no_key(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"serverTime": 123}

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, *, params):
            calls.append(("get", url, params))
            return FakeResponse()

    monkeypatch.setattr(client.httpx, "Client", FakeClient)

    assert client._request("spot", "time", {}) == {"serverTime": 123}
    assert calls[0] == ("init", {"timeout": 6.0, "follow_redirects": False})
    assert calls[1] == ("get", "https://data-api.binance.vision/api/v3/time", {})
