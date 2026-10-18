from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx
import pytest

from app.custom.global_markets import us_screeners
from app.custom.global_markets.catalog import MarketDataError

STAMP = int(time.time()) - 3600


def quote(symbol, *, stamp=STAMP, currency="USD", price=100.0, change=1.25, volume=500,
          name=None, quote_type="EQUITY"):
    return {
        "symbol": symbol,
        "shortName": name or f"{symbol} Inc.",
        "quoteType": quote_type,
        "currency": currency,
        "regularMarketTime": stamp,
        "regularMarketPrice": price,
        "regularMarketChangePercent": change,
        "regularMarketVolume": volume,
    }


def payload(rows, count=None):
    return {"finance": {"result": [{
        "count": len(rows) if count is None else count,
        "quotes": rows,
    }], "error": None}}


def test_fetch_unions_three_bounded_screens_and_preserves_normalized_quote_fields():
    calls = []

    def handler(request):
        params = dict(request.url.params)
        screen = params["scrIds"]
        calls.append((request, params))
        rows = {
            "day_gainers": [quote("AAPL"), quote("FOREIGN", currency="EUR")],
            "day_losers": [quote("AAPL", stamp=STAMP + 1, price=101, change=-2.5), quote("MSFT")],
            "most_actives": [quote("MSFT"), quote("NVDA", volume=None, quote_type="ETF")],
        }[screen]
        return httpx.Response(200, json=payload(rows))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = us_screeners._collect(client, now=time.time())

    assert [params["scrIds"] for _, params in calls] == list(us_screeners.SCREEN_IDS)
    for request, params in calls:
        assert request.url.host == "query1.finance.yahoo.com"
        assert params["count"] == str(us_screeners.MAX_PER_SCREEN)
        assert params["region"] == "US"
        assert "start" not in params and "pn" not in params

    pool = result["pool"]
    assert pool["source"] == "yahoo_screener"
    assert pool["scope"] == "us_screener_pool"
    assert pool["universe_label"].endswith("not the full US market)")
    assert pool["all_requested_screens_received"] is True
    assert pool["received_rows"] == 6
    assert pool["unique_symbols"] == 3
    assert pool["duplicates_removed"] == 2
    assert pool["filtered_non_usd"] == 1
    assert pool["max_candidates"] == 750
    assert all(row["currency"] == "USD" for row in result["quotes"])

    by_symbol = {row["symbol"]: row for row in result["quotes"]}
    apple = by_symbol["AAPL"]
    assert apple["quote_at_ms"] == (STAMP + 1) * 1000
    assert apple["regularMarketTime"] == STAMP + 1
    assert apple["quote_date"] == datetime.fromtimestamp(
        STAMP + 1, UTC
    ).astimezone(us_screeners._US_ZONE).date().isoformat()
    assert apple["price"] == apple["regularMarketPrice"] == 101
    assert apple["change_pct"] == pytest.approx(-0.025)
    assert apple["regularMarketChangePercent"] == -2.5
    assert apple["currency"] == "USD"
    assert apple["volume"] == apple["regularMarketVolume"] == 500
    assert apple["amount"] is None and apple["turnover_rate"] is None and apple["industry"] is None
    assert apple["source"] == "yahoo_screener" and apple["scope"] == "us_screener_pool"
    assert apple["screen_ids"] == ["day_gainers", "day_losers"]
    assert by_symbol["NVDA"]["volume"] is None
    assert by_symbol["NVDA"]["kind"] == "etf"


def test_any_screen_failure_aborts_instead_of_returning_a_partial_pool():
    calls = []

    def handler(request):
        screen = dict(request.url.params)["scrIds"]
        calls.append(screen)
        if screen == "day_losers":
            return httpx.Response(429)
        return httpx.Response(200, json=payload([quote("AAPL")]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(MarketDataError, match=r"限流|失败"):
        us_screeners._collect(client, now=time.time())
    assert calls == ["day_gainers", "day_losers"]


@pytest.mark.parametrize("bad_payload", [
    {},
    {"finance": {"error": {"code": "Bad Request"}, "result": []}},
    {"finance": {"error": None, "result": [{"count": 251, "quotes": [quote("AAPL")] * 251}]}},
    {"finance": {"error": None, "result": [{"count": 1, "quotes": [None]}]}},
])
def test_invalid_or_oversized_screen_structure_fails_closed(bad_payload):
    def handler(_request):
        return httpx.Response(200, json=bad_payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(MarketDataError):
        us_screeners._read_screen(client, "day_gainers")


def test_response_body_size_limit_is_enforced():
    def handler(_request):
        return httpx.Response(200, content=b"x" * (us_screeners.MAX_RESPONSE_BYTES + 1))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(MarketDataError, match="读取上限"):
        us_screeners._read_screen(client, "day_gainers")


def test_non_usd_or_invalid_quote_time_is_not_returned_and_empty_pool_fails():
    invalid = [
        quote("EURQ", currency="EUR"),
        quote("FUTURE", stamp=int(time.time()) + 1000),
        quote("BADPRICE", price=0),
        quote("BADPCT", change="not-a-number"),
    ]

    def handler(_request):
        return httpx.Response(200, json=payload(invalid))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(MarketDataError, match="没有可用"):
        us_screeners._collect(client, now=time.time())


def test_reported_count_must_match_requested_page_bound():
    def handler(_request):
        return httpx.Response(200, json=payload([quote("AAPL")], count=0))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(MarketDataError, match="记录无效"):
        us_screeners._read_screen(client, "most_actives")
def test_public_fetch_uses_bounded_client_and_no_auth_headers(monkeypatch):
    original_client = httpx.Client
    options = []
    def client_factory(**kwargs):
        options.append(kwargs)
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json=payload([quote("AAPL")]))
        )
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(us_screeners.httpx, "Client", client_factory)
    result = us_screeners.fetch_us_screener_pool()

    assert len(options) == 1
    assert options[0]["timeout"] is us_screeners.TIMEOUT
    assert options[0]["timeout"].connect == 4.0
    assert options[0]["timeout"].read == 8.0
    assert options[0]["follow_redirects"] is False
    headers = {key.lower(): value for key, value in options[0]["headers"].items()}
    assert "cookie" not in headers and "authorization" not in headers
    assert headers["user-agent"].startswith("Mozilla/")
    assert result["quotes"][0]["fetched_at_ms"] > result["quotes"][0]["quote_at_ms"]
