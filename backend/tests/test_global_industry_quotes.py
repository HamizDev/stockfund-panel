from datetime import date
from email.message import Message

import pytest

from app.custom.global_markets import industry_quotes


def _row(
    ticker="AAPL",
    *,
    name="苹果",
    provider_symbol="AAPL.OQ",
    price="343.46",
    wall_time="2026-10-16 16:00:02",
    change_pct="0.43",
    currency="USD",
    status="200",
):
    fields = [""] * 73
    fields[0] = status
    fields[1] = name
    fields[2] = provider_symbol
    fields[3] = price
    fields[30] = wall_time
    fields[32] = change_pct
    fields[35] = currency
    return f'v_us{ticker}="{"~".join(fields)}";'


@pytest.fixture(autouse=True)
def reset_quote_state():
    with industry_quotes._lock:
        industry_quotes._cache.clear()
        industry_quotes._last_error.clear()
        industry_quotes._retry_after.clear()
        industry_quotes._active_key = None
    yield
    with industry_quotes._lock:
        industry_quotes._cache.clear()
        industry_quotes._last_error.clear()
        industry_quotes._retry_after.clear()
        industry_quotes._active_key = None


def test_parser_uses_only_verified_fields_and_keeps_source_date_without_epoch():
    quotes = industry_quotes._parse_response(
        _row(), ("AAPL",), today=date(2026, 10, 18)
    )

    quote = quotes["AAPL"]
    assert quote["name"] == "苹果"
    assert quote["provider_symbol"] == "AAPL.OQ"
    assert quote["price"] == 343.46
    assert quote["change_pct"] == 0.0043
    assert quote["currency"] == "USD"
    assert quote["source_wall_time"] == "2026-10-16 16:00:02"
    assert quote["quote_date"] == "2026-10-16"
    assert quote["basis"] == "source_reported_date"
    assert "时区" in quote["disclosure"] and "延迟未知" in quote["disclosure"]
    assert "quote_at_ms" not in quote


def test_parser_converts_negative_percent_points_to_fraction():
    quotes = industry_quotes._parse_response(
        _row(change_pct="-7.06"), ("AAPL",), today=date(2026, 10, 18)
    )
    assert quotes["AAPL"]["change_pct"] == -0.0706


@pytest.mark.parametrize(
    "row",
    [
        _row(provider_symbol="MSFT.OQ"),
        _row(currency="HKD"),
        _row(price="nan"),
        _row(price="inf"),
        _row(price="0"),
        _row(wall_time="2026-10-16 16:00"),
        _row(wall_time="2026-10-19 00:00:00"),
        _row(status="404"),
    ],
)
def test_parser_omits_rows_with_identity_currency_price_or_date_errors(row):
    quotes = industry_quotes._parse_response(
        row, ("AAPL",), today=date(2026, 10, 18)
    )
    assert quotes == {}


def test_parser_keeps_missing_change_percent_missing_not_zero():
    quotes = industry_quotes._parse_response(
        _row(change_pct=""), ("AAPL",), today=date(2026, 10, 18)
    )
    assert "change_pct" not in quotes["AAPL"]


def test_parser_rejects_duplicate_records_for_a_requested_ticker():
    with pytest.raises(industry_quotes.QuoteFetchError, match="duplicate"):
        industry_quotes._parse_response(
            _row() + "\n" + _row(), ("AAPL",), today=date(2026, 10, 18)
        )


def test_fetch_batch_uses_bounded_public_get_and_decodes_declared_charset(monkeypatch):
    body = _row().encode("gbk")
    headers = Message()
    headers["Content-Type"] = "text/html; charset=GBK"
    headers["Content-Length"] = str(len(body))

    class FakeResponse:
        status = 200

        def __enter__(self):
            self.headers = headers
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            assert size == industry_quotes.MAX_RESPONSE_BYTES + 1
            return body

    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(industry_quotes, "urlopen", fake_urlopen)
    quotes = industry_quotes._fetch_batch(("AAPL",))

    request, timeout = calls[0]
    assert request.get_method() == "GET"
    assert request.full_url == industry_quotes.BASE_URL + "usAAPL"
    assert timeout == industry_quotes.REQUEST_TIMEOUT_SECONDS
    assert quotes["AAPL"]["name"] == "苹果"


def test_fetch_batch_rejects_oversized_response_before_read(monkeypatch):
    headers = Message()
    headers["Content-Type"] = "text/html; charset=GBK"
    headers["Content-Length"] = str(industry_quotes.MAX_RESPONSE_BYTES + 1)

    class FakeResponse:
        status = 200

        def __init__(self, response_headers):
            self.headers = response_headers

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            pytest.fail("oversized response should be rejected before reading")

    monkeypatch.setattr(
        industry_quotes,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(headers),
    )
    with pytest.raises(industry_quotes.QuoteFetchError, match="size limit"):
        industry_quotes._fetch_batch(("AAPL",))


def test_request_limits_are_enforced_before_scheduling(monkeypatch):
    monkeypatch.setattr(industry_quotes, "_start_refresh", lambda key: None)
    with pytest.raises(ValueError, match="1 to 100"):
        industry_quotes.get(set())
    with pytest.raises(ValueError, match="1 to 100"):
        industry_quotes.get({f"T{i:03}" for i in range(101)})
    with pytest.raises(ValueError, match="uppercase"):
        industry_quotes.get({"aapl"})


