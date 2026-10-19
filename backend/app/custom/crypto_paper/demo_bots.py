"""Small, isolated strategy budgets executed only through the OKX demo transport.

Each preset owns one distinct instrument. Budgets are attribution ledgers, not
exchange subaccounts. Durable intents are never retried after uncertain POSTs.
Restart recovery requires a separately opted-in, credential-bound policy and
a fresh read-only ownership preflight. Existing accounts remain session-only.
"""
# ruff: noqa: RUF001 -- localized simulation controls.
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.services.fs_utils import atomic_write_text

from . import auto, demo_market, demo_service, demo_store, demo_trading
from . import demo_execution as execution
from .demo_client import DemoAPIError


class _SendFence:
    def __init__(self):
        self.condition = threading.Condition()
        self.inflight = 0


_STORE_LOCK = threading.RLock()
_TICK_LOCK = threading.Lock()
_RUNTIME_LOCK = threading.RLock()
_RUNTIME: dict[tuple[str, str], dict] = {}
_CONTROL: dict[str, int] = {}
_SEND_GATES: dict[str, _SendFence] = {}
_BOOT_CHECKS: dict[str, dict] = {}
HK = timezone(timedelta(hours=8))
D = Decimal
PRESETS = (
    {'id': 'okx_btc_breakout_10', 'name': 'BTC 区间突破 · 10U', 'symbol': 'BTCUSDT',
     'strategy_id': 'channel_breakout', 'strategy_params': {'lookback': 12}, 'interval': '15m',
     'description': '已收盘15分钟K突破此前12根高点后做多；向下突破只用于退出。'},
    {'id': 'okx_eth_trend_10', 'name': 'ETH 均线趋势 · 10U', 'symbol': 'ETHUSDT',
     'strategy_id': 'ema_trend', 'strategy_params': {'fast_period': 8, 'slow_period': 24}, 'interval': '5m',
     'description': '5分钟EMA8高于EMA24并收盘确认后做多；反向或止盈止损退出。'},
    {'id': 'okx_sol_pullback_10', 'name': 'SOL 强势回撤 · 10U', 'symbol': 'SOLUSDT',
     'strategy_id': 'bb_aroon', 'strategy_params': {'bb_period': 20, 'aroon_period': 30}, 'interval': '5m',
     'description': '5分钟布林下轨回撤结合Aroon强势过滤，只做多；均值回归或趋势转弱退出。'},
)


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)
    confirm_demo: bool = Field(default=False, strict=True)
    resume_on_restart: bool | None = Field(default=None, strict=True)


class OwnershipError(DemoAPIError):
    """An invariant requiring operator review rather than an automatic retry."""


def _path(data_dir: Path) -> Path:
    return data_dir / 'crypto_demo_bots' / 'okx.json'


def _load(data_dir):
    path = _path(data_dir)
    if not path.exists():
        return {'schema': 1, 'exchange': 'okx', 'bots': {}}
    if path.stat().st_size > 2_000_000:
        raise DemoAPIError('策略记录达到大小上限，保留原文件并暂停新增订单')
    state = json.loads(path.read_text(encoding='utf-8'))
    known = {p['id']: p for p in PRESETS}
    if (state.get('schema') != 1 or state.get('exchange') != 'okx' or not isinstance(state.get('bots'), dict)
            or not set(state['bots']) <= set(known)):
        raise DemoAPIError('官方模拟策略记录格式异常，原文件保留')
    for identifier, bot in state['bots'].items():
        preset = known[identifier]
        if (any(bot.get(k) != v for k, v in preset.items()) or bot.get('budget_usdt') != 10
                or bot.get('max_notional') != 90 or bot.get('leverage') != 10):
            raise DemoAPIError('官方模拟预设参数发生变化，请先核对原记录')
        policy = bot.get('resume_on_restart', False)
        if not isinstance(policy, bool) or (policy and (
                not isinstance(bot.get('resume_fingerprint'), str)
                or len(bot['resume_fingerprint']) != 64
                or any(char not in '0123456789abcdef' for char in bot['resume_fingerprint']))):
            raise DemoAPIError('模拟重启恢复配置无效，请核对后重新启用')
    return state


def _save(data_dir, state):
    raw = json.dumps(state, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > 2_000_000:
        raise DemoAPIError('策略记录达到大小上限，未覆盖原文件')
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, raw)


def _edit(data_dir, identifier, change):
    with _STORE_LOCK:
        state = _load(data_dir)
        bot = state['bots'].get(identifier)
        if bot is None:
            raise DemoAPIError('官方模拟策略不存在')
        change(bot)
        _save(data_dir, state)
        return copy.deepcopy(bot)


def _runtime_key(data_dir, identifier):
    return str(data_dir.resolve()), identifier


def _credentials(data_dir):
    return demo_trading._credentials(data_dir, 'okx')


def _saved_fingerprint(data_dir):
    try:
        credentials = demo_store.read(data_dir, 'okx')
    except (demo_store.DemoStoreError, OSError, ValueError) as exc:
        raise DemoAPIError('本机保存的模拟连接不可读取，请重新连接并核对') from exc
    return hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest() if credentials else None


