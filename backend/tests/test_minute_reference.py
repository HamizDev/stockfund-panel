from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import polars as pl
import pytest
from app.api.kline import _get_previous_closes
from app.services import minute_reference as service


@pytest.fixture(autouse=True)
def context(monkeypatch, tmp_path):
    service._CACHE.clear()
    service._PENDING.clear()
    monkeypatch.setattr(service.preferences, 'get_daily_data_provider', lambda: 'tickflow')
    monkeypatch.setattr(service.preferences, 'get_minute_data_provider', lambda: 'stocksdk')
    monkeypatch.setattr(service.minute_adjust, 'minute_basis_is_raw', lambda _: False)
    return SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path), get_daily_asset=Mock(return_value=daily()))


def daily():
    return pl.DataFrame({'symbol': ['515880.SH'] * 2,
                         'date': [date(2026, 10, 15), date(2026, 10, 16)],
                         'close': [.521, .522]})


def minutes(close=.522):
    return pl.DataFrame({'datetime': [datetime(2026, 10, 16, 15)], 'close': [close]})


def test_stale_close_is_not_yesterdays_close():
    repo = Mock()
    repo.get_daily_asset.return_value = pl.DataFrame({'date': [date(2026, 10, 9)], 'close': [.592]})
    assert _get_previous_closes(repo, '515880.SH', [date(2026, 10, 16)], 'etf') == {date(2026, 10, 16): None}


def test_friday_close_is_valid_for_monday():
    repo = Mock()
    repo.get_daily_asset.return_value = pl.DataFrame({'date': [date(2026, 10, 16)], 'close': [.522]})
    assert _get_previous_closes(repo, '515880.SH', [date(2026, 10, 19)], 'etf')[date(2026, 10, 19)] == .522


def test_online_reference_repairs_stale_local_and_is_cached(context, monkeypatch):
    load = Mock(return_value=(daily(), {'source': 'online', 'price_basis': 'unadjusted'}))
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', load)
    for _ in range(2):
        value = service.resolve(context, None, '515880.SH', date(2026, 10, 16), 'etf', minutes(), None)
        assert value == {'prev_close': .521, 'prev_close_source': 'online_daily', 'prev_close_date': '2026-10-15'}
    load.assert_called_once()


@pytest.mark.parametrize('frame,meta,close', [
    (daily(), {'source': 'local', 'price_basis': 'local_enriched'}, .522),
    (daily(), {'source': 'online', 'price_basis': 'unadjusted'}, .650),
    (daily().head(1), {'source': 'online', 'price_basis': 'unadjusted'}, .522),
])
def test_no_reference_from_old_fallback_or_incompatible_anchor(context, monkeypatch, frame, meta, close):
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, meta))
    value = service.resolve(context, None, '515880.SH', date(2026, 10, 16), 'etf', minutes(close), None)
    assert value['prev_close'] is None
    assert value['prev_close_warning']


def test_provider_failure_cached_without_exposing_configuration(context, monkeypatch):
    load = Mock(side_effect=ValueError('private provider configuration'))
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', load)
    for _ in range(2):
        result = service.resolve(context, None, '515880.SH', date(2026, 10, 16), 'etf', minutes(), None)
        assert 'private' not in str(result)
    load.assert_called_once()


def test_valid_local_reference_never_fetches(context, monkeypatch):
    load = Mock(side_effect=AssertionError('unexpected network'))
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', load)
    assert service.resolve(context, None, '515880.SH', date(2026, 10, 16), 'etf', minutes(), .521)['prev_close'] == .521
    load.assert_not_called()


def test_incompatible_local_basis_is_repaired_before_using_reference(context, monkeypatch):
    context.get_daily_asset.return_value = daily().with_columns(pl.col('close') * 2)
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (daily(), {'source': 'online', 'price_basis': 'unadjusted'}))
    value = service.resolve(context, None, '515880.SH', date(2026, 10, 16), 'etf', minutes(), 1.042)
    assert value['prev_close'] == .521
    assert value['prev_close_source'] == 'online_daily'


def test_adjusted_minutes_use_shared_projection(context, monkeypatch):
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (daily(), {'source': 'online', 'price_basis': 'unadjusted'}))
    monkeypatch.setattr(service.minute_adjust, 'minute_basis_is_raw', lambda _: True)
    monkeypatch.setattr(service.minute_adjust, 'apply_minute_adjustment', lambda frame, *_: frame.with_columns(pl.col('close') * .5))
    result = service.resolve(context, None, '515880.SH', date(2026, 10, 16), 'etf', minutes(.261), None)
    assert result['prev_close'] == .2605


def test_slow_reference_is_bounded_and_shared_across_callers(context, monkeypatch):
    from concurrent.futures import Future

    monkeypatch.setattr(service, '_WAIT_SECONDS', .01)
    # A controlled future models an upstream request without any real network.
    future = Future()
    pool = Mock()
    pool.submit.return_value = future
    monkeypatch.setattr(service, '_POOL', pool)
    for _ in range(2):
        assert service._online_daily(context, None, '515880.SH') is None
    assert pool.submit.call_count == 1
    assert service._online_daily(context, None, '512800.SH') is None
    assert service._online_daily(context, None, '159915.SZ') is None
    assert pool.submit.call_count == 2
    assert len(service._PENDING) == 2
