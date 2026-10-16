from __future__ import annotations

import copy
import json
import threading
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.crypto_paper import auto, demo_service
from app.custom.crypto_paper import demo_execution as adapter
from app.custom.crypto_paper import demo_trading as trading
from app.custom.crypto_paper.demo_client import DemoAPIError
from app.custom.crypto_paper.demo_routes import build_router

NOW = 1_800_000_120_000
CLIENT_ID = 'dm' + 'a' * 30


@pytest.fixture
def venue(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, '_ARMS', {})
    monkeypatch.setattr(trading, '_PLANS', {})
    monkeypatch.setattr(trading, '_OPS', {})
    monkeypatch.setattr(trading, '_CONTROL', {})
    monkeypatch.setattr(trading.time, 'time', lambda: NOW / 1000)
    monkeypatch.setattr(adapter.httpx, 'Client', lambda *a, **k: pytest.fail('No external HTTP in execution tests'))
    sessions = {}
    credentials = {'exchange': 'bitget', 'account_mode': 'classic', 'site': 'global',
                   'api_key': 'fake-demo-key', 'secret_key': 'fake-secret', 'passphrase': 'fake-passphrase'}
    for exchange in ('okx', 'bitget'):
        sessions[demo_service._key(tmp_path, exchange)] = {'credentials': {**credentials, 'exchange': exchange}}
    monkeypatch.setattr(demo_service, '_SESSIONS', sessions)
    monkeypatch.setattr(demo_service, '_DISCONNECTED', set())
    monkeypatch.setattr(demo_service, '_GENERATIONS', {})
    state = {'positions': [], 'orders': [], 'equity': '1000', 'calls': [], 'query_state': 'filled', 'throw': False}
    monkeypatch.setattr(adapter, 'setup', lambda *a: None)
    monkeypatch.setattr(adapter, 'snapshot', lambda *a: {'positions': copy.deepcopy(state['positions']),
        'orders': copy.deepcopy(state['orders']), 'balances': [{'currency': 'USDT', 'equity': state['equity']}]})
    monkeypatch.setattr(adapter, 'rules', lambda c, s: {'step': '.001', 'minimum': '.001',
        'maximum': '100', 'multiplier': '1' if c['exchange'] == 'bitget' else '.1', 'min_notional': '5',
        'unit': 'ETH' if c['exchange'] == 'bitget' else '张'})
    monkeypatch.setattr(trading.demo_market, 'quote', lambda *a: {'bid': '1999', 'ask': '2000', 'mark': '2000', 'asof_ms': NOW})
    def send(c, symbol, action, quantity, client_id):
        state['calls'].append((c['exchange'], symbol, action, quantity, client_id))
        assert trading._journal(tmp_path, c['exchange'])['orders'][-1]['state'] == 'pending'
        if state['throw']:
            raise DemoAPIError('timeout')
        state['positions'] = ([{'symbol': symbol if c['exchange'] == 'bitget' else adapter.instrument(symbol),
                               'side': action.removeprefix('open_'), 'quantity': quantity, 'entry_price': '2000'}]
                              if action.startswith('open_') else [])
        return 'official-order-1'
    monkeypatch.setattr(adapter, 'submit', send)
    monkeypatch.setattr(adapter, 'reconcile', lambda *a: state['query_state'])
    return state


def arm(tmp_path, source=None, exchange='bitget'):
    return trading.arm(tmp_path, exchange, trading.ArmRequest(enabled=True, confirm_demo=True,
        source_account_id=source, symbol='ETHUSDT', max_notional=10, daily_loss_limit=5))


def test_unarmed_state_never_posts_or_runs_auto(tmp_path, venue):
    assert trading.status(tmp_path, 'bitget')['armed'] is False
    trading.tick(tmp_path)
    assert venue['calls'] == []
    with pytest.raises(DemoAPIError, match='未启用'):
        trading.plan(tmp_path, 'bitget', trading.PlanRequest(action='open_long'))
    assert not trading._path(tmp_path, 'bitget').exists()


