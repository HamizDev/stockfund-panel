from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from app.custom.global_markets import provider
from app.custom.global_markets.catalog import MarketDataError, normalize_symbol, yahoo_symbol


def fixture(market="us", interval="1d"):
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "AAPL" if market == "us" else "0700.HK",
                 "currency": "USD" if market == "us" else "HKD",
                 "exchangeTimezoneName": "America/New_York" if market == "us" else "Asia/Hong_Kong",
                 "instrumentType": "EQUITY", "dataGranularity": interval,
                 "regularMarketTime": 1792180801, "regularMarketPrice": 343.46,
                 "regularMarketChangePercent": 0.43, "longName": "Apple Inc."},
        "timestamp": [1792157400, 1792071000],
        "indicators": {"quote": [{"open": [346.085, 346.95], "high": [347.82, 347.85],
                                   "low": [340.46, 341.6], "close": [343.46, 342],
                                   "volume": [59946368, 39767256]}],
                       "adjclose": [{"adjclose": [1, 2]}]},
    }]}}


@pytest.mark.parametrize(("market", "input_", "expected", "remote"), [
    ("hk", "700", "00700.HK", "0700.HK"), ("hk", "02800.HK", "02800.HK", "2800.HK"),
    ("hk", "00005.hk", "00005.HK", "0005.HK"), ("us", "aapl", "AAPL", "AAPL"),
    ("us", "BRK.B", "BRK-B", "BRK-B"),
])
def test_explicit_market_symbols(market, input_, expected, remote):
    assert normalize_symbol(market, input_) == expected
    assert yahoo_symbol(market, input_) == remote


@pytest.mark.parametrize(("market", "symbol"), [
    ("us", "00700.HK"), ("us", "600519.SH"), ("us", "ABC.HK"),
    ("hk", "AAPL"), ("hk", "0"), ("hk", "../700"), ("us", "https://example.com"),
    ("cn", "AAPL"), ("hk", "123456"), ("us", "AAPL?x=y"),
])
def test_other_markets_and_paths_rejected(market, symbol):
    with pytest.raises(ValueError):
        normalize_symbol(market, symbol)


def test_raw_prices_percent_and_share_volume_are_not_mixed_with_adjusted_prices():
    result = provider.parse_chart("us", "AAPL", "1d", fixture())
    assert result["currency"] == "USD" and result["timezone"] == "America/New_York"
    assert result["name"] == "苹果" and result["symbol"] == "AAPL"
    assert result["quote"]["change_pct"] == pytest.approx(0.0043)
    assert result["bars"][-1] == {"time": "2026-10-16", "open": 346.085, "high": 347.82,
                                    "low": 340.46, "close": 343.46, "volume": 59946368}
    assert result["price_basis"] == "source_ohlc"


def test_minute_timestamps_remain_unambiguous_epoch_seconds():
    result = provider.parse_chart("us", "AAPL", "1m", fixture(interval="1m"))
    assert result["bars"][-1]["time"] == 1792157400
    assert datetime.fromtimestamp(result["bars"][-1]["time"], UTC).hour == 13


@pytest.mark.parametrize(("key", "value"), [
    ("symbol", "MSFT"), ("currency", "HKD"), ("exchangeTimezoneName", "Asia/Shanghai"),
    ("instrumentType", "CRYPTOCURRENCY"), ("dataGranularity", "1m"),
])
def test_upstream_identity_and_contract_must_match(key, value):
    payload = fixture()
    payload["chart"]["result"][0]["meta"][key] = value
    with pytest.raises(MarketDataError):
        provider.parse_chart("us", "AAPL", "1d", payload)


def test_missing_prices_are_not_zero_filled_and_bad_ohlc_is_skipped():
    payload = fixture()
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"][0] = None
    result = provider.parse_chart("us", "AAPL", "1d", payload)
    assert len(result["bars"]) == 1
    assert any("1条" in warning for warning in result["warnings"])
    payload["chart"]["result"][0]["indicators"]["quote"][0]["low"][1] = 9999
    with pytest.raises(MarketDataError):
        provider.parse_chart("us", "AAPL", "1d", payload)


