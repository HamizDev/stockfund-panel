from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor

import httpx
import pytest

from app.custom.global_markets import hk_company_industry, service
from app.custom.global_markets.catalog import MarketDataError


@pytest.fixture(autouse=True)
def clear_hk_classification_cache():
    keys = [key for key in service._CACHE if key[0] in {"hk-company-industry", "chart"}]
    failures = [key for key in service._FAILURES if key[0] in {"hk-company-industry", "chart"}]
    for key in keys:
        service._CACHE.pop(key, None)
    for key in failures:
        service._FAILURES.pop(key, None)
    for symbol in ("00700.HK", "00005.HK"):
        hk_company_industry._CACHE.pop(symbol, None)
        hk_company_industry._FAILURES.pop(symbol, None)
    yield
    for key in keys + [key for key in service._CACHE if key[0] in {"hk-company-industry", "chart"}]:
        service._CACHE.pop(key, None)
    for key in failures + [key for key in service._FAILURES if key[0] in {"hk-company-industry", "chart"}]:
        service._FAILURES.pop(key, None)
    for symbol in ("00700.HK", "00005.HK"):
        hk_company_industry._CACHE.pop(symbol, None)
        hk_company_industry._FAILURES.pop(symbol, None)


def row(**overrides):
    return {
        "SECUCODE": "00700.HK",
        "SECURITY_CODE": "00700",
        "ORG_NAME": "腾讯控股有限公司",
        "BELONG_INDUSTRY": "软件服务",
        **overrides,
    }


def payload(rows):
    return {"success": True, "result": {"data": rows}}


def test_parse_returns_only_the_exact_f10_company_industry_contract():
    result = hk_company_industry.parse_payload(payload([row()]), "700")

    assert result is not None
    assert result["source"] == "eastmoney_hk_f10"
    assert result["system"] == "eastmoney_belong_industry"
    assert result["industry"] == "软件服务"
    assert isinstance(result["fetched_at_ms"], int) and result["fetched_at_ms"] > 0
    assert "source_as_of" not in result


@pytest.mark.parametrize("bad_row", [
    row(SECUCODE="00005.HK"),
    row(SECURITY_CODE="00005"),
    row(ORG_NAME=""),
    row(ORG_NAME="bad\nname"),
])
def test_parse_fails_closed_for_identity_or_company_record_mismatch(bad_row):
    with pytest.raises(MarketDataError):
        hk_company_industry.parse_payload(payload([bad_row]), "00700.HK")


@pytest.mark.parametrize("bad_payload", [
    {},
    {"success": False, "result": {"data": []}},
    {"success": True, "result": {"data": {}}},
    payload([row(), row()]),
    payload([None]),
])
def test_parse_rejects_invalid_response_shapes(bad_payload):
    with pytest.raises(MarketDataError):
        hk_company_industry.parse_payload(bad_payload, "00700.HK")


@pytest.mark.parametrize("industry", [None, "", "--", "x" * (hk_company_industry.MAX_INDUSTRY_LENGTH + 1), "bad\tvalue"])
def test_missing_or_invalid_industry_remains_unclassified(industry):
    assert hk_company_industry.parse_payload(payload([row(BELONG_INDUSTRY=industry)]), "00700.HK") is None


def test_fetch_requests_one_exact_symbol_anonymously_with_bounded_response(monkeypatch):
    real_client = httpx.Client
    calls = []

    def client_factory(**kwargs):
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=payload([row()]))

        client = real_client(transport=httpx.MockTransport(handler), **kwargs)
        assert kwargs["timeout"] is hk_company_industry.TIMEOUT
        assert kwargs["follow_redirects"] is False
        return client

    monkeypatch.setattr(hk_company_industry.httpx, "Client", client_factory)
    result = hk_company_industry.fetch("700")

    assert result["industry"] == "软件服务"
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "GET" and request.url.host == "datacenter.eastmoney.com"
    assert request.url.path == "/securities/api/data/v1/get"
    assert dict(request.url.params) == hk_company_industry.PARAMS | {
        "filter": '(SECUCODE="00700.HK")',
    }
    assert "authorization" not in request.headers and "cookie" not in request.headers


def test_fetch_rejects_oversize_response_without_retry(monkeypatch):
    real_client = httpx.Client
    calls = []

    def client_factory(**kwargs):
        def handler(_request):
            calls.append(True)
            return httpx.Response(200, content=b"x" * (hk_company_industry.MAX_RESPONSE_BYTES + 1))

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(hk_company_industry.httpx, "Client", client_factory)
    with pytest.raises(MarketDataError, match="读取上限"):
        hk_company_industry.fetch("00700.HK")
    assert len(calls) == 1


