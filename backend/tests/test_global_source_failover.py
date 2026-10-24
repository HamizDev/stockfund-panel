from __future__ import annotations

import json
import threading
import time
from contextlib import asynccontextmanager, contextmanager

import httpx
import pytest

from app.custom.global_markets import provider
from app.custom.global_markets.catalog import MarketDataError
from app.data_providers.source_health import SourceHealth
from app.services import preferences


class _FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


@pytest.fixture
def failover(monkeypatch):
    clock = _FakeClock()
    health = SourceHealth(clock=clock, base_cooldown=30, max_cooldown=300, max_entries=128)
    monkeypatch.setattr(preferences, "load", lambda: {"data_source_failover_enabled": True})
    monkeypatch.setattr(provider, "_FAILOVER_HEALTH", health)
    return health


def _install_client(monkeypatch, responder):
    calls = []

    def response(url, params, timeout, method, headers):
        calls.append({"method": method, "url": url, "params": params,
                      "timeout": timeout, "headers": headers})
        outcome = responder(url, params)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, tuple):
            status, body = outcome
        else:
            status, body = 200, json.dumps(outcome).encode()
        return httpx.Response(status, content=body)

    class Client:
        def __init__(self, *, timeout, follow_redirects):
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @contextmanager
        def stream(self, method, url, *, params, headers, timeout=None):
            yield response(url, params, timeout or self.timeout, method, headers)

    class AsyncClient:
        def __init__(self, *, timeout, follow_redirects):
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, *, params, headers):
            @asynccontextmanager
            async def request():
                yield response(url, params, self.timeout, method, headers)

            return request()

    monkeypatch.setattr(provider.httpx, "Client", Client)
    monkeypatch.setattr(provider.httpx, "AsyncClient", AsyncClient)
    return calls


def _valid_chart_payload():
    stamp = int(time.time()) - 86_400
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "AAPL", "currency": "USD",
                 "exchangeTimezoneName": "America/New_York", "instrumentType": "EQUITY",
                 "dataGranularity": "1d", "regularMarketTime": stamp,
                 "regularMarketPrice": 10.5},
        "timestamp": [stamp],
        "indicators": {"quote": [{"open": [10.0], "high": [11.0], "low": [9.0],
                                   "close": [10.5], "volume": [100]}]},
    }]}}


def test_failover_opt_in_is_required_and_query1_remains_primary(monkeypatch):
    monkeypatch.setattr(preferences, "load", lambda: {})
    calls = _install_client(monkeypatch, lambda *_: {"quotes": []})

    assert provider._json("/v1/finance/search", {"q": "AAPL"}) == {"quotes": []}
    assert [call["url"] for call in calls] == ["https://query1.finance.yahoo.com/v1/finance/search"]


def test_enabled_mode_uses_healthy_query1_without_probing_query2(monkeypatch, failover):
    calls = _install_client(monkeypatch, lambda *_: {"quotes": []})

    assert provider._json("/v1/finance/search", {"q": "AAPL"}) == {"quotes": []}
    assert [call["url"].split("/")[2] for call in calls] == ["query1.finance.yahoo.com"]


@pytest.mark.parametrize("failure", ["timeout", "429", "5xx", "invalid_json", "oversize"])
def test_transient_or_invalid_primary_response_uses_fixed_sibling_host(monkeypatch, failover, failure):
    def respond(url, _params):
        if "query2." in url:
            return {"quotes": []}
        if failure == "timeout":
            return httpx.ReadTimeout("private upstream detail")
        if failure == "429":
            return 429, b"rate limited"
        if failure == "5xx":
            return 503, b"private upstream detail"
        if failure == "invalid_json":
            return 200, b"not json"
        return 200, b" " * (provider._MAX_RESPONSE_BYTES + 1)

    calls = _install_client(monkeypatch, respond)

    assert provider._json("/v1/finance/search", {"q": "AAPL"}) == {"quotes": []}
    assert [call["url"].split("/")[2] for call in calls] == [
        "query1.finance.yahoo.com", "query2.finance.yahoo.com",
    ]
    assert all(call["timeout"].read <= 8 and call["timeout"].connect <= 4 for call in calls)