def _send_gate(data_dir):
    with _RUNTIME_LOCK:
        return _SEND_GATES.setdefault(str(data_dir.resolve()), _SendFence())


@contextmanager
def _sending(data_dir, identifier):
    fence = _send_gate(data_dir)
    with fence.condition:
        _active(data_dir, identifier)
        fence.inflight += 1
    # HTTP holds no condition/store mutex; pause waits on the reservation.
    try:
        yield
    finally:
        with fence.condition:
            fence.inflight -= 1
            fence.condition.notify_all()


@contextmanager
def connection_change(data_dir, exchange):
    """Fence credential commits against the final check/POST boundary.

    Waiting releases the condition mutex. Only the local connection commit is
    fenced; snapshot HTTP stays outside this context.
    """
    if exchange != 'okx':
        yield
        return
    root = str(data_dir.resolve())
    fence = _send_gate(data_dir)
    with fence.condition:
        while fence.inflight:
            fence.condition.wait()
        with _RUNTIME_LOCK:
            _CONTROL[root] = _CONTROL.get(root, 0) + 1
            for key, runtime in _RUNTIME.items():
                if key[0] == root:
                    runtime['armed'] = False
                    runtime.pop('verified', None)
        with _STORE_LOCK:
            state = _load(data_dir)
            opted_in = [bot for bot in state['bots'].values() if bot.get('resume_on_restart', False)]
            for bot in opted_in:
                bot['resume_on_restart'] = False
            if opted_in:
                _save(data_dir, state)
        yield


def any_armed(data_dir):
    root = str(data_dir.resolve())
    with _RUNTIME_LOCK:
        return any(key[0] == root and runtime.get('armed') for key, runtime in _RUNTIME.items())


def create_presets(data_dir):
    with _STORE_LOCK:
        state = _load(data_dir)
        for preset in PRESETS:
            state['bots'].setdefault(preset['id'], {
                **copy.deepcopy(preset), 'budget_usdt': 10, 'max_notional': 90, 'leverage': 10, 'enabled': False,
                'status': 'paused', 'reason': '已准备；连接欧易官方模拟后可启用',
                'last_check_ms': None, 'last_bar_ms': None, 'last_signal': None,
                'position': None, 'position_verified': False, 'last_verified_ms': None,
                'pending': None, 'orders': [], 'trades': [], 'realized': '0', 'fees': '0',
                'peak': '10', 'max_drawdown_pct': 0, 'valuation': None, 'daily': {},
            })
        _save(data_dir, state)
    return status(data_dir)


def _active(data_dir, identifier):
    credentials, generation, fingerprint = _credentials(data_dir)
    with _RUNTIME_LOCK:
        runtime = _RUNTIME.get(_runtime_key(data_dir, identifier), {})
        if not runtime.get('armed') or (runtime.get('generation'), runtime.get('fingerprint')) != (generation, fingerprint):
            raise OwnershipError('连接已变化或执行已暂停，请重新连接并启用')
        return credentials, fingerprint


def _owned_position(bot, snapshot):
    selected = [p for p in snapshot['positions'] if p['symbol'] == execution.instrument(bot['symbol'])]
    if len(selected) > 1:
        raise OwnershipError('同标的存在多个官方仓位，请在官方核对')
    actual = selected[0] if selected else None
    saved = bot['position']
    if bool(actual) != bool(saved):
        raise OwnershipError('官方持仓与本策略记录不一致，不接管手动仓位')
    if saved and (actual['side'] != saved['side'] or D(actual['quantity']) != D(saved['quantity'])
                  or D(actual.get('leverage') or '0') != bot['leverage'] or actual.get('margin_mode') != 'isolated'
                  or abs(execution._positive(actual.get('entry_price')) / D(saved['entry_price']) - 1) > D('.000001')):
        raise OwnershipError('官方仓位数量、方向或保证金配置已变化，请人工核对')
    return actual


def _available(snapshot):
    matches = [row for row in snapshot['balances'] if row.get('currency') == 'USDT']
    if len(matches) != 1:
        raise DemoAPIError('官方模拟账户缺少可核对的USDT可用余额')
    try:
        amount = D(matches[0]['available'])
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return amount
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise DemoAPIError('官方模拟USDT可用余额无效') from exc


