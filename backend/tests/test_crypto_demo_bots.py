from __future__ import annotations

import copy
import json
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.crypto_paper import demo_bots as bots
from app.custom.crypto_paper import demo_routes, demo_trading
from app.custom.crypto_paper.demo_client import DemoAPIError

NOW = 1_800_000_120_000
ETH = 'okx_eth_trend_10'
BTC = 'okx_btc_breakout_10'
SOL = 'okx_sol_pullback_10'


@pytest.fixture
def venue(tmp_path, monkeypatch):
    monkeypatch.setattr(bots, '_RUNTIME', {})
    monkeypatch.setattr(bots, '_CONTROL', {})
    monkeypatch.setattr(bots, '_SEND_GATES', {})
    monkeypatch.setattr(demo_trading, '_OPS', {})
    monkeypatch.setattr(demo_trading, '_ARMS', {})
    monkeypatch.setattr(bots.time, 'time', lambda: NOW / 1000)
    state = {'generation': 0, 'fingerprint': 'connection-a', 'connected': True, 'target': 'long',
             'positions': [], 'orders': [], 'posts': [], 'fills': {}, 'price': '100', 'throw': False}
    credentials = {'exchange': 'okx', 'site': 'global', 'account_mode': 'classic'}
    def session(_data_dir):
        if not state['connected']:
            raise DemoAPIError('请先连接官方模拟账户')
        return credentials, state['generation'], state['fingerprint']
    monkeypatch.setattr(bots, '_credentials', session)
    monkeypatch.setattr(bots.execution, 'setup', lambda *a: None)
    monkeypatch.setattr(bots.execution, 'snapshot', lambda *a: {
        'positions': copy.deepcopy(state['positions']), 'orders': state['orders'],
        'balances': [{'currency': 'USDT', 'equity': '1000', 'available': '1000'}]})
    monkeypatch.setattr(bots.execution, 'rules', lambda *a: {
        'step': '.01', 'minimum': '.01', 'maximum': '10000', 'multiplier': '1', 'min_notional': '0', 'tick_size': '.01'})
    monkeypatch.setattr(bots.demo_market, 'quote', lambda *a: {
        'bid': state['price'], 'ask': state['price'], 'mark': state['price'], 'asof_ms': NOW})
    def candles(exchange, symbol, interval, site):
        period = 300_000 if interval == '5m' else 900_000
        closed = NOW // period * period - 1
        return {'bars': [{'open_time_ms': closed - (100-i)*period + 1,
                         'close_time_ms': closed - (99-i)*period, 'open': '100', 'high': '101',
                         'low': '99', 'close': '100', 'volume': '100'} for i in range(100)]}
    monkeypatch.setattr(bots.demo_market, 'candles', candles)
    monkeypatch.setattr(bots.auto, 'signal', lambda *a: state['target'])
    def submit(credentials, symbol, action, quantity, client_id):
        durable = bots._load(tmp_path)['bots']
        record = next(b for b in durable.values() if (b.get('pending') or {}).get('client_id') == client_id)
        assert record['pending']['state'] == 'pending'
        state['posts'].append((symbol, action, quantity, client_id))
        if state['throw']:
            raise DemoAPIError('timeout')
        state['fills'][client_id] = {'instId': bots.execution.instrument(symbol), 'clOrdId': client_id,
            'state': 'filled', 'accFillSz': quantity, 'avgPx': state['price'], 'fee': '-.004',
            'feeCcy': 'USDT', 'tdMode': 'isolated', 'side': 'buy' if action in ('open_long', 'close_short') else 'sell'}
        state['positions'] = [p for p in state['positions'] if p['symbol'] != bots.execution.instrument(symbol)]
        if action.startswith('open_'):
            state['positions'].append({'symbol': bots.execution.instrument(symbol), 'side': action.removeprefix('open_'),
                'quantity': quantity, 'entry_price': state['price'], 'leverage': '10', 'margin_mode': 'isolated'})
        return 'official-' + client_id
    def lookup(credentials, method, path, *, params=None, body=None):
        assert path == '/api/v5/trade/order'
        if method == 'POST':
            assert body['ordType'] == 'ioc' and body['tdMode'] == 'isolated'
            action = 'close_long' if body['reduceOnly'] else 'open_long'
            assert body['side'] == ('sell' if body['reduceOnly'] else 'buy')
            if not body['reduceOnly']:
                assert Decimal(body['px']) * Decimal(body['sz']) <= 90
            return [{'sCode': '0', 'ordId': submit(credentials, body['instId'].replace('-USDT-SWAP', 'USDT'),
                action, body['sz'], body['clOrdId'])}]
        assert method == 'GET'
        value = state['fills'].get(params['clOrdId'])
        if value is None:
            raise DemoAPIError('尚未查到明确订单')
        return [copy.deepcopy(value)]
    monkeypatch.setattr(bots.execution, '_transport', lookup)
    return state


