from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
from app.api.kline import router
from app.market_time import CN_TZ
from app.services import minute_response_cache
from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet
from fastapi import FastAPI
from fastapi.testclient import TestClient

_CLOSED_NOW = datetime(2026, 10, 19, 16, 0, tzinfo=CN_TZ)
_TODAY = date(2026, 10, 19)
_HISTORICAL_DAY = date(2026, 10, 16)


def _frame(rows: int, *, day: date = _HISTORICAL_DAY, symbol: str = "515880.SH") -> pl.DataFrame:
    start = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=30)
    return pl.DataFrame({
        "symbol": [symbol] * rows,
        "datetime": [start + timedelta(minutes=index) for index in range(rows)],
        "close": [1.0 + index / 100 for index in range(rows)],
    })


class _Repo:
    def __init__(self, data_dir: Path, local: pl.DataFrame | None = None) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)
        self.local = local if local is not None else pl.DataFrame()

    def resolve_asset_type(self, symbol: str) -> str:
        return "etf"

    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        return pl.DataFrame({"symbol": [], "name": []})

    def get_minute(self, symbol: str, trade_date: date, asset_type: str = "etf") -> pl.DataFrame:
        return self.local.clone()

    def get_daily_asset(self, asset_type, symbol, start, end, columns=None):
        return pl.DataFrame({"date": [], "close": []})


def _client(repo: _Repo) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    app.state.capabilities = CapabilitySet({
        Cap.KLINE_MINUTE_BY_SYMBOL: CapabilityLimits(),
    })
    return TestClient(app)


def _patch_api(
    monkeypatch,
    *,
    now: datetime = _CLOSED_NOW,
    today: date = _TODAY,
    data_dir: Path | None = None,
    patch_preference: bool = True,
) -> None:
    import app.api.kline as kline_api

    if data_dir is not None:
        from app.config import settings
        from app.services import preferences

        monkeypatch.setattr(settings, "data_dir", data_dir)
        monkeypatch.setattr(preferences, "_cache", None)
        monkeypatch.setattr(preferences, "_cache_sig", None)
    monkeypatch.setattr(kline_api, "cn_now", lambda: now)
    monkeypatch.setattr(kline_api, "cn_today", lambda: today)
    monkeypatch.setattr(kline_api, "in_continuous_session", lambda: False)
    monkeypatch.setattr(kline_api, "_get_previous_closes", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(kline_api, "_get_price_limit_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kline_api.minute_reference, "resolve", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(kline_api.minute_reference, "prefetch", lambda *_args, **_kwargs: None)

    provider = SimpleNamespace(name="stocksdk", minute_fail_closed=True)

    def resolve_provider(provider_name: str, asset_type: str = "stock"):
        if provider_name == "tickflow":
            return (None, True, None)
        return (provider, False, None)

    monkeypatch.setattr(kline_api.kline_sync, "_resolve_minute_provider", resolve_provider)
    if patch_preference:
        monkeypatch.setattr(
            "app.services.preferences.get_minute_data_provider",
            lambda: "stocksdk",
        )
    minute_response_cache.minute_response_cache.clear()


def test_closed_day_repeated_etf_fallback_fetches_upstream_once(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path)
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(20)))

    first = client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)})
    second = client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)})

    assert first.status_code == second.status_code == 200
    assert first.json()["source"] == second.json()["source"] == "live"
    assert len(first.json()["rows"]) == len(second.json()["rows"]) == 241
    assert calls == 1


def test_new_complete_local_partition_takes_priority_over_warm_fallback(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path)
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    repo = _Repo(tmp_path, _frame(20))
    client = _client(repo)

    warmed = client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)})
    repo.local = _frame(241)
    local = client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)})

    assert warmed.json()["source"] == "live"
    assert local.json()["source"] == "local"
    assert len(local.json()["rows"]) == 241
    assert calls == 1


def test_new_incomplete_local_partition_invalidates_cached_fallback(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path)
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    repo = _Repo(tmp_path, _frame(20))
    client = _client(repo)
    params = {"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)}

    client.get("/api/kline/minute", params=params)
    repo.local = _frame(21)
    client.get("/api/kline/minute", params=params)

    assert calls == 2


def test_current_day_intraday_fallback_reuses_short_cache_then_fetches_new_bar(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    now = datetime(2026, 10, 19, 10, 0, tzinfo=CN_TZ)
    _patch_api(monkeypatch, now=now, data_dir=tmp_path)
    clock = [0.0]
    cache = minute_response_cache.MinuteResponseCache(ttl_seconds=5, clock=lambda: clock[0])
    monkeypatch.setattr(minute_response_cache, "live_minute_response_cache", cache, raising=False)
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(20 + calls, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(0, day=_TODAY)))
    for _ in range(2):
        response = client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_TODAY)})
        assert response.status_code == 200
        assert response.json()["source"] == "live"

    assert calls == 1
    assert len(response.json()["rows"]) == 21
    assert cache.entry_count == 1
    clock[0] = 5.01
    fresh = client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_TODAY)})
    assert calls == 2
    assert len(fresh.json()["rows"]) == 22
    assert minute_response_cache.minute_response_cache.entry_count == 0


