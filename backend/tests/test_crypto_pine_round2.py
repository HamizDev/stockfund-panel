from __future__ import annotations

import copy
import json

import pytest

from app.custom.crypto_paper import auto, rules, strategy_draft
from app.custom.crypto_paper import pine_research as pine

NEW_IDS = ('supertrend_pullback', 'bb_aroon')


def flat_bars(count=200):
    return [dict(open=100., high=101., low=99., close=100.) for _ in range(count)]


def trend_bars(direction=1):
    return [dict(open=300 + direction * i, high=301 + direction * i,
                 low=299 + direction * i, close=300 + direction * i) for i in range(200)]


def test_supertrend_wilder_atr_and_recursive_bands_fixed_fixture():
    rows = [dict(open=c, high=c + 1, low=c - 1, close=c) for c in (10, 11, 14, 15, 10)]
    result = pine._supertrend_series(rows, 2, 1)
    # TR = [2,2,4,2,6]; seeded ATR2 = [2,3,2.5,4.25].
    assert [row['atr'] for row in result] == pytest.approx([2, 3, 2.5, 4.25])
    assert [row['direction'] for row in result] == [-1, 1, 1, -1]
    assert [row['supertrend'] for row in result] == pytest.approx([13, 11, 12.5, 14.25])


@pytest.mark.parametrize('direction,target', [(1, 'short'), (-1, 'long')])
def test_supertrend_flip_reverses_target_only_after_closed_price_crosses_band(direction, target):
    rows = trend_bars(direction)
    assert pine.analyse('supertrend_pullback', rows)['target'] == 'hold'
    price = rows[-1]['close'] - direction * 30
    rows[-1] = dict(open=price, high=price + 1, low=price - 1, close=price)
    result = pine.analyse('supertrend_pullback', rows)
    assert result['conditions']['trend_flip']
    assert result['target'] == target
    assert auto.signal('supertrend_pullback', rows, 'spot') == ('flat' if target == 'short' else target)


@pytest.mark.parametrize('direction,target', [(1, 'long'), (-1, 'short')])
@pytest.mark.parametrize('previous_offset', [0, 1])
def test_supertrend_rebound_can_reenter_without_existing_entry_price(direction, target, previous_offset):
    rows = trend_bars(direction)
    # Wide ATR filter allows a brief EMA cross without reversing the ST trend.
    params = dict(atr_period=10, atr_multiplier=5, ema_period=10)
    ema = pine._ema([row['close'] for row in rows[:-2]], 10)[-1]
    for index, price in ((-2, ema - direction * previous_offset), (-1, ema + direction * 3)):
        rows[index] = dict(open=price, high=price + 3, low=price - 3, close=price)
    result = pine.analyse('supertrend_pullback', rows, params)
    assert not result['conditions']['trend_flip']
    assert result['conditions']['rebound_up' if direction == 1 else 'rebound_down']
    assert result['target'] == target


def test_bb_aroon_buys_downward_break_only_in_recent_high_regime():
    rows = flat_bars()
    rows[-3]['close'] = 99
    rows[-2]['high'] = 110
    rows[-1].update(close=90, low=89)
    result = pine.analyse('bb_aroon', rows)
    assert result['target'] == 'long'
    assert result['metrics']['aroon_up'] == 99
    # 18*100+99+90 mean=99.45; population variance=4.7475.
    assert result['metrics']['bb_middle'] == 99.45
    assert result['metrics']['bb_lower'] == pytest.approx(99.45 - 2 * 4.7475**.5)
    assert result['conditions']['crossed_below_lower']
    assert '仍可能继续下跌' in result['reason']
    # No cross on a second bar that remains below the moving lower band.
    rows.append(dict(open=90, high=91, low=79, close=80))
    result = pine.analyse('bb_aroon', rows[-200:])
    assert result['target'] == 'hold'


@pytest.mark.parametrize('lag,expected,target', [(10, 90, 'hold'), (30, 70, 'hold'), (31, 69, 'flat')])
def test_aroon_uses_l_plus_one_and_strict_thresholds(lag, expected, target):
    rows = flat_bars()
    rows[-lag - 1]['high'] = 120
    if lag == 10:
        rows[-3]['close'] = 99
        rows[-1].update(close=90, low=89)
    result = pine.analyse('bb_aroon', rows)
    assert result['metrics']['aroon_up'] == expected
    assert result['target'] == target
    assert auto.signal('bb_aroon', rows, 'usdm') == target


