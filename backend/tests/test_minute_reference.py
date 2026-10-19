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
    load = Mock(return_value=(daily(), {'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow'}))
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', load)
    for _ in range(2):
        value = service.resolve(context, None, '515880.SH', date(2026, 10, 16), 'etf', minutes(), None)
        assert value == {'prev_close': .521, 'prev_close_source': 'online_daily', 'prev_close_date': '2026-10-15'}
    load.assert_called_once()


@pytest.mark.parametrize('frame,meta,close', [
    (daily(), {'source': 'local', 'price_basis': 'local_enriched'}, .522),
    (daily(), {'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow'}, .650),
    (daily().head(1), {'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow'}, .522),
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
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (daily(), {'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow'}))
    value = service.resolve(context, None, '515880.SH', date(2026, 10, 16), 'etf', minutes(), 1.042)
    assert value['prev_close'] == .521
    assert value['prev_close_source'] == 'online_daily'


def test_adjusted_minutes_use_shared_projection(context, monkeypatch):
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (daily(), {'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow'}))
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


def test_prefetch_does_not_wait_and_reuses_existing_bounded_task(context, monkeypatch):
    from concurrent.futures import Future

    future = Future()
    pool = Mock()
    pool.submit.return_value = future
    monkeypatch.setattr(service, '_POOL', pool)
    assert service.prefetch(context, None, '515880.SH') is None
    assert service.prefetch(context, None, '515880.SH') is None
    assert pool.submit.call_count == 1
    future.set_result(daily())
    assert service._online_daily(context, None, '515880.SH').equals(daily())
    assert pool.submit.call_count == 1


@pytest.mark.parametrize('local_close', [None, .521])
def test_minute_request_only_prefetches_missing_reference(context, monkeypatch, local_close):
    import threading

    from app.api import kline
    from app.tickflow.capabilities import CapabilitySet
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    started, release = threading.Event(), threading.Event()
    operations = []

    def snapshot(*_):
        assert local_close is None, 'valid local reference must not start an online query'
        operations.append('daily-start')
        started.set()
        assert release.wait(2), 'minute fetch did not start while daily reference was pending'
        return daily(), {'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow'}

    def fetch(*_, **__):
        try:
            if local_close is None:
                assert started.wait(1), 'reference started only after the slow minute fetch'
            else:
                assert not started.is_set()
            operations.append('minute-fetch')
            return minutes().with_columns(pl.lit('515880.SH').alias('symbol'))
        finally:
            release.set()

    context.resolve_asset_type = lambda _: 'etf'
    context.get_minute = lambda *_, **__: pl.DataFrame()
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', snapshot)
    monkeypatch.setattr(kline, '_get_asset_info', lambda *_: {'name': '通信ETF'})
    monkeypatch.setattr(kline, '_get_previous_closes', lambda *_: {date(2026, 10, 16): local_close})
    monkeypatch.setattr(kline, '_get_price_limit_info', lambda *_: None)
    monkeypatch.setattr(kline.kline_sync, 'fetch_minute_single', fetch)
    app = FastAPI()
    app.state.repo = context
    app.state.capabilities = CapabilitySet({})
    app.include_router(kline.router)
    with TestClient(app) as client:
        response = client.get('/api/kline/minute?symbol=515880.SH&date=2026-10-16')
    assert response.status_code == 200
    assert response.json()['prev_close'] == .521
    assert operations == (['daily-start', 'minute-fetch'] if local_close is None else ['minute-fetch'])


@pytest.mark.parametrize('change_timing', ['queued', 'during-fetch', 'metadata-mismatch'])
def test_source_changes_never_publish_under_original_key(context, monkeypatch, change_timing):
    from concurrent.futures import Future

    state = {'revision': 1, 'provider': 'tickflow'}
    monkeypatch.setattr(service, '_preference_revision', lambda: state['revision'])
    monkeypatch.setattr(service.preferences, 'get_daily_data_provider', lambda: state['provider'])
    calls = []

    def snapshot(*_):
        calls.append(state['provider'])
        requested = state['provider']
        if change_timing == 'during-fetch':
            # A -> B -> A keeps the name but an atomic preferences write changes revision.
            state.update(provider='stocksdk', revision=2)
            state.update(provider='tickflow', revision=3)
        return daily(), {'source': 'online', 'price_basis': 'unadjusted',
                         'requested_provider': 'stocksdk' if change_timing == 'metadata-mismatch' else requested}

    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', snapshot)
    future = Future()
    pool = Mock()
    pool.submit.return_value = future
    monkeypatch.setattr(service, '_POOL', pool)
    service.prefetch(context, None, '515880.SH')
    callback, *args = pool.submit.call_args.args
    if change_timing == 'queued':
        state.update(provider='stocksdk', revision=2)
    value = callback(*args)
    future.set_result(value)
    assert value is None
    assert not any(frame is not None for _, frame in service._CACHE.values())
    assert not service._PENDING
    assert calls == ([] if change_timing == 'queued' else ['tickflow'])


def test_baostock_fallback_keeps_the_requested_provider_identity(context, monkeypatch):
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (daily(), {
        'source': 'online', 'price_basis': 'unadjusted', 'provider': 'baostock', 'requested_provider': 'tickflow',
    }))
    result = service.resolve(context, None, '515880.SH', date(2026, 10, 16), 'etf', minutes(), None)
    assert result['prev_close'] == .521
