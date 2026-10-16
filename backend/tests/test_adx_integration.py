from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import polars as pl
from app.backtest.matrix import (
    MatrixComputeCache,
    _append_market_row,
    _overwrite_latest_market_row,
    _readonly_market_view,
    _writable_market_copy,
    build_market_data_matrix,
    load_market_data_matrix_from_parquet,
    matrix_feature,
    slice_market_data_matrix,
)
from app.factors.registry import get_factor
from app.indicators.pipeline import compute_indicators
from app.strategy.scoring import scoring_warmup_bars

FIELDS = ('adx_14', 'adx_plus_di_14', 'adx_minus_di_14')


def bars():
    rows = []
    for symbol, first in (('510300.SH', 0), ('518880.SH', 12)):
        for i in range(first, 70):
            close = 100 + (i if i < 35 else 70 - i)
            rows.append(dict(symbol=symbol, date=date(2026, 1, 1) + timedelta(days=i),
                             open=float(close), high=float(close + 1), low=float(close - 1),
                             close=float(close), volume=1000.0))
    return pl.DataFrame(rows)


def test_requested_adx_is_causal_and_pruned_without_other_indicator_columns():
    data = bars()
    full = compute_indicators(data)
    subset = compute_indicators(data, needed={'adx_14'})
    assert set(subset.columns) == set(data.columns) | {'adx_14'}
    assert subset['adx_14'].equals(full['adx_14'])
    assert not any(name.startswith('__adx14') for name in full.columns)
    for symbol in data['symbol'].unique():
        prefix = data.filter(pl.col('symbol') == symbol).head(40)
        expected = compute_indicators(prefix, needed={'adx_14'})
        actual = subset.filter(pl.col('symbol') == symbol).head(40)
        assert expected['adx_14'].equals(actual['adx_14'])


def test_matrix_and_polars_match_with_ipo_prefix_and_share_cached_directional_calculation():
    data = bars()
    calculated = compute_indicators(data, needed=set(FIELDS))
    market = build_market_data_matrix(data)
    cache = MatrixComputeCache(max_bytes=8 * 1024 * 1024)
    with cache.activate(market):
        actual = {field: matrix_feature(market, field) for field in FIELDS}
        assert matrix_feature(market, 'adx_14') is actual['adx_14']
    for column, symbol in enumerate(market.symbols):
        expected = calculated.filter(pl.col('symbol') == symbol)
        offset = 0 if symbol == '510300.SH' else 12
        for field in FIELDS:
            np.testing.assert_allclose(actual[field][offset:, column], expected[field].to_numpy(),
                                       rtol=2e-5, atol=2e-5, equal_nan=True)
            assert np.isnan(actual[field][:offset, column]).all()
    assert cache.snapshot()['operations']['directional_wilder_14']['hits'] == 2


def test_registry_preserves_seed_warmups_for_strategy_use():
    assert get_factor('adx_14').warmup_bars == 28
    assert scoring_warmup_bars({'adx_14': 1}) == 28
    assert scoring_warmup_bars({'adx_plus_di_14': 1}) == 15
    assert scoring_warmup_bars({'adx_14': 0, 'ma20_bias': 1}) == 1


def test_recorded_all_null_bar_stops_adx_but_absent_ipo_slots_do_not():
    missing_date = date(2026, 1, 1) + timedelta(days=45)
    data = bars().with_columns([
        pl.when((pl.col('symbol') == '510300.SH') & (pl.col('date') == missing_date))
        .then(None).otherwise(pl.col(name)).alias(name)
        for name in ('open', 'high', 'low', 'close')
    ])
    market = build_market_data_matrix(data)
    assert market.observed[45, 0] and not market.observed[0, 1]
    expected = compute_indicators(data, needed=set(FIELDS))
    for field in FIELDS:
        actual = matrix_feature(market, field)
        assert np.isnan(actual[45:, 0]).all()
        for column, symbol in enumerate(market.symbols):
            offset = 0 if column == 0 else 12
            np.testing.assert_allclose(actual[offset:, column],
                expected.filter(pl.col('symbol') == symbol)[field].to_numpy(),
                rtol=2e-5, atol=2e-5, equal_nan=True)
    # Identical OHLC arrays with a different record mask must not reuse ADX.
    mask = np.array(market.observed, copy=True)
    mask[45, 0] = False
    absent = replace(market, observed=mask)
    with MatrixComputeCache(max_bytes=8 * 1024 * 1024).activate(market):
        assert np.isnan(matrix_feature(market, 'adx_14')[46, 0])
        assert np.isfinite(matrix_feature(absent, 'adx_14')[46, 0])


def test_observed_mask_survives_parquet_cache_slice_and_live_updates(tmp_path):
    data = bars().with_columns([
        pl.when((pl.col('symbol') == '510300.SH') &
                (pl.col('date') == date(2026, 2, 15)))
        .then(None).otherwise(pl.col(name)).alias(name)
        for name in ('open', 'high', 'low', 'close')
    ])
    root = tmp_path / 'parquet'
    for day in data['date'].unique().to_list():
        partition = root / f'date={day.isoformat()}'
        partition.mkdir(parents=True)
        data.filter(pl.col('date') == day).drop('date').write_parquet(partition / 'part.parquet')
    expected = build_market_data_matrix(data)
    for cache_root in (None, tmp_path / 'cache', tmp_path / 'cache'):
        loaded = load_market_data_matrix_from_parquet(root, date(2026, 1, 1), date(2026, 3, 11),
            field_columns=set(), cache_root=cache_root)
        np.testing.assert_array_equal(loaded.observed, expected.observed)
        assert np.isnan(matrix_feature(loaded, 'adx_14')[45:, 0]).all()
        sliced = slice_market_data_matrix(loaded, 10, 50)
        np.testing.assert_array_equal(sliced.observed, expected.observed[10:50])
        assert not sliced.observed.flags.writeable
        writable = _writable_market_copy(sliced)
        latest = slice_market_data_matrix(expected, 45, 46)
        ids = np.arange(len(expected.symbols))
        _overwrite_latest_market_row(writable, latest, ids)
        np.testing.assert_array_equal(writable.observed[-1], latest.observed[0])
        view = _readonly_market_view(writable)
        assert not view.observed.flags.writeable
        appended = _append_market_row(writable, latest, ids)
        np.testing.assert_array_equal(appended.observed[-1], latest.observed[0])