def start(tmp_path, identifier=None):
    bots.create_presets(tmp_path)
    return bots.set_enabled(tmp_path, bots.EnabledRequest(enabled=True, confirm_demo=True), identifier)


def row(tmp_path, identifier=ETH):
    return next(b for b in bots.status(tmp_path)['bots'] if b['id'] == identifier)


def enable_restart_resume(tmp_path, venue, monkeypatch):
    venue['fingerprint'] = 'a' * 64
    monkeypatch.setattr(bots, '_saved_fingerprint', lambda _: venue['fingerprint'])
    monkeypatch.setattr(bots, '_BOOT_CHECKS', {})
    bots.create_presets(tmp_path)
    return bots.set_enabled(tmp_path, bots.EnabledRequest(
        enabled=True, confirm_demo=True, resume_on_restart=True), ETH)


def test_opted_in_restart_checks_ownership_and_resumes_without_setup(tmp_path, venue, monkeypatch):
    enable_restart_resume(tmp_path, venue, monkeypatch)
    bots._RUNTIME.clear()
    checks = []
    monkeypatch.setattr(bots.demo_service, 'snapshot', lambda *a, **kw: checks.append('account'))
    monkeypatch.setattr(bots, 'preflight', lambda *_, **__: checks.append('preflight') or {'ready': True})
    monkeypatch.setattr(bots.execution, 'setup', lambda *a: pytest.fail('Restart must not change account settings'))
    venue['target'] = 'hold'
    bots.tick(tmp_path)
    assert checks == ['account', 'preflight']
    assert row(tmp_path)['armed']
    assert row(tmp_path)['resume_on_restart']
    assert venue['posts'] == []


def test_pause_revokes_restart_resume(tmp_path, venue, monkeypatch):
    enable_restart_resume(tmp_path, venue, monkeypatch)
    bots.set_enabled(tmp_path, bots.EnabledRequest(enabled=False), ETH)
    bots._RUNTIME.clear()
    monkeypatch.setattr(bots, 'preflight', lambda *_: pytest.fail('Paused account must remain paused'))
    bots.tick(tmp_path)
    assert not row(tmp_path)['armed']
    assert not row(tmp_path)['resume_on_restart']
    assert venue['posts'] == []


@pytest.mark.parametrize('blocked', ['different_key', 'missing_saved_profile', 'pending_order', 'failed_preflight'])
def test_restart_resume_does_not_bypass_connection_or_order_checks(tmp_path, venue, monkeypatch, blocked):
    enable_restart_resume(tmp_path, venue, monkeypatch)
    bots._RUNTIME.clear()
    monkeypatch.setattr(bots.demo_service, 'snapshot', lambda *a, **kw: None)
    monkeypatch.setattr(bots, 'preflight', lambda *_, **__: {'ready': blocked != 'failed_preflight'})
    if blocked == 'different_key':
        venue['fingerprint'] = 'different-connection'
    elif blocked == 'missing_saved_profile':
        monkeypatch.setattr(bots, '_saved_fingerprint', lambda _: None)
    elif blocked == 'pending_order':
        bots._edit(tmp_path, ETH, lambda b: b.update(pending={'client_id': 'old-request'}))
        monkeypatch.setattr(bots, 'reconcile', lambda *_: None)
    bots.tick(tmp_path)
    assert not row(tmp_path)['armed']
    assert venue['posts'] == []