def test_arm_requires_explicit_confirmation_and_session_credentials(tmp_path, venue, monkeypatch):
    with pytest.raises(DemoAPIError, match='明确确认'):
        trading.arm(tmp_path, 'bitget', trading.ArmRequest(enabled=True))
    monkeypatch.setattr(demo_service, '_SESSIONS', {})
    with pytest.raises(DemoAPIError, match='先在本次运行中连接'):
        arm(tmp_path)


def test_manual_preview_submit_and_no_resubmit_with_same_plan(tmp_path, venue):
    assert arm(tmp_path)['armed']
    plan = trading.plan(tmp_path, 'bitget', trading.PlanRequest(action='open_long'))
    assert plan['quantity'] == '0.005' and plan['quantity_unit'] == 'ETH'
    assert not venue['calls']
    with pytest.raises(DemoAPIError, match='明确确认'):
        trading.submit(tmp_path, 'bitget', trading.SubmitRequest(plan_id=plan['id'], confirm=False))
    result = trading.submit(tmp_path, 'bitget', trading.SubmitRequest(plan_id=plan['id'], confirm=True))
    assert result['order']['state'] == 'submitted'
    assert len(venue['calls']) == 1
    with pytest.raises(DemoAPIError, match='已经提交'):
        trading.submit(tmp_path, 'bitget', trading.SubmitRequest(plan_id=plan['id'], confirm=True))
    assert len(venue['calls']) == 1
    text = trading._path(tmp_path, 'bitget').read_text(encoding='utf-8')
    assert 'fake-demo-key' not in text and 'fake-secret' not in text and 'fake-passphrase' not in text


def test_okx_contract_multiplier_is_not_treated_as_base_quantity(tmp_path, venue):
    arm(tmp_path, exchange='okx')
    value = trading.plan(tmp_path, 'okx', trading.PlanRequest(action='open_long'))
    assert value['quantity'] == '0.05'  # .05 contracts * .1 ETH * 2000 = 10 USDT.
    assert value['quantity_unit'] == '张'


def test_expired_plan_or_changed_connection_cannot_post(tmp_path, venue, monkeypatch):
    arm(tmp_path)
    value = trading.plan(tmp_path, 'bitget', trading.PlanRequest(action='open_short'))
    monkeypatch.setattr(trading.time, 'time', lambda: (NOW + 45000) / 1000)
    with pytest.raises(DemoAPIError, match='过期'):
        trading.submit(tmp_path, 'bitget', trading.SubmitRequest(plan_id=value['id'], confirm=True))
    monkeypatch.setattr(trading.time, 'time', lambda: NOW / 1000)
    demo_service._GENERATIONS[demo_service._key(tmp_path, 'bitget')] = 1
    with pytest.raises(DemoAPIError, match='连接已变化'):
        trading.submit(tmp_path, 'bitget', trading.SubmitRequest(plan_id=value['id'], confirm=True))
    assert not venue['calls']


def test_uncertain_post_is_durable_pauses_and_only_reconciles(tmp_path, venue):
    arm(tmp_path)
    plan = trading.plan(tmp_path, 'bitget', trading.PlanRequest(action='open_long'))
    venue['throw'] = True
    result = trading.submit(tmp_path, 'bitget', trading.SubmitRequest(plan_id=plan['id'], confirm=True))
    assert result['order']['state'] == 'uncertain'
    assert not trading.status(tmp_path, 'bitget')['armed']
    with pytest.raises(DemoAPIError, match='尚有未对账'):
        arm(tmp_path)
    venue['query_state'] = 'new'
    assert trading.reconcile(tmp_path, 'bitget', result['order']['client_id'])['order']['state'] == 'uncertain'
    venue['query_state'] = 'filled'
    assert trading.reconcile(tmp_path, 'bitget', result['order']['client_id'])['order']['state'] == 'confirmed'
    assert len(venue['calls']) == 1


