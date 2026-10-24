from datetime import date, datetime, timedelta
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
    monkeypatch.setattr(service.trading_calendar, 'previous_trading_day', _previous_weekday)
    return SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path), get_daily_asset=Mock(return_value=daily()))


def _previous_weekday(day):
    expected = day - timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    return expected


def daily():
    return pl.DataFrame({'symbol': ['515880.SH'] * 2,
                         'date': [date(2026, 10, 15), date(2026, 10, 16)],
                         'close': [.521, .522]})


def minutes(close=.522):
    return pl.DataFrame({'datetime': [datetime(2026, 10, 16, 15)], 'close': [close]})


def daily_rows(*rows):
    return pl.DataFrame({
        'symbol': ['515880.SH'] * len(rows),
        'date': [day for day, _ in rows],
        'close': [close for _, close in rows],
    })


def intraday_minutes(trade_date, close=.512, *, include_session_close=False):
    times = [datetime(trade_date.year, trade_date.month, trade_date.day, 11, 30)]
    closes = [close]
    if include_session_close:
        times.append(datetime(trade_date.year, trade_date.month, trade_date.day, 15, 0))
        closes.append(close)
    return pl.DataFrame({
        'datetime': times,
        'close': closes,
    })


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


def test_today_intraday_accepts_latest_previous_daily_close_without_session(context, monkeypatch):
    trade_date = date(2026, 10, 22)
    frame = daily_rows((date(2026, 10, 21), .535))
    monkeypatch.setattr(service, 'cn_today', lambda: trade_date)
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))

    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date), None)

    assert result == {
        'prev_close': .535,
        'prev_close_source': 'online_daily',
        'prev_close_date': '2026-10-21',
    }


def test_explicit_raw_etf_source_projects_previous_close_in_the_same_basis(context, monkeypatch):
    from app.data_providers import custom
    from app.plugins.txquote.provider import TxQuoteProvider

    trade_date = date(2026, 10, 22)
    frame = daily_rows((date(2026, 10, 21), .535))
    monkeypatch.setattr(service, 'cn_today', lambda: trade_date)
    monkeypatch.setattr(service.preferences, 'get_minute_data_provider', lambda: 'txquote')
    monkeypatch.setattr(custom, 'get_provider', lambda _: TxQuoteProvider())
    monkeypatch.setattr(service, '_online_daily', lambda *_: frame)
    monkeypatch.setattr(service.minute_adjust, 'apply_minute_adjustment',
                        lambda data, *_: data.with_columns(pl.col('close') * 2))
    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date, close=1.024), None)
    assert result['prev_close'] == 1.07
    assert result['prev_close_date'] == '2026-10-21'


def test_monday_intraday_accepts_friday_close_without_session(context, monkeypatch):
    trade_date = date(2026, 10, 19)
    frame = daily_rows((date(2026, 10, 16), .522))
    monkeypatch.setattr(service, 'cn_today', lambda: trade_date)
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))

    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date), None)

    assert result['prev_close'] == .522
    assert result['prev_close_date'] == '2026-10-16'


def test_today_intraday_rejects_online_daily_older_than_previous_weekday(context, monkeypatch):
    trade_date = date(2026, 10, 22)
    frame = daily_rows((date(2026, 10, 20), .530))
    monkeypatch.setattr(service, 'cn_today', lambda: trade_date)
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))

    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date), None)

    assert result['prev_close'] is None
    assert result['prev_close_warning']


def test_historical_minutes_accept_previous_session_across_october_holiday(context, monkeypatch):
    trade_date = date(2026, 10, 8)
    frame = daily_rows((date(2026, 9, 30), .530), (trade_date, .512))
    monkeypatch.setattr(service.trading_calendar, 'previous_trading_day', lambda *_: date(2026, 9, 30))
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))

    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date, close=.512, include_session_close=True), None)

    assert result['prev_close'] == .530
    assert result['prev_close_date'] == '2026-09-30'


def test_historical_minutes_reject_non_adjacent_close_across_holiday(context, monkeypatch):
    trade_date = date(2026, 10, 8)
    frame = daily_rows((date(2026, 9, 29), .530), (trade_date, .512))
    monkeypatch.setattr(service.trading_calendar, 'previous_trading_day', lambda *_: date(2026, 9, 30))
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))

    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date, close=.512), None)

    assert result['prev_close'] is None
    assert result['prev_close_warning']