def test_pause_during_restart_preflight_wins(tmp_path, venue, monkeypatch):
    enable_restart_resume(tmp_path, venue, monkeypatch)
    bots._RUNTIME.clear()
    monkeypatch.setattr(bots.demo_service, 'snapshot', lambda *a, **kw: None)
    def check(_, **__):
        bots.set_enabled(tmp_path, bots.EnabledRequest(enabled=False), ETH)
        return {'ready': True}
    monkeypatch.setattr(bots, 'preflight', check)
    bots.tick(tmp_path)
    assert not row(tmp_path)['enabled'] and not row(tmp_path)['armed']
    assert not row(tmp_path)['resume_on_restart']
    assert venue['posts'] == []


def test_restart_permission_is_not_carried_to_a_different_key(tmp_path, venue, monkeypatch):
    enable_restart_resume(tmp_path, venue, monkeypatch)
    venue['fingerprint'] = 'different-connection'
    bots.set_enabled(tmp_path, bots.EnabledRequest(enabled=True, confirm_demo=True), ETH)
    assert row(tmp_path)['armed']
    assert not row(tmp_path)['resume_on_restart']


def test_restart_opt_in_needs_a_saved_connection_before_arming(tmp_path, venue, monkeypatch):
    bots.create_presets(tmp_path)
    monkeypatch.setattr(bots, '_saved_fingerprint', lambda _: None)
    with pytest.raises(DemoAPIError, match='保存到本机'):
        bots.set_enabled(tmp_path, bots.EnabledRequest(
            enabled=True, confirm_demo=True, resume_on_restart=True), ETH)
    assert not row(tmp_path)['armed'] and not row(tmp_path)['enabled']
    assert venue['posts'] == []


def test_cancelled_zero_fill_revokes_restart_permission(tmp_path, venue, monkeypatch):
    enable_restart_resume(tmp_path, venue, monkeypatch)
    bots.tick(tmp_path)
    identifier = venue['posts'][0][-1]
    venue['fills'][identifier].update(state='canceled', accFillSz='0')
    venue['positions'] = []
    bots.reconcile(tmp_path, ETH)
    assert not row(tmp_path)['armed'] and not row(tmp_path)['resume_on_restart']
    bots._RUNTIME.clear()
    monkeypatch.setattr(bots, 'preflight', lambda *_, **__: pytest.fail('Rejected strategy must remain stopped'))
    bots.tick(tmp_path)
    assert not row(tmp_path)['armed'] and len(venue['posts']) == 1


def test_single_opt_in_uses_only_its_ten_usdt_and_instrument_checks(tmp_path, venue, monkeypatch):
    enable_restart_resume(tmp_path, venue, monkeypatch)
    bots._RUNTIME.clear()
    monkeypatch.setattr(bots.demo_service, 'snapshot', lambda *a, **kw: None)
    monkeypatch.setattr(bots.execution, 'snapshot', lambda *_: {
        'positions': [], 'orders': [], 'balances': [{'currency': 'USDT', 'available': '10'}]})
    queries = []
    def read_only(credentials, method, path, *, params=None):
        assert method == 'GET'
        if path == '/api/v5/account/config':
            return [{'acctLv': '2', 'posMode': 'net_mode'}]
        assert path == '/api/v5/account/leverage-info'
        assert params['instId'] == 'ETH-USDT-SWAP'
        queries.append(params['instId'])
        return [{'instId': params['instId'], 'mgnMode': 'isolated', 'lever': '10'}]
    monkeypatch.setattr(bots.execution, '_transport', read_only)
    venue['target'] = 'hold'
    bots.tick(tmp_path)
    assert queries == ['ETH-USDT-SWAP']
    assert row(tmp_path)['armed']
    assert not row(tmp_path, BTC)['armed'] and not row(tmp_path, SOL)['armed']
    assert venue['posts'] == []


def test_connection_change_revokes_persistent_resume_even_with_same_key(tmp_path, venue, monkeypatch):
    enable_restart_resume(tmp_path, venue, monkeypatch)
    with bots.connection_change(tmp_path, 'okx'):
        pass
    assert not row(tmp_path)['armed'] and not row(tmp_path)['resume_on_restart']
    bots._RUNTIME.clear()
    monkeypatch.setattr(bots, 'preflight', lambda *_, **__: pytest.fail('Reconnecting requires a new opt-in'))
    bots.tick(tmp_path)
    assert not row(tmp_path)['armed'] and venue['posts'] == []