def test_aroon_equal_highs_choose_most_recent_and_upper_reentry_exits_long():
    rows = flat_bars()
    rows[-31]['high'] = rows[-2]['high'] = 120
    rows[-2]['close'] = 115
    rows[-1]['close'] = 103
    rows[-1]['high'] = 104
    result = pine.analyse('bb_aroon', rows)
    assert result['metrics']['aroon_up'] == 99
    assert result['conditions']['crossed_below_upper']
    assert result['target'] == 'flat'  # Must never open short, even for a futures account.


def test_zero_width_previous_bands_prioritize_exit_over_simultaneous_entry():
    rows = flat_bars()
    rows[-2]['high'] = 110
    rows[-1].update(close=90, low=89)
    result = pine.analyse('bb_aroon', rows)
    assert result['conditions']['crossed_below_upper']
    assert result['conditions']['crossed_below_lower']
    assert result['conditions']['aroon_entry']
    assert result['target'] == 'flat'


@pytest.mark.parametrize('id', NEW_IDS)
def test_analysis_prefix_is_causal_and_never_mutates_inputs(id):
    data = trend_bars()
    original = copy.deepcopy(data)
    prefix = copy.deepcopy(data[:150])
    before = pine.analyse(id, prefix)
    data[150:] = flat_bars(50)
    assert pine.analyse(id, data[:150]) == before
    pine.analyse(id, prefix)
    assert prefix == original[:150]


@pytest.mark.parametrize('id,params,required', [
    ('supertrend_pullback', dict(atr_period=30, atr_multiplier=5, ema_period=120), 121),
    ('bb_aroon', dict(bb_period=60, aroon_period=120), 121),
])
def test_warmup_fits_existing_200_closed_bar_budget(id, params, required):
    assert rules.minimum_bars(id, params) == required
    with pytest.raises(ValueError, match='不足'):
        auto.signal(id, flat_bars(required - 1), 'usdm', params)
    assert auto.signal(id, flat_bars(), 'usdm', params) in ('long', 'short', 'flat', 'hold')


@pytest.mark.parametrize('id,key,bad', [
    ('supertrend_pullback', 'atr_period', 4), ('supertrend_pullback', 'atr_period', 31),
    ('supertrend_pullback', 'atr_multiplier', 0), ('supertrend_pullback', 'atr_multiplier', 6),
    ('supertrend_pullback', 'ema_period', 9), ('supertrend_pullback', 'ema_period', 121),
    ('bb_aroon', 'bb_period', 9), ('bb_aroon', 'bb_period', 61),
    ('bb_aroon', 'aroon_period', 19), ('bb_aroon', 'aroon_period', 121),
    ('bb_aroon', 'aroon_period', True), ('supertrend_pullback', 'atr_multiplier', 2.5),
])
def test_parameter_bounds_reject_before_any_signal(id, key, bad):
    params = rules.parameters(id)
    params[key] = bad
    with pytest.raises(ValueError):
        rules.parameters(id, params)


@pytest.mark.parametrize('id', NEW_IDS)
def test_unknown_parameters_and_oversized_input_fail_closed(id):
    params = rules.parameters(id)
    with pytest.raises(ValueError):
        rules.parameters(id, {**params, 'future': 1})
    with pytest.raises(ValueError, match='1-200'):
        pine.analyse(id, flat_bars(201))


@pytest.mark.parametrize('id', NEW_IDS)
def test_ai_model_accepts_new_family_and_uses_same_signal_diagnostics(id):
    model = strategy_draft.parse_draft(json.dumps(dict(
        name='Research', strategy_id=id, strategy_params=rules.parameters(id), allocation_pct=5.,
        stop_loss_pct=1., take_profit_pct=2., rationale='Independent closed bar rule research.',
        risk_notes=['Not independently backtested.'])), strategy_draft.DraftRequest())
    result = strategy_draft.diagnostics(model, flat_bars(), 'usdm')
    assert result['latest_signal'] == auto.signal(id, flat_bars(), 'usdm')
    assert result['evaluated_bars'] == 201 - rules.minimum_bars(id)
    assert sum(result['signal_counts'].values()) == result['evaluated_bars']
    assert '收益' in result['method']
