from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.crypto_paper import demo_bots as bots
from app.custom.crypto_paper import demo_routes
from app.custom.crypto_paper.demo_client import DemoAPIError


@pytest.fixture
def venue(tmp_path, monkeypatch):
    monkeypatch.setattr(bots, '_RUNTIME', {})
    monkeypatch.setattr(bots, '_CONTROL', {})
    monkeypatch.setattr(bots.demo_trading, '_OPS', {})
    monkeypatch.setattr(bots, '_credentials', lambda _: ({'exchange': 'okx'}, 1, 'connection'))
    monkeypatch.setattr(bots.demo_trading, 'status', lambda *_: {'armed': False})
    state = {'mode': 'net_mode', 'leverage': '10', 'orders': []}
    monkeypatch.setattr(bots.execution, 'snapshot', lambda _: {'positions': [], 'orders': state['orders'],
        'balances': [{'currency': 'USDT', 'available': '1000'}]})
    calls = []
    def get(credentials, method, path, **kwargs):
        assert method == 'GET'
        calls.append(path)
        if path == '/api/v5/account/config':
            return [{'acctLv': '2', 'posMode': state['mode'], 'private': 'not-for-response'}]
        assert path == '/api/v5/account/leverage-info'
        return [{'instId': kwargs['params']['instId'], 'mgnMode': 'isolated', 'lever': state['leverage']}]
    monkeypatch.setattr(bots.execution, '_transport', get)
    bots.create_presets(tmp_path)
    return state, calls


def test_preflight_does_not_arm_write_or_submit(tmp_path, venue):
    before = bots._path(tmp_path).read_bytes()
    report = bots.preflight(tmp_path)
    assert report['ready'] is True
    assert len(report['bots']) == 3
    assert len(venue[1]) == 4
    assert not bots.any_armed(tmp_path)
    assert bots._path(tmp_path).read_bytes() == before
    assert 'not-for-response' not in str(report)


def test_reports_hedge_mode_and_three_times_separately(tmp_path, venue):
    venue[0].update(mode='long_short_mode', leverage='3')
    report = bots.preflight(tmp_path)
    assert report['ready'] is False
    check = next(c for c in report['checks'] if c['label'] == '持仓模式')
    assert check == {'label': '持仓模式', 'passed': False, 'actual': '双向持仓', 'required': '单向持仓'}
    assert all(b['checks'][0] == {'label': '逐仓杠杆', 'passed': False, 'actual': '3 倍', 'required': '10 倍'} for b in report['bots'])


@pytest.mark.parametrize('leverage', ['NaN', 'Infinity', '-1', None, 'untrusted-payload', '1e-1000', '1e-999999999', '9' * 100])
def test_invalid_leverage_stays_unknown_and_unready(tmp_path, venue, leverage):
    venue[0]['leverage'] = leverage
    report = bots.preflight(tmp_path)
    assert report['ready'] is False
    assert all(b['checks'][0]['actual'] == '不可核对' for b in report['bots'])
    assert 'untrusted-payload' not in str(report)


def test_active_order_blocks_only_affected_instrument(tmp_path, venue):
    venue[0]['orders'] = [{'symbol': 'ETH-USDT-SWAP'}]
    report = bots.preflight(tmp_path)
    assert report['ready'] is False
    checks = {b['symbol']: b['checks'][1]['passed'] for b in report['bots']}
    assert checks == {'BTCUSDT': True, 'ETHUSDT': False, 'SOLUSDT': True}


def test_preflight_get_route_is_no_store_and_same_origin(tmp_path, venue):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(demo_routes.build_router(), prefix='/api/custom/crypto-paper')
    client = TestClient(app)
    result = client.get('/api/custom/crypto-paper/demo/bots/okx/preflight')
    assert result.status_code == 200 and result.json()['ready'] is True
    assert result.headers['cache-control'] == 'no-store'
    assert client.get('/api/custom/crypto-paper/demo/bots/okx/preflight', headers={'origin': 'https://other.invalid'}).status_code == 403


def test_changed_connection_rejects_old_report(tmp_path, venue, monkeypatch):
    sessions = iter([({'exchange': 'okx'}, 1, 'first'), ({'exchange': 'okx'}, 2, 'second')])
    monkeypatch.setattr(bots, '_credentials', lambda _: next(sessions))
    with pytest.raises(DemoAPIError, match='连接已变化'):
        bots.preflight(tmp_path)
