from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.watchlist import router
from app.services import watchlist_refresh as service


class Repo:
    def __init__(self, symbols=('ETF_A.SH', 'ETF_B.SH'), ready=('ETF_A.SH',)):
        self.symbols = list(symbols)
        self.ready = list(ready)
        self.stock_snapshot = object()
        self.warmed = False

    def get_etf_symbol_set(self):
        return set(self.symbols)

    def get_enriched_latest_asset(self, asset):
        assert asset == 'etf'
        return pl.DataFrame(
            {'symbol': self.ready, 'close': [1.25] * len(self.ready)},
            schema={'symbol': pl.String, 'close': pl.Float64},
        ), date(2026, 10, 23)

    def refresh_etf_enriched(self):
        self.warmed = True
        self.ready = list(self.symbols)


@pytest.fixture
def setup(monkeypatch):
    repo = Repo()
    events = []
    monkeypatch.setattr(
        service.watchlist,
        'list_symbols',
        lambda: [{'symbol': symbol} for symbol in ('ETF_A.SH', 'STOCK_A.SH', 'ETF_B.SH')],
    )
    store = SimpleNamespace(
        create=lambda: ('new-job', True),
        progress=lambda *_args, **_kwargs: None,
        succeed=lambda *_args: events.append('success'),
        fail=lambda *_args: events.append('fail'),
    )
    monkeypatch.setattr(service, 'job_store', store)
    monkeypatch.setattr(service, 'try_acquire_run_slot', lambda _job_id: True)
    monkeypatch.setattr(service, 'release_run_slot', lambda _job_id: events.append('release'))
    monkeypatch.setattr(service, 'run_with_capacity', lambda _job_id, fn: fn())
    monkeypatch.setattr(service.index_sync, 'sync_etf_adj_factor', lambda *_args, **_kwargs: (0, []))

    def fetch(_repo, _caps, **kwargs):
        events.append(kwargs)
        assert (kwargs['end_date'] - kwargs['start_date']).days == 365
        return 241 * len(kwargs['symbols_override'])

    monkeypatch.setattr(service.index_sync, 'sync_and_persist_etf_daily', fetch)
    return repo, store, events


def test_repairs_only_missing_etfs_then_publishes_before_success(setup):
    repo, _, events = setup
    stock_snapshot = repo.stock_snapshot
    quote = SimpleNamespace()

    @contextmanager
    def paused():
        events.append('paused')
        try:
            yield
        finally:
            events.append('resumed')

    quote.paused = paused
    result = service.sync_etfs(repo, object(), quote)

    assert result['requested'] == ['ETF_B.SH']
    assert result['rows_written'] == 241 and result['remaining'] == []
    assert repo.warmed and repo.stock_snapshot is stock_snapshot
    assert events[0] == 'paused' and events[-3:] == ['resumed', 'success', 'release']


def test_manual_refresh_includes_ready_etfs_but_never_stocks(setup):
    repo, _, _ = setup
    result = service.sync_etfs(repo, object(), force=True)
    assert result['requested'] == ['ETF_A.SH', 'ETF_B.SH']


def test_ready_watchlist_does_not_start_a_job(setup):
    repo, store, events = setup
    repo.ready = list(repo.symbols)
    store.create = lambda: pytest.fail('No sync needed')

    result = service.sync_etfs(repo, object())

    assert not result['requested'] and not events


def test_existing_job_is_not_rescheduled_or_marked_failed(setup):
    repo, store, events = setup
    store.create = lambda: ('existing', False)

    with pytest.raises(service.WatchlistRefreshBusyError):
        service.sync_etfs(repo, object())

    assert events == []


def test_write_slot_failure_does_not_fetch_or_release_another_owner(setup, monkeypatch):
    repo, _, events = setup
    monkeypatch.setattr(service, 'try_acquire_run_slot', lambda _job_id: False)

    with pytest.raises(service.WatchlistRefreshBusyError):
        service.sync_etfs(repo, object())

    assert events == ['fail']


def test_fetch_failure_releases_slot_without_clearing_snapshot(setup, monkeypatch):
    repo, _, events = setup
    monkeypatch.setattr(
        service.index_sync,
        'sync_and_persist_etf_daily',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('upstream failure detail')),
    )

    with pytest.raises(RuntimeError, match='upstream failure detail'):
        service.sync_etfs(repo, object())

    assert repo.ready == ['ETF_A.SH'] and not repo.warmed
    assert events == ['fail', 'release']


def test_zero_rows_is_failure_when_requested_etfs_are_missing(setup, monkeypatch):
    repo, _, events = setup
    monkeypatch.setattr(service.index_sync, 'sync_and_persist_etf_daily', lambda *_args, **_kwargs: 0)

    with pytest.raises(RuntimeError, match='未取得'):
        service.sync_etfs(repo, object())

    assert not repo.warmed and events == ['fail', 'release']


def test_zero_rows_is_not_reported_as_success_for_forced_refresh(setup, monkeypatch):
    repo, _, events = setup
    repo.ready = list(repo.symbols)
    monkeypatch.setattr(service.index_sync, 'sync_and_persist_etf_daily', lambda *_args, **_kwargs: 0)

    with pytest.raises(RuntimeError, match='未取得'):
        service.sync_etfs(repo, object(), force=True)

    assert not repo.warmed and events == ['fail', 'release']


