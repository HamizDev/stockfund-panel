from __future__ import annotations

import httpx
import pytest

from app.custom.global_markets import service, us_classification
from app.custom.global_markets.catalog import MarketDataError


@pytest.fixture(autouse=True)
def clear_classification_cache():
    key = ("us-classification",)
    service._CACHE.pop(key, None)
    service._FAILURES.pop(key, None)
    yield
    service._CACHE.pop(key, None)
    service._FAILURES.pop(key, None)


def source_row(symbol, sector="Technology", industry="Software"):
    return {"symbol": symbol, "sector": sector, "industry": industry}


def payload(rows, as_of="asOf from Nasdaq"):
    return {"data": {"asOf": as_of, "rows": rows}}


def test_parse_normalizes_symbols_merges_identical_rows_and_drops_conflicts():
    result = us_classification._parse_payload(payload([
        source_row("aapl"),
        source_row("BRK.B", "Financials", "Insurance"),
        source_row("BRK-B", "Financials", "Insurance"),
        source_row("CLASH", "Technology", "Software"),
        source_row("CLASH", "Industrials", "Software"),
        source_row("EMPTY", "-", "N/A"),
        source_row("BAD$"),
    ]))

    assert result["source_as_of"] == "asOf from Nasdaq"
    assert result["row_count"] == 7
    assert result["items"] == {
        "AAPL": {"sector": "Technology", "industry": "Software"},
        "BRK-B": {"sector": "Financials", "industry": "Insurance"},
    }
    assert result["covered"] == 2
    assert result["sector_count"] == result["industry_count"] == 2
    assert result["conflict_count"] == 1
    assert result["invalid_symbol_count"] == 1


def test_missing_asof_stays_none_and_invalid_classification_strings_are_ignored():
    result = us_classification._parse_payload({"data": {"rows": [
        source_row("AAPL", "Technology", "I" * (us_classification.MAX_CLASSIFICATION_LENGTH + 1)),
        source_row("MSFT", "-", "Software"),
    ]}})

    assert result["source_as_of"] is None
    assert result["items"] == {
        "AAPL": {"sector": "Technology", "industry": None},
        "MSFT": {"sector": None, "industry": "Software"},
    }


@pytest.mark.parametrize("bad_payload", [
    {},
    {"data": {}},
    {"data": {"rows": {}}},
    {"data": {"rows": [None]}},
    {"data": {"rows": [{"symbol": "AAPL", "sector": "Technology"}]}},
    {"data": {"rows": [source_row("AAPL")], "asOf": "x" * (us_classification.MAX_AS_OF_LENGTH + 1)}},
])
def test_bad_source_schema_fails_closed(bad_payload):
    with pytest.raises(MarketDataError):
        us_classification._parse_payload(bad_payload)


def test_source_row_limit_is_checked_before_processing():
    rows = [source_row("AAPL")] * (us_classification.MAX_SOURCE_ROWS + 1)
    with pytest.raises(MarketDataError, match="数量或结构"):
        us_classification._parse_payload(payload(rows))


