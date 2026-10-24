import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import secrets_store
from app.custom.global_markets import keyed_sources as sources
from app.custom.global_markets import routes, service
from app.custom.global_markets.catalog import MarketDataError

KEY = "unit-test-key-only-1234567890"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(sources.settings, "data_dir", tmp_path)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("ADANOS_API_KEY", raising=False)
    for queue in sources._CALLS.values():
        queue.clear()
    sources._COOLDOWN.clear()
    service._CACHE.clear()
    service._PENDING.clear()
    service._FAILURES.clear()


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(sources.httpx, "Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))


def quote(**changes):
    return {"c": 120, "pc": 100, "dp": 20, "t": 1780000000, **changes}


def test_verify_uses_official_header_once_preserves_existing_credentials_and_masks(monkeypatch):
    secrets_store.save({"ai_api_key": "old-test-value"})
    calls = []
    def handle(req):
        calls.append(req)
        assert req.url.host == "finnhub.io" and req.headers["X-Finnhub-Token"] == KEY
        assert "token" not in req.url.params and KEY not in str(req.url)
        return httpx.Response(200, json=quote())
    transport(monkeypatch, handle)
    result = sources.configure("finnhub", KEY)
    assert len(calls) == 1 and KEY not in json.dumps(result)
    assert result["sources"][0]["configured"]
    assert secrets_store.load() == {"ai_api_key": "old-test-value", "finnhub_api_key": KEY}


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_failed_verification_never_replaces_prior_key_or_exposes_body(monkeypatch, status):
    secrets_store.save({"finnhub_api_key": "previous-test-key"})
    calls = []
    def handle(req):
        calls.append(req)
        return httpx.Response(status, text=f"secret-response-{KEY}", headers={"Location": "https://invalid.example/"})
    transport(monkeypatch, handle)
    with pytest.raises(MarketDataError) as error:
        sources.configure("finnhub", KEY)
    assert KEY not in str(error.value) and "secret-response" not in str(error.value)
    assert secrets_store.load()["finnhub_api_key"] == "previous-test-key"
    assert len(calls) == 1


@pytest.mark.parametrize("key", [None, 123, "short", "test key contains space", "\n", "x" * 513])
def test_invalid_key_never_sent_to_upstream(monkeypatch, key):
    monkeypatch.setattr(sources, "_json", lambda *a, **kw: pytest.fail("must not query"))
    with pytest.raises(ValueError):
        sources.configure("adanos", key)


def test_corrupt_store_is_preserved_without_erasing_other_credentials(monkeypatch, tmp_path):
    path = tmp_path / "user_data/secrets.json"
    path.parent.mkdir()
    path.write_text("broken original", encoding="utf-8")
    monkeypatch.setattr(sources, "_quote", lambda *a, **kw: quote())
    with pytest.raises(MarketDataError):
        sources.configure("finnhub", KEY)
    assert path.read_text(encoding="utf-8") == "broken original"


def test_status_and_remove_preserve_environment_fallback_and_other_fields(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "env-test-fallback-only")
    secrets_store.save({"finnhub_api_key": KEY, "adanos_api_key": "another-test-key", "ai_model": "keep"})
    result = sources.remove("finnhub")
    assert result["sources"][0]["configured"]
    assert "env-test-fallback-only" not in json.dumps(result)
    assert secrets_store.load() == {"adanos_api_key": "another-test-key", "ai_model": "keep"}


def test_quote_decimal_change_and_missing_timestamp_are_not_fabricated(monkeypatch):
    monkeypatch.setattr(sources, "_json", lambda *a, **kw: quote(t=None, dp=999))
    row = sources._quote("AAPL", candidate=KEY)
    assert row["change_pct"] == pytest.approx(.2) and row["as_of_ms"] is None
    assert row["previous_close"] == 100


def test_supplement_partial_failure_keeps_valid_quote_and_converts_millions_usd(monkeypatch):
    secrets_store.save({"finnhub_api_key": KEY})
    calls = []
    def fetch(name, path, params, **kwargs):
        calls.append(path)
        if path == "/quote":
            return quote()
        if path == "/stock/profile2":
            return {"ticker": "AAPL", "currency": "USD", "name": "Apple", "marketCapitalization": 123.5}
        raise MarketDataError("新闻未获免费端点权限")
    monkeypatch.setattr(sources, "_json", fetch)
    row = sources.supplement("AAPL")
    assert row["quote"]["price"] == 120 and row["profile"]["market_cap_usd"] == 123_500_000
    assert row["news"] == [] and row["warnings"] == ["新闻未获免费端点权限"]
    sources.supplement("AAPL")
    assert len(calls) == 3  # successes shared; failed endpoint observes existing cooldown cache


@pytest.mark.parametrize("profile", [{"ticker": "MSFT", "currency": "USD"},
                                      {"ticker": "AAPL", "currency": "HKD"}, {"currency": "USD"}])
def test_profile_identity_or_currency_mismatch_is_rejected(monkeypatch, profile):
    monkeypatch.setattr(sources, "_json", lambda *a, **kw: profile)
    with pytest.raises(MarketDataError):
        sources._profile("AAPL")


def test_share_class_mapping_and_news_safe_links_times(monkeypatch):
    seen = []
    def fetch(name, path, params, **kwargs):
        seen.append(params["symbol"])
        return [{"headline": "Good", "url": "https://example.com/news", "datetime": 1780000000},
                {"headline": "Bad", "url": "javascript:alert(1)", "datetime": 1780000000},
                {"headline": "Bad", "url": "https://user:password@example.com/", "datetime": 1780000000},
                {"headline": "Bad", "url": "https://example.com/", "datetime": 9999999999}]
    monkeypatch.setattr(sources, "_json", fetch)
    rows = sources._news("BRK-B")
    assert seen == ["BRK.B"] and len(rows) == 1 and rows[0]["published_at_ms"] == 1780000000000


@pytest.mark.parametrize("source", list(sources.SOURCES))
def test_selected_sentiment_source_only_one_request_and_cache(monkeypatch, source):
    secrets_store.save({"adanos_api_key": KEY})
    calls = []
    def handle(req):
        calls.append(req)
        assert req.url.host == "api.adanos.com" and req.url.path == sources.SOURCES[source]
        assert req.headers["X-API-Key"] == KEY
        params = parse_qs(req.url.query.decode())
        assert params["tickers"] == ["AAPL"] and "days" not in params and "from" in params and "to" in params
        return httpx.Response(200, json={"period_days": 7, "stocks": [{"ticker": "AAPL", "buzz_score": 12,
            "bullish_pct": 75, "mentions": 123, "trade_count": 10}]})
    transport(monkeypatch, handle)
    first = sources.sentiment("AAPL", source)
    second = sources.sentiment("AAPL", source)
    assert len(calls) == 1 and second["cached"] and first["bullish_pct"] == 75
    assert first["mentions"] == (10 if source == "polymarket" else 123)


def test_unknown_ticker_or_out_of_range_values_stay_missing(monkeypatch):
    monkeypatch.setattr(sources, "_json", lambda *a, **kw: {"stocks": [{"ticker": "MSFT", "buzz_score": 999}]})
    result = sources._sentiment("AAPL", "news", candidate=KEY)
    assert result["buzz_score"] is None and result["bullish_pct"] is None and result["warnings"]


def test_key_change_isolates_shared_cache(monkeypatch):
    secrets_store.save({"adanos_api_key": KEY})
    calls = []
    monkeypatch.setattr(sources, "_json", lambda *a, **kw: calls.append(1) or {"stocks": []})
    sources.sentiment("AAPL", "news")
    secrets_store.save({"adanos_api_key": "rotated-test-key-only"})
    sources.sentiment("AAPL", "news")
    assert len(calls) == 2
    assert KEY not in repr(service._CACHE) and "rotated-test-key-only" not in repr(service._CACHE)


@pytest.mark.parametrize("symbol", ["00700.HK", "515880.SH", "^GSPC", "https://invalid.example", "BTC-USDT"])
def test_non_us_or_index_inputs_cannot_spend_research_quota(monkeypatch, symbol):
    monkeypatch.setattr(sources, "_json", lambda *a, **kw: pytest.fail("must not query"))
    with pytest.raises(ValueError):
        sources.supplement(symbol)


def test_routes_only_explicit_post_spends_sentiment_and_key_errors_do_not_echo(monkeypatch):
    app = FastAPI()
    app.include_router(routes.build_router())
    calls = []
    monkeypatch.setattr(sources, "sentiment", lambda symbol, source: calls.append(source) or {"symbol": symbol})
    with TestClient(app) as client:
        assert client.get("/api/custom/global-markets/research-sources").status_code == 200
        assert calls == []
        assert client.get("/api/custom/global-markets/research-sentiment?symbol=AAPL").status_code == 405
        assert client.post("/api/custom/global-markets/research-sentiment?symbol=AAPL&source=news").status_code == 200
        assert calls == ["news"]
        response = client.post("/api/custom/global-markets/research-sources/finnhub", json={"api_key": "private short"})
        assert response.status_code == 422 and "private short" not in response.text
        assert client.post("/api/custom/global-markets/research-sources/unknown", json={"api_key": KEY}).status_code == 422


def test_response_size_and_network_errors_are_sanitized(monkeypatch):
    transport(monkeypatch, lambda req: httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))
    with pytest.raises(MarketDataError, match="上限"):
        sources._quote("AAPL", candidate=KEY)
    def fail(req):
        raise httpx.ConnectError(f"private error {KEY}")
    transport(monkeypatch, fail)
    with pytest.raises(MarketDataError) as error:
        sources._quote("AAPL", candidate=KEY)
    assert KEY not in str(error.value)


@pytest.mark.parametrize("clear_first", [False, True])
def test_shared_store_serializes_concurrent_updates_without_losing_fields(monkeypatch, clear_first):
    secrets_store.save({"ai_model": "keep", "finnhub_api_key": "old-test-key"})
    original_load = secrets_store.load
    first_read = threading.Barrier(2)
    def synchronized_read():
        result = original_load()
        with suppress(threading.BrokenBarrierError):
            first_read.wait(timeout=0.3)
        return result
    monkeypatch.setattr(secrets_store, "load", synchronized_read)
    def primary():
        if clear_first:
            secrets_store.clear("finnhub_api_key")
        else:
            secrets_store.save({"finnhub_api_key": KEY})
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(primary), pool.submit(secrets_store.save, {"adanos_api_key": "other-test-key"})]
        errors = [job.exception() for job in jobs]
    assert errors == [None, None]
    result = original_load()
    assert result["ai_model"] == "keep" and result["adanos_api_key"] == "other-test-key"
    assert ("finnhub_api_key" not in result) if clear_first else result["finnhub_api_key"] == KEY