@pytest.mark.parametrize("mutation", ["duplicate", "future", "boolean", "short_fields", "empty"])
def test_malformed_times_and_fields_fail_closed(mutation):
    payload = copy.deepcopy(fixture())
    row = payload["chart"]["result"][0]
    if mutation == "duplicate":
        row["timestamp"][1] = row["timestamp"][0]
    elif mutation == "future":
        row["timestamp"][0] = 99999999999
    elif mutation == "boolean":
        row["timestamp"][0] = True
    elif mutation == "short_fields":
        row["indicators"]["quote"][0]["high"] = []
    else:
        payload["chart"]["result"] = []
    with pytest.raises(MarketDataError):
        provider.parse_chart("us", "AAPL", "1d", payload)


@pytest.mark.parametrize("location", ["chart", "result", "meta", "indicators", "quote"])
def test_wrong_container_types_are_safe_upstream_errors(location):
    payload = fixture()
    result = payload["chart"]["result"][0]
    if location == "chart":
        payload["chart"] = []
    elif location == "result":
        payload["chart"]["result"] = [None]
    elif location == "meta":
        result["meta"] = []
    elif location == "indicators":
        result["indicators"] = []
    else:
        result["indicators"]["quote"] = [[]]
    with pytest.raises(MarketDataError):
        provider.parse_chart("us", "AAPL", "1d", payload)


def test_search_is_market_scoped(monkeypatch):
    monkeypatch.setattr(provider, "_json", lambda *args: {"quotes": [
        {"symbol": "AAPL", "quoteType": "EQUITY", "exchange": "NMS", "longname": "Apple"},
        {"symbol": "0700.HK", "quoteType": "EQUITY", "exchange": "HKG", "longname": "Tencent"},
        {"symbol": "SPY", "quoteType": "ETF", "exchange": "PCX", "longname": "SPDR"},
        {"symbol": "BTC-USD", "quoteType": "CRYPTOCURRENCY", "exchange": "CCC"},
    ]})
    assert [row["symbol"] for row in provider.search("us", "a")] == ["AAPL", "SPY"]
    assert [row["symbol"] for row in provider.search("hk", "a")] == ["00700.HK"]


def test_malformed_search_rows_cannot_break_catalog(monkeypatch):
    monkeypatch.setattr(provider, "_json", lambda *args: {"quotes": [
        {"symbol": "IBM", "quoteType": ["EQUITY"], "exchange": "NYQ"},
        {"symbol": "IBM", "quoteType": "EQUITY", "exchange": ["NYQ"]},
        {"symbol": None, "quoteType": "EQUITY", "exchange": "NYQ"},
        {"symbol": "IBM", "quoteType": "EQUITY", "exchange": "NYQ", "longname": 123},
    ]})
    assert provider.search("us", "ibm") == [{"symbol": "IBM", "name": "IBM", "market": "us", "kind": "stock"}]


def test_us_search_deduplicates_ads_and_excludes_korean_common_shares(monkeypatch):
    monkeypatch.setattr(provider, "_json", lambda *args: {"quotes": [
        {"symbol": "SKHY", "quoteType": "EQUITY", "exchange": "NMS", "longname": "SK hynix Inc."},
        {"symbol": "SKHY", "quoteType": "EQUITY", "exchange": "NMS", "longname": "SK Hynix"},
        {"symbol": "000660.KS", "quoteType": "EQUITY", "exchange": "KSC", "longname": "SK hynix Inc."},
    ]})
    assert provider.search("us", "Hynix") == [{
        "symbol": "SKHY", "name": "SK海力士（Nasdaq ADS）", "market": "us", "kind": "stock",  # noqa: RUF001
    }]


def test_empty_source_search_does_not_issue_request(monkeypatch):
    monkeypatch.setattr(provider, "_json", lambda *args: pytest.fail("empty query must not call upstream"))
    assert provider.search("us", "  ") == []


def test_null_symbol_and_quote_containers_are_safe_upstream_errors():
    for is_symbol in (True, False):
        payload = fixture()
        result = payload["chart"]["result"][0]
        if is_symbol:
            result["meta"]["symbol"] = None
        else:
            result["indicators"]["quote"] = [None]
        with pytest.raises(MarketDataError):
            provider.parse_chart("us", "AAPL", "1d", payload)
