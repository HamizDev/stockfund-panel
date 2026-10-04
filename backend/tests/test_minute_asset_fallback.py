from datetime import datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.data_providers import custom
from app.services import kline_sync, minute_adjust, preferences


def test_stock_only_primary_preserves_installed_etf_minute_source(monkeypatch):
    calls = []

    class Provider:
        def __init__(self, name, assets):
            self.name = name
            self.minute_asset_types = assets

        def get_minute(self, symbols, **kwargs):
            calls.append((self.name, symbols, kwargs["asset_type"]))
            return pl.DataFrame({"symbol": symbols, "datetime": [datetime(2026, 9, 30, 15, 0)] * len(symbols), "close": [4.0] * len(symbols)})

    providers = {"zzshare": Provider("zzshare", ("stock",)), "stocksdk": Provider("stocksdk", ("stock", "etf"))}
    monkeypatch.setattr(custom, "provider_has_dataset", lambda name, dataset: name in providers and dataset == "minute")
    monkeypatch.setattr(custom, "get_provider", providers.__getitem__)
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "zzshare")
    stock, fallback = kline_sync._try_custom_minute(["000001.SZ"], None, None, "stock")
    assert not fallback and stock.height == 1
    etf, fallback = kline_sync._try_custom_minute(["510300.SH"], None, None, "etf")
    assert not fallback and etf.height == 1
    assert calls == [("zzshare", ["000001.SZ"], "stock"), ("stocksdk", ["510300.SH"], "etf")]


def test_unsupported_etf_without_installed_alternative_uses_native_fallback(monkeypatch):
    monkeypatch.setattr(custom, "provider_has_dataset", lambda name, dataset: name == "zzshare")
    monkeypatch.setattr(custom, "get_provider", lambda _: SimpleNamespace(minute_asset_types=("stock",)))
    provider, fallback, error = kline_sync._resolve_minute_provider("zzshare", asset_type="etf")
    assert provider is None and fallback and error is None


def _raw_anchor(tmp_path, close=10.0):
    part = tmp_path / "kline_daily/date=2026-09-30/part.parquet"
    part.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": ["000001.SZ"], "close": [close]}).write_parquet(part)


def _bar(close=10.0, hour=15):
    return pl.DataFrame({"symbol": ["000001.SZ"], "datetime": [datetime(2026, 9, 30, hour, 0)], "close": [close]})


@pytest.mark.parametrize("close,hour", [(9.0, 15), (10.0, 14)])
def test_unknown_minute_basis_cannot_enter_raw_projection(tmp_path, close, hour):
    _raw_anchor(tmp_path)
    with pytest.raises(ValueError, match="分钟价基待验证"):
        minute_adjust.verify_minute_raw_anchors(_bar(close, hour), tmp_path)


def test_unknown_minute_basis_requires_all_days_and_symbols(tmp_path):
    _raw_anchor(tmp_path)
    frame = pl.concat([_bar(), _bar().with_columns(pl.lit("600519.SH").alias("symbol"))])
    with pytest.raises(ValueError, match="分钟价基待验证"):
        minute_adjust.verify_minute_raw_anchors(frame, tmp_path)


def test_checked_unknown_basis_respects_legacy_forward_price_storage(tmp_path, monkeypatch):
    from app.config import settings

    _raw_anchor(tmp_path)
    factors = tmp_path / "adj_factor/all.parquet"
    factors.parent.mkdir()
    pl.DataFrame({"symbol": ["000001.SZ"], "trade_date": [datetime(2026, 10, 8).date()], "ex_factor": [1.25]}).write_parquet(factors)
    provider = SimpleNamespace(name="zzshare", minute_price_basis="unverified", get_minute=lambda *a, **kw: _bar())
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(custom, "provider_has_dataset", lambda name, ds: name == "zzshare")
    monkeypatch.setattr(custom, "get_provider", lambda name: provider)
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "zzshare")
    frame, fallback = kline_sync._try_custom_minute(["000001.SZ"], None, None, "stock", raw_basis=True)
    assert not fallback and frame["close"][0] == 10.0
    legacy, fallback = kline_sync._try_custom_minute(["000001.SZ"], None, None, "stock", raw_basis=False)
    assert not fallback and legacy["close"][0] == 8.0


def test_unchecked_intraday_minute_uses_installed_alternative(tmp_path, monkeypatch):
    from app.config import settings

    providers = {
        "zzshare": SimpleNamespace(name="zzshare", minute_price_basis="unverified", get_minute=lambda *a, **kw: _bar(hour=14)),
        "stocksdk": SimpleNamespace(name="stocksdk", minute_asset_types=("stock", "etf"), get_minute=lambda *a, **kw: _bar(close=11.0)),
    }
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(custom, "provider_has_dataset", lambda name, ds: name in providers)
    monkeypatch.setattr(custom, "get_provider", providers.__getitem__)
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "zzshare")
    result, fallback = kline_sync._try_custom_minute(["000001.SZ"], None, None, "stock", raw_basis=True)
    assert not fallback and result["close"][0] == 11.0


def test_unknown_source_missing_requested_symbol_uses_alternative(tmp_path, monkeypatch):
    from app.config import settings

    _raw_anchor(tmp_path)
    replacement = pl.concat([_bar(), _bar().with_columns(pl.lit("600519.SH").alias("symbol"))])
    providers = {
        "zzshare": SimpleNamespace(name="zzshare", minute_price_basis="unverified", get_minute=lambda *a, **kw: _bar()),
        "stocksdk": SimpleNamespace(name="stocksdk", minute_asset_types=("stock",), get_minute=lambda *a, **kw: replacement),
    }
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(custom, "provider_has_dataset", lambda name, ds: name in providers)
    monkeypatch.setattr(custom, "get_provider", providers.__getitem__)
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "zzshare")
    frame, fallback = kline_sync._try_custom_minute(["000001.SZ", "600519.SH"], None, None, "stock", raw_basis=True)
    assert not fallback and set(frame["symbol"]) == {"000001.SZ", "600519.SH"}


@pytest.mark.parametrize("missing_minute", [True, False])
def test_unknown_source_full_day_requires_every_session_minute(tmp_path, monkeypatch, missing_minute):
    from app.config import settings

    _raw_anchor(tmp_path)
    times = [datetime(2026, 9, 30, 9, 31) + timedelta(minutes=i) for i in range(120)]
    times += [datetime(2026, 9, 30, 13, 1) + timedelta(minutes=i) for i in range(120)]
    if missing_minute:
        times.pop(30)  # Keep the valid 15:00 raw anchor but leave an intraday gap.
    response = pl.DataFrame({"symbol": ["000001.SZ"] * len(times), "datetime": times, "close": [10.0] * len(times)})
    providers = {
        "zzshare": SimpleNamespace(name="zzshare", minute_price_basis="unverified", get_minute=lambda *a, **kw: response),
        "stocksdk": SimpleNamespace(name="stocksdk", minute_asset_types=("stock",), get_minute=lambda *a, **kw: _bar(close=11.0)),
    }
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(custom, "provider_has_dataset", lambda name, ds: name in providers)
    monkeypatch.setattr(custom, "get_provider", providers.__getitem__)
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "zzshare")
    frame, fallback = kline_sync._try_custom_minute(
        ["000001.SZ"], datetime(2026, 9, 30, 9, 25), datetime(2026, 9, 30, 15, 5), "stock", raw_basis=True,
    )
    assert not fallback
    assert frame.height == (1 if missing_minute else 240)
    assert frame["close"][0] == (11.0 if missing_minute else 10.0)
