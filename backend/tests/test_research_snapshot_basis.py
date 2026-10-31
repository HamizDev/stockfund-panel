from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.market_time import CN_TZ
from app.services import analysis_snapshot as snapshots


@pytest.mark.parametrize('declared_basis, verified', [(None, False), ('qfq', False), ('raw', True)])
def test_unknown_or_adjusted_provider_is_not_declared_raw(monkeypatch, declared_basis, verified):
    frame = pl.DataFrame([dict(symbol='515880.SH', date=date(2026, 10, 27),
        open=5., high=6., low=4., close=5., volume=100.)])
    provider = SimpleNamespace(daily_price_basis=declared_basis, get_daily=lambda *a, **k: frame)
    monkeypatch.setattr(snapshots.preferences, 'get_daily_data_provider', lambda: 'research_fixture')
    monkeypatch.setattr(snapshots.custom, 'provider_has_dataset', lambda *a: True)
    monkeypatch.setattr(snapshots.custom, 'get_provider', lambda *a: provider)
    monkeypatch.setattr(snapshots, 'compute_enriched', lambda value: value)
    repo = SimpleNamespace(resolve_asset_type=lambda *a: 'etf', get_daily_asset=lambda *a: pl.DataFrame())
    _, meta = snapshots.load_snapshot(repo, None, '515880.SH', 250,
        as_of_time=datetime(2026, 10, 27, 16, tzinfo=CN_TZ))
    assert meta['raw_reference_verified'] is verified
    assert meta['price_basis'] == ('unadjusted' if verified else 'provider_unverified')
