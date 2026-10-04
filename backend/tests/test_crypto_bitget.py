"""Bitget public USDT perpetual adapter contract tests."""
from __future__ import annotations

from collections import OrderedDict

import pytest

from app.custom.crypto_paper import bitget

_HOUR_MS = 60 * 60 * 1000
_DAY_MS = 24 * _HOUR_MS


def _ok(data):
    return {"code": "00000", "msg": "success", "data": data}


def _ticker(symbol="BTCUSDT", *, ts=1_790_812_800_000):
    return {
        "symbol": symbol,
        "bidPr": "100.00",
        "askPr": "101.00",
        "markPrice": "100.50",
        "ts": str(ts),
        "fundingRate": "0.0002",
    }


def _contract(symbol="BTCUSDT", **updates):
    row = {
        "symbol": symbol,
        "quoteCoin": "USDT",
        "supportMarginCoins": ["USDT"],
        "symbolType": "perpetual",
        "symbolStatus": "normal",
        "minTradeNum": "0.01",
        "sizeMultiplier": "0.01",
        "minTradeUSDT": "5",
        "maxMarketOrderQty": "1900",
        "maxLever": "150",
        "takerFeeRate": "0.0006",
    }
    row.update(updates)
    return row


def _current(symbol="BTCUSDT", *, interval="8", next_update=1_800_000_000_000):
    return {
        "symbol": symbol,
        "fundingRate": "-0.0003",
        "fundingRateInterval": interval,
        "nextUpdate": str(next_update),
    }


def _candle(open_time_ms: int, *, open_price="100", high="103", low="99", close="101") -> list[str]:
    return [str(open_time_ms), open_price, high, low, close, "2.50", "252.50"]


def _funding(symbol: str, funding_time_ms: int, rate="0.0001") -> dict:
    return {"symbol": symbol, "fundingTime": str(funding_time_ms), "fundingRate": rate}


def _mark(open_time_ms: int, *, open_price="90") -> list[str]:
    return [str(open_time_ms), open_price, "95", "89", "92", "0", "0"]


def _clear_caches(monkeypatch):
    monkeypatch.setattr(bitget, "_RULES", {})
    monkeypatch.setattr(bitget, "_MARKS", OrderedDict())


def test_quote_maps_contract_units_funding_schedule_and_supplier_time(monkeypatch):
    _clear_caches(monkeypatch)
    calls = []

    def fake_request(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "ticker":
            return _ok([_ticker()])
        if endpoint == "current-fund-rate":
            return _ok([_current(interval="4", next_update=1_800_000_000_000)])
        if endpoint == "contracts":
            return _ok([_contract()])
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", fake_request)

    row = bitget.quote("usdm", "BTCUSDT")

    assert row == {
        "market": "usdm",
        "exchange": "bitget",
        "symbol": "BTCUSDT",
        "bid": "100.00",
        "ask": "101.00",
        "mark": "100.50",
        "last_funding_rate": "-0.0003",
        "next_funding_time": 1_800_000_000_000,
        "funding_interval_hours": 4,
        "asof_ms": 1_790_812_800_000,
        "step_size": "0.01",
        "min_qty": "0.01",
        "max_qty": "1900",
        "min_notional": "5",
        "max_leverage": 150,
        "taker_fee_rate": "0.0006",
    }
    assert calls == [
        ("ticker", {"productType": "USDT-FUTURES", "symbol": "BTCUSDT"}),
        ("current-fund-rate", {"productType": "USDT-FUTURES", "symbol": "BTCUSDT"}),
        ("contracts", {"productType": "USDT-FUTURES", "symbol": "BTCUSDT"}),
    ]


def test_quote_uses_base_coin_quantity_and_cached_rules_are_not_mutable(monkeypatch):
    _clear_caches(monkeypatch)
    contract_calls = 0

    def fake_request(endpoint, _params):
        nonlocal contract_calls
        if endpoint == "ticker":
            return _ok([_ticker()])
        if endpoint == "current-fund-rate":
            return _ok([_current()])
        if endpoint == "contracts":
            contract_calls += 1
            return _ok([_contract(minTradeNum="0.02", sizeMultiplier="0.01", maxMarketOrderQty="12.34")])
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", fake_request)

    first = bitget.quote("usdm", "BTCUSDT")
    first["step_size"] = "999"
    second = bitget.quote("usdm", "BTCUSDT")

    assert second["min_qty"] == "0.02"
    assert second["step_size"] == "0.01"
    assert second["max_qty"] == "12.34"
    assert contract_calls == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"symbolStatus": "maintain"},
        {"symbolType": "delivery"},
        {"quoteCoin": "USDC"},
        {"supportMarginCoins": ["BTC"]},
        {"sizeMultiplier": "0"},
        {"minTradeNum": "0.015"},
        {"maxMarketOrderQty": "NaN"},
        {"minTradeUSDT": "-1"},
        {"maxLever": "NaN"},
        {"maxLever": "0"},
        {"takerFeeRate": "NaN"},
    ],
    ids=["status", "perpetual", "quote-usdt", "margin-usdt", "step", "quantity-grid", "max-qty", "min-notional", "leverage-nan", "leverage-zero", "fee"],
)
def test_quote_rejects_unsupported_or_malformed_contract_rules(monkeypatch, updates):
    _clear_caches(monkeypatch)

    def fake_request(endpoint, _params):
        if endpoint == "ticker":
            return _ok([_ticker()])
        if endpoint == "current-fund-rate":
            return _ok([_current()])
        if endpoint == "contracts":
            return _ok([_contract(**updates)])
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", fake_request)

    with pytest.raises(ValueError):
        bitget.quote("usdm", "BTCUSDT")


