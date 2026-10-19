import pytest
from app.custom.global_markets import symbol_directory
from app.custom.global_markets.catalog import MarketDataError


def _code(index: int) -> str:
    chars = []
    while index:
        index, digit = divmod(index, 26)
        chars.append(chr(ord("A") + digit))
    return "T" + "".join(reversed(chars or ["A"]))


def _file(header: tuple[str, ...], rows: list[list[str]]) -> str:
    return "\n".join([
        "|".join(header),
        *("|".join(row) for row in rows),
        "File Creation Time: 1016202621:31" + "|" * (len(header) - 1),
    ])


def _nasdaq_row(symbol: str, name: str, *, etf: str = "N", test: str = "N", status: str = "N") -> list[str]:
    return [symbol, name, "Q", test, status, "100", etf, "N"]


def _other_row(symbol: str, name: str, *, exchange: str = "N", etf: str = "N", test: str = "N",
               cqs: str | None = None) -> list[str]:
    return [symbol, name, exchange, cqs if cqs is not None else symbol, etf, "100", test, symbol]


def test_parse_nasdaq_directory_filters_test_and_invalid_symbols_and_detects_etf():
    rows = [
        _nasdaq_row("SKHY", "SK hynix Inc. - American Depositary Shares"),
        _nasdaq_row("AAAP", "Pacer ETF", etf="Y"),
        _nasdaq_row("TEST", "Test security", test="Y"),
        _nasdaq_row("BAD$", "Invalid ticker"),
        _nasdaq_row("BAD2", "Unknown financial status", status="X"),
        *[_nasdaq_row(_code(i), f"Example Issuer {i}") for i in range(1, 101)],
    ]
    result = symbol_directory._parse_file(
        _file(symbol_directory._NASDAQ_HEADER, rows), symbol_directory._NASDAQ_HEADER, nasdaq_listed=True
    )
    by_symbol = {symbol: (name, kind) for symbol, name, kind in result}
    assert by_symbol["SKHY"] == ("SK hynix Inc. - American Depositary Shares", "stock")
    assert by_symbol["AAAP"][1] == "etf"
    assert "TEST" not in by_symbol and "BAD$" not in by_symbol and "BAD2" not in by_symbol


def test_parse_other_exchange_directory_uses_us_ticker_and_filters_test_and_invalid_rows():
    rows = [
        _other_row("BRK.B", "Berkshire Hathaway Inc. Class B Common Stock"),
        _other_row("ETFA", "Example Fund", etf="Y", exchange="P"),
        _other_row("AAC=", "Unit with unsupported ticker", cqs="AAC.WS"),
        _other_row("TEST", "Test security", test="Y"),
        _other_row("BAD$", "Invalid ticker"),
        _other_row("EXCH", "Unknown exchange", exchange="X"),
        *[_other_row(_code(i), f"Example Issuer {i}") for i in range(1, 101)],
    ]
    result = symbol_directory._parse_file(
        _file(symbol_directory._OTHER_HEADER, rows), symbol_directory._OTHER_HEADER, nasdaq_listed=False
    )
    by_symbol = {symbol: (name, kind) for symbol, name, kind in result}
    assert by_symbol["BRK-B"] == ("Berkshire Hathaway Inc. Class B Common Stock", "stock")
    assert by_symbol["ETFA"][1] == "etf"
    assert by_symbol["AAC-WS"][0] == "Unit with unsupported ticker"
    assert "TEST" not in by_symbol and "BAD$" not in by_symbol and "EXCH" not in by_symbol


def test_refresh_uses_exactly_two_anonymous_bounded_https_requests(monkeypatch):
    nasdaq_rows = [_nasdaq_row(_code(i), f"Issuer {i}") for i in range(1, 101)]
    other_rows = [_other_row(_code(i + 100), f"Other Issuer {i}") for i in range(1, 101)]
    payloads = {
        symbol_directory.NASDAQ_LISTED_URL: _file(symbol_directory._NASDAQ_HEADER, nasdaq_rows).encode(),
        symbol_directory.OTHER_LISTED_URL: _file(symbol_directory._OTHER_HEADER, other_rows).encode(),
    }
    requests = []

    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_bytes(self):
            yield self.body

    class Client:
        def __init__(self, *, timeout, follow_redirects):
            assert timeout.connect == symbol_directory.REQUEST_TIMEOUT_SECONDS
            assert timeout.read == symbol_directory.REQUEST_TIMEOUT_SECONDS
            assert follow_redirects is False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, *, headers):
            requests.append((method, url, headers))
            return Response(payloads[url])

    monkeypatch.setattr(symbol_directory.httpx, "Client", Client)
    rows = symbol_directory._download_rows()
    assert len(rows) == 200
    assert [request[1] for request in requests] == [
        symbol_directory.NASDAQ_LISTED_URL,
        symbol_directory.OTHER_LISTED_URL,
    ]
    assert all(method == "GET" and headers == {"Accept": "text/plain"}
               for method, _, headers in requests)