def test_calendar_unknown_keeps_weekday_fallback_and_rejects_long_holiday_gap(context, monkeypatch):
    trade_date = date(2026, 10, 8)
    frame = daily_rows((date(2026, 9, 30), .530), (trade_date, .512))
    monkeypatch.setattr(service.trading_calendar, 'previous_trading_day', lambda *_: None)
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))

    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date, close=.512), None)

    assert result['prev_close'] is None


def test_today_partial_daily_does_not_hide_a_missing_previous_weekday(context, monkeypatch):
    trade_date = date(2026, 10, 22)
    frame = daily_rows((date(2026, 10, 20), .530), (trade_date, .512))
    monkeypatch.setattr(service, 'cn_today', lambda: trade_date)
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))
    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date), None)
    assert result['prev_close'] is None


def test_today_intraday_other_minute_provider_rejects_missing_session(context, monkeypatch):
    trade_date = date(2026, 10, 22)
    frame = daily_rows((date(2026, 10, 21), .535))
    monkeypatch.setattr(service, 'cn_today', lambda: trade_date)
    monkeypatch.setattr(service.preferences, 'get_minute_data_provider', lambda: 'eltdx_gateway')
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))

    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date), None)

    assert result['prev_close'] is None
    assert result['prev_close_warning']


def test_today_intraday_projects_raw_basis_daily_reference(context, monkeypatch):
    trade_date = date(2026, 10, 22)
    frame = daily_rows((date(2026, 10, 21), .535))
    projected = []
    monkeypatch.setattr(service, 'cn_today', lambda: trade_date)
    monkeypatch.setattr(service.minute_adjust, 'minute_basis_is_raw', lambda _: True)
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))

    def project(daily_frame, *_):
        projected.append(daily_frame)
        return daily_frame.with_columns(pl.col('close') * .5)

    monkeypatch.setattr(service.minute_adjust, 'apply_minute_adjustment', project)
    result = service.resolve(context, None, '515880.SH', trade_date, 'etf',
                             intraday_minutes(trade_date), None)

    assert result['prev_close'] == .2675
    assert result['prev_close_date'] == '2026-10-21'
    assert len(projected) == 1


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

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import kline
    from app.tickflow.capabilities import CapabilitySet

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


def test_current_minute_api_keeps_native_fallback_raw_for_the_online_reference(context, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import kline
    from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet

    trade_date = date(2026, 10, 22)
    frame = daily_rows((date(2026, 10, 21), .535))
    raw_minutes = intraday_minutes(trade_date).with_columns(pl.lit('515880.SH').alias('symbol'))
    native = Mock(return_value=raw_minutes)
    monkeypatch.setattr(service, 'cn_today', lambda: trade_date)
    monkeypatch.setattr(kline, 'cn_today', lambda: trade_date)
    monkeypatch.setattr(kline, 'in_continuous_session', lambda: True)
    monkeypatch.setattr(kline, '_get_asset_info', lambda *_: {'name': '通信ETF'})
    monkeypatch.setattr(kline, '_get_previous_closes', lambda *_: {trade_date: None})
    monkeypatch.setattr(kline, '_get_price_limit_info', lambda *_: None)
    monkeypatch.setattr(kline, '_minute_fallback_source_context', lambda *_: ('native-fallback', False))
    monkeypatch.setattr(kline.kline_sync, '_try_custom_minute', lambda *_, **__: (None, True))
    monkeypatch.setattr(kline.kline_sync, 'get_client', lambda: SimpleNamespace(klines=SimpleNamespace(batch=native)))
    monkeypatch.setattr(kline.kline_sync, '_compact_klines_to_df', lambda raw, **_: raw)
    monkeypatch.setattr(kline.kline_sync, '_normalize_minute', lambda raw: raw)
    monkeypatch.setattr(service.analysis_snapshot, 'load_snapshot', lambda *_: (frame, {
        'source': 'online', 'price_basis': 'unadjusted', 'requested_provider': 'tickflow',
    }))
    context.resolve_asset_type = lambda _: 'etf'
    app = FastAPI()
    app.state.repo = context
    app.state.capabilities = CapabilitySet({Cap.KLINE_MINUTE_BY_SYMBOL: CapabilityLimits()})
    app.include_router(kline.router)
    with TestClient(app) as client:
        response = client.get('/api/kline/minute?symbol=515880.SH&date=2026-10-22&live=1')
    assert response.status_code == 200
    assert response.json()['prev_close'] == .535
    assert native.call_args.kwargs['adjust'] == 'none'