@pytest.mark.parametrize(
    "market,symbol",
    [("spot", "BTCUSDT"), ("usdm", "DOGEUSDT")],
)
def test_quote_rejects_market_and_symbol_before_http(monkeypatch, market, symbol):
    monkeypatch.setattr(bitget, "_request", lambda *_args: pytest.fail("must reject before HTTP"))

    with pytest.raises(ValueError):
        bitget.quote(market, symbol)


def test_public_request_is_unauthenticated_get_to_fixed_bitget_host(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            calls.append(("status",))

        def json(self):
            return _ok({"serverTime": "123"})

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

    monkeypatch.setattr(bitget.httpx, "Client", FakeClient)

    payload = bitget._request("time", {})

    assert payload == _ok({"serverTime": "123"})
    assert calls == [
        ("init", {"timeout": 6.0, "follow_redirects": False}),
        ("get", "https://api.bitget.com/api/v2/public/time", {}),
        ("status",),
    ]
    with pytest.raises(ValueError):
        bitget._request("https://attacker.invalid", {})


def test_business_error_reports_only_safe_code():
    with pytest.raises(ValueError, match="code=40037") as exc_info:
        bitget._data({"code": "40037", "msg": "secret response text", "data": []}, list, "行情")
    assert "secret response text" not in str(exc_info.value)


def test_klines_sort_rows_and_filter_open_candle_using_supplier_time(monkeypatch):
    calls = []

    def fake_request(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "time":
            return _ok({"serverTime": str(3 * _HOUR_MS + 500)})
        if endpoint == "candles":
            return _ok([
                _candle(3 * _HOUR_MS),
                _candle(2 * _HOUR_MS),
                _candle(_HOUR_MS),
                _candle(0),
            ])
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", fake_request)

    rows = bitget.klines("usdm", "BTCUSDT", limit=4)

    assert [row["open_time_ms"] for row in rows] == [0, _HOUR_MS, 2 * _HOUR_MS]
    assert rows[0] == {
        "open_time_ms": 0,
        "close_time_ms": _HOUR_MS - 1,
        "open": "100",
        "high": "103",
        "low": "99",
        "close": "101",
        "volume": "2.50",
    }
    assert [endpoint for endpoint, _params in calls] == ["time", "candles"]
    assert calls[1][1] == {
        "productType": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "granularity": "1H",
        "limit": "4",
    }


def test_klines_maps_four_hour_granularity_and_end_time(monkeypatch):
    calls = []

    def fake_request(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "time":
            return _ok({"serverTime": str(9 * _HOUR_MS)})
        return _ok([_candle(2 * 4 * _HOUR_MS), _candle(4 * _HOUR_MS), _candle(0)])

    monkeypatch.setattr(bitget, "_request", fake_request)

    rows = bitget.klines("usdm", "ETHUSDT", interval="4h", limit=3, end_ms=8 * _HOUR_MS)

    assert [row["open_time_ms"] for row in rows] == [0, 4 * _HOUR_MS]
    assert calls[1][1] == {
        "productType": "USDT-FUTURES",
        "symbol": "ETHUSDT",
        "granularity": "4H",
        "limit": "3",
        "endTime": str(8 * _HOUR_MS),
    }


@pytest.mark.parametrize(
    "rows,match",
    [
        ([_candle(0), _candle(0)], "重复"),
        ([_candle(0), _candle(2 * _HOUR_MS)], "缺口"),
        ([[_candle(0)[0]]], "格式"),
        ([_candle(0, high="98")], "OHLC"),
        ([[*_candle(0)[:6], "-1"]], "数值"),
    ],
    ids=["duplicate", "gap", "malformed", "ohlc", "bad-quote-volume"],
)
def test_klines_reject_duplicate_gap_and_bad_rows(monkeypatch, rows, match):
    def fake_request(endpoint, _params):
        return _ok({"serverTime": str(10 * _HOUR_MS)}) if endpoint == "time" else _ok(rows)

    monkeypatch.setattr(bitget, "_request", fake_request)

    with pytest.raises(ValueError, match=match):
        bitget.klines("usdm", "BTCUSDT", limit=10)


@pytest.mark.parametrize(
    "market,symbol,interval,limit",
    [
        ("spot", "BTCUSDT", "1h", 2),
        ("usdm", "DOGEUSDT", "1h", 2),
        ("usdm", "BTCUSDT", "30m", 2),
        ("usdm", "BTCUSDT", "1h", 1),
        ("usdm", "BTCUSDT", "1h", 1001),
    ],
)
def test_klines_validate_inputs_before_http(monkeypatch, market, symbol, interval, limit):
    monkeypatch.setattr(bitget, "_request", lambda *_args: pytest.fail("must reject before HTTP"))

    with pytest.raises(ValueError):
        bitget.klines(market, symbol, interval=interval, limit=limit)


def test_funding_history_paginates_descending_pages_deduplicates_and_estimates_mark(monkeypatch):
    _clear_caches(monkeypatch)
    calls = []
    event_times = [index * _HOUR_MS for index in range(101)]

    def fake_request(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "current-fund-rate":
            return _ok([_current("SOLUSDT", interval="1", next_update=1_000 * _HOUR_MS)])
        if endpoint == "history-fund-rate":
            if params["pageNo"] == "1":
                return _ok([_funding("SOLUSDT", stamp) for stamp in reversed(event_times[1:])])
            return _ok([_funding("SOLUSDT", event_times[1]), _funding("SOLUSDT", event_times[0])])
        if endpoint == "history-mark-candles":
            stamp = int(params["startTime"])
            return _ok([_mark(stamp)])
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", fake_request)

    rows = bitget.funding_history("SOLUSDT", event_times[0], event_times[-1])

    assert len(rows) == 101
    assert rows[0] == {
        "funding_time_ms": 0,
        "rate": "0.0001",
        "mark_price": "90",
        "mark_price_basis": "bitget_1m_mark_open_estimate",
    }
    assert rows[-1]["funding_time_ms"] == event_times[-1]
    history_calls = [params for endpoint, params in calls if endpoint == "history-fund-rate"]
    assert history_calls == [
        {"symbol": "SOLUSDT", "productType": "USDT-FUTURES", "pageNo": "1", "pageSize": "100"},
        {"symbol": "SOLUSDT", "productType": "USDT-FUTURES", "pageNo": "2", "pageSize": "100"},
    ]
    mark_calls = [params for endpoint, params in calls if endpoint == "history-mark-candles"]
    assert mark_calls[0] == {
        "productType": "USDT-FUTURES",
        "symbol": "SOLUSDT",
        "granularity": "1m",
        "startTime": "0",
        "endTime": "60000",
        "limit": "2",
    }
    assert len(mark_calls) == 101


def test_funding_history_rejects_gaps_larger_than_maximum_published_interval(monkeypatch):
    _clear_caches(monkeypatch)
    start = 30 * _DAY_MS
    events = [_funding("BTCUSDT", start), _funding("BTCUSDT", start + 9 * _HOUR_MS)]

    def fake_request(endpoint, _params):
        if endpoint == "current-fund-rate":
            return _ok([_current(next_update=start + 30 * _HOUR_MS)])
        if endpoint == "history-fund-rate":
            return _ok(list(reversed(events)))
        if endpoint == "history-mark-candles":
            return _ok([_mark(int(_params["startTime"]))])
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", fake_request)

    with pytest.raises(ValueError, match="缺口"):
        bitget.funding_history("BTCUSDT", start, start + 9 * _HOUR_MS)


def test_funding_history_requires_latest_published_boundary_event(monkeypatch):
    _clear_caches(monkeypatch)
    boundary = 10 * _DAY_MS
    start = boundary - _HOUR_MS
    end = boundary + _HOUR_MS

    def fake_request(endpoint, _params):
        if endpoint == "current-fund-rate":
            return _ok([_current("BTCUSDT", interval="8", next_update=boundary + 8 * _HOUR_MS)])
        if endpoint == "history-fund-rate":
            return _ok([_funding("BTCUSDT", boundary - 4 * _HOUR_MS)])
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", fake_request)

    with pytest.raises(ValueError, match="尚未发布"):
        bitget.funding_history("BTCUSDT", start, end)


def test_funding_history_fails_when_exact_event_mark_is_missing(monkeypatch):
    _clear_caches(monkeypatch)
    event = 100 * _HOUR_MS

    def fake_request(endpoint, _params):
        if endpoint == "current-fund-rate":
            return _ok([_current("ETHUSDT", next_update=event + 100 * _HOUR_MS)])
        if endpoint == "history-fund-rate":
            return _ok([_funding("ETHUSDT", event, "-0.0002")])
        if endpoint == "history-mark-candles":
            return _ok([_mark(event + 60_000)])
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", fake_request)

    with pytest.raises(ValueError, match="标记价格不可用"):
        bitget.funding_history("ETHUSDT", event, event)


def test_funding_history_rejects_stalled_pagination_and_bad_order(monkeypatch):
    _clear_caches(monkeypatch)
    start = _HOUR_MS
    page = [_funding("BTCUSDT", start + (100 - index) * _HOUR_MS) for index in range(100)]

    def stalled(endpoint, params):
        if endpoint == "current-fund-rate":
            return _ok([_current(next_update=start + 500 * _HOUR_MS)])
        if endpoint == "history-fund-rate":
            return _ok(page)
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", stalled)
    with pytest.raises(ValueError, match="没有前进"):
        bitget.funding_history("BTCUSDT", start, start + 100 * _HOUR_MS)

    _clear_caches(monkeypatch)

    def bad_order(endpoint, _params):
        if endpoint == "current-fund-rate":
            return _ok([_current(next_update=start + 500 * _HOUR_MS)])
        if endpoint == "history-fund-rate":
            return _ok([_funding("BTCUSDT", start), _funding("BTCUSDT", start + _HOUR_MS)])
        raise AssertionError(endpoint)

    monkeypatch.setattr(bitget, "_request", bad_order)
    with pytest.raises(ValueError, match="倒序"):
        bitget.funding_history("BTCUSDT", start, start + _HOUR_MS)


@pytest.mark.parametrize(
    "symbol,start_ms,end_ms",
    [
        ("DOGEUSDT", 0, 1),
        ("BTCUSDT", -1, 1),
        ("BTCUSDT", 2, 1),
        ("BTCUSDT", 0, 30 * _DAY_MS + 1),
    ],
)
def test_funding_history_validates_symbol_and_window_before_http(monkeypatch, symbol, start_ms, end_ms):
    monkeypatch.setattr(bitget, "_request", lambda *_args: pytest.fail("must reject before HTTP"))

    with pytest.raises(ValueError):
        bitget.funding_history(symbol, start_ms, end_ms)