def test_limits_selected_set_before_creating_a_job(setup, monkeypatch):
    repo, store, _ = setup
    repo.symbols = [f'ETF_{index:02d}' for index in range(61)]
    monkeypatch.setattr(service.watchlist, 'list_symbols', lambda: [{'symbol': symbol} for symbol in repo.symbols])
    store.create = lambda: pytest.fail('Must reject before scheduling')

    with pytest.raises(service.WatchlistRefreshLimitError, match='60'):
        service.sync_etfs(repo, object(), force=True)


@pytest.mark.parametrize('value', [None, 0.0, float('nan'), float('inf'), -1.0])
def test_bad_price_remains_missing(value):
    repo = SimpleNamespace(
        get_enriched_latest_asset=lambda _asset: (
            pl.DataFrame({'symbol': ['ETF_A.SH'], 'close': [value]}),
            None,
        ),
    )

    assert service.missing_etfs(repo, ['ETF_A.SH']) == ['ETF_A.SH']


def _test_app():
    app = FastAPI()
    app.include_router(router)
    app.state.repo = object()
    app.state.capabilities = object()
    return app


def test_api_returns_success_and_invalidates_storage_cache(monkeypatch):
    result = {
        'requested': ['ETF_A.SH'],
        'rows_written': 1,
        'remaining': [],
        'as_of': '2026-10-23',
        'warnings': [],
    }
    invalidations = []
    monkeypatch.setattr(service, 'sync_etfs', lambda *_args, **_kwargs: result)
    monkeypatch.setattr('app.api.data.invalidate_storage_cache', lambda: invalidations.append(True))

    with TestClient(_test_app()) as client:
        response = client.post('/api/watchlist/sync-daily', json={'force': True})

    assert response.status_code == 200 and response.json() == result
    assert invalidations == [True]


@pytest.mark.parametrize(
    ('exception', 'status'),
    [
        (service.WatchlistRefreshBusyError('busy'), 409),
        (service.WatchlistRefreshLimitError('limit'), 422),
        (RuntimeError('upstream failure detail'), 502),
        (ValueError('upstream failure detail'), 502),
    ],
)
def test_api_maps_errors_without_leaking_upstream_text(monkeypatch, exception, status):
    monkeypatch.setattr(
        service,
        'sync_etfs',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(exception),
    )

    with TestClient(_test_app()) as client:
        response = client.post('/api/watchlist/sync-daily', json={'force': False})

    assert response.status_code == status
    assert 'upstream failure detail' not in response.text


def test_adjustment_sync_precedes_price_history_and_is_watchlist_bounded(setup, monkeypatch):
    repo, _, events = setup

    def factors(symbols, current_repo, _caps, **kwargs):
        assert symbols == ['ETF_B.SH'] and current_repo is repo
        assert (kwargs['end_time'] - kwargs['start_time']).days == 365
        events.append('factors')
        return 0, []

    monkeypatch.setattr(service.index_sync, 'sync_etf_adj_factor', factors)
    result = service.sync_etfs(repo, object())

    assert events[0] == 'factors' and isinstance(events[1], dict)
    assert result['warnings']


def test_adjustment_failure_is_visible_without_leaking_provider_text(setup, monkeypatch):
    repo, _, _ = setup
    monkeypatch.setattr(
        service.index_sync,
        'sync_etf_adj_factor',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError('upstream failure detail')),
    )

    result = service.sync_etfs(repo, object())

    assert result['rows_written'] > 0 and '未完成' in result['warnings'][0]
    assert 'upstream failure detail' not in str(result)


def test_cache_publish_failure_does_not_report_existing_prices_as_success(setup, monkeypatch):
    repo, _, events = setup
    repo.ready = list(repo.symbols)
    monkeypatch.setattr(
        repo,
        'refresh_etf_enriched',
        lambda: (_ for _ in ()).throw(RuntimeError('cache refresh failure')),
    )

    with pytest.raises(RuntimeError, match='cache refresh failure'):
        service.sync_etfs(repo, object(), force=True)

    assert events[-2:] == ['fail', 'release'] and 'success' not in events


def test_strict_repository_refresh_preserves_cache_and_raises_on_read_failure(tmp_path, monkeypatch):
    from app.tickflow.repository import KlineRepository

    repo = KlineRepository.__new__(KlineRepository)
    repo.store = SimpleNamespace(data_dir=tmp_path)
    cache = pl.DataFrame({'symbol': ['ETF_A.SH'], 'close': [1.0]})
    cache_date = date(2026, 10, 22)
    repo._etf_enriched_cache = cache
    repo._etf_enriched_cache_date = cache_date
    (tmp_path / 'kline_etf_enriched' / 'date=2026-10-23').mkdir(parents=True)
    monkeypatch.setattr(
        pl,
        'read_parquet',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError('broken parquet')),
    )

    with pytest.raises(ValueError, match='broken parquet'):
        repo.refresh_etf_enriched()

    assert repo._etf_enriched_cache is cache
    assert repo._etf_enriched_cache_date == cache_date
    repo._refresh_etf_enriched()  # Existing best-effort callers remain compatible.