def preflight(data_dir, *, identifiers=None):
    """Read current demo settings without arming, writing a ledger or sending orders."""
    with demo_trading._op(demo_trading._key(data_dir, 'okx')):
        credentials, generation, fingerprint = _credentials(data_dir)
        with _STORE_LOCK:
            bots = list(copy.deepcopy(_load(data_dir)['bots']).values())
        if identifiers is not None:
            selected_ids = set(identifiers)
            if not selected_ids or not selected_ids <= {b['id'] for b in bots}:
                raise DemoAPIError('待检查的官方模拟策略不存在')
            bots = [b for b in bots if b['id'] in selected_ids]
        config = execution._transport(credentials, 'GET', '/api/v5/account/config')
        if not isinstance(config, list) or len(config) != 1 or not isinstance(config[0], dict):
            raise DemoAPIError('官方模拟账户配置不可核对，请稍后重试')
        account = config[0]
        account_label = {'1': '现货模式', '2': '合约模式', '3': '跨币种保证金模式', '4': '组合保证金模式'}.get(account.get('acctLv'), '未知模式')
        position_label = {'net_mode': '单向持仓', 'long_short_mode': '双向持仓'}.get(account.get('posMode'), '未知模式')
        snapshot = execution.snapshot(credentials)
        enough = _available(snapshot) >= sum(D(bot['budget_usdt']) for bot in bots)
        legacy_armed = demo_trading.status(data_dir, 'okx')['armed']
        common = [
            {'label': '账户模式', 'passed': account.get('acctLv') == '2', 'actual': account_label, 'required': '合约模式'},
            {'label': '持仓模式', 'passed': account.get('posMode') == 'net_mode', 'actual': position_label, 'required': '单向持仓'},
            {'label': '原单策略', 'passed': not legacy_armed, 'actual': '请先暂停' if legacy_armed else '未运行', 'required': '未运行'},
            {'label': '模拟额度', 'passed': enough, 'actual': '充足' if enough else '不足或不可核对', 'required': '覆盖全部策略预算'},
        ]
        items = []
        for bot in bots:
            rows = execution._transport(credentials, 'GET', '/api/v5/account/leverage-info', params={
                'instId': execution.instrument(bot['symbol']), 'mgnMode': 'isolated'})
            valid = isinstance(rows, list) and bool(rows) and all(isinstance(row, dict) for row in rows)
            leverages = []
            if valid:
                for row in rows:
                    try:
                        raw_leverage = str(row.get('lever') or '')
                        if len(raw_leverage) > 16:
                            continue
                        value = execution._positive(raw_leverage)
                        if 1 <= value <= 1000:
                            leverages.append(format(value, 'f'))
                    except DemoAPIError:
                        pass
            leverage_ok = valid and all(row.get('instId') == execution.instrument(bot['symbol'])
                and row.get('mgnMode') == 'isolated' and row.get('lever') is not None for row in rows)
            leverage_ok = bool(leverage_ok and len(leverages) == len(rows) and all(D(v) == bot['leverage'] for v in leverages))
            owned = True
            try:
                _owned_position(bot, snapshot)
            except OwnershipError:
                owned = False
            owned = owned and (not bot['position'] or bot['position'].get('fingerprint') == fingerprint)
            clear = owned and not bot['pending'] and not any(row['symbol'] == execution.instrument(bot['symbol']) for row in snapshot['orders'])
            checks = [
                {'label': '逐仓杠杆', 'passed': leverage_ok, 'actual': ' / '.join(dict.fromkeys(leverages)) + ' 倍' if leverages else '不可核对', 'required': f'{bot["leverage"]} 倍'},
                {'label': '委托与仓位', 'passed': clear, 'actual': '可核对' if clear else '有待核对记录', 'required': '无冲突或待对账委托'},
            ]
            items.append({'id': bot['id'], 'name': bot['name'], 'symbol': bot['symbol'], 'checks': checks})
        _, current_generation, current_fingerprint = _credentials(data_dir)
        if (generation, fingerprint) != (current_generation, current_fingerprint):
            raise DemoAPIError('检查期间连接已变化，请重新检查')
        return {'exchange': 'okx', 'environment': 'demo', 'checked_at_ms': int(time.time() * 1000),
            'ready': bool(items) and all(c['passed'] for c in common) and all(c['passed'] for item in items for c in item['checks']),
            'checks': common, 'bots': items}


