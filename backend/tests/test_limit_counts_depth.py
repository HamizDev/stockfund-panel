from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.api import screener
from app.services import market_overview_builder as overview
from app.services.limit_counts import price_limit_scope, sealed_counts
from app.services.screener import ScreenerService


def test_snapshot_counts_only_price_limit_members_and_missing_is_pending():
    snapshot = {'A': {'sealed': True}, 'B': {'sealed': False}, 'unrelated': {'sealed': False}}
    assert sealed_counts(['A', 'B', 'C', 'A'], snapshot, True) == {'real': 1, 'fake': 1, 'pending': 1}
    assert sealed_counts(['A', 'B'], snapshot, False) == {'real': 0, 'fake': 0, 'pending': 2}


def test_price_scope_explains_st_counts_and_deduplicates_symbols():
    rows = [
        {'symbol': '600001.SH', 'name': '*ST示例', 'signal_limit_up': True},
        {'symbol': '600002.SH', 'name': '普通示例', 'signal_limit_up': True},
        {'symbol': '920001.BJ', 'name': '北交示例', 'signal_limit_down': True},
    ]
    result = price_limit_scope([*rows, rows[1]])
    assert result['covered'] == 3
    assert result['non_st_up'] == result['non_st_down'] == 1
    assert '含ST及北交所' in result['label']


@pytest.mark.parametrize('direction', ['up', 'down'])
def test_price_breadth_and_ladder_do_not_change_with_depth_snapshot(tmp_path, monkeypatch, direction):
    day = date(2026, 10, 15)
    frame = pl.DataFrame({
        'symbol': ['600001.SH', '600002.SH', '600003.SH'],
        'name': ['涨停例', '跌停例', '普通例'], 'close': [11., 9., 10.],
        'volume': [100., 100., 100.], 'change_pct': [.1, -.1, 0.],
        'signal_limit_up': [True, False, False], 'signal_limit_down': [False, True, False],
        'signal_broken_limit_up': [False, False, False], 'signal_limit_down_recovery': [False, False, False],
        'consecutive_limit_ups': [1, 0, 0], 'consecutive_limit_downs': [0, 1, 0],
    })
    monkeypatch.setattr(ScreenerService, '_load_enriched_for_date', lambda *_: frame)
    monkeypatch.setattr(ScreenerService, 'load_prior_consecutive', lambda *_: pl.DataFrame({'symbol': ['600003.SH'], 'prev_consec': [1]}))
    monkeypatch.setattr(overview, '_index_quotes', lambda *_: [])
    monkeypatch.setattr(overview, '_dimension_rank', lambda *_a, **_k: {'leading': [], 'lagging': []})
    monkeypatch.setattr(overview, '_em_industry_rank', lambda: {'leading': [], 'lagging': []})
    depth = SimpleNamespace(
        get_sealed_map=lambda *_a, **_k: {'600001.SH': {'sealed': False, 'vol': 0}, '600003.SH': {'sealed': False, 'vol': 0}},
        is_sealed_ready=lambda _: True, get_sealed_age=lambda _: 10,
    )
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    state = SimpleNamespace(repo=repo, depth_service=depth)
    ladder = screener.limit_ladder(SimpleNamespace(app=SimpleNamespace(state=state)), as_of=day, direction=direction, ext_columns=None)
    market = overview.build_market_overview(repo, depth_service=depth, as_of=day)
    assert ladder['counts'] == {'up': 1, 'down': 1}
    assert ladder['sealed_counts']['fake'] == (1 if direction == 'up' else 0)
    assert market['limit']['limit_up'] == market['limit']['limit_down'] == 1
    assert market['limit']['sealed_counts_up'] == {'real': 0, 'fake': 1, 'pending': 0}
    assert market['limit']['sealed_counts_down'] == {'real': 0, 'fake': 0, 'pending': 1}
    members = {r['symbol']: r for tier in ladder['tiers'] for r in tier['stocks']}
    assert members['600001.SH' if direction == 'up' else '600002.SH']['status'] == ('limit_up' if direction == 'up' else 'limit_down')
    assert members['600003.SH']['sealed_status'] is None