def test_day_loss_blocks_new_entries_but_allows_explicit_reduce_only_plan(tmp_path, venue):
    arm(tmp_path)
    venue['equity'] = '994'
    with pytest.raises(DemoAPIError, match='日亏限额'):
        trading.plan(tmp_path, 'bitget', trading.PlanRequest(action='open_long'))
    venue['positions'] = [{'symbol': 'ETHUSDT', 'side': 'long', 'quantity': '.004', 'entry_price': '2000'}]
    plan = trading.plan(tmp_path, 'bitget', trading.PlanRequest(action='close_long'))
    assert plan['reduce_only'] and plan['quantity'] == '0.004'


def test_opening_notional_cap_does_not_block_risk_reducing_full_close(tmp_path, venue):
    arm(tmp_path)
    venue['positions'] = [{'symbol': 'ETHUSDT', 'side': 'short', 'quantity': '.006', 'entry_price': '1600'}]
    value = trading.plan(tmp_path, 'bitget', trading.PlanRequest(action='close_short'))
    assert Decimal(value['estimated_notional']) > 10
    result = trading.submit(tmp_path, 'bitget', trading.SubmitRequest(plan_id=value['id'], confirm=True))
    assert result['order']['state'] == 'submitted'
    assert venue['calls'][-1][2] == 'close_short'


def test_pause_works_during_inflight_preflight_and_late_arm_cannot_reenable(tmp_path, venue, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    errors = []
    def setup(*a):
        entered.set()
        release.wait(5)
    monkeypatch.setattr(adapter, 'setup', setup)
    def enable():
        try:
            arm(tmp_path)
        except DemoAPIError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=enable)
    thread.start()
    assert entered.wait(2)
    result = trading.arm(tmp_path, 'bitget', trading.ArmRequest(enabled=False))
    assert not result['armed']
    release.set()
    thread.join(5)
    assert errors and not trading.status(tmp_path, 'bitget')['armed']
    assert not venue['calls']


def test_cross_origin_orders_rejected_and_default_execution_remains_disarmed(tmp_path, venue):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(build_router(), prefix='/api/custom/crypto-paper')
    with TestClient(app) as client:
        prefix = '/api/custom/crypto-paper/demo/trading/bitget'
        assert client.get(prefix).json()['armed'] is False
        result = client.post(prefix + '/arm', json={'enabled': True, 'confirm_demo': True},
                             headers={'Origin': 'https://elsewhere.invalid'})
        assert result.status_code == 403
        assert client.post(prefix + '/plan', json={'action': 'open_long'}).status_code == 400
        assert client.post(prefix + '/arm', json={'enabled': True, 'max_notional': 101}).status_code == 422
    assert not venue['calls']


def test_auto_rule_runs_only_after_arming_and_does_not_change_local_account(tmp_path, venue, monkeypatch):
    created = auto.create(tmp_path, dict(name='source', market='usdm', exchange='bitget', symbol='ETHUSDT',
        strategy_id='supertrend_pullback', interval='1h', leverage=1, initial_cash=10000, allocation_pct=10,
        stop_loss_pct=2, take_profit_pct=4, request_id='source'))[0]
    before = auto._path(tmp_path).read_bytes()
    end = NOW // 3_600_000 * 3_600_000 - 1
    bars = [{'close_time_ms': end, 'open': '2000', 'high': '2001', 'low': '1999', 'close': '2000'}] * 200
    monkeypatch.setattr(trading.demo_market, 'candles', lambda *a: {'bars': bars})
    monkeypatch.setattr(auto, 'signal', lambda *a: 'long')
    arm(tmp_path, source=created['id'])
    trading.tick(tmp_path)
    assert len(venue['calls']) == 1
    trading.tick(tmp_path)  # Reconcile fill; same position, no duplicate entry.
    assert len(venue['calls']) == 1
    assert auto._path(tmp_path).read_bytes() == before
    assert not auto.accounts(tmp_path)[0]['enabled']
    state = auto._load(tmp_path)
    state['accounts'][created['id']]['stop_loss_pct'] = 3
    auto._save(tmp_path, state)
    trading.tick(tmp_path)
    assert not trading.status(tmp_path, 'bitget')['armed']
    assert len(venue['calls']) == 1