def set_enabled(data_dir, request: EnabledRequest, identifier=None):
    if request.resume_on_restart and not request.enabled:
        raise DemoAPIError('重启恢复须在明确启用官方模拟时设置')
    root = str(data_dir.resolve())
    with _STORE_LOCK:
        state = _load(data_dir)
        ids = [identifier] if identifier else list(state['bots'])
        if not ids or any(item not in state['bots'] for item in ids):
            raise DemoAPIError('请先准备官方模拟策略组')
    with _RUNTIME_LOCK:
        token = _CONTROL[root] = _CONTROL.get(root, 0) + 1
        if not request.enabled:
            for item in ids:
                _RUNTIME.setdefault(_runtime_key(data_dir, item), {})['armed'] = False
    if not request.enabled:
        # A completed pause fences the start of any subsequent HTTP POST.
        # In-flight transport may finish first; it is never silently retried.
        fence = _send_gate(data_dir)
        with fence.condition:
            while fence.inflight:
                fence.condition.wait()
        for item in ids:
            _edit(data_dir, item, lambda b: b.update(enabled=False, resume_on_restart=False,
                status='paused', reason='已暂停；不会自动平仓，已有持仓和挂单需继续核对'))
        return status(data_dir)
    if not request.confirm_demo:
        raise DemoAPIError('请明确确认仅使用官方模拟资金启用这组策略')
    with demo_trading._op(demo_trading._key(data_dir, 'okx')):
        if demo_trading.status(data_dir, 'okx')['armed']:
            raise DemoAPIError('请先暂停原单策略官方模拟执行，避免共享仓位冲突')
        credentials, generation, fingerprint = _credentials(data_dir)
        policies = {}
        for item in ids:
            bot = state['bots'][item]
            opted_in = request.resume_on_restart if request.resume_on_restart is not None else (
                bot.get('resume_on_restart', False) and bot.get('resume_fingerprint') == fingerprint)
            if opted_in and _saved_fingerprint(data_dir) != fingerprint:
                raise DemoAPIError('重启恢复需要先将当前官方模拟连接保存到本机')
            policies[item] = bool(opted_in)
        snapshot = execution.snapshot(credentials)
        if _available(snapshot) < sum(D(state['bots'][item]['budget_usdt']) for item in ids):
            raise DemoAPIError('官方模拟USDT可用余额不足策略额度总和')
        for item in ids:
            bot = state['bots'][item]
            if bot['pending']:
                raise DemoAPIError('策略存在待对账订单，请先对账；不会重复发单')
            if bot['position'] and bot['position'].get('fingerprint') != fingerprint:
                raise OwnershipError('已有策略仓位不属于当前模拟连接')
            _owned_position(bot, snapshot)
            if any(row['symbol'] == execution.instrument(bot['symbol']) for row in snapshot['orders']):
                raise DemoAPIError('策略标的存在官方活动委托，请先处理')
            execution.setup(credentials, bot['symbol'], bot['leverage'])
        _, current_generation, current_fingerprint = _credentials(data_dir)
        with _RUNTIME_LOCK:
            if token != _CONTROL[root] or (generation, fingerprint) != (current_generation, current_fingerprint):
                raise DemoAPIError('启用期间连接已变化或已暂停，本次未启用')
            for item in ids:
                _RUNTIME[_runtime_key(data_dir, item)] = {'armed': True, 'generation': generation,
                    'fingerprint': fingerprint, 'verified': (generation, fingerprint)}
        for item in ids:
            _edit(data_dir, item, lambda b, item=item: b.update(enabled=True, position_verified=True, last_verified_ms=int(time.time() * 1000),
                resume_on_restart=policies[item], resume_fingerprint=fingerprint if policies[item] else None,
                status='waiting', reason='官方模拟已启用，等待已收盘K线与交易条件'))
    return status(data_dir)


def _resume_after_restart(data_dir):
    root = str(data_dir.resolve())
    with _STORE_LOCK:
        selected = [b for b in _load(data_dir)['bots'].values()
                    if b['enabled'] and b.get('resume_on_restart', False)]
    with _RUNTIME_LOCK:
        selected = [copy.deepcopy(b) for b in selected if _runtime_key(data_dir, b['id']) not in _RUNTIME]
        if not selected or time.monotonic() < _BOOT_CHECKS.get(root, {}).get('next_check', 0):
            return
        token = _CONTROL.get(root, 0)
        _BOOT_CHECKS[root] = {'next_check': time.monotonic() + 60,
                              'reason': '正在核对重启后的官方模拟连接与仓位'}
    try:
        # This may lazily load an already-saved encrypted profile. It respects
        # explicit disconnects and never creates or alters API credentials.
        demo_service.snapshot(data_dir, 'okx', refresh=True)
        _, generation, fingerprint = _credentials(data_dir)
        if _saved_fingerprint(data_dir) != fingerprint or any(
                b.get('resume_fingerprint') != fingerprint for b in selected):
            raise DemoAPIError('模拟连接已变化，重启恢复未启用，请核对后重新授权')
        if any(b['pending'] for b in selected):
            # The existing tick may now query the old client ID with the saved
            # connection; no arming or new request is allowed before it settles.
            raise DemoAPIError('存在待核对委托，重启恢复等待官方对账')
        checked = preflight(data_dir, identifiers=[b['id'] for b in selected])
        if not checked['ready']:
            raise DemoAPIError('官方模拟重启预检尚未通过，请检查运行条件')
        _, current_generation, current_fingerprint = _credentials(data_dir)
        with _STORE_LOCK, _RUNTIME_LOCK:
            current = _load(data_dir)['bots']
            if token != _CONTROL.get(root, 0) or (generation, fingerprint) != (current_generation, current_fingerprint):
                return
            for bot in selected:
                latest = current[bot['id']]
                if (not latest['enabled'] or not latest.get('resume_on_restart') or latest['pending']
                        or latest.get('resume_fingerprint') != fingerprint
                        or _runtime_key(data_dir, bot['id']) in _RUNTIME):
                    return
            for bot in selected:
                _RUNTIME[_runtime_key(data_dir, bot['id'])] = {
                    'armed': True, 'generation': generation, 'fingerprint': fingerprint,
                    'verified': (generation, fingerprint), 'resumed_after_restart': True}
                _edit(data_dir, bot['id'], lambda b: b.update(position_verified=True,
                    last_verified_ms=int(time.time() * 1000), status='waiting',
                    reason='重启后官方模拟对账通过，已按原规则恢复执行'))
        with _RUNTIME_LOCK:
            _BOOT_CHECKS.pop(root, None)
    except DemoAPIError as exc:
        with _RUNTIME_LOCK:
            _BOOT_CHECKS[root]['reason'] = str(exc)


