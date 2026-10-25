from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.services import watchlist_refresh as service


def test_forced_refresh_fails_when_zero_rows_even_if_cached_etf_is_ready(monkeypatch):
    events = []

    class Repo:
        def get_etf_symbol_set(self):
            return {'ETF_A.SH'}

        def get_enriched_latest_asset(self, asset):
            assert asset == 'etf'
            return pl.DataFrame(
                {'symbol': ['ETF_A.SH'], 'close': [1.25]},
                schema={'symbol': pl.String, 'close': pl.Float64},
            ), date(2026, 10, 23)

        def refresh_etf_enriched(self):
            pytest.fail('A zero-row sync must not publish a new cache')

    repo = Repo()
    store = SimpleNamespace(
        create=lambda: ('job', True),
        progress=lambda *_args, **_kwargs: None,
        succeed=lambda *_args: events.append('succeed'),
        fail=lambda *_args: events.append('fail'),
    )
    monkeypatch.setattr(service.watchlist, 'list_symbols', lambda: [{'symbol': 'ETF_A.SH'}])
    monkeypatch.setattr(service, 'job_store', store)
    monkeypatch.setattr(service, 'try_acquire_run_slot', lambda _job_id: True)
    monkeypatch.setattr(service, 'release_run_slot', lambda _job_id: events.append('release'))
    monkeypatch.setattr(service, 'run_with_capacity', lambda _job_id, run: run())
    monkeypatch.setattr(service.index_sync, 'sync_etf_adj_factor', lambda *_args, **_kwargs: (0, []))
    monkeypatch.setattr(service.index_sync, 'sync_and_persist_etf_daily', lambda *_args, **_kwargs: 0)

    with pytest.raises(RuntimeError, match='未取得'):
        service.sync_etfs(repo, object(), force=True)

    assert events == ['fail', 'release']