def test_read_only_status_and_presets_do_not_create_orders_or_arm(tmp_path, venue):
    assert bots.status(tmp_path)['bots'] == []
    assert not bots._path(tmp_path).exists()
    result = bots.create_presets(tmp_path)
    assert len(result['bots']) == 3
    assert len({b['symbol'] for b in result['bots']}) == 3
    assert all(b['budget_usdt'] == 10 and b['max_notional'] == 90 and b['leverage'] == 10 and not b['armed'] for b in result['bots'])
    original = bots._path(tmp_path).read_bytes()
    bots.tick(tmp_path)
    bots.status(tmp_path)
    assert bots._path(tmp_path).read_bytes() == original
    assert venue['posts'] == []


def test_start_requires_confirmation_connection_and_no_legacy_arm(tmp_path, venue, monkeypatch):
    bots.create_presets(tmp_path)
    with pytest.raises(DemoAPIError, match='明确确认'):
        bots.set_enabled(tmp_path, bots.EnabledRequest(enabled=True))
    venue['connected'] = False
    with pytest.raises(DemoAPIError, match='先连接'):
        start(tmp_path)
    venue['connected'] = True
    monkeypatch.setattr(demo_trading, 'status', lambda *a: {'armed': True})
    with pytest.raises(DemoAPIError, match='原单策略'):
        start(tmp_path)
    assert venue['posts'] == []


def test_manual_official_arm_is_blocked_while_bot_group_runs(tmp_path, venue):
    start(tmp_path)
    with pytest.raises(DemoAPIError, match='多策略'):
        demo_trading.arm(tmp_path, 'okx', demo_trading.ArmRequest(enabled=True, confirm_demo=True))
    assert venue['posts'] == []


def test_three_symbols_can_open_independently_without_exceeding_ninety_usdt(tmp_path, venue):
    start(tmp_path)
    bots.tick(tmp_path)
    assert len(venue['posts']) == 3
    assert len({post[0] for post in venue['posts']}) == 3
    assert all(Decimal(post[2]) * 100 <= 90 for post in venue['posts'])
    bots.tick(tmp_path)  # Reconcile complete official fills, no duplicate POST.
    assert all(b['position'] is not None for b in bots.status(tmp_path)['bots'])
    bots.tick(tmp_path)  # Same closed candle, same positions.
    assert len(venue['posts']) == 3


def test_roundtrip_uses_actual_complete_fills_and_fees_only_once(tmp_path, venue):
    start(tmp_path, ETH)
    bots.tick(tmp_path)
    assert row(tmp_path)['metrics']['closed_trades'] == 0
    bots.reconcile(tmp_path, ETH)
    assert row(tmp_path)['metrics']['fees_usdt'] == '0.004'
    venue['price'] = '102'  # Take profit uses current quote, not a historical candle.
    bots.tick(tmp_path)
    assert venue['posts'][-1][1] == 'close_long'
    bots.reconcile(tmp_path, ETH)
    bots.tick(tmp_path)  # Verify the resulting official flat position.
    metrics = row(tmp_path)['metrics']
    assert metrics['closed_trades'] == 1 and metrics['wins'] == 1 and metrics['win_rate_pct'] == 100
    expected = Decimal(venue['posts'][0][2]) * 2 - Decimal('.008')
    assert Decimal(metrics['realized_pnl_usdt']) == expected
    assert Decimal(metrics['allocation_equity_usdt']) == 10 + expected
    assert Decimal(metrics['fees_usdt']) == Decimal('.008')
    assert metrics['funding_included'] is False
    bots.reconcile(tmp_path, ETH)
    assert row(tmp_path)['metrics'] == metrics


def test_uncertain_post_is_durable_pauses_and_is_never_retried(tmp_path, venue):
    start(tmp_path, ETH)
    venue['throw'] = True
    bots.tick(tmp_path)
    assert len(venue['posts']) == 1
    assert not row(tmp_path)['armed']
    assert row(tmp_path)['orders'][-1]['state'] == 'uncertain'
    assert '不自动重发' in row(tmp_path)['reason']
    for _ in range(2):
        bots.tick(tmp_path)
    assert len(venue['posts']) == 1
    with pytest.raises(DemoAPIError, match='待对账'):
        start(tmp_path, ETH)


