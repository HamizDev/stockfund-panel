"""Regression tests for ELTDX's fail-closed minute routing contract."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.data_providers import custom
from app.services import kline_sync, preferences
from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet


def _minute_frame(symbol: str = "000001.SZ", close: float = 10.0) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol],
        "datetime": [datetime(2026, 9, 30, 9, 31)],
        "open": [close],
        "high": [close + 0.1],
        "low": [close - 0.1],
        "close": [close],
        "volume": [100.0],
        "amount": [None],
    })


def _route_provider(monkeypatch, provider_name: str, provider: object) -> list[str]:
    """Route only the named fake provider; unexpected alternative lookup fails."""
    get_provider_names: list[str] = []

    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: provider_name)
    monkeypatch.setattr(
        custom,
        "provider_has_dataset",
        lambda name, dataset: name == provider_name and dataset == "minute",
    )

    def get_provider(name: str):
        get_provider_names.append(name)
        if name != provider_name:
            pytest.fail(f"unexpected provider fallback: {name}")
        return provider

    monkeypatch.setattr(custom, "get_provider", get_provider)
    return get_provider_names


def _eltdx(provider_get_minute) -> SimpleNamespace:
    return SimpleNamespace(
        name="eltdx_gateway",
        minute_asset_types=("stock", "etf"),
        minute_max_symbols_per_request=1,
        minute_price_basis="raw",
        minute_fail_closed=True,
        get_minute=provider_get_minute,
    )


def _tickflow_minute_capset() -> CapabilitySet:
    return CapabilitySet({Cap.KLINE_MINUTE_BY_SYMBOL: CapabilityLimits()})


def test_eltdx_request_failure_does_not_fall_back_to_stocksdk_or_tickflow(monkeypatch):
    provider_call = MagicMock(side_effect=RuntimeError("gateway unavailable"))
    provider = _eltdx(provider_call)
    get_provider_names = _route_provider(monkeypatch, "eltdx_gateway", provider)
    get_client = MagicMock(side_effect=AssertionError("must not fall back to TickFlow"))
    monkeypatch.setattr(kline_sync, "get_client", get_client)

    with pytest.raises(RuntimeError, match="gateway unavailable"):
        kline_sync.fetch_minute_single(
            "000001.SZ",
            date(2026, 9, 30),
            asset_type="stock",
            capset=_tickflow_minute_capset(),
        )

    provider_call.assert_called_once()
    assert get_provider_names == ["eltdx_gateway"]
    get_client.assert_not_called()


def test_eltdx_rejects_index_while_normal_provider_keeps_tickflow_fallback(monkeypatch):
    eltdx_get_minute = MagicMock()
    get_provider_names = _route_provider(
        monkeypatch, "eltdx_gateway", _eltdx(eltdx_get_minute)
    )
    get_client = MagicMock(side_effect=AssertionError("ELTDX index must fail closed"))
    monkeypatch.setattr(kline_sync, "get_client", get_client)

    with pytest.raises(ValueError, match="不支持此资产类型"):
        kline_sync.fetch_minute_single(
            "000300.SH",
            date(2026, 9, 30),
            asset_type="index",
            capset=_tickflow_minute_capset(),
        )

    eltdx_get_minute.assert_not_called()
    assert get_provider_names == ["eltdx_gateway"]
    get_client.assert_not_called()

    normal_get_minute = MagicMock()
    normal_provider = SimpleNamespace(
        name="normal_stock_source",
        minute_asset_types=("stock",),
        get_minute=normal_get_minute,
    )
    _route_provider(monkeypatch, "normal_stock_source", normal_provider)
    tickflow_batch = MagicMock(return_value=[])
    monkeypatch.setattr(
        kline_sync,
        "get_client",
        lambda: SimpleNamespace(klines=SimpleNamespace(batch=tickflow_batch)),
    )

    result = kline_sync.fetch_minute_single(
        "000300.SH",
        date(2026, 9, 30),
        asset_type="index",
        capset=_tickflow_minute_capset(),
    )

    assert result.is_empty()
    normal_get_minute.assert_not_called()
    tickflow_batch.assert_called_once()


def test_eltdx_raw_price_basis_adjusts_only_when_raw_basis_is_false(
    monkeypatch, tmp_path
):
    from app.config import settings

    source_frame = _minute_frame()
    provider_get_minute = MagicMock(return_value=source_frame)
    provider = _eltdx(provider_get_minute)
    _route_provider(monkeypatch, "eltdx_gateway", provider)
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    def apply_adjustment(frame, data_dir, asset_type):
        assert data_dir == tmp_path
        assert asset_type == "stock"
        return frame.with_columns((pl.col("close") / 2).alias("close"))

    apply_spy = MagicMock(side_effect=apply_adjustment)
    monkeypatch.setattr(kline_sync.minute_adjust, "apply_minute_adjustment", apply_spy)

    adjusted, fallback = kline_sync._try_custom_minute(
        ["000001.SZ"], None, None, asset_type="stock", raw_basis=False
    )

    assert fallback is False
    assert adjusted["close"].to_list() == [5.0]
    apply_spy.assert_called_once()

    apply_spy.reset_mock()
    raw, fallback = kline_sync._try_custom_minute(
        ["000001.SZ"], None, None, asset_type="stock", raw_basis=True
    )

    assert fallback is False
    assert raw["close"].to_list() == [10.0]
    apply_spy.assert_not_called()


@pytest.mark.parametrize("asset_type, factor_dir, symbol", [
    ("stock", "adj_factor", "000001.SZ"),
    ("etf", "adj_factor_etf", "510300.SH"),
])
def test_eltdx_legacy_conversion_uses_real_adjustment_engine_with_nontrivial_factor(
    monkeypatch, tmp_path, asset_type, factor_dir, symbol,
):
    from app.config import settings

    folder = tmp_path / factor_dir
    folder.mkdir()
    # Synthetic future event gives the observed day's legacy price a 0.5 ratio.
    pl.DataFrame({
        "symbol": [symbol, symbol],
        "trade_date": [date(2026, 9, 1), date(2026, 10, 1)],
        "ex_factor": [1.0, 2.0],
    }).write_parquet(folder / "all.parquet")
    frame = _minute_frame(symbol).with_columns(pl.col("amount").cast(pl.Float64))
    _route_provider(monkeypatch, "eltdx_gateway", _eltdx(lambda *_args, **_kwargs: frame))
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    legacy, fallback = kline_sync._try_custom_minute(
        [symbol], None, None, asset_type=asset_type, raw_basis=False,
    )
    assert fallback is False
    for column in ("open", "high", "low", "close"):
        assert legacy[column][0] == pytest.approx(frame[column][0] / 2)
    assert legacy["amount"].to_list() == [None]
    assert legacy["volume"].to_list() == frame["volume"].to_list()
    raw, fallback = kline_sync._try_custom_minute(
        [symbol], None, None, asset_type=asset_type, raw_basis=True,
    )
    assert fallback is False
    assert raw.equals(frame)
