"""Deterministic tests for Tencent HK ETF quote parsing and batches."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.custom.global_markets import hk_tencent_etf as tencent
from app.custom.global_markets.catalog import MarketDataError

_HK = ZoneInfo("Asia/Hong_Kong")
_NOW = datetime(2026, 10, 20, 16, 15, tzinfo=_HK).timestamp()


def _directory(*codes: str) -> list[dict]:
    return [
        {
            "symbol": f"{code}.HK",
            "name": f"港股ETF {code}",
            "kind": "etf",
            "currency": "HKD",
        }
        for code in codes
    ]


def _fields(code: str, *, pct: str = "-1.13", timestamp: str = "2026/10/20 16:14:30") -> list[str]:
    fields = [""] * 78
    fields[1] = "Tencent name is not authoritative"
    fields[2] = code
    fields[3] = "24.540"
    fields[6] = "123456789"
    fields[30] = timestamp
    fields[31] = "-0.280"
    fields[32] = pct
    fields[37] = "999999.00"
    return fields


def _encoded_entry(symbol: str, *, data: str | None = None, fields: list[str] | None = None) -> str:
    if data is None:
        assert fields is not None
        data = "~".join(fields)
    return f'v_{symbol}="{data}"'


def test_parse_row_normalizes_identity_currency_time_and_percent():
    fields = _fields("02800")
    row = tencent.parse_row("hk02800", fields, _NOW, {"02800.HK": _directory("02800")[0]})

    assert row is not None
    assert row["symbol"] == "02800.HK"
    assert row["name"] == "港股ETF 02800"
    assert row["kind"] == "etf"
    assert row["currency"] == "HKD"
    assert row["price"] == 24.54
    assert row["change_pct"] == pytest.approx(-0.0113)
    assert row["quote_at_ms"] == round(datetime(2026, 10, 20, 16, 14, 30, tzinfo=_HK).timestamp() * 1000)
    assert row["volume"] is None
    assert row["amount"] is None


@pytest.mark.parametrize("timestamp", ["2026/10/20 16:14:30", "20261020161430"])
def test_parse_row_accepts_the_two_supported_timestamp_formats(timestamp: str):
    row = tencent.parse_row("hk02800", _fields("02800", timestamp=timestamp), _NOW,
                            {"02800.HK": _directory("02800")[0]})

    assert row is not None
    assert row["quote_at_ms"] == round(datetime(2026, 10, 20, 16, 14, 30, tzinfo=_HK).timestamp() * 1000)


@pytest.mark.parametrize("symbol", ["HK02800", "hk2800", "hk028000", "xhk02800", "hk0a800"])
def test_parse_row_rejects_noncanonical_tencent_symbol(symbol: str):
    assert tencent.parse_row(symbol, _fields("02800"), _NOW, {"02800.HK": _directory("02800")[0]}) is None


def test_parse_row_rejects_response_code_mismatch():
    assert tencent.parse_row("hk02800", _fields("03033"), _NOW, {"02800.HK": _directory("02800")[0]}) is None


@pytest.mark.parametrize("timestamp", ["", "not-a-time", "2026/10/20 16:20:01", "20261020162001"])
def test_parse_row_rejects_missing_invalid_or_more_than_five_minutes_future_time(timestamp: str):
    assert tencent.parse_row("hk02800", _fields("02800", timestamp=timestamp), _NOW,
                             {"02800.HK": _directory("02800")[0]}) is None


@pytest.mark.parametrize(
    ("security", "expected"),
    [
        ({"symbol": "02800.HK", "name": "x", "kind": "stock", "currency": "HKD"}, None),
        ({"symbol": "02800.HK", "name": "x", "kind": "etf", "currency": "USD"}, None),
        ({"symbol": "02800.HK", "name": "x", "kind": "etf", "currency": "HKD"}, "02800.HK"),
    ],
)
def test_parse_row_requires_hkex_etf_hkd_allowlist(security: dict, expected: str | None):
    row = tencent.parse_row("hk02800", _fields("02800"), _NOW, {"02800.HK": security})

    assert (row["symbol"] if row else None) == expected


def _install_mock_client(monkeypatch: pytest.MonkeyPatch, handler):
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def client_factory(*args, **kwargs):
        return real_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(tencent.httpx, "Client", client_factory)


def _requested_symbols(request: httpx.Request) -> list[str]:
    query = request.url.params.get("q", "")
    if not query:
        query = request.url.path.partition("=")[2]
    return [part for part in query.split(",") if part]


def _install_directory(monkeypatch: pytest.MonkeyPatch, *codes: str) -> None:
    from app.custom.global_markets import service

    monkeypatch.setattr(service, "hk_directory", lambda: _directory(*codes))
    monkeypatch.setattr(tencent.time, "sleep", lambda _seconds: None)


def test_fetch_etf_pool_streams_https_batches_and_accounts_for_empty_quotes(monkeypatch: pytest.MonkeyPatch):
    _install_directory(monkeypatch, "02800", "03033", "03110")
    monkeypatch.setattr(tencent, "BATCH_SIZE", 2)
    requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        batch = _requested_symbols(request)
        requests.append(batch)
        entries = []
        for symbol in batch:
            if symbol == "hk03033":
                entries.append(_encoded_entry(symbol, data=""))
            else:
                entries.append(_encoded_entry(symbol, fields=_fields(symbol[-5:])))
        return httpx.Response(200, content=(";".join(entries) + ";").encode("ascii"))

    _install_mock_client(monkeypatch, handler)
    rows, total = tencent.fetch_etf_pool()

    assert requests == [["hk02800", "hk03033"], ["hk03110"]]
    assert total == 3
    assert {row["symbol"] for row in rows} == {"02800.HK", "03110.HK"}


def test_fetch_etf_pool_counts_invalid_timestamp_quote_as_unavailable(monkeypatch: pytest.MonkeyPatch):
    _install_directory(monkeypatch, "02800", "03033")
    monkeypatch.setattr(tencent, "BATCH_SIZE", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        entries = [
            _encoded_entry("hk02800", fields=_fields("02800")),
            _encoded_entry("hk03033", fields=_fields("03033", timestamp="")),
        ]
        return httpx.Response(200, content=(";".join(entries) + ";").encode("ascii"))

    _install_mock_client(monkeypatch, handler)
    rows, total = tencent.fetch_etf_pool()

    assert total == 2
    assert [row["symbol"] for row in rows] == ["02800.HK"]


def test_fetch_etf_pool_rejects_batch_with_only_empty_quote_data(monkeypatch: pytest.MonkeyPatch):
    _install_directory(monkeypatch, "02800")

    _install_mock_client(
        monkeypatch,
        lambda _request: httpx.Response(200, content=_encoded_entry("hk02800", data="").encode("ascii")),
    )

    with pytest.raises(MarketDataError):
        tencent.fetch_etf_pool()


@pytest.mark.parametrize(
    "response_symbols",
    [
        ["hk02800"],  # One requested code is missing.
        ["hk02800", "hk09999"],  # One foreign/unrequested code is present.
        ["hk02800", "hk02800"],  # Duplicate code.
    ],
)
def test_fetch_etf_pool_rejects_missing_foreign_or_duplicate_codes(
    monkeypatch: pytest.MonkeyPatch, response_symbols: list[str],
):
    _install_directory(monkeypatch, "02800", "03033")
    monkeypatch.setattr(tencent, "BATCH_SIZE", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        entries = [_encoded_entry(symbol, fields=_fields(symbol[-5:])) for symbol in response_symbols]
        return httpx.Response(200, content=(";".join(entries) + ";").encode("ascii"))

    _install_mock_client(monkeypatch, handler)

    with pytest.raises(MarketDataError):
        tencent.fetch_etf_pool()


def test_fetch_etf_pool_rejects_malformed_payload(monkeypatch: pytest.MonkeyPatch):
    _install_directory(monkeypatch, "02800")

    _install_mock_client(monkeypatch, lambda _request: httpx.Response(200, content=b"not-a-tencent-row"))

    with pytest.raises(MarketDataError):
        tencent.fetch_etf_pool()


def test_fetch_etf_pool_does_not_retry_http_refusal(monkeypatch: pytest.MonkeyPatch):
    _install_directory(monkeypatch, "02800")
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, content=b"limited")

    _install_mock_client(monkeypatch, handler)

    with pytest.raises(MarketDataError):
        tencent.fetch_etf_pool()

    assert calls == 1


def test_fetch_etf_pool_rejects_oversized_stream(monkeypatch: pytest.MonkeyPatch):
    _install_directory(monkeypatch, "02800")

    _install_mock_client(monkeypatch, lambda _request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))

    with pytest.raises(MarketDataError):
        tencent.fetch_etf_pool()


def test_fetch_etf_pool_stops_at_total_deadline(monkeypatch: pytest.MonkeyPatch):
    _install_directory(monkeypatch, "02800", "03033")
    monkeypatch.setattr(tencent, "BATCH_SIZE", 1)
    monotonic_values = iter((0.0, 0.0, 31.0))
    monkeypatch.setattr(tencent, "time", type("FakeClock", (), {
        "time": staticmethod(lambda: _NOW),
        "perf_counter": staticmethod(lambda: 0.0),
        "monotonic": staticmethod(lambda: next(monotonic_values)),
        "sleep": staticmethod(lambda _seconds: None),
    }))
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        symbol = _requested_symbols(request)[0]
        entry = _encoded_entry(symbol, fields=_fields(symbol[-5:]))
        return httpx.Response(200, content=entry.encode("ascii"))

    _install_mock_client(monkeypatch, handler)

    with pytest.raises(MarketDataError):
        tencent.fetch_etf_pool()

    assert calls == 1
