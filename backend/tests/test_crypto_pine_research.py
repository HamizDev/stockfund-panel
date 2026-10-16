from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.crypto_paper import auto, ledger, rules
from app.custom.crypto_paper import pine_research as pine
from app.custom.crypto_paper import strategy_draft as draft
from app.custom.crypto_paper.routes import build_router

NOW = 1_800_000_120_000
HOUR = 3_600_000


def bars(direction=1, count=200):
    last = NOW // HOUR * HOUR - HOUR
    output = []
    for i in range(count):
        price = 300 + direction * i
        opening = last - (count - i - 1) * HOUR
        output.append({'open_time_ms': opening, 'close_time_ms': opening + HOUR - 1,
                       'open': price - direction * .25, 'high': price + 1, 'low': price - 1,
                       'close': price, 'volume': 100})
    return output


def squeeze_bars(direction=1):
    result = bars(0)
    for row in result:
        row.update(open=100, high=101, low=99, close=100)
    result[-1].update(open=100, close=100 + direction * 50, high=151 if direction > 0 else 101,
                      low=99 if direction > 0 else 49)
    return result


@pytest.mark.parametrize('direction,target', [(1, 'long'), (-1, 'short')])
def test_dmi_uses_three_directional_bars_strict_adx_and_candle_filter(direction, target):
    data = bars(direction)
    result = pine.analyse('dmi_confirm', data)
    assert result['target'] == target
    assert result['metrics']['adx_14'] == pytest.approx(100)
    assert auto.signal('dmi_confirm', data, 'usdm') == target
    assert auto.signal('dmi_confirm', data, 'spot') == ('flat' if direction < 0 else target)
    data[-1]['open'] = data[-1]['close'] + direction * .25
    assert pine.analyse('dmi_confirm', data)['target'] == 'hold'


@pytest.mark.parametrize('direction,target', [(1, 'long'), (-1, 'short')])
def test_squeeze_requires_release_and_causal_regression_direction(direction, target):
    data = squeeze_bars(direction)
    before = pine.analyse('squeeze_release', data[:-1])
    assert before['target'] == 'hold'
    result = pine.analyse('squeeze_release', data)
    assert result['target'] == target
    assert result['conditions']['previous_squeeze']
    assert result['conditions']['release']
    assert result['metrics']['momentum_price'] * direction > 0
    assert auto.signal('squeeze_release', data, 'usdm') == target
    data[-1]['close'] = 1e12
    data[-1]['high'] = 1e12
    assert pine.analyse('squeeze_release', data[:-1]) == before


@pytest.mark.parametrize('strategy', sorted(pine.IDS))
@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), -1, 0])
def test_invalid_ohlc_is_rejected_instead_of_generating_signal(strategy, bad):
    data = bars()
    data[40]['close'] = bad
    with pytest.raises((ValueError, TypeError)):
        auto.signal(strategy, data, 'usdm')


@pytest.mark.parametrize('id,params', [
    ('dmi_confirm', {'ma_period': 55, 'adx_threshold': True}),
    ('dmi_confirm', {'ma_period': 121, 'adx_threshold': 23}),
    ('dmi_confirm', {'ma_period': 55, 'adx_threshold': 51}),
    ('squeeze_release', {'lookback': 61}), ('squeeze_release', {'lookback': 9}),
    ('squeeze_release', {'lookback': 20, 'future': 1}),
])
def test_research_parameters_are_bounded_and_exact(id, params):
    with pytest.raises(ValueError):
        rules.parameters(id, params)


@pytest.mark.parametrize('id,params,required', [
    ('dmi_confirm', {'ma_period': 120, 'adx_threshold': 23}, 121),
    ('squeeze_release', {'lookback': 60}, 121),
])
def test_largest_rule_parameters_fit_fetch_budget_and_enforce_warmup(id, params, required):
    assert rules.minimum_bars(id, params) == required
    with pytest.raises(ValueError, match='不足'):
        auto.signal(id, bars(count=required - 1), 'usdm', params)
    assert auto.signal(id, bars(count=200), 'usdm', params) in ('long', 'short', 'hold')


