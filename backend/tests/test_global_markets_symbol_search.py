
import json

import pytest
from app.custom.global_markets import symbol_search
from app.custom.global_markets.catalog import MarketDataError


def response(records: str) -> bytes:
    return f"v_hint={json.dumps(records, ensure_ascii=True)};".encode()


def test_parser_keeps_only_selected_market_and_normalizes_hk_code():
    payload = response(
        "sh~688981~中芯国际~zxgj~GP-A-KCB^hk~981~中芯国际~zxgj~GP^"
        "us~smicy.ps~中芯国际ADR~smicy~GP"
    )

    rows = symbol_search._parse_response("hk", payload)

    assert rows == [{"symbol": "00981.HK", "name": "中芯国际", "market": "hk", "kind": "stock"}]


def test_parser_normalizes_us_venues_and_rejects_other_market_codes():
    payload = response(
        "us~brk.a.n~伯克希尔~bkxe~GP^us~spy.am~SPDR S&P 500 ETF~spy~GP^"
        "us~foreign.ks~Foreign issuer~foreign~GP^us~bad.us~Invalid suffix~bad~GP^"
        "hk~00700~腾讯控股~tx~GP^sz~000001~平安银行~payh~GP"
    )

    rows = symbol_search._parse_response("us", payload)

    assert [row["symbol"] for row in rows] == ["BRK-A", "SPY"]
    assert rows[0]["kind"] == "stock"
    assert rows[1]["kind"] == "etf"
    assert all(row["market"] == "us" for row in rows)


def test_parser_deduplicates_caps_results_and_accepts_empty_response():
    records = "^".join(
        f"us~T{chr(65 + i)}.oq~Company {i}~company~GP" for i in range(25)
    )
    duplicate = records + "^us~TA.oq~Duplicate name~duplicate~GP"

    rows = symbol_search._parse_response("us", response(duplicate))

    assert len(rows) == symbol_search.MAX_RESULTS
    assert [row["symbol"] for row in rows].count("TA") == 1
    assert symbol_search._parse_response("us", response("N")) == []


@pytest.mark.parametrize(
    "payload",
    [b"<html>blocked</html>", b"v_hint=not-json;", b"\xff"],
)
def test_parser_rejects_malformed_responses(payload):
    with pytest.raises(MarketDataError):
        symbol_search._parse_response("us", payload)


def test_parser_rejects_oversized_response():
    with pytest.raises(MarketDataError, match="读取上限"):
        symbol_search._parse_response("us", b"x" * (symbol_search.MAX_RESPONSE_BYTES + 1))


def test_search_makes_one_bounded_anonymous_request(monkeypatch):
    calls = []
    body = response("us~skhy.oq~SK海力士~skhy~GP")

    class FakeResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def iter_bytes(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield body

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def stream(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(symbol_search.httpx, "Client", FakeClient)

    rows = symbol_search.search("us", "SK海力士")

    assert len([call for call in calls if call[0] == "GET"]) == 1
    assert calls[0][1]["timeout"].read == symbol_search.REQUEST_TIMEOUT_SECONDS
    request = calls[1]
    assert request[1] == symbol_search.SEARCH_URL
    assert request[2]["params"] == {"v": "2", "t": "all", "c": "1", "q": "SK海力士"}
    assert request[2]["headers"] == {"Accept": "text/plain"}
    assert rows == [{"symbol": "SKHY", "name": "SK海力士", "market": "us", "kind": "stock"}]
