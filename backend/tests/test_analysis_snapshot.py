from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest
from app.api.stock_analysis import router
from app.services import analysis_snapshot as service
from fastapi import FastAPI
from fastapi.testclient import TestClient

SYMBOL = "515880.SH"


def rows(day=date(2026, 10, 16), symbol=SYMBOL, close=.592):
    return pl.DataFrame({"symbol": [symbol], "date": [day], "open": [.59],
        "high": [.60], "low": [.58], "close": [float(close)], "volume": [1000.], "amount": [592.]})


@pytest.fixture
def context(monkeypatch):
    calls = []
    provider = SimpleNamespace(get_daily=lambda symbols, **kwargs: (calls.append((symbols, kwargs)), rows())[1])
    monkeypatch.setattr(service, "cn_now", lambda: datetime(2026, 10, 18, 12))
    monkeypatch.setattr(service.preferences, "get_daily_data_provider", lambda: "fake")
    monkeypatch.setattr(service.custom, "provider_has_dataset", lambda name, dataset: True)
    monkeypatch.setattr(service.custom, "get_provider", lambda name: provider)
    monkeypatch.setattr(service, '_baostock_daily', lambda *args: pl.DataFrame())
    repo = SimpleNamespace(resolve_asset_type=lambda symbol: "etf",
        get_daily_asset=lambda *args: pl.DataFrame())
    return repo, provider, calls


def test_fetches_only_selected_asset_and_never_persists(context):
    repo, _, calls = context
    frame, result = service.load_snapshot(repo, None, SYMBOL, 250)
    assert frame.height == 1
    assert calls[0][0] == [SYMBOL]
    assert calls[0][1]["asset_type"] == "etf"
    assert calls[0][1]["end_time"].date() == date(2026, 10, 18)
    assert result["as_of"] == "2026-10-16"  # Sunday is not invented as a trading session.
    assert result["price_basis"] == "unadjusted"
    assert result["source"] == "online"
    assert result["warning"] is None


def test_foreign_future_and_invalid_rows_are_rejected(context):
    repo, provider, _ = context
    provider.get_daily = lambda *a, **k: pl.concat([
        rows(), rows(symbol="600000.SH"), rows(day=date(2026, 10, 19)), rows(close=100)])
    frame, result = service.load_snapshot(repo, None, SYMBOL, 250)
    assert frame.height == 1
    assert result["as_of"] == "2026-10-16"
    assert result["rows"][0]["close"] == .592


@pytest.mark.parametrize("failure", ["error", "empty", "capability"])
def test_provider_failure_keeps_local_adjusted_frame_without_mixing(context, monkeypatch, failure):
    repo, provider, _ = context
    local = rows(day=date(2026, 10, 9), close=.595)
    repo.get_daily_asset = lambda *a: local
    if failure == "error":
        provider.get_daily = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret token"))
    elif failure == "empty":
        provider.get_daily = lambda *a, **k: pl.DataFrame()
    else:
        monkeypatch.setattr(service.custom, "provider_has_dataset", lambda *a: False)
    frame, result = service.load_snapshot(repo, None, SYMBOL, 250)
    assert frame.equals(local)
    assert result["source"] == "local"
    assert result["price_basis"] == "local_enriched"
    assert result["warning"] and "secret" not in str(result)


def test_does_not_replace_newer_local_data_with_older_online(context):
    repo, provider, _ = context
    local = rows(day=date(2026, 10, 16))
    repo.get_daily_asset = lambda *a: local
    provider.get_daily = lambda *a, **k: rows(day=date(2026, 10, 9))
    frame, result = service.load_snapshot(repo, None, SYMBOL, 250)
    assert frame.equals(local)
    assert result["source"] == "local"
    assert result["as_of"] == "2026-10-16"
    assert result["warning"]


def test_selected_baostock_fallback_has_actual_provider_and_does_not_mix_prices(context, monkeypatch):
    repo, provider, _ = context
    provider.get_daily = lambda *a, **k: pl.DataFrame()
    calls = []
    monkeypatch.setattr(service, '_baostock_daily', lambda *args: (calls.append(args), rows())[1])
    frame, result = service.load_snapshot(repo, None, SYMBOL, 250)
    assert len(calls) == 1 and calls[0][0] == SYMBOL and calls[0][-1] == 'etf'
    assert frame.height == 1 and result['provider'] == 'baostock'
    assert result['source'] == 'online' and result['price_basis'] == 'unadjusted'
    assert 'BaoStock' in result['warning']


def test_failed_optional_fallback_keeps_existing_local_snapshot(context, monkeypatch):
    repo, provider, _ = context
    provider.get_daily = lambda *a, **k: pl.DataFrame()
    local = rows()
    repo.get_daily_asset = lambda *a: local
    monkeypatch.setattr(service, '_baostock_daily', lambda *args: (_ for _ in ()).throw(RuntimeError('private token')))
    frame, result = service.load_snapshot(repo, None, SYMBOL, 250)
    assert frame.equals(local) and result['source'] == 'local'
    assert 'private' not in str(result)


def test_legacy_baostock_setting_uses_only_the_selected_daily_helper(context, monkeypatch):
    repo, _, _ = context
    calls = []
    monkeypatch.setattr(service.preferences, 'get_daily_data_provider', lambda: 'baostock')
    monkeypatch.setattr(service, '_baostock_daily', lambda *args: (calls.append(args[0]), rows())[1])
    frame, result = service.load_snapshot(repo, None, SYMBOL, 250)
    assert calls == [SYMBOL] and frame.height == 1 and result['provider'] == 'baostock'


def test_api_levels_and_rows_share_one_frame(context):
    repo, _, calls = context
    app = FastAPI()
    app.state.repo = repo
    app.include_router(router)
    with TestClient(app) as client:
        result = client.get("/api/stock-analysis/snapshot", params={"symbol": SYMBOL}).json()
        assert result["analysis"]["dates"] == [result["as_of"]]
        assert result["analysis"]["close"] == result["rows"][-1]["close"]
        assert len(calls) == 1
        assert client.get("/api/stock-analysis/snapshot", params={"symbol": "../data"}).status_code == 422


def test_api_empty_state_is_honest(context):
    repo, provider, _ = context
    provider.get_daily = lambda *a, **k: pl.DataFrame()
    app = FastAPI()
    app.state.repo = repo
    app.include_router(router)
    with TestClient(app) as client:
        result = client.get("/api/stock-analysis/snapshot", params={"symbol": SYMBOL}).json()
    assert result["rows"] == [] and result["as_of"] is None
    assert result["analysis"]["close"] is None
    assert result["warning"]