def _update_valuation(bot, quote):
    pnl = D(0)
    if bot['position']:
        p = bot['position']
        sign = 1 if p['side'] == 'long' else -1
        pnl = (D(quote['mark']) - D(p['entry_price'])) * D(p['quantity']) * D(p['multiplier']) * sign
    equity = D(bot['budget_usdt']) + D(bot['realized']) + pnl
    peak = max(D(bot['peak']), equity)
    bot['peak'] = str(peak)
    bot['max_drawdown_pct'] = max(bot['max_drawdown_pct'], float((peak - equity) / peak * 100))
    bot['valuation'] = {'asof_ms': quote['asof_ms'], 'pnl': str(pnl), 'equity': str(equity)}
    day = datetime.now(HK).date().isoformat()
    if bot['daily'].get('date') != day:
        bot['daily'] = {'date': day, 'initial': str(equity)}
    return equity


def _order_details(credentials, bot, pending):
    raw = execution._transport(credentials, 'GET', '/api/v5/trade/order', params={
        'instId': execution.instrument(bot['symbol']), 'clOrdId': pending['client_id']})
    if (not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict)
            or raw[0].get('clOrdId') != pending['client_id']
            or raw[0].get('instId') != execution.instrument(bot['symbol'])):
        raise OwnershipError('官方查单标识不匹配，保留待对账状态')
    row = raw[0]
    expected_side = 'buy' if pending['action'] in ('open_long', 'close_short') else 'sell'
    if row.get('side') != expected_side or row.get('tdMode') != 'isolated':
        raise OwnershipError('官方查单方向或保证金模式不符，保留原记录')
    state = row.get('state')
    if state not in ('live', 'partially_filled', 'filled', 'canceled', 'mmp_canceled'):
        raise OwnershipError('官方订单状态不可识别，保留原记录')
    return row


def reconcile(data_dir, identifier):
    with demo_trading._op(demo_trading._key(data_dir, 'okx')):
        with _STORE_LOCK:
            bot = copy.deepcopy(_load(data_dir)['bots'].get(identifier))
        if bot is None:
            raise DemoAPIError('官方模拟策略不存在')
        if not bot['pending']:
            return status(data_dir)
        credentials, _, fingerprint = _credentials(data_dir)
        pending = bot['pending']
        if pending['fingerprint'] != fingerprint:
            raise OwnershipError('待对账订单不属于当前连接，请恢复原模拟连接')
        raw = _order_details(credentials, bot, pending)
        result = raw['state']
        if result in ('live', 'partially_filled'):
            _edit(data_dir, identifier, lambda b: b.update(status='reconciling', reason='官方委托尚未完整成交，等待对账；不重复发单'))
            return status(data_dir)
        quantity = D(raw.get('accFillSz', '0'))
        if result != 'filled':
            if not quantity.is_finite() or quantity != 0:
                raise OwnershipError('订单部分成交后结束，请在官方核对仓位；本策略保持待对账')
            def rejected(b):
                b['orders'][-1].update(state='rejected', exchange_state=result)
                b.update(pending=None, resume_on_restart=False, status='paused',
                         reason='官方订单未成交而结束，策略已暂停；不会重发')
            _edit(data_dir, identifier, rejected)
            with _RUNTIME_LOCK:
                _RUNTIME.setdefault(_runtime_key(data_dir, identifier), {})['armed'] = False
            return status(data_dir)
        if quantity != D(pending['quantity']):
            raise OwnershipError('官方累计成交数量与计划不一致，请人工核对')
        price = execution._positive(raw.get('avgPx'))
        if pending['action'] == 'open_long' and price > D(pending['limit_price']):
            raise OwnershipError('官方成交价格超过本策略保护限价，请核对原始订单')
        fee = D(raw.get('fee'))
        if not fee.is_finite() or raw.get('feeCcy') != 'USDT':
            raise OwnershipError('成交费用缺少USDT完整记录，暂不计算业绩')
        def apply(b):
            if not b['pending'] or b['pending']['client_id'] != pending['client_id']:
                raise OwnershipError('待对账状态已变化')
            b['fees'] = str(D(b['fees']) - fee)
            b['realized'] = str(D(b['realized']) + fee)
            record = b['orders'][-1]
            if record['client_id'] != pending['client_id']:
                raise OwnershipError('订单记录顺序不一致')
            record.update(state='filled', filled_quantity=str(quantity), avg_price=str(price), fee_usdt=str(-fee))
            if pending['action'].startswith('open_'):
                if b['position']:
                    raise OwnershipError('策略已有仓位，拒绝重复记账')
                b['position'] = {'side': pending['action'].removeprefix('open_'), 'quantity': str(quantity),
                    'entry_price': str(price), 'multiplier': pending['multiplier'], 'entry_fee': str(fee),
                    'notional': str(quantity * D(pending['multiplier']) * price), 'fingerprint': fingerprint,
                    'opened_ms': pending['created_at_ms']}
            else:
                held = b['position']
                if not held or D(held['quantity']) != quantity or held['side'] != pending['action'].removeprefix('close_'):
                    raise OwnershipError('平仓成交与策略持仓不匹配')
                gross = (price - D(held['entry_price'])) * quantity * D(held['multiplier']) * (1 if held['side'] == 'long' else -1)
                b['realized'] = str(D(b['realized']) + gross)
                b['trades'].append({'entry_ms': held['opened_ms'], 'exit_ms': int(time.time() * 1000),
                    'gross_pnl': str(gross), 'net_pnl': str(gross + D(held['entry_fee']) + fee)})
                b['position'] = None
            b.update(pending=None, position_verified=False, status='waiting', reason='官方完整成交已对账，等待后续规则')
            b['valuation'] = None
        _edit(data_dir, identifier, apply)
    return status(data_dir)