def test_exchange_rejection_diagnostic_is_durable_and_post_is_never_retried(tmp_path, venue, monkeypatch):
    original_transport = bots.execution._transport
    posts = []

    def transport(credentials, method, path, **kwargs):
        if method == 'POST':
            posts.append((method, path, kwargs['body']['clOrdId']))
            raise DemoAPIError('模拟委托结果待核对', category='exchange_code', code='51008')
        return original_transport(credentials, method, path, **kwargs)

    monkeypatch.setattr(bots.execution, '_transport', transport)
    start(tmp_path, ETH)
    bots.tick(tmp_path)

    saved = bots._load(tmp_path)['bots'][ETH]
    assert not row(tmp_path)['armed']
    assert saved['pending']['state'] == 'uncertain'
    assert saved['pending']['diagnostic_category'] == 'exchange_code'
    assert saved['pending']['diagnostic_code'] == '51008'
    assert saved['orders'][-1]['state'] == 'uncertain'
    assert saved['orders'][-1]['diagnostic_category'] == 'exchange_code'
    assert saved['orders'][-1]['diagnostic_code'] == '51008'
    assert '[demo-error:exchange_code:51008]' in saved['reason']

    for _ in range(2):
        bots.tick(tmp_path)
    assert len(posts) == 1
    assert bots._load(tmp_path)['bots'][ETH]['pending']['state'] == 'uncertain'


@pytest.mark.parametrize('terminal', ['partially_filled', 'canceled'])
def test_partial_fill_never_counts_as_a_complete_trade_or_clears_pending(tmp_path, venue, terminal):
    start(tmp_path, ETH)
    bots.tick(tmp_path)
    cid = venue['posts'][0][3]
    venue['fills'][cid].update(state=terminal, accFillSz='.01')
    if terminal == 'canceled':
        with pytest.raises(DemoAPIError, match='部分成交'):
            bots.reconcile(tmp_path, ETH)
    else:
        bots.reconcile(tmp_path, ETH)
    assert bots._load(tmp_path)['bots'][ETH]['pending'] is not None
    assert row(tmp_path)['metrics']['closed_trades'] == 0
    bots.tick(tmp_path)
    assert len(venue['posts']) == 1


def test_connection_change_and_restart_never_silently_resume_orders(tmp_path, venue):
    start(tmp_path, ETH)
    venue['generation'] += 1
    bots.tick(tmp_path)
    assert not row(tmp_path)['armed'] and venue['posts'] == []
    start(tmp_path, ETH)
    bots._RUNTIME.clear()  # The actual restart boundary.
    bots.tick(tmp_path)
    assert not row(tmp_path)['armed'] and venue['posts'] == []


def test_existing_manual_position_is_not_taken_over_or_closed(tmp_path, venue):
    venue['positions'] = [{'symbol': 'ETH-USDT-SWAP', 'side': 'long', 'quantity': '1',
                           'entry_price': '100', 'leverage': '1', 'margin_mode': 'isolated'}]
    with pytest.raises(DemoAPIError, match='不接管'):
        start(tmp_path, ETH)
    assert venue['posts'] == []


def test_manual_quantity_change_blocks_close_and_preserves_original_book(tmp_path, venue):
    start(tmp_path, ETH)
    bots.tick(tmp_path)
    bots.reconcile(tmp_path, ETH)
    venue['positions'][0]['quantity'] = '2'
    venue['price'] = '95'
    bots.tick(tmp_path)
    assert len(venue['posts']) == 1
    assert not row(tmp_path)['armed']
    assert row(tmp_path)['position']['quantity'] == venue['posts'][0][2]


def test_pause_during_final_preflight_prevents_post(tmp_path, venue, monkeypatch):
    start(tmp_path, ETH)
    original = bots.execution.setup
    calls = []
    def setup(*args):
        original(*args)
        calls.append(1)
        bots.set_enabled(tmp_path, bots.EnabledRequest(enabled=False), ETH)
    monkeypatch.setattr(bots.execution, 'setup', setup)
    bots.tick(tmp_path)
    assert calls and venue['posts'] == []
    assert not row(tmp_path)['armed']