@pytest.fixture
def public_source(monkeypatch):
    state = {'bars': bars(), 'quote': {
        'exchange': 'bitget', 'market': 'usdm', 'symbol': 'ETHUSDT', 'asof_ms': NOW,
        'bid': '500', 'ask': '501', 'mark': '500', 'max_leverage': 20,
        'taker_fee_rate': '0.0006', 'last_funding_rate': '0.0001',
        'step_size': '.001', 'min_qty': '.001', 'min_notional': '5',
        'funding_interval_hours': 8, 'next_funding_time': (NOW // (8 * HOUR) + 1) * 8 * HOUR}}
    source = SimpleNamespace(klines=lambda *a, **kw: copy.deepcopy(state['bars']),
                             quote=lambda *a: copy.deepcopy(state['quote']), funding_history=lambda *a: [])
    monkeypatch.setattr(auto, '_source', lambda exchange: source)
    monkeypatch.setattr(auto.time, 'time', lambda: NOW / 1000)
    monkeypatch.setattr(auto, '_BARS', {})
    return state


@pytest.mark.parametrize('id', sorted(pine.IDS))
def test_preview_and_ai_use_same_rules_without_account_or_model_side_effects(public_source, monkeypatch, tmp_path, id):
    monkeypatch.setattr(draft.ai_provider, 'generate_ai_text', lambda *a, **k: pytest.fail('No AI for preview'))
    monkeypatch.setattr(ledger, 'execute', lambda *a, **k: pytest.fail('No orders for preview'))
    app = FastAPI()
    app.include_router(build_router())
    with TestClient(app) as client:
        response = client.post('/api/custom/crypto-paper/strategy-preview', json={
            'strategy_id': id, 'strategy_params': rules.parameters(id), 'leverage': 1})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['analysis']['target'] == auto.signal(id, public_source['bars'], 'usdm')
    assert result['evidence']['data_end_ms'] < NOW
    assert '胜率' in result['method']
    assert 'return_pct' not in result['analysis']
    assert not list(tmp_path.iterdir())
    model = draft.parse_draft(json.dumps({
        'name': 'Research', 'strategy_id': id, 'strategy_params': rules.parameters(id),
        'allocation_pct': 5., 'stop_loss_pct': 1., 'take_profit_pct': 2.,
        'rationale': 'Research rule adaptation from observed public data.',
        'risk_notes': ['No profitability claim.']}), draft.DraftRequest())
    assert model.strategy_id == id


def test_preview_stale_or_forming_candles_fail_without_signal(public_source):
    public_source['bars'][-1]['close_time_ms'] = NOW + HOUR
    with pytest.raises(ValueError, match='未收盘'):
        draft.preview(draft.PreviewRequest())


@pytest.mark.parametrize('id', sorted(pine.IDS))
def test_new_paper_accounts_remain_opt_in_and_one_signal_action_per_closed_bar(public_source, tmp_path, id):
    body = {'name': 'Research', 'market': 'usdm', 'exchange': 'bitget', 'symbol': 'ETHUSDT',
            'strategy_id': id, 'strategy_params': rules.parameters(id), 'interval': '1h',
            'leverage': 1, 'initial_cash': '10000', 'allocation_pct': 10,
            'stop_loss_pct': 90, 'take_profit_pct': 90, 'request_id': id}
    if id == 'squeeze_release':
        public_source['bars'] = squeeze_bars()
    elif id == 'supertrend_pullback':
        public_source['bars'][-1].update(open=200, high=201, low=199, close=200)
    elif id == 'bb_aroon':
        for row in public_source['bars']:
            row.update(open=100, high=101, low=99, close=100)
        public_source['bars'][-3]['close'] = 99
        public_source['bars'][-2]['high'] = 110
        public_source['bars'][-1].update(close=90, low=89)
    account = auto.create(tmp_path, body)[0]
    assert not account['enabled']
    assert auto.run_once(tmp_path, account['id'])['processed'] == 0
    auto.set_enabled(tmp_path, account['id'], True)
    assert auto.run_once(tmp_path, account['id'])['processed'] == 1
    before = auto.detail(tmp_path, account['id'])
    assert len(before['trades']) == 1
    if id == 'supertrend_pullback':
        assert float(before['trades'][0]['price']) <= float(public_source['quote']['bid'])
    else:
        assert float(before['trades'][0]['price']) >= float(public_source['quote']['ask'])
    auto.run_once(tmp_path, account['id'])
    assert auto.detail(tmp_path, account['id'])['trades'] == before['trades']