@pytest.mark.parametrize('exchange,mode,header,path,qtyfield', [
    ('okx', 'classic', 'x-simulated-trading', '/api/v5/trade/order', 'sz'),
    ('bitget', 'classic', 'paptrading', '/api/v2/mix/order/place-order', 'size'),
    ('bitget', 'uta', 'paptrading', '/api/v3/trade/place-order', 'qty'),
])
def test_transport_always_signs_demo_header_and_exact_body(exchange, mode, header, path, qtyfield, monkeypatch):
    observed = []
    real_client = httpx.Client
    def handle(request):
        observed.append(request)
        assert request.url.path == path and request.method == 'POST'
        assert request.headers[header] == '1'
        body = json.loads(request.content)
        assert body[qtyfield] == '.01'
        assert body['reduceOnly'] in (True, 'YES', 'yes')
        assert body.get('tdMode', body.get('marginMode')) == 'isolated'
        data = [{'sCode': '0', 'ordId': 'test-order'}] if exchange == 'okx' else {'orderId': 'test-order'}
        return httpx.Response(200, json={'code': '0' if exchange == 'okx' else '00000', 'data': data})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(**kw, transport=httpx.MockTransport(handle)))
    credentials = dict(exchange=exchange, account_mode=mode, site='global', api_key='fake', secret_key='fake', passphrase='fake')
    assert adapter.submit(credentials, 'ETHUSDT', 'close_long', '.01', CLIENT_ID) == 'test-order'
    assert len(observed) == 1


def test_transport_refuses_arbitrary_endpoints_before_http(monkeypatch):
    monkeypatch.setattr(httpx, 'Client', lambda **kw: pytest.fail('No HTTP for invalid path'))
    credentials = dict(exchange='bitget', account_mode='classic', site='global')
    with pytest.raises(DemoAPIError, match='白名单'):
        adapter._transport(credentials, 'POST', '/api/v2/spot/wallet/withdrawal', body={})


@pytest.mark.parametrize('mode', ['classic', 'uta', 'okx'])
def test_settings_fail_closed_when_not_explicitly_one_way_isolated_one_x(mode, monkeypatch):
    credentials = dict(exchange='okx' if mode == 'okx' else 'bitget', account_mode=mode)
    if mode == 'classic':
        value = {'posMode': 'one_way_mode', 'marginMode': 'isolated', 'isolatedLongLever': '1', 'isolatedShortLever': '2'}
    elif mode == 'uta':
        value = {'holdMode': 'one_way_mode', 'symbolConfigList': []}
    else:
        value = [{'acctLv': '2', 'posMode': 'long_short_mode'}]
    monkeypatch.setattr(adapter, '_transport', lambda *a, **k: value)
    with pytest.raises(DemoAPIError):
        adapter.setup(credentials, 'ETHUSDT')


def test_disconnecting_session_immediately_reports_execution_paused(tmp_path, venue):
    arm(tmp_path)
    demo_service._DISCONNECTED.add(demo_service._key(tmp_path, 'bitget'))
    assert trading.status(tmp_path, 'bitget')['armed'] is False
    assert not venue['calls']


def test_uta_external_position_is_not_guessed_into_a_base_coin_close(tmp_path, venue):
    demo_service._SESSIONS[demo_service._key(tmp_path, 'bitget')]['credentials']['account_mode'] = 'uta'
    arm(tmp_path)
    venue['positions'] = [{'symbol': 'ETHUSDT', 'side': 'long', 'quantity': '.004', 'entry_price': '2000'}]
    with pytest.raises(DemoAPIError, match='原始持仓数量'):
        trading.plan(tmp_path, 'bitget', trading.PlanRequest(action='close_long'))
    assert not venue['calls']