def test_intraday_live_view_reuses_only_requested_instrument_without_local_writes(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    now = datetime(2026, 10, 19, 10, 0, tzinfo=CN_TZ)
    _patch_api(monkeypatch, now=now, data_dir=tmp_path)
    monkeypatch.setattr(kline_api, "in_continuous_session", lambda: True)
    cache = minute_response_cache.MinuteResponseCache(ttl_seconds=5)
    monkeypatch.setattr(minute_response_cache, "live_minute_response_cache", cache, raising=False)
    calls = []

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        calls.append(symbol)
        return _frame(20, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path))
    for symbol in ("515880.SH", "515880.SH", "512800.SH"):
        response = client.get("/api/kline/minute", params={"symbol": symbol, "date": str(_TODAY), "live": 1})
        assert response.status_code == 200
        assert response.json()["symbol"] == symbol
        assert response.json()["rows"][0]["symbol"] == symbol
    assert calls == ["515880.SH", "512800.SH"]
    assert not list(tmp_path.rglob("*.parquet"))


def test_current_day_post_close_fallback_can_be_cached(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path)
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(20, day=_TODAY)))
    for _ in range(2):
        response = client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_TODAY)})
        assert response.status_code == 200

    assert calls == 1


def test_partial_current_day_post_close_result_is_retried_not_cached(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path)
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(215, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(20, day=_TODAY)))
    for _ in range(2):
        response = client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_TODAY)})
        assert response.status_code == 200

    assert calls == 2
    assert minute_response_cache.minute_response_cache.entry_count == 0


def test_live_flag_is_not_a_force_for_historical_closed_day_fallback(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path)
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(20)))
    for _ in range(2):
        response = client.get("/api/kline/minute", params={
            "symbol": "515880.SH", "date": str(_HISTORICAL_DAY), "live": 1,
        })
        assert response.status_code == 200

    assert calls == 1


def test_provider_switch_uses_a_different_cache_key(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path)
    selected = ["stocksdk"]
    monkeypatch.setattr(
        "app.services.preferences.get_minute_data_provider",
        lambda: selected[0],
    )

    def resolve_provider(provider_name: str, asset_type: str = "stock"):
        if provider_name == "tickflow":
            return (None, True, None)
        return (SimpleNamespace(name=provider_name, minute_fail_closed=True), False, None)

    monkeypatch.setattr(kline_api.kline_sync, "_resolve_minute_provider", resolve_provider)
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(20)))
    client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)})
    selected[0] = "tickflow"
    client.get("/api/kline/minute", params={"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)})

    assert calls == 2


def test_provider_preference_aba_rewrite_does_not_cache_fetch_under_old_source(monkeypatch, tmp_path):
    import app.api.kline as kline_api
    from app.services import preferences

    _patch_api(monkeypatch, data_dir=tmp_path, patch_preference=False)
    monkeypatch.setattr(preferences, "_allowed_data_providers", lambda: {"tickflow", "stocksdk"})
    preferences.save({"minute_data_provider": "stocksdk"})
    preference_path = tmp_path / "user_data" / "preferences.json"
    original_identity = preference_path.stat()
    calls = 0
    observed_providers = []

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            observed_providers.append(preferences.get_minute_data_provider())
            preferences.save({"minute_data_provider": "tickflow"})
            observed_providers.append(preferences.get_minute_data_provider())
            preferences.save({"minute_data_provider": "stocksdk"})
            observed_providers.append(preferences.get_minute_data_provider())
            final_identity = preference_path.stat()
            assert (
                original_identity.st_dev,
                original_identity.st_ino,
                original_identity.st_mtime_ns,
                original_identity.st_size,
            ) != (
                final_identity.st_dev,
                final_identity.st_ino,
                final_identity.st_mtime_ns,
                final_identity.st_size,
            )
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(20)))
    params = {"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)}
    responses = [client.get("/api/kline/minute", params=params) for _ in range(3)]

    assert all(response.status_code == 200 for response in responses)
    assert observed_providers == ["stocksdk", "tickflow", "stocksdk"]
    # The ABA fetch is not cached; the next stable fetch warms the cache.
    assert calls == 2


def test_missing_preference_file_keeps_default_route_without_creating_config(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path, patch_preference=False)
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(20)))
    params = {"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)}
    responses = [client.get("/api/kline/minute", params=params) for _ in range(2)]

    assert all(response.status_code == 200 for response in responses)
    assert calls == 1
    assert not (tmp_path / "user_data" / "preferences.json").exists()


def test_invalid_preference_file_bypasses_cache_without_rewriting_it(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path)
    preference_path = tmp_path / "user_data" / "preferences.json"
    preference_path.parent.mkdir(parents=True, exist_ok=True)
    original = "{ invalid json"
    preference_path.write_text(original, encoding="utf-8")
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(20)))
    params = {"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)}
    responses = [client.get("/api/kline/minute", params=params) for _ in range(2)]

    assert all(response.status_code == 200 for response in responses)
    assert calls == 2
    assert preference_path.read_text(encoding="utf-8") == original


def test_unverified_price_basis_keeps_per_fetch_anchor_validation(monkeypatch, tmp_path):
    import app.api.kline as kline_api

    _patch_api(monkeypatch, data_dir=tmp_path)
    monkeypatch.setattr(
        kline_api.kline_sync,
        "_resolve_minute_provider",
        lambda provider_name, asset_type="stock": (
            SimpleNamespace(name="unverified", minute_fail_closed=True, minute_price_basis="unverified"),
            False,
            None,
        ),
    )
    calls = 0

    def fetch(symbol, trade_date, asset_type="stock", *, capset, raw_basis=False):
        nonlocal calls
        calls += 1
        return _frame(241, day=trade_date, symbol=symbol)

    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", fetch)
    client = _client(_Repo(tmp_path, _frame(20)))
    params = {"symbol": "515880.SH", "date": str(_HISTORICAL_DAY)}
    client.get("/api/kline/minute", params=params)
    client.get("/api/kline/minute", params=params)

    assert calls == 2