def test_directory_data_is_cached_in_memory_and_results_are_bounded(monkeypatch):
    rows = tuple((f"T{i:03d}", f"Example Issuer {i}", "etf" if i == 1 else "stock") for i in range(35))
    calls = []
    monkeypatch.setattr(symbol_directory, "_download_rows", lambda: calls.append(True) or rows)
    monkeypatch.setattr(symbol_directory, "_CACHE_ROWS", None)
    monkeypatch.setattr(symbol_directory, "_CACHE_EXPIRES", 0.0)
    monkeypatch.setattr(symbol_directory, "_FAILURE_UNTIL", 0.0)

    result = symbol_directory.search("Example Issuer 1")
    assert result[0] == {"symbol": "T001", "name": "Example Issuer 1", "market": "us", "kind": "etf"}
    assert len(result) <= symbol_directory.MAX_RESULTS
    assert symbol_directory.search("example issuer 1") == result
    assert len(calls) == 1
    assert len(symbol_directory._CACHE_ROWS) == 35 <= symbol_directory.MAX_ROWS


def test_failed_refresh_is_short_cached(monkeypatch):
    calls = []
    clock = [100.0]
    monkeypatch.setattr(symbol_directory, "_download_rows", lambda: calls.append(True) or (_ for _ in ()).throw(
        ValueError("bad directory")))
    monkeypatch.setattr(symbol_directory.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(symbol_directory, "_CACHE_ROWS", None)
    monkeypatch.setattr(symbol_directory, "_CACHE_EXPIRES", 0.0)
    monkeypatch.setattr(symbol_directory, "_FAILURE_UNTIL", 0.0)

    for _ in range(2):
        try:
            symbol_directory.search("Example")
        except MarketDataError:
            pass
        else:
            raise AssertionError("a failed directory download must not return results")
    assert len(calls) == 1
    clock[0] += symbol_directory.FAILURE_SECONDS + 1
    with pytest.raises(MarketDataError):
        symbol_directory.search("Example")
    assert len(calls) == 2


def test_service_uses_directory_only_after_yahoo_misses_or_fails(monkeypatch):
    from app.custom.global_markets import provider, service

    directory_calls = []
    fallback = [{"symbol": "OPEN", "name": "Open Systems Inc.", "market": "us", "kind": "stock"}]
    monkeypatch.setattr(provider, "search", lambda market, query: [] if query == "Open Systems" else fallback)
    monkeypatch.setattr(symbol_directory, "search", lambda query: directory_calls.append(query) or fallback)

    miss = service.catalog("us", "Open Systems")
    hit = service.catalog("us", "Yahoo Match")
    assert miss["items"] == fallback and directory_calls == ["Open Systems"]
    assert any("Nasdaq Trader" in warning for warning in miss["warnings"])
    assert hit["items"] == fallback and directory_calls == ["Open Systems"]


def test_service_uses_directory_when_yahoo_search_raises(monkeypatch):
    from app.custom.global_markets import provider, service

    fallback = [{"symbol": "OPEN", "name": "Open Systems Inc.", "market": "us", "kind": "stock"}]
    monkeypatch.setattr(provider, "search", lambda *args: (_ for _ in ()).throw(MarketDataError("upstream")))
    monkeypatch.setattr(symbol_directory, "search", lambda _: fallback)
    result = service.catalog("us", "Open Systems Inc")
    assert result["items"] == fallback
    assert len(result["warnings"]) == 2


def test_local_alias_and_non_us_market_never_fetch_directory(monkeypatch):
    from app.custom.global_markets import provider, service

    monkeypatch.setattr(provider, "search", lambda *args: (_ for _ in ()).throw(AssertionError("unexpected Yahoo search")))
    monkeypatch.setattr(symbol_directory, "search", lambda *args: (_ for _ in ()).throw(
        AssertionError("unexpected Nasdaq directory request")))
    assert service.catalog("us", "海力士")["items"][0]["symbol"] == "SKHY"
    assert service.catalog("hk", "南方兩倍做多海力士")["items"][0]["symbol"] == "07709.HK"