def test_get_starts_one_background_refresh_and_returns_loading(monkeypatch):
    started = []
    monkeypatch.setattr(industry_quotes, "_start_refresh", lambda key: started.append(key))

    state, quotes = industry_quotes.get({"NVDA", "AAPL"})

    assert state["state"] == "loading"
    assert state["data"] is None
    assert state["loading"] is True
    assert state["stale"] is False
    assert state["error"] is None
    assert quotes == {}
    assert started == [("AAPL", "NVDA")]


def test_get_returns_fresh_cache_with_fetched_at_metadata(monkeypatch):
    now = industry_quotes.time.monotonic()
    key = ("AAPL",)
    quote = {"symbol": "AAPL", "price": 343.46}
    industry_quotes._cache[key] = industry_quotes._Snapshot(
        quotes={"AAPL": quote},
        fetched_at_ms=123456,
        completed_monotonic=now,
    )
    monkeypatch.setattr(
        industry_quotes,
        "_start_refresh",
        lambda key: pytest.fail("fresh cache should not refresh"),
    )

    state, quotes = industry_quotes.get({"AAPL"})

    assert state["state"] == "data"
    assert state["data"] == {"fetched_at_ms": 123456}
    assert state["loading"] is False
    assert state["stale"] is False
    assert state["error"] is None
    assert quotes == {"AAPL": quote}
    quotes["AAPL"]["price"] = 0
    assert industry_quotes._cache[key].quotes["AAPL"]["price"] == 343.46


def test_stale_cache_is_returned_while_refresh_runs(monkeypatch):
    key = ("AAPL",)
    cached = {"symbol": "AAPL", "price": 12.0}
    industry_quotes._cache[key] = industry_quotes._Snapshot(
        quotes={"AAPL": cached},
        fetched_at_ms=555,
        completed_monotonic=industry_quotes.time.monotonic() - industry_quotes.TTL_SECONDS - 1,
    )
    monkeypatch.setattr(industry_quotes, "_start_refresh", lambda key: None)

    state, quotes = industry_quotes.get({"AAPL"})

    assert state["state"] == "stale"
    assert state["data"] == {"fetched_at_ms": 555}
    assert state["loading"] is True
    assert state["stale"] is True
    assert state["error"] is None
    assert quotes == {"AAPL": cached}


def test_two_batches_are_bounded_and_assembled_before_return(monkeypatch):
    symbols = tuple(f"T{i:03}" for i in range(70))
    calls = []

    def fake_fetch_batch(batch):
        calls.append(batch)
        return {ticker: {"symbol": ticker, "price": 1.0} for ticker in batch}

    monkeypatch.setattr(industry_quotes, "_fetch_batch", fake_fetch_batch)
    result = industry_quotes._fetch_complete_snapshot(symbols)

    assert [len(batch) for batch in calls] == [60, 10]
    assert all(len(batch) <= industry_quotes.MAX_BATCH_SIZE for batch in calls)
    assert set(result) == set(symbols)


def test_failed_second_batch_keeps_previous_complete_snapshot(monkeypatch):
    symbols = tuple(f"T{i:03}" for i in range(70))
    key = symbols
    old = {"OLD": {"symbol": "OLD", "price": 42.0}}
    previous = industry_quotes._Snapshot(
        quotes=old,
        fetched_at_ms=987654,
        completed_monotonic=industry_quotes.time.monotonic() - industry_quotes.TTL_SECONDS - 5,
    )
    industry_quotes._cache[key] = previous
    calls = 0

    def fake_fetch_batch(batch):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {ticker: {"symbol": ticker, "price": 1.0} for ticker in batch}
        raise industry_quotes.QuoteFetchError("read failed")

    monkeypatch.setattr(industry_quotes, "_fetch_batch", fake_fetch_batch)
    industry_quotes._active_key = key
    industry_quotes._refresh_snapshot(key)

    assert calls == 2
    assert industry_quotes._cache[key] is previous
    assert industry_quotes._last_error[key] == industry_quotes.REFRESH_ERROR
    assert industry_quotes._active_key is None

    monkeypatch.setattr(industry_quotes, "_start_refresh", lambda key: None)
    state, quotes = industry_quotes.get(set(symbols))
    assert state["state"] == "stale"
    assert state["data"] == {"fetched_at_ms": 987654}
    assert state["loading"] is False
    assert state["stale"] is True
    assert state["error"] == industry_quotes.REFRESH_ERROR
    assert quotes == old


def test_error_state_has_required_shape_and_user_readable_error(monkeypatch):
    key = ("AAPL",)
    industry_quotes._last_error[key] = industry_quotes.REFRESH_ERROR
    industry_quotes._retry_after[key] = industry_quotes.time.monotonic() + 30
    monkeypatch.setattr(
        industry_quotes,
        "_start_refresh",
        lambda key: pytest.fail("retry delay should suppress another request"),
    )

    state, quotes = industry_quotes.get({"AAPL"})

    assert state["state"] == "error"
    assert state["data"] is None
    assert state["loading"] is False
    assert state["stale"] is False
    assert state["error"] == industry_quotes.REFRESH_ERROR
    assert quotes == {}


def test_sparse_success_keeps_missing_symbols_absent_not_zero(monkeypatch):
    symbols = tuple(f"T{i:03}" for i in range(70))

    def fake_fetch_batch(batch):
        return {batch[0]: {"symbol": batch[0], "price": 2.0}}

    monkeypatch.setattr(industry_quotes, "_fetch_batch", fake_fetch_batch)
    result = industry_quotes._fetch_complete_snapshot(symbols)

    assert set(result) == {symbols[0], symbols[60]}
    assert all("price" in quote_row for quote_row in result.values())
    assert all(ticker not in result for ticker in symbols[1:60] + symbols[61:])
