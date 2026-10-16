"""Explicitly armed official-demo execution with durable ambiguous-order handling.

Arming is memory-only and therefore cleared on service restart. Keys stay in
the existing credential service; journals hold fingerprints and order evidence.
"""
# ruff: noqa: RUF001 -- localized trading controls.
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.fs_utils import atomic_write_text

from . import auto, demo_market, demo_service, rules
from . import demo_execution as execution
from .demo_client import DemoAPIError

_LOCK = threading.RLock()
_ARMS: dict[tuple[str, str], dict] = {}
_OPS: dict[tuple[str, str], tuple[int, int]] = {}
_PLANS: dict[str, dict] = {}
_TICK_LOCK = threading.Lock()
_CONTROL: dict[tuple[str, str], int] = {}
HK = timezone(timedelta(hours=8))


class ArmRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)
    confirm_demo: bool = Field(default=False, strict=True)
    symbol: Literal['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] = 'ETHUSDT'
    source_account_id: str | None = Field(default=None, max_length=80)
    max_notional: float = Field(default=10, ge=5, le=100, allow_inf_nan=False)
    daily_loss_limit: float = Field(default=5, ge=.1, le=100, allow_inf_nan=False)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: Literal['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] = 'ETHUSDT'
    action: Literal['open_long', 'open_short', 'close_long', 'close_short']
    notional: float = Field(default=10, ge=5, le=100, allow_inf_nan=False)


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    confirm: bool = Field(strict=True)


def _key(data_dir, exchange):
    if exchange not in ('okx', 'bitget'):
        raise DemoAPIError('不支持的官方模拟交易所')
    return str(data_dir.resolve()), exchange


@contextmanager
def _op(key):
    owner = threading.get_ident()
    with _LOCK:
        previous = _OPS.get(key)
        if previous and previous[0] != owner:
            raise DemoAPIError('该官方模拟连接正在处理请求，请稍后再试')
        _OPS[key] = (owner, (previous[1] if previous else 0) + 1)
    # Only a reservation is held during HTTP; no mutex/data-store lock is held.
    try:
        yield
    finally:
        with _LOCK:
            current = _OPS[key]
            if current[1] == 1:
                _OPS.pop(key)
            else:
                _OPS[key] = (owner, current[1] - 1)


def _credentials(data_dir, exchange):
    key = demo_service._key(data_dir, exchange)
    with demo_service._LOCK:
        entry = demo_service._SESSIONS.get(key)
        if entry is None or key in demo_service._DISCONNECTED or key in demo_service._CONNECTING:
            raise DemoAPIError('请先在本次运行中连接官方模拟账户，再单独启用模拟执行')
        credentials = copy.deepcopy(entry['credentials'])
        generation = demo_service._GENERATIONS.get(key, 0)
    fingerprint = hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest()
    return credentials, generation, fingerprint


def _path(data_dir, exchange):
    return data_dir / 'crypto_demo_trading' / f'{exchange}.json'


def _journal(data_dir, exchange):
    path = _path(data_dir, exchange)
    if not path.exists():
        return {'schema': 1, 'orders': [], 'decisions': [], 'daily': {}}
    if path.stat().st_size > 2_000_000:
        raise DemoAPIError('模拟执行日志达到大小上限，请先归档；原记录保留')
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema') != 1 or not isinstance(value.get('orders'), list):
        raise DemoAPIError('模拟执行日志格式异常，暂停发单；原记录保留')
    return value


def _save(data_dir, exchange, value):
    path = _path(data_dir, exchange)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(text.encode()) > 2_000_000:
        raise DemoAPIError('模拟执行日志达到大小上限，请先归档；未新增请求')
    atomic_write_text(path, text)


def _note(data_dir, exchange, status, reason, target=None):
    key = _key(data_dir, exchange)
    state = _ARMS.setdefault(key, {})
    state['reason'] = reason
    value = _journal(data_dir, exchange)
    events = value.setdefault('decisions', [])
    previous = events[-1] if events else {}
    if (previous.get('status'), previous.get('reason'), previous.get('target')) == (status, reason, target):
        return
    events.append({'at_ms': int(time.time() * 1000), 'status': status, 'reason': reason, 'target': target})
    value['decisions'] = events[-100:]
    _save(data_dir, exchange, value)


def status(data_dir: Path, exchange: str):
    key = _key(data_dir, exchange)
    with _LOCK:
        state = _ARMS.get(key, {}).copy()
    if state.get('armed'):
        try:
            _, generation, fingerprint = _credentials(data_dir, exchange)
            changed = (generation, fingerprint) != (state['generation'], state['fingerprint'])
        except DemoAPIError:
            changed = True
        if changed:
            with _LOCK:
                _ARMS[key].update(armed=False, reason='官方连接已断开或变化，执行已暂停；请重新连接并核对')
                state = _ARMS[key].copy()
    journal = _journal(data_dir, exchange)
    fields = ('client_id', 'order_id', 'state', 'symbol', 'action', 'quantity',
              'estimated_notional', 'created_at_ms', 'exchange_state')
    return {'armed': bool(state.get('armed')), 'auto': bool(state.get('armed') and state.get('source_account_id')),
                'source_account_id': state.get('source_account_id'), 'symbol': state.get('symbol', 'ETHUSDT'),
                'max_notional': state.get('max_notional', 10), 'daily_loss_limit': state.get('daily_loss_limit', 5),
                'reason': state.get('reason', '尚未启用；重启后需重新连接并启用'),
                'managed': bool(state.get('managed')), 'decisions': copy.deepcopy(journal.get('decisions', [])),
                'orders': [{field: row.get(field) for field in fields} for row in journal['orders'][-50:]]}


def _source(data_dir, account_id):
    with auto._STORE_LOCK:
        account = copy.deepcopy(auto._load(data_dir)['accounts'].get(account_id))
    if not account or account.get('exchange') != 'bitget' or account['market'] != 'usdm':
        raise DemoAPIError('规则来源必须是有效的Bitget本地模拟策略账户')
    fields = ('id', 'symbol', 'strategy_id', 'strategy_params', 'interval', 'stop_loss_pct', 'take_profit_pct')
    config = {field: account.get(field) for field in fields}
    rules.parameters(account['strategy_id'], account.get('strategy_params'))
    return config, hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def _equity(snapshot):
    values = [row.get('equity') for row in snapshot['balances'] if row.get('currency') == 'USDT']
    if len(values) != 1:
        raise DemoAPIError('官方模拟账户必须有可核对的USDT权益')
    return execution._positive(values[0])


def _position(snapshot, exchange, symbol):
    positions = snapshot['positions']
    if len(positions) > 1:
        raise DemoAPIError('当前只支持一个官方模拟持仓，请先在官方处理其他仓位')
    if positions and positions[0]['symbol'] != (execution.instrument(symbol) if exchange == 'okx' else symbol):
        raise DemoAPIError('官方账户存在其他标的持仓，暂停新增指令')
    return positions[0] if positions else None


def _daily(data_dir, exchange, snapshot, fingerprint):
    value = _journal(data_dir, exchange)
    equity = _equity(snapshot)
    day = datetime.now(HK).date().isoformat()
    basis = value.get('daily', {})
    if basis.get('date') != day or basis.get('fingerprint') != fingerprint:
        basis = {'date': day, 'fingerprint': fingerprint, 'initial': str(equity), 'peak': str(equity)}
    basis['peak'] = str(max(Decimal(basis['peak']), equity))
    value['daily'] = basis
    _save(data_dir, exchange, value)
    loss = max(Decimal(0), Decimal(basis['initial']) - equity)
    drawdown = (Decimal(basis['peak']) - equity) / Decimal(basis['peak']) * 100
    return loss, drawdown


def arm(data_dir: Path, exchange: str, request: ArmRequest):
    key = _key(data_dir, exchange)
    if not request.enabled:
        with _LOCK:
            _CONTROL[key] = _CONTROL.get(key, 0) + 1
            state = _ARMS.setdefault(key, {})
            state.update(armed=False, reason='官方模拟执行已暂停；已有持仓、挂单及已经在途的请求需继续核对')
        return status(data_dir, exchange)
    with _op(key):
        if exchange == 'okx':
            from .demo_bots import any_armed

            if any_armed(data_dir):
                raise DemoAPIError('请先暂停多策略官方模拟执行，避免共享仓位冲突')
        with _LOCK:
            token = _CONTROL[key] = _CONTROL.get(key, 0) + 1
        if not request.confirm_demo:
            raise DemoAPIError('请明确确认使用官方模拟专用密钥并启用模拟执行')
        credentials, generation, fingerprint = _credentials(data_dir, exchange)
        execution.setup(credentials, request.symbol)
        snapshot = execution.snapshot(credentials)
        if snapshot['orders']:
            raise DemoAPIError('官方存在活动订单，请先处理后再启用')
        source_hash = None
        if request.source_account_id:
            source, source_hash = _source(data_dir, request.source_account_id)
            if source['symbol'] != request.symbol or snapshot['positions']:
                raise DemoAPIError('自动规则的标的须一致，且启用时官方账户必须空仓')
        # No ambiguous request is forgotten by re-arming or restarting.
        if any(row['state'] in ('pending', 'submitted', 'uncertain') for row in _journal(data_dir, exchange)['orders']
               if row.get('fingerprint') == fingerprint):
            raise DemoAPIError('尚有未对账的模拟订单，请先按客户端订单号查询')
        _daily(data_dir, exchange, snapshot, fingerprint)
        _, current_generation, current_fingerprint = _credentials(data_dir, exchange)
        with _LOCK:
            if _CONTROL.get(key) != token or (current_generation, current_fingerprint) != (generation, fingerprint):
                raise DemoAPIError('启用期间已暂停或连接已变化，请重新核对')
            _ARMS[key] = {**request.model_dump(exclude={'confirm_demo', 'enabled'}), 'armed': True,
                          'generation': generation, 'fingerprint': fingerprint, 'source_hash': source_hash,
                          'managed': False, 'last_bar': None, 'pending_client_id': None}
        _note(data_dir, exchange, 'armed', '已启用官方模拟执行；规则来源仅复制参数，不修改本地账户')
        return status(data_dir, exchange)


def _active(data_dir, exchange):
    state = _ARMS.get(_key(data_dir, exchange), {})
    if not state.get('armed'):
        raise DemoAPIError('官方模拟执行未启用')
    credentials, generation, fingerprint = _credentials(data_dir, exchange)
    if state['generation'] != generation or state['fingerprint'] != fingerprint:
        state['armed'] = False
        raise DemoAPIError('官方连接已变化，请重新核对并启用')
    return state, credentials


def plan(data_dir: Path, exchange: str, request: PlanRequest, *, origin=None, bar_time=None):
    key = _key(data_dir, exchange)
    with _op(key):
        state, credentials = _active(data_dir, exchange)
        if request.symbol != state['symbol'] or (request.action.startswith('open_') and request.notional > state['max_notional']):
            raise DemoAPIError('标的或单笔估算金额超过本次启用范围')
        if state.get('pending_client_id'):
            raise DemoAPIError('上一笔订单尚未对账，不生成新计划')
        execution.setup(credentials, request.symbol)
        snapshot = execution.snapshot(credentials)
        if snapshot['orders']:
            raise DemoAPIError('存在活动订单，等待成交或在官方处理后再试')
        position = _position(snapshot, exchange, request.symbol)
        opening = request.action.startswith('open_')
        if opening and position:
            raise DemoAPIError('已有官方模拟持仓，不允许重复开仓')
        if not opening and (position is None or position['side'] != request.action.removeprefix('close_')):
            raise DemoAPIError('没有可按该方向减仓的官方持仓')
        if (not opening and execution.mode(credentials) == 'uta'
                and (not state.get('managed') or position['side'] != state.get('managed_side')
                     or execution._positive(position['quantity']) != execution._positive(state['managed_qty']))):
            raise DemoAPIError('UTA原始持仓数量仅允许与本次已记录开仓数量逐项匹配后减仓；其他持仓请在官方处理')
        loss, drawdown = _daily(data_dir, exchange, snapshot, state['fingerprint'])
        if opening and (loss >= Decimal(str(state['daily_loss_limit'])) or drawdown >= 15):
            raise DemoAPIError('日亏限额或15%权益回撤阈值已触发，停止新增开仓；减仓仍可单独确认')
        spec = execution.rules(credentials, request.symbol)
        quote = demo_market.quote(exchange, request.symbol, credentials['site'])
        price = execution._positive(quote['ask' if request.action in ('open_long', 'close_short') else 'bid'])
        step, multiplier = execution._positive(spec['step']), execution._positive(spec['multiplier'])
        quantity = (Decimal(str(request.notional)) / price / multiplier / step).to_integral_value(rounding=ROUND_DOWN) * step
        if not opening:
            quantity = execution._positive(position['quantity'])
        notional = quantity * multiplier * price
        minimum_notional = Decimal(str(spec['min_notional']))
        if (quantity < execution._positive(spec['minimum']) or quantity % step
                or quantity > execution._positive(spec['maximum']) or (opening and notional > Decimal(str(state['max_notional'])))
                or (opening and notional < minimum_notional)):
            raise DemoAPIError('金额不足最小交易单位、超过限额或持仓过大；请调整金额或在官方模拟盘处理')
        now = int(time.time() * 1000)
        if not -5000 <= now - quote['asof_ms'] <= 10000:
            raise DemoAPIError('官方公开报价已过期，未生成订单计划')
        identifier = uuid.uuid4().hex
        value = {'id': identifier, 'expires_at_ms': now + 45000, 'symbol': request.symbol, 'action': request.action,
                 'quantity': format(quantity, 'f'), 'quantity_unit': spec['unit'], 'estimated_notional': str(notional),
                 'price': str(price), 'reduce_only': not opening, 'leverage': 1, 'margin_mode': 'isolated',
                 'warnings': ['市价成交可能滑点；限额为下单前估算，最终以官方成交记录为准。',
                              '金额限额针对新增开仓；reduceOnly减仓按官方现有持仓数量校验。',
                              '日亏与回撤阈值阻止新增开仓，不保证持仓损失上限；暂停不会自动平仓。']}
        with _LOCK:
            if len(_PLANS) >= 100:
                for old_id in [item for item, saved in _PLANS.items() if saved['public']['expires_at_ms'] < now]:
                    _PLANS.pop(old_id)
            if len(_PLANS) >= 100:
                raise DemoAPIError('待确认计划已达上限，请等待过期后重试')
            _PLANS[identifier] = {'public': value, 'key': key, 'fingerprint': state['fingerprint'],
                                  'generation': state['generation'], 'multiplier': str(multiplier),
                                  'origin': origin, 'bar_time': bar_time, 'consumed': False}
        return copy.deepcopy(value)


def submit(data_dir: Path, exchange: str, request: SubmitRequest):
    key = _key(data_dir, exchange)
    with _op(key):
        if not request.confirm:
            raise DemoAPIError('请明确确认本笔官方模拟订单')
        saved = _PLANS.get(request.plan_id)
        if not saved or saved['key'] != key or saved['consumed']:
            raise DemoAPIError('计划不存在、连接不符或已经提交；请查单，不要重复发单')
        state, credentials = _active(data_dir, exchange)
        value = saved['public']
        if (saved['fingerprint'] != state['fingerprint'] or saved['generation'] != state['generation']
                or int(time.time() * 1000) >= value['expires_at_ms']):
            raise DemoAPIError('订单计划已过期或连接已变化，请重新预览')
        # Recheck private state, settings and public quote after user confirmation.
        fresh = plan(data_dir, exchange, PlanRequest(symbol=value['symbol'], action=value['action'],
                                                    notional=min(state['max_notional'], 100)))
        with _LOCK:
            _PLANS.pop(fresh['id'], None)
        if value['reduce_only'] and fresh['quantity'] != value['quantity']:
            raise DemoAPIError('官方持仓数量已变化，请重新预览减仓计划')
        quote = demo_market.quote(exchange, value['symbol'], credentials['site'])
        current_price = execution._positive(quote['ask' if value['action'] in ('open_long', 'close_short') else 'bid'])
        expected = execution._positive(value['price'])
        if (abs(current_price / expected - 1) > Decimal('.005')
                or (not value['reduce_only'] and Decimal(value['quantity']) * Decimal(saved['multiplier'])
                    * current_price > Decimal(str(state['max_notional'])))):
            raise DemoAPIError('确认期间价格变化或金额超过限额，请重新预览')
        _active(data_dir, exchange)
        journal = _journal(data_dir, exchange)
        if len(journal['orders']) >= 500:
            raise DemoAPIError('官方模拟订单日志已达500笔上限，请先归档；原记录保留')
        row = {key: value[key] for key in ('symbol', 'action', 'quantity', 'estimated_notional')}
        row.update(client_id='dm' + uuid.uuid4().hex[:30], order_id=None, state='pending',
                   created_at_ms=int(time.time() * 1000), fingerprint=state['fingerprint'],
                   source_account_id=saved['origin'], bar_time_ms=saved['bar_time'])
        journal['orders'].append(row)
        _save(data_dir, exchange, journal)  # Durable intent precedes the only POST attempt.
        saved['consumed'] = True
        state['pending_client_id'] = row['client_id']
        started = False
        try:
            _active(data_dir, exchange)
            started = True
            row['order_id'] = execution.submit(credentials, value['symbol'], value['action'], value['quantity'], row['client_id'])
            row['state'] = 'submitted'
            if value['action'].startswith('open_'):
                state.update(managed=True, managed_side=value['action'].removeprefix('open_'), managed_qty=value['quantity'])
            _note(data_dir, exchange, 'submitted', '官方模拟请求已接受，等待查单确认；已在途请求的回执不会自动重新启用执行')
        except DemoAPIError:
            row['state'] = 'uncertain' if started else 'rejected'
            state['armed'] = False
            if not started:
                state['pending_client_id'] = None
            state['reason'] = ('订单结果不明，模拟执行已暂停；仅按客户端订单号查单，不自动重发' if started
                               else '请求发送前已暂停或连接变化，本笔未发送')
        # Reload to retain any decision evidence appended while awaiting transport.
        current = _journal(data_dir, exchange)
        current['orders'] = [row if item['client_id'] == row['client_id'] else item for item in current['orders']]
        _save(data_dir, exchange, current)
        return {'order': {field: row.get(field) for field in (
            'client_id', 'order_id', 'state', 'symbol', 'action', 'quantity', 'estimated_notional', 'created_at_ms')}}


def reconcile(data_dir: Path, exchange: str, client_id: str):
    key = _key(data_dir, exchange)
    if not re.fullmatch(r'dm[a-f0-9]{30}', client_id):
        raise DemoAPIError('客户端订单号无效')
    with _op(key):
        credentials, _, fingerprint = _credentials(data_dir, exchange)
        journal = _journal(data_dir, exchange)
        row = next((item for item in journal['orders'] if item['client_id'] == client_id), None)
        if row is None or row.get('fingerprint') != fingerprint:
            raise DemoAPIError('该订单不属于当前官方模拟连接')
        raw = execution.reconcile(credentials, row['symbol'], client_id)
        row['exchange_state'] = raw
        # Keep accepted/live/partial orders pending. Only known terminal states clear the gate.
        if raw in ('filled', 'canceled', 'cancelled', 'rejected', 'partially_filled_canceled'):
            row['state'] = 'confirmed'
            state = _ARMS.get(key, {})
            if state.get('pending_client_id') == client_id:
                state['pending_client_id'] = None
                if raw != 'filled':
                    state.update(armed=False, reason='订单未完整成交，自动执行暂停；请核对官方持仓')
                elif row['action'].startswith('close_'):
                    state['managed'] = False
        _save(data_dir, exchange, journal)
        return {'order': {field: row.get(field) for field in (
            'client_id', 'order_id', 'state', 'symbol', 'action', 'quantity', 'estimated_notional', 'created_at_ms', 'exchange_state')}}


def tick(data_dir: Path):
    if not _TICK_LOCK.acquire(blocking=False):
        return
    try:
        for exchange in ('okx', 'bitget'):
            key = _key(data_dir, exchange)
            state = _ARMS.get(key, {})
            if not state.get('armed') or not state.get('source_account_id'):
                continue
            with _op(key):
                try:
                    _tick_account(data_dir, exchange, state)
                except (DemoAPIError, ValueError, KeyError, TypeError, OSError):
                    state['armed'] = False
                    _note(data_dir, exchange, 'blocked', '官方模拟预检或状态异常，已暂停；请核对账户和查单记录')
    finally:
        _TICK_LOCK.release()


def _tick_account(data_dir, exchange, state):
    source, digest = _source(data_dir, state['source_account_id'])
    if digest != state['source_hash']:
        raise DemoAPIError('规则来源已变化，需重新确认')
    _, credentials = _active(data_dir, exchange)
    if state.get('pending_client_id'):
        result = reconcile(data_dir, exchange, state['pending_client_id'])['order']
        if result.get('exchange_state') != 'filled' or not state['armed']:
            _note(data_dir, exchange, 'waiting', '上一笔官方模拟订单尚未完整成交或对账，等待核对')
            return
    snapshot = execution.snapshot(credentials)
    if snapshot['orders']:
        _note(data_dir, exchange, 'waiting', '官方尚有活动订单，等待成交或撤单')
        return
    position = _position(snapshot, exchange, state['symbol'])
    if position:
        if (not state.get('managed') or position['side'] != state.get('managed_side')
                or execution._positive(position['quantity']) != execution._positive(state['managed_qty'])):
            raise DemoAPIError('官方持仓与本次执行记录不一致')
    elif state.get('managed'):
        raise DemoAPIError('官方持仓与本次执行记录不一致')
    bars = demo_market.candles(exchange, state['symbol'], source['interval'], credentials['site'])['bars'][-200:]
    quote = demo_market.quote(exchange, state['symbol'], credentials['site'])
    period = 3_600_000 if source['interval'] == '1h' else 14_400_000
    if not bars or not 0 < quote['asof_ms'] - bars[-1]['close_time_ms'] <= 2 * period:
        raise DemoAPIError('策略K线不足、未收盘或过期')
    bar = bars[-1]['close_time_ms']
    target = auto.signal(source['strategy_id'], bars, 'usdm', source.get('strategy_params'))
    action = None
    if position:
        entry = execution._positive(position['entry_price'])
        mark = execution._positive(quote['mark'])
        change = (mark / entry - 1) * (1 if position['side'] == 'long' else -1) * 100
        risk = change <= -Decimal(str(source['stop_loss_pct'])) or change >= Decimal(str(source['take_profit_pct']))
        if risk or (bar != state['last_bar'] and target not in ('hold', position['side'])):
            action = 'close_' + position['side']
    elif bar != state['last_bar'] and target in ('long', 'short'):
        action = 'open_' + target
    if action is None:
        _note(data_dir, exchange, 'waiting', '规则未产生新动作、已有同向持仓或本根已经处理', target)
        return
    previous = _journal(data_dir, exchange)['orders']
    if any(row.get('source_account_id') == state['source_account_id'] and row.get('bar_time_ms') == bar
           and row['action'] == action and row.get('fingerprint') == state['fingerprint'] for row in previous):
        _note(data_dir, exchange, 'waiting', '本根同方向请求已有记录，等待下一根；未重复发单', target)
        return
    value = plan(data_dir, exchange, PlanRequest(symbol=state['symbol'], action=action, notional=state['max_notional']),
                 origin=state['source_account_id'], bar_time=bar)
    state['last_bar'] = bar
    submit(data_dir, exchange, SubmitRequest(plan_id=value['id'], confirm=True))
