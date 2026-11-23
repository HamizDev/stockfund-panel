"""Exact status aggregates avoid broad view binding and redundant file stats."""
import threading
from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.api import data


class Repo:
    def __init__(self, path):
        self.store = SimpleNamespace(data_dir=path)
        self.calls = []

    def execute_one(self, sql, params=None):
        self.calls.append((sql, params))
        if 'instruments' in sql:
            return (3,)
        return (6, date(2026, 10, 1), date(2026, 10, 2), 2, 2)


@pytest.mark.parametrize(
    'view', ['kline_index_daily', 'kline_index_enriched', 'kline_etf_daily', 'kline_etf_enriched'],
)
def test_exact_projection_handles_mixed_extra_columns_and_nulls(tmp_path, view):
    for day in (1, 2):
        folder = tmp_path / view / f'date=2026-10-{day:02d}'
        folder.mkdir(parents=True)
        frame = pl.DataFrame({'symbol': ['a', 'b', None],
                              'date': [date(2026, 10, day)] * 3,
                              'close': [1., 2., 3.]})
        if day == 2:
            frame = frame.with_columns(pl.lit('new').alias('quote_ts'))
        frame.write_parquet(folder / 'part.parquet')
    repo = Repo(tmp_path)
    stats = (
        data._safe_aggregate_etf_daily(repo)
        if view == 'kline_etf_daily'
        else data._safe_aggregate(repo, view)
    )
    assert stats == {'rows': 6, 'earliest_date': '2026-10-01',
                     'latest_date': '2026-10-02', 'symbols_covered': 2, 'trading_days': 2}
    assert repo.calls == []


def test_adj_factor_projection_preserves_filtered_dates_and_null_semantics(tmp_path):
    (tmp_path / 'kline_daily' / 'date=2026-10-01').mkdir(parents=True)
    (tmp_path / 'kline_daily' / 'date=2026-10-02').mkdir()
    folder = tmp_path / 'adj_factor'
    folder.mkdir()
    frame = pl.DataFrame({'symbol': ['old', 'a', 'a', None, 'new'],
                          'trade_date': [date(2026, 9, 30), date(2026, 10, 1),
                                         date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 3)]})
    frame.write_parquet(folder / 'all.parquet')
    repo = Repo(tmp_path)
    stats = data._safe_aggregate_adj_factor(repo)
    assert stats == {'rows': 3, 'symbols_covered': 1, 'trading_days': 2,
                     'earliest_date': '2026-10-01', 'latest_date': '2026-10-02'}
    assert repo.calls == []


def test_legacy_view_remains_available_without_standard_parquet(tmp_path):
    repo = Repo(tmp_path)
    assert data._safe_aggregate(repo, 'kline_index_daily')['rows'] == 6
    assert len(repo.calls) == 1


def test_empty_parquet_is_not_a_fake_nonempty_table(tmp_path):
    folder = tmp_path / 'kline_index_daily'
    folder.mkdir()
    pl.DataFrame(schema={'symbol': pl.String, 'date': pl.Date}).write_parquet(
        folder / 'empty.parquet',
    )
    repo = Repo(tmp_path)
    assert data._safe_aggregate(repo, 'kline_index_daily') is None
    assert repo.calls == []


def test_etf_empty_store_keeps_legacy_index_filter(tmp_path):
    class LegacyRepo(Repo):
        def execute_one(self, sql, params=None):
            self.calls.append((sql, params))
            if 'FROM kline_etf_daily' in sql:
                return (0, None, None, 0, 0)
            assert 'FROM kline_index_daily' in sql
            assert "asset_type = 'etf'" in sql
            return (2, date(2026, 10, 1), date(2026, 10, 2), 1, 2)
    repo = LegacyRepo(tmp_path)
    assert data._safe_aggregate_etf_daily(repo) == {
        'rows': 2, 'earliest_date': '2026-10-01', 'latest_date': '2026-10-02',
        'symbols_covered': 1, 'trading_days': 2,
    }
    assert len(repo.calls) == 2


def test_incompatible_legacy_schema_falls_back_to_view(tmp_path):
    folder = tmp_path / 'kline_index_daily'
    folder.mkdir()
    pl.DataFrame({'symbol': ['a'], 'date': ['2026-10-01']}).write_parquet(
        folder / 'part.parquet',
    )
    repo = Repo(tmp_path)
    assert data._safe_aggregate(repo, 'kline_index_daily')['rows'] == 6
    assert len(repo.calls) == 1


def test_all_null_required_columns_follow_sql_distinct_semantics(tmp_path):
    folder = tmp_path / 'kline_index_daily'
    folder.mkdir()
    frame = pl.DataFrame(
        {'symbol': [None], 'date': [None]}, schema={'symbol': pl.String, 'date': pl.Date},
    )
    frame.write_parquet(folder / 'part.parquet')
    stats = data._safe_aggregate(Repo(tmp_path), 'kline_index_daily')
    assert stats == {'rows': 1, 'symbols_covered': 0, 'trading_days': 0,
                     'earliest_date': None, 'latest_date': None}


def test_daily_partition_summary_uses_directory_entries(tmp_path, monkeypatch):
    (tmp_path / 'kline_daily' / 'date=2026-10-01').mkdir(parents=True)
    (tmp_path / 'kline_daily' / 'date=2026-10-02').mkdir()
    (tmp_path / 'kline_daily' / 'date=not-a-date').mkdir()
    (tmp_path / 'kline_daily' / 'date=2026-10-03').write_text('file')
    def no_stat(_path):
        raise AssertionError('Directory entries already contain type information')
    monkeypatch.setattr(type(tmp_path), 'is_dir', no_stat)
    assert data._safe_aggregate_daily(Repo(tmp_path))['trading_days'] == 2


def test_status_overlaps_storage_io_and_rechecks_after_table_reads(tmp_path, monkeypatch):
    data.invalidate_data_cache(None)
    scan_started = threading.Event()
    tables_started = threading.Event()
    scans = 0
    def scan(_path):
        nonlocal scans
        scans += 1
        scan_started.set()
        assert tables_started.wait(timeout=2)
        return {'total_size_mb': 2.0}
    def table(_name, _fetch):
        assert scan_started.wait(timeout=2)
        tables_started.set()
        return {'rows': 1}
    monkeypatch.setattr(data, '_compute_storage', scan)
    monkeypatch.setattr(data, '_get_table_stats', table)
    monkeypatch.setattr(data, '_last_finished', lambda _: None)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=Repo(tmp_path))))
    try:
        result = data.status(request)
        assert result['daily'] == {'rows': 1}
        assert result['storage'] == {'total_size_mb': 2.0}
        assert scans == 1
    finally:
        data.invalidate_data_cache(None)