def test_minimum_trade_size_never_increases_budget_to_force_a_trade(tmp_path, venue, monkeypatch):
    start(tmp_path, ETH)
    monkeypatch.setattr(bots.execution, 'rules', lambda *a: {
        'step': '1', 'minimum': '1', 'maximum': '100', 'multiplier': '1', 'min_notional': '0', 'tick_size': '.01'})
    bots.tick(tmp_path)
    assert venue['posts'] == []
    assert row(tmp_path)['budget_usdt'] == 10 and row(tmp_path)['max_notional'] == 90
    assert '不足当前最小交易单位' in row(tmp_path)['reason']


def test_stale_quote_or_open_candle_never_posts(tmp_path, venue, monkeypatch):
    start(tmp_path, ETH)
    monkeypatch.setattr(bots.demo_market, 'candles', lambda *a: {'bars': [{'close_time_ms': NOW + 1000}]})
    bots.tick(tmp_path)
    assert venue['posts'] == []


def test_daily_loss_gate_blocks_new_entry_but_risk_close_stays_available(tmp_path, venue):
    start(tmp_path, ETH)
    bots._edit(tmp_path, ETH, lambda b: b.update(realized='-1.5', daily={
        'date': bots.datetime.now(bots.HK).date().isoformat(), 'initial': '10'}))
    bots.tick(tmp_path)
    assert venue['posts'] == []
    # A separate managed position can still exit after the drawdown threshold.
    bots._edit(tmp_path, ETH, lambda b: b.update(realized='0', last_bar_ms=None, max_drawdown_pct=0))
    bots.tick(tmp_path)
    bots.reconcile(tmp_path, ETH)
    bots._edit(tmp_path, ETH, lambda b: b.update(max_drawdown_pct=25))
    venue['price'] = '98'
    bots.tick(tmp_path)
    assert venue['posts'][-1][1] == 'close_long'


def test_saved_budget_cannot_be_expanded_through_tampered_config(tmp_path, venue):
    bots.create_presets(tmp_path)
    value = json.loads(bots._path(tmp_path).read_text(encoding='utf-8'))
    value['bots'][ETH]['max_notional'] = 10000
    bots._path(tmp_path).write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(DemoAPIError, match='参数发生变化'):
        bots.status(tmp_path)


def test_same_origin_routes_report_actual_status_and_require_explicit_enable(tmp_path, venue):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(demo_routes.build_router())
    with TestClient(app) as client:
        assert client.get('/demo/bots/okx').json()['bots'] == []
        assert client.post('/demo/bots/okx/presets', headers={'Origin': 'https://foreign.invalid'}).status_code == 403
        assert client.post('/demo/bots/okx/presets').status_code == 200
        assert client.post('/demo/bots/okx/enabled', json={'enabled': True}).status_code == 400
        result = client.post('/demo/bots/okx/enabled', json={'enabled': True, 'confirm_demo': True})
        assert result.status_code == 200 and all(b['armed'] for b in result.json()['bots'])
        assert venue['posts'] == []  # Enabling alone never manufactures a fill.


def test_unknown_pending_position_is_not_displayed_as_zero_equity(tmp_path, venue):
    start(tmp_path, ETH)
    bots.tick(tmp_path)
    current = row(tmp_path)
    assert current['position_verified'] is False
    assert current['metrics']['open_pnl_usdt'] is None
    assert current['metrics']['allocation_equity_usdt'] is None
    assert 'filled_quantity' not in current['orders'][0]  # No null disguised as a confirmed fill.


def test_short_signals_only_flatten_and_never_open_short(tmp_path, venue):
    start(tmp_path)
    venue['target'] = 'short'
    bots.tick(tmp_path)
    assert venue['posts'] == []
    assert all(b['last_signal'] == 'flat' for b in bots.status(tmp_path)['bots'])


def test_completed_pause_fences_the_start_of_new_http_posts(tmp_path, venue, monkeypatch):
    start(tmp_path, ETH)
    entered, release, pause_started, paused = (threading.Event() for _ in range(4))
    trace = []
    original = bots.execution._transport
    def transport(*args, **kwargs):
        if args[1] == 'POST':
            trace.append('post-started')
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(bots.execution, '_transport', transport)
    run = threading.Thread(target=bots.tick, args=(tmp_path,))
    run.start()
    assert entered.wait(5)
    def pause():
        pause_started.set()
        bots.set_enabled(tmp_path, bots.EnabledRequest(enabled=False), ETH)
        trace.append('pause-returned')
        paused.set()
    stop = threading.Thread(target=pause)
    stop.start()
    assert pause_started.wait(5)
    assert not paused.wait(.1)
    release.set()
    run.join(5)
    stop.join(5)
    assert not run.is_alive() and not stop.is_alive()
    assert trace == ['post-started', 'pause-returned']
    assert not row(tmp_path)['armed']
    bots.tick(tmp_path)
    assert len(venue['posts']) == 1