def _send(data_dir, bot, action, quote, spec):
    identifier = bot['id']
    credentials, fingerprint = _active(data_dir, identifier)
    snapshot = execution.snapshot(credentials)
    actual = _owned_position(bot, snapshot)
    if any(row['symbol'] == execution.instrument(bot['symbol']) for row in snapshot['orders']):
        raise DemoAPIError('该标的已有活动委托，等待处理；未新增请求')
    execution.setup(credentials, bot['symbol'], bot['leverage'])
    fresh = demo_market.quote('okx', bot['symbol'], credentials['site'])
    if not -5000 <= int(time.time() * 1000) - fresh['asof_ms'] <= 10_000:
        raise DemoAPIError('官方报价已过期，未发单')
    price = execution._positive(fresh['ask' if action in ('open_long', 'close_short') else 'bid'])
    original = execution._positive(quote['ask' if action in ('open_long', 'close_short') else 'bid'])
    if abs(price / original - 1) > D('.005'):
        raise DemoAPIError('预检期间价格变化超过0.5%，等待下一轮')
    step, multiplier = execution._positive(spec['step']), execution._positive(spec['multiplier'])
    opening = action.startswith('open_')
    if action not in ('open_long', 'close_long'):
        raise OwnershipError('这组小额策略仅做多，不创建空头仓位')
    limit_price = price * (D('1.002') if opening else D('.998'))
    # Exact decimal tick alignment is required by the official instrument.
    tick_size = execution._positive(spec['tick_size'])
    limit_price = (limit_price / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size
    if opening:
        if actual or len(bot['orders']) >= 480:
            raise OwnershipError('已有仓位或订单日志达到上限，不新增开仓')
        equity = D(bot['budget_usdt']) + D(bot['realized'])
        cap = min(D(bot['max_notional']), equity * D('.9') * bot['leverage'])
        quantity = (cap / limit_price / multiplier / step).to_integral_value(rounding=ROUND_DOWN) * step
        if _available(snapshot) < cap / bot['leverage'] + cap * D('.001'):
            raise DemoAPIError('官方可用余额不足开仓及费用余量')
    else:
        if actual is None or action != 'close_' + actual['side']:
            raise OwnershipError('没有与本策略匹配的可减仓持仓')
        quantity = D(actual['quantity'])
    notional = quantity * multiplier * limit_price
    if (quantity < execution._positive(spec['minimum']) or quantity > execution._positive(spec['maximum'])
            or quantity % step or (opening and (notional > cap or notional < D(spec['min_notional'])))):
        raise DemoAPIError('10U额度不足当前最小交易单位，本轮跳过，不提高额度')
    pending = {'client_id': 'dm' + uuid.uuid4().hex[:30], 'action': action, 'quantity': str(quantity),
        'estimated_notional': str(notional), 'multiplier': str(multiplier), 'fingerprint': fingerprint,
        'limit_price': str(limit_price), 'created_at_ms': int(time.time() * 1000), 'bar_ms': bot['last_bar_ms'], 'state': 'pending'}
    _active(data_dir, identifier)
    def intent(b):
        if b['pending']:
            raise OwnershipError('已有待对账请求，不重复发单')
        b['orders'].append(copy.deepcopy(pending))
        b.update(pending=copy.deepcopy(pending), position_verified=False, status='reconciling', reason='意图已保存，等待官方模拟回执')
    _edit(data_dir, identifier, intent)
    started = False
    try:
        with _sending(data_dir, identifier):
            started = True
            payload = {'instId': execution.instrument(bot['symbol']), 'tdMode': 'isolated',
                'side': 'buy' if opening else 'sell', 'ordType': 'ioc', 'sz': str(quantity),
                'px': format(limit_price, 'f'), 'clOrdId': pending['client_id'], 'reduceOnly': not opening}
            response = execution._transport(credentials, 'POST', '/api/v5/trade/order', body=payload)
            if (not isinstance(response, list) or len(response) != 1 or not isinstance(response[0], dict)
                    or str(response[0].get('sCode')) != '0'
                    or not isinstance(response[0].get('ordId'), str) or not 1 <= len(response[0]['ordId']) <= 80):
                raise DemoAPIError('官方未明确接受本次模拟委托，请按客户端号对账')
            order_id = response[0]['ordId']
        def accepted(b):
            b['orders'][-1].update(order_id=order_id, state='submitted')
            b['pending'].update(order_id=order_id, state='submitted')
            b.update(status='reconciling', reason='官方已接受，等待完整成交对账')
        _edit(data_dir, identifier, accepted)
    except DemoAPIError as exc:
        category, code = exc.diagnostic_category, exc.diagnostic_code
        def uncertain(b):
            b['orders'][-1]['state'] = 'uncertain' if started else 'rejected'
            b['pending']['state'] = 'uncertain' if started else 'rejected'
            for record in (b['orders'][-1], b['pending']):
                if category:
                    record.update(diagnostic_category=category, diagnostic_code=code)
            if not started:
                b['pending'] = None
            reason = '请求结果待核对，策略已暂停；只查单不自动重发' if started else '发送前已暂停，本笔未发送'
            b.update(status='paused', resume_on_restart=False,
                     reason=str(DemoAPIError(reason, category=category, code=code)))
        _edit(data_dir, identifier, uncertain)
        with _RUNTIME_LOCK:
            _RUNTIME.setdefault(_runtime_key(data_dir, identifier), {})['armed'] = False


def _tick_bot(data_dir, bot):
    identifier = bot['id']
    if bot['pending']:
        reconcile(data_dir, identifier)
        return
    credentials, fingerprint = _active(data_dir, identifier)
    snapshot = execution.snapshot(credentials)
    _owned_position(bot, snapshot)
    _active(data_dir, identifier)
    with _RUNTIME_LOCK:
        runtime = _RUNTIME[_runtime_key(data_dir, identifier)]
        runtime['verified'] = (runtime['generation'], fingerprint)
    _edit(data_dir, identifier, lambda b: b.update(position_verified=True, last_verified_ms=int(time.time() * 1000)))
    quote = demo_market.quote('okx', bot['symbol'], credentials['site'])
    bot = _edit(data_dir, identifier, lambda b: _update_valuation(b, quote))
    held = bot['position']
    action = None
    if held:
        change = (D(quote['mark']) / D(held['entry_price']) - 1) * (1 if held['side'] == 'long' else -1) * 100
        if change <= D('-.8') or change >= D('1.2') or int(time.time() * 1000) - held['opened_ms'] >= 3_600_000:
            action = 'close_' + held['side']
    if action is None:
        bars = demo_market.candles('okx', bot['symbol'], bot['interval'], credentials['site'])['bars'][-200:]
        period = 300_000 if bot['interval'] == '5m' else 900_000
        if not bars or not 0 < quote['asof_ms'] - bars[-1]['close_time_ms'] <= 2 * period:
            raise DemoAPIError('策略K线未收盘、过期或历史不足，本轮未发单')
        target = auto.signal(bot['strategy_id'], bars, 'usdm', bot['strategy_params'])
        if target == 'short':
            target = 'flat'
        bar = bars[-1]['close_time_ms']
        is_new = bar != bot['last_bar_ms']
        bot = _edit(data_dir, identifier, lambda b: b.update(last_bar_ms=bar, last_signal=target))
        # The optional observer only queues public evidence; it cannot veto orders.
        from app.custom.jevos.service import offer_strategy

        offer_strategy(data_dir, bot, bars)
        if held and is_new and target not in ('hold', held['side']):
            action = 'close_' + held['side']
        elif not held and is_new and target in ('long', 'short'):
            equity = D(bot['valuation']['equity'])
            daily_loss = D(bot['daily']['initial']) - equity
            if daily_loss >= 1 or bot['max_drawdown_pct'] >= 20:
                raise DemoAPIError('策略日亏1U或20%回撤门槛已触发，停止新增开仓')
            action = 'open_' + target
    if action:
        spec = execution.rules(credentials, bot['symbol'])
        _send(data_dir, bot, action, quote, spec)
    else:
        _edit(data_dir, identifier, lambda b: b.update(status='waiting', reason='规则未产生新动作，等待下一根已收盘K线或退出条件'))


def tick(data_dir):
    if not _TICK_LOCK.acquire(blocking=False):
        return
    try:
        with demo_trading._op(demo_trading._key(data_dir, 'okx')):
            _resume_after_restart(data_dir)
            with _STORE_LOCK:
                bots = list(copy.deepcopy(_load(data_dir)['bots']).values())
            for bot in bots:
                with _RUNTIME_LOCK:
                    armed = _RUNTIME.get(_runtime_key(data_dir, bot['id']), {}).get('armed')
                if not armed and not bot['pending']:
                    continue
                _edit(data_dir, bot['id'], lambda b: b.update(last_check_ms=int(time.time() * 1000), position_verified=False))
                try:
                    _tick_bot(data_dir, bot)
                except OwnershipError as exc:
                    with _RUNTIME_LOCK:
                        _RUNTIME.setdefault(_runtime_key(data_dir, bot['id']), {})['armed'] = False
                    reason = str(exc)
                    _edit(data_dir, bot['id'], lambda b, reason=reason: b.update(
                        status='paused', resume_on_restart=False, reason=reason))
                except DemoAPIError as exc:
                    allowed = {
                        '请先在OKX模拟盘设为合约账户、单向持仓模式',
                        '请先在OKX官方模拟盘将该合约设为逐仓1倍',
                        '请先在OKX官方模拟盘将该合约设为逐仓10倍',
                        '10U额度不足当前最小交易单位，本轮跳过，不提高额度',
                        '官方可用余额不足开仓及费用余量',
                        '策略日亏1U或20%回撤门槛已触发，停止新增开仓',
                        '策略K线未收盘、过期或历史不足，本轮未发单',
                        '该标的已有活动委托，等待处理；未新增请求',
                        '官方报价已过期，未发单',
                        '预检期间价格变化超过0.5%，等待下一轮',
                    }
                    reason = str(exc) if str(exc) in allowed else '行情、连接或官方配置预检暂未通过；未重发订单，等待下一轮核对'
                    reason = str(DemoAPIError(reason, category=exc.diagnostic_category, code=exc.diagnostic_code))
                    _edit(data_dir, bot['id'], lambda b, reason=reason: b.update(status='blocked', reason=reason))
                except (ValueError, TypeError, KeyError, ArithmeticError):
                    _edit(data_dir, bot['id'], lambda b: b.update(status='blocked', reason='行情、连接或官方配置预检暂未通过；未重发订单，等待下一轮核对'))
    finally:
        _TICK_LOCK.release()


def status(data_dir):
    try:
        _, generation, fingerprint = _credentials(data_dir)
        ready = True
    except DemoAPIError:
        ready, generation, fingerprint = False, None, None
    with _STORE_LOCK:
        bots = list(copy.deepcopy(_load(data_dir)['bots']).values())
    result = []
    for bot in bots:
        with _RUNTIME_LOCK:
            runtime = _RUNTIME.get(_runtime_key(data_dir, bot['id']), {})
            armed = bool(runtime.get('armed') and ready and (runtime.get('generation'), runtime.get('fingerprint')) == (generation, fingerprint))
            if runtime and not armed:
                runtime['armed'] = False
        valuation = bot['valuation']
        fresh = bool(valuation and -5000 <= int(time.time() * 1000) - valuation['asof_ms'] <= 60_000)
        flat = bot['position'] is None
        verified = bool(bot.get('position_verified') and not bot['pending'] and ready
            and runtime.get('verified') == (generation, fingerprint)
            and bot.get('last_verified_ms') is not None and -5000 <= int(time.time() * 1000) - bot['last_verified_ms'] <= 60_000)
        wins = sum(D(trade['net_pnl']) > 0 for trade in bot['trades'])
        count = len(bot['trades'])
        record = {k: bot[k] for k in ('id', 'name', 'symbol', 'strategy_id', 'interval', 'description', 'budget_usdt',
            'max_notional', 'leverage', 'enabled', 'status', 'reason', 'last_check_ms', 'last_bar_ms', 'last_signal')}
        record['armed'] = armed
        record['resume_on_restart'] = bot.get('resume_on_restart', False)
        record['position_verified'] = verified
        if bot['enabled'] and not armed:
            record['status'] = 'paused'
            if not ready:
                record['reason'] = '请先连接欧易官方模拟，再重新启用策略；原成交与待对账订单保留'
            elif not runtime:
                if bot.get('resume_on_restart', False):
                    with _RUNTIME_LOCK:
                        record['reason'] = _BOOT_CHECKS.get(str(data_dir.resolve()), {}).get(
                            'reason', '已授权重启恢复，等待官方模拟连接与仓位核验')
                else:
                    record['reason'] = '服务重启后未启用执行，请重新启用；原成交保留'
            elif (runtime.get('generation'), runtime.get('fingerprint')) != (generation, fingerprint):
                record['reason'] = '官方模拟连接已变化，请核对后重新启用；原成交保留'
        record['position'] = ({k: bot['position'][k] for k in ('side', 'quantity', 'entry_price', 'notional')} if not flat else None)
        record['metrics'] = {'closed_trades': count, 'wins': wins, 'win_rate_pct': wins / count * 100 if count else None,
            'realized_pnl_usdt': bot['realized'], 'fees_usdt': bot['fees'],
            'open_pnl_usdt': ('0' if flat else valuation['pnl'] if fresh else None) if verified else None,
            'allocation_equity_usdt': (str(D(bot['budget_usdt']) + D(bot['realized'])) if flat else valuation['equity'] if fresh else None) if verified else None,
            'max_drawdown_pct': bot['max_drawdown_pct'], 'funding_included': False}
        record['orders'] = [{k: order[k] for k in ('client_id', 'action', 'state', 'created_at_ms',
            'filled_quantity', 'avg_price', 'fee_usdt') if order.get(k) is not None} for order in bot['orders'][-20:]]
        result.append(record)
    return {'exchange': 'okx', 'environment': 'demo', 'connection_ready': ready,
        'legacy_armed': demo_trading.status(data_dir, 'okx')['armed'], 'bots': result}