def test_enrich_uses_strict_us_identity_fills_only_missing_classification_and_preserves_quotes(monkeypatch):
    classifications = {
        "AAPL": {"sector": "Technology", "industry": "Software"},
        "BRK-B": {"sector": "Financials", "industry": "Insurance"},
        "XLE": {"sector": "Energy", "industry": None},
    }
    source = {
        "source": us_classification.SOURCE,
        "source_as_of": "asOf from Nasdaq",
        "fetched_at_ms": 1_900_000_000_000,
        "row_count": 3,
        "covered": 3,
        "sector_count": 3,
        "industry_count": 2,
        "distinct_sector_count": 3,
        "distinct_industry_count": 2,
        "conflict_count": 0,
        "items": classifications,
    }
    calls = []

    def cached(key, ttl, fetch):
        calls.append((key, ttl, fetch))
        return source

    monkeypatch.setattr(service, "_cached", cached)
    rows = [
        {"market": "us", "symbol": "aapl", "price": 101.25, "change_pct": 0.0125,
         "quote_at_ms": 123, "volume": 456, "sector": None, "industry": ""},
        {"market": "us", "symbol": "BRK.B", "price": 200.0, "industry": "Existing"},
        {"market": "hk", "symbol": "AAPL", "price": 7.0},
        {"market": "us", "symbol": "XLE", "price": 8.0},
        {"symbol": "AAPL", "price": 9.0},
    ]
    before_quotes = [
        {key: value for key, value in row.items() if key not in {"sector", "industry", "industry_source"}}
        for row in rows
    ]

    metadata = us_classification.enrich(rows)

    assert rows[0]["sector"] == "Technology" and rows[0]["industry"] == "Software"
    assert rows[0]["industry_source"] == "nasdaq_screener"
    assert rows[1]["sector"] == "Financials" and rows[1]["industry"] == "Existing"
    assert "industry_source" not in rows[1]
    assert rows[2] == {"market": "hk", "symbol": "AAPL", "price": 7.0}
    assert rows[3]["sector"] == "Energy" and "industry" not in rows[3]
    assert "industry_source" not in rows[3]
    assert rows[4] == {"symbol": "AAPL", "price": 9.0}
    after_quotes = [
        {key: value for key, value in row.items() if key not in {"sector", "industry", "industry_source"}}
        for row in rows
    ]
    assert after_quotes == before_quotes
    assert metadata["source"] == "nasdaq_screener"
    assert metadata["source_as_of"] == "asOf from Nasdaq"
    assert metadata["matched"] == 3
    assert metadata["enriched"] == 3
    assert metadata["row_count"] == 3 and metadata["covered"] == 3
    assert metadata["error"] is None
    assert calls[0][:2] == (("us-classification",), 86_400)
    assert calls[0][2] is us_classification.fetch


def test_enrich_failure_keeps_quote_rows_unchanged_and_uses_shared_failure_cache(monkeypatch):
    real_client = httpx.Client
    calls = []

    def client_factory(**kwargs):
        def handler(request):
            calls.append(request)
            return httpx.Response(429)

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(us_classification.httpx, "Client", client_factory)
    row = {"market": "us", "symbol": "AAPL", "price": 101.25,
           "change_pct": 0.01, "quote_at_ms": 789, "volume": 42}
    original = dict(row)

    first = us_classification.enrich([row])
    second = us_classification.enrich([row])

    assert first["error"] == second["error"] == "Nasdaq证券分类暂不可用"
    assert first["source_as_of"] is None and first["fetched_at_ms"] is None
    assert row == original
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.host == "api.nasdaq.com"
    assert "authorization" not in calls[0].headers
    assert "cookie" not in calls[0].headers
    assert ("us-classification",) in service._FAILURES


def test_fetch_reads_bounded_anonymous_download_response_without_retry(monkeypatch):
    real_client = httpx.Client
    calls = []

    def client_factory(**kwargs):
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=payload([
                source_row("AAPL", "Technology", "Software"),
                source_row("SPY", "Financials", "Exchange Traded Funds"),
            ], as_of="Nasdaq source date"))

        client = real_client(transport=httpx.MockTransport(handler), **kwargs)
        assert kwargs["timeout"] is us_classification.TIMEOUT
        assert kwargs["follow_redirects"] is False
        return client

    monkeypatch.setattr(us_classification.httpx, "Client", client_factory)
    result = us_classification.fetch()

    assert len(calls) == 1
    request = calls[0]
    assert request.method == "GET" and request.url.host == "api.nasdaq.com"
    assert dict(request.url.params) == us_classification.PARAMS
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert result["source_as_of"] == "Nasdaq source date"
    assert result["row_count"] == result["covered"] == 2
    assert result["sector_count"] == result["industry_count"] == 2
    assert result["fetched_at_ms"] > 0
    assert result["items"]["AAPL"]["industry"] == "Software"


def test_fetch_rejects_response_over_byte_limit_without_retry(monkeypatch):
    real_client = httpx.Client
    calls = []

    def client_factory(**kwargs):
        def handler(_request):
            calls.append(True)
            return httpx.Response(200, content=b"x" * (us_classification.MAX_RESPONSE_BYTES + 1))

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(us_classification.httpx, "Client", client_factory)
    with pytest.raises(MarketDataError, match="读取上限"):
        us_classification.fetch()
    assert len(calls) == 1