def test_connection_commit_waits_for_inflight_transport_then_disarms(tmp_path, venue, monkeypatch):
    start(tmp_path, ETH)
    entered, release, changed = (threading.Event() for _ in range(3))
    original = bots.execution._transport
    def transport(*args, **kwargs):
        if args[1] == 'POST':
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(bots.execution, '_transport', transport)
    run = threading.Thread(target=bots.tick, args=(tmp_path,))
    run.start()
    assert entered.wait(5)
    def transition():
        with bots.connection_change(tmp_path, 'okx'):
            venue['generation'] += 1
        changed.set()
    change = threading.Thread(target=transition)
    change.start()
    assert not changed.wait(.1)
    release.set()
    run.join(5)
    change.join(5)
    assert changed.is_set() and not row(tmp_path)['armed']
    bots.tick(tmp_path)
    assert len(venue['posts']) == 1


def test_background_runner_invokes_demo_bots_and_stops_cleanly(tmp_path, venue, monkeypatch):
    from app.custom.crypto_paper import research_service

    runner = bots.auto.Runner(tmp_path)
    calls = []
    monkeypatch.setattr(bots.auto, 'run_once', lambda *a, **kw: None)
    monkeypatch.setattr(research_service, 'maybe_daily', lambda *a: None)
    monkeypatch.setattr(demo_trading, 'tick', lambda *a: None)
    def cycle(data_dir):
        calls.append(data_dir)
        runner.stop_event.set()
    monkeypatch.setattr(bots, 'tick', cycle)
    runner._loop()
    assert calls == [tmp_path]


@pytest.mark.parametrize('actual', [1, 10, 20])
def test_ten_times_setup_is_read_only_and_requires_exact_effective_leverage(monkeypatch, actual):
    calls = []
    def transport(credentials, method, path, **kwargs):
        calls.append((method, path))
        if path == '/api/v5/account/config':
            return [{'acctLv': '2', 'posMode': 'net_mode'}]
        return [{'instId': 'ETH-USDT-SWAP', 'mgnMode': 'isolated', 'lever': str(actual)}]
    monkeypatch.setattr(bots.execution, '_transport', transport)
    credentials = {'exchange': 'okx', 'site': 'global'}
    if actual == 10:
        bots.execution.setup(credentials, 'ETHUSDT', 10)
    else:
        with pytest.raises(DemoAPIError, match='逐仓10倍'):
            bots.execution.setup(credentials, 'ETHUSDT', 10)
    assert calls and all(method == 'GET' for method, _ in calls)


def test_verified_display_is_bound_to_current_connection_and_process(tmp_path, venue):
    start(tmp_path, ETH)
    assert row(tmp_path)['position_verified']
    venue['generation'] += 1
    venue['fingerprint'] = 'connection-b'
    current = row(tmp_path)
    assert not current['position_verified']
    assert current['metrics']['allocation_equity_usdt'] is None
    assert current['metrics']['open_pnl_usdt'] is None
    start(tmp_path, ETH)
    bots._RUNTIME.clear()
    assert not row(tmp_path)['position_verified']


def test_malformed_post_row_is_uncertain_disarmed_and_not_retried(tmp_path, venue, monkeypatch):
    start(tmp_path, ETH)
    original = bots.execution._transport
    calls = []
    def transport(*args, **kwargs):
        if args[1] == 'POST':
            calls.append(1)
            return [None]
        return original(*args, **kwargs)
    monkeypatch.setattr(bots.execution, '_transport', transport)
    bots.tick(tmp_path)
    assert not row(tmp_path)['armed']
    assert row(tmp_path)['orders'][-1]['state'] == 'uncertain'
    bots.tick(tmp_path)
    assert calls == [1]