def test_cooling_query1_is_skipped_while_query2_remains_usable(monkeypatch, failover):
    calls = _install_client(monkeypatch, lambda url, _params: (
        httpx.ReadTimeout("private detail") if "query1." in url else {"quotes": []}
    ))

    assert provider._json("/v1/finance/search", {"q": "AAPL"}) == {"quotes": []}
    assert provider._json("/v1/finance/search", {"q": "MSFT"}) == {"quotes": []}
    assert [call["url"].split("/")[2] for call in calls] == [
        "query1.finance.yahoo.com", "query2.finance.yahoo.com", "query2.finance.yahoo.com",
    ]


def test_both_hosts_failing_returns_safe_error_without_upstream_details(monkeypatch, failover):
    calls = _install_client(monkeypatch, lambda *_: httpx.ReadTimeout("secret-like detail"))

    with pytest.raises(MarketDataError) as error:
        provider._json("/v8/finance/chart/AAPL", {"interval": "1d"})

    assert len(calls) == 2
    assert "secret-like detail" not in str(error.value)
    assert "https://" not in str(error.value)


def test_http_404_is_explicit_and_does_not_try_sibling_host(monkeypatch, failover):
    calls = _install_client(monkeypatch, lambda *_: (404, b"missing"))

    with pytest.raises(MarketDataError, match="未收录"):
        provider._json("/v8/finance/chart/UNKNOWN", {"interval": "1d"})

    assert [call["url"].split("/")[2] for call in calls] == ["query1.finance.yahoo.com"]


@pytest.mark.parametrize("invalid", [{}, {"chart": {"error": None, "result": None}}])
def test_semantically_invalid_chart_payload_uses_sibling_host(monkeypatch, failover, invalid):
    calls = _install_client(monkeypatch, lambda url, _params: (
        invalid if "query1." in url else _valid_chart_payload()
    ))

    chart = provider.fetch_chart("us", "AAPL", "1d")

    assert chart["bars"] and chart["symbol"] == "AAPL"
    assert [call["url"].split("/")[2] for call in calls] == [
        "query1.finance.yahoo.com", "query2.finance.yahoo.com",
    ]
    host1 = next(row for row in failover.snapshot() if row["key"] == ["yahoo_public", "query1.finance.yahoo.com"])
    assert host1["failures"] == 1


def test_search_missing_quotes_uses_sibling_but_empty_quotes_is_valid(monkeypatch, failover):
    calls = _install_client(monkeypatch, lambda url, _params: (
        {} if "query1." in url else {"quotes": []}
    ))

    assert provider.search("us", "AAPL") == []
    assert [call["url"].split("/")[2] for call in calls] == [
        "query1.finance.yahoo.com", "query2.finance.yahoo.com",
    ]
    host1 = next(row for row in failover.snapshot() if row["key"] == ["yahoo_public", "query1.finance.yahoo.com"])
    assert host1["failures"] == 1


def test_search_empty_quotes_on_primary_does_not_trigger_failover(monkeypatch, failover):
    calls = _install_client(monkeypatch, lambda *_: {"quotes": []})

    assert provider.search("us", "AAPL") == []
    assert [call["url"].split("/")[2] for call in calls] == ["query1.finance.yahoo.com"]


def test_older_concurrent_success_does_not_clear_new_host_failure(failover):
    key = ("yahoo_public", "query1.finance.yahoo.com")
    entered = threading.Event()
    release = threading.Event()
    old_result = []

    def slow_success():
        entered.set()
        assert release.wait(1)
        return {"quotes": []}

    worker = threading.Thread(target=lambda: old_result.append(
        failover.attempt(key, slow_success, accept=lambda value: isinstance(value, dict))))
    worker.start()
    assert entered.wait(1)

    failed = failover.attempt(
        key,
        lambda: (_ for _ in ()).throw(ValueError("private detail")),
        accept=lambda value: isinstance(value, dict),
    )
    assert not failed.success
    release.set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert old_result and old_result[0].success

    skipped = failover.attempt(key, lambda: pytest.fail("cooled host must not be called"),
                               accept=lambda value: isinstance(value, dict))
    assert skipped.skipped
    assert all("private detail" not in repr(row) for row in failover.snapshot())
