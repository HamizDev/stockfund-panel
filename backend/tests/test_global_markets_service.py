from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.global_markets import provider, service
from app.custom.global_markets.catalog import MarketDataError
from app.custom.global_markets.routes import build_router


@pytest.fixture(autouse=True)
def clear_cache():
    service._CACHE.clear()
    service._PENDING.clear()
    service._FAILURES.clear()
    yield
    service._CACHE.clear()
    service._PENDING.clear()
    service._FAILURES.clear()


def value(market, symbol, interval):
    return {"market": market, "symbol": symbol, "interval": interval,
            "bars": [{"close": 10}], "fetched_at_ms": 100, "cached": False,
            "stale": False, "error": None}


def test_market_interval_keys_and_defensive_copies(monkeypatch):
    calls = []

    def fetch(*args):
        calls.append(args)
        return value(*args)

    monkeypatch.setattr(provider, "fetch_chart", fetch)
    monkeypatch.setattr(service, "_submit_hk_classification", lambda _symbol: None)
    service.chart("hk", "700", "1d")["bars"][0]["close"] = 999
    hit = service.chart("hk", "00700.HK", "1d")
    assert hit["cached"] and hit["bars"][0]["close"] == 10
    service.chart("us", "AAPL", "1d")
    service.chart("us", "AAPL", "1m")
    assert len(calls) == 3
    assert len(service._CACHE) == 3


def test_chart_route_preserves_optional_classification_contract(monkeypatch):
    app = FastAPI()
    app.include_router(build_router())
    expected = {"market": "hk", "classification": {
        "source": "eastmoney_hk_f10", "system": "eastmoney_belong_industry",
        "industry": "软件服务", "fetched_at_ms": 123,
    }}
    monkeypatch.setattr(service, "chart", lambda *_args: expected)

    with TestClient(app) as client:
        response = client.get("/api/custom/global-markets/chart?market=hk&symbol=00700&interval=1d")

    assert response.status_code == 200
    assert response.json() == expected


def test_stale_data_is_explicit_and_original_fetch_time_retained(monkeypatch):
    key = ("chart", "us", "AAPL", "1d")
    service._CACHE[key] = (time.monotonic() - 1000, value("us", "AAPL", "1d"))
    calls = []

    def fail(*args):
        calls.append(args)
        raise MarketDataError("暂时限流")

    monkeypatch.setattr(provider, "fetch_chart", fail)
    for _ in range(2):
        result = service.chart("us", "AAPL", "1d")
        assert result["cached"] and result["stale"]
        assert result["error"] == "暂时限流" and result["fetched_at_ms"] == 100
    assert len(calls) == 1


def test_expired_cache_cannot_masquerade_as_available_data(monkeypatch):
    key = ("chart", "us", "AAPL", "1d")
    service._CACHE[key] = (time.monotonic() - 86401, value("us", "AAPL", "1d"))
    monkeypatch.setattr(provider, "fetch_chart", lambda *args: (_ for _ in ()).throw(MarketDataError("离线")))
    with pytest.raises(MarketDataError):
        service.chart("us", "AAPL", "1d")


def test_waiter_timeout_retains_only_explicitly_stale_cache():
    class Waiting:
        def result(self, timeout):
            raise TimeoutError

    key = ("chart", "us", "AAPL", "1d")
    service._CACHE[key] = (time.monotonic() - 1000, value("us", "AAPL", "1d"))
    service._PENDING[key] = Waiting()
    result = service.chart("us", "AAPL", "1d")
    assert result["cached"] and result["stale"] and result["error"]
    assert result["fetched_at_ms"] == 100
    service._CACHE.clear()
    with pytest.raises(MarketDataError):
        service.chart("us", "AAPL", "1d")


def test_parallel_same_request_shares_one_fetch(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fetch(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return value(*args)

    monkeypatch.setattr(provider, "fetch_chart", fetch)
    with ThreadPoolExecutor(max_workers=4) as executor:
        first = executor.submit(service.chart, "us", "AAPL", "1m")
        assert entered.wait(2)
        others = [executor.submit(service.chart, "us", "AAPL", "1m") for _ in range(3)]
        release.set()
        assert first.result()["symbol"] == "AAPL"
        assert all(f.result()["symbol"] == "AAPL" for f in others)
    assert len(calls) == 1 and not service._PENDING


def test_cache_and_failure_records_are_bounded(monkeypatch):
    monkeypatch.setattr(service, "MAX_CACHE", 3)
    for n in range(5):
        service._cached((n,), 60, lambda n=n: [n])
    assert len(service._CACHE) == 3
    for n in range(5):
        with pytest.raises(MarketDataError):
            service._cached(("bad", n), 60, lambda: (_ for _ in ()).throw(MarketDataError("断开")))
    assert len(service._FAILURES) == 3 and not service._PENDING


def test_local_names_need_no_network_and_no_cross_market_results(monkeypatch):
    monkeypatch.setattr(provider, "search", lambda *args: pytest.fail("local lookup must not request upstream"))
    assert service.catalog("hk", "腾讯")["items"][0]["symbol"] == "00700.HK"
    assert service.catalog("us", "苹果")["items"][0]["symbol"] == "AAPL"
    assert all(row["market"] == "us" for row in service.catalog("us")["items"])


def test_remote_failure_exposes_a_code_probe_not_a_fabricated_instrument(monkeypatch):
    monkeypatch.setattr(provider, "search", lambda *args: (_ for _ in ()).throw(MarketDataError("坏请求")))
    monkeypatch.setattr(service.symbol_search, "search", lambda *_: [])
    monkeypatch.setattr(service.symbol_directory, "search", lambda _: [])
    result = service.catalog("us", "IBM")
    assert result["items"][0]["name"].startswith("代码查询")
    assert len(result["warnings"]) == 2
    assert not service.catalog("us", "00700.HK")["items"]


def test_api_validation_errors_and_resource_contract(monkeypatch):
    app = FastAPI()
    app.include_router(build_router())
    client = TestClient(app)
    root = "/api/custom/global-markets"
    for suffix in ("/chart?market=us&symbol=00700.HK", "/chart?market=cn&symbol=AAPL",
                   "/chart?market=us&symbol=AAPL&interval=1h", "/catalog?market=us&q=" + "a" * 61):
        assert client.get(root + suffix).status_code == 422
    monkeypatch.setattr(provider, "fetch_chart", lambda *args: (_ for _ in ()).throw(MarketDataError("免费行情暂不可用")))
    response = client.get(root + "/chart?market=us&symbol=AAPL")
    assert response.status_code == 503 and response.json()["detail"] == "免费行情暂不可用"
    resources = client.get(root + "/resources").json()["items"]
    assert len(resources) == 11 and len({row["id"] for row in resources}) == 11
    assert all(row["url"].startswith("https://") and row["free_scope"] and row["paid_scope"] for row in resources)
    assert client.post(root + "/chart?market=us&symbol=AAPL").status_code == 405