def test_get_uses_shared_per_symbol_ttl_cache_and_failures_become_null(monkeypatch):
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        return {
            "source": hk_company_industry.SOURCE,
            "system": hk_company_industry.SYSTEM,
            "industry": "软件服务",
            "fetched_at_ms": 123,
        }

    monkeypatch.setattr(hk_company_industry, "fetch", fetch)
    first = hk_company_industry.get("700")
    second = hk_company_industry.get("00700.HK")

    assert first == second == {
        "source": "eastmoney_hk_f10",
        "system": "eastmoney_belong_industry",
        "industry": "软件服务",
        "fetched_at_ms": 123,
    }
    assert calls == ["00700.HK"]
    assert "00700.HK" in hk_company_industry._CACHE
    assert ("hk-company-industry", "00700.HK") not in service._CACHE

    def fail(_symbol):
        calls.append("failure")
        raise MarketDataError("upstream failure")

    monkeypatch.setattr(hk_company_industry, "fetch", fail)
    assert hk_company_industry.get("00005") is None
    assert hk_company_industry.get("00005.HK") is None
    assert calls.count("failure") == 1
    assert "00005.HK" in hk_company_industry._FAILURES


def test_get_singleflights_concurrent_reads_for_the_same_symbol(monkeypatch):
    fetch_started = threading.Event()
    waiter_entered = threading.Event()
    release_fetch = threading.Event()
    calls = []

    class TrackingFuture(Future):
        def result(self, timeout=None):
            waiter_entered.set()
            return super().result(timeout=timeout)

    def fetch(symbol):
        calls.append(symbol)
        fetch_started.set()
        assert release_fetch.wait(2)
        return {
            "source": hk_company_industry.SOURCE,
            "system": hk_company_industry.SYSTEM,
            "industry": "软件服务",
            "fetched_at_ms": 123,
        }

    monkeypatch.setattr(hk_company_industry, "Future", TrackingFuture)
    monkeypatch.setattr(hk_company_industry, "fetch", fetch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(hk_company_industry.get, "700")
        assert fetch_started.wait(1)
        second = executor.submit(hk_company_industry.get, "00700.HK")
        assert waiter_entered.wait(1)
        release_fetch.set()
        assert first.result(timeout=2) == second.result(timeout=2)
    assert calls == ["00700.HK"]


def chart_value(kind="stock", symbol="00700.HK"):
    return {
        "market": "hk", "symbol": symbol, "kind": kind, "currency": "HKD",
        "quote": {"price": 600.0, "change_pct": 0.01}, "quote_at_ms": 456,
        "bars": [{"time": "2026-10-19", "close": 600.0}],
        "fetched_at_ms": 123, "cached": False, "stale": False, "error": None,
    }


def ready_future(value):
    future = Future()
    future.set_result(value)
    return future


def test_chart_attaches_only_independent_classification_for_verified_hkd_stock(monkeypatch):
    monkeypatch.setattr(service.provider, "fetch_chart", lambda *args: chart_value())
    metadata = {
        "source": "eastmoney_hk_f10", "system": "eastmoney_belong_industry",
        "industry": "软件服务", "fetched_at_ms": 789,
    }
    monkeypatch.setattr(service, "_submit_hk_classification", lambda _symbol: ready_future(metadata))

    result = service.chart("hk", "700", "1d")
    cached_chart = service._CACHE[("chart", "hk", "00700.HK", "1d")][1]

    assert result["classification"] == metadata
    assert result["quote"] == {"price": 600.0, "change_pct": 0.01}
    assert result["quote_at_ms"] == 456 and result["bars"][0]["close"] == 600.0
    assert "classification" not in cached_chart


def test_chart_does_not_attach_industry_to_etf_and_tolerates_pending_lookup(monkeypatch):
    monkeypatch.setattr(service.provider, "fetch_chart", lambda *args: chart_value(kind="etf"))
    calls = []
    metadata = {"source": "eastmoney_hk_f10", "system": "eastmoney_belong_industry",
                "industry": "不应显示", "fetched_at_ms": 789}
    monkeypatch.setattr(service, "_submit_hk_classification",
                        lambda symbol: calls.append(symbol) or ready_future(metadata))
    etf = service.chart("hk", "700", "1d")
    assert etf["classification"] is None and calls == []

    monkeypatch.setattr(service, "_submit_hk_classification",
                        lambda symbol: calls.append(symbol) or Future())
    monkeypatch.setattr(service, "HK_CLASSIFICATION_WAIT_SECONDS", 0.001)
    monkeypatch.setattr(service.provider, "fetch_chart",
                        lambda market, symbol, interval: chart_value(symbol=symbol))
    stock = service.chart("hk", "00005", "1d")
    assert stock["classification"] is None and calls == ["00005.HK"]


def test_chart_failure_never_starts_optional_f10_request(monkeypatch):
    def fail(*_args):
        raise MarketDataError("primary chart unavailable")

    monkeypatch.setattr(service.provider, "fetch_chart", fail)
    monkeypatch.setattr(service, "_submit_hk_classification",
                        lambda _symbol: pytest.fail("F10 must wait for a valid primary chart"))

    with pytest.raises(MarketDataError, match="primary chart unavailable"):
        service.chart("hk", "700", "1d")
