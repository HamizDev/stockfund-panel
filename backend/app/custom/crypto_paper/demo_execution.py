"""Fixed-host, simulated-only execution adapters; no live transport or mode changes."""
# ruff: noqa: RUF001 -- localized execution errors.
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from . import bitget, demo_accounts, demo_client
from .demo_client import DemoAPIError

GET_PATHS = {
    'okx': {'/api/v5/account/config', '/api/v5/account/leverage-info', '/api/v5/trade/order'},
    'classic': {'/api/v2/mix/account/account', '/api/v2/mix/order/detail'},
    'uta': {'/api/v3/account/settings', '/api/v3/trade/order-info'},
}
ORDER_PATHS = {'okx': '/api/v5/trade/order', 'classic': '/api/v2/mix/order/place-order',
               'uta': '/api/v3/trade/place-order'}


def mode(credentials):
    return 'okx' if credentials['exchange'] == 'okx' else credentials['account_mode']


def _transport(credentials: dict, method: str, path: str, *, params=None, body=None):
    flavor = mode(credentials)
    if credentials['exchange'] not in ('okx', 'bitget') or flavor not in GET_PATHS:
        raise DemoAPIError('不支持的官方模拟账户')
    if not ((method == 'GET' and path in GET_PATHS[flavor])
            or (method == 'POST' and path == ORDER_PATHS[flavor])):
        raise DemoAPIError('模拟执行接口不在白名单内')
    host = demo_client.OKX_HOSTS[credentials['site']] if flavor == 'okx' else demo_client.BITGET_HOST
    request_path = path + ('?' + str(httpx.QueryParams(params)) if params else '')
    raw = json.dumps(body, separators=(',', ':'), ensure_ascii=True) if body is not None else ''
    stamp = (datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
             if flavor == 'okx' else str(time.time_ns() // 1_000_000))
    signature = base64.b64encode(hmac.new(credentials['secret_key'].encode(),
        (stamp + method + request_path + raw).encode(), hashlib.sha256).digest()).decode()
    if flavor == 'okx':
        headers = {'OK-ACCESS-KEY': credentials['api_key'], 'OK-ACCESS-SIGN': signature,
                   'OK-ACCESS-TIMESTAMP': stamp, 'OK-ACCESS-PASSPHRASE': credentials['passphrase'],
                   'x-simulated-trading': '1'}
    else:
        headers = {'ACCESS-KEY': credentials['api_key'], 'ACCESS-SIGN': signature,
                   'ACCESS-TIMESTAMP': stamp, 'ACCESS-PASSPHRASE': credentials['passphrase'], 'paptrading': '1'}
    headers['Content-Type'] = 'application/json'
    try:
        with httpx.Client(timeout=httpx.Timeout(12, connect=8), follow_redirects=False) as client:
            response = client.request(method, host + request_path, headers=headers,
                                      content=raw if method == 'POST' else None)
            if response.status_code >= 300 or len(response.content) > 1_000_000:
                raise DemoAPIError('模拟请求未取得明确结果，请按客户端订单号查单')
            value = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, DemoAPIError):
            raise
        raise DemoAPIError('模拟请求未取得明确结果，请按客户端订单号查单') from exc
    success = '0' if flavor == 'okx' else '00000'
    if not isinstance(value, dict) or str(value.get('code')) != success or 'data' not in value:
        raise DemoAPIError('官方模拟接口拒绝请求或结果不明；请检查模拟密钥、权限及订单状态')
    return value['data']


def setup(credentials: dict, symbol: str, leverage: int = 1) -> None:
    """Read current effective settings. Never switch account/position/leverage modes."""
    if symbol not in bitget.SYMBOLS or type(leverage) is not int or leverage not in (1, 10):
        raise DemoAPIError('不支持的官方模拟标的')
    flavor = mode(credentials)
    if flavor == 'okx':
        config = _transport(credentials, 'GET', '/api/v5/account/config')
        if not isinstance(config, list) or len(config) != 1 or config[0].get('acctLv') != '2' or config[0].get('posMode') != 'net_mode':
            raise DemoAPIError('请先在OKX模拟盘设为合约账户、单向持仓模式')
        rows = _transport(credentials, 'GET', '/api/v5/account/leverage-info', params={
            'instId': instrument(symbol), 'mgnMode': 'isolated'})
        if not isinstance(rows, list) or not rows or any(
                row.get('instId') != instrument(symbol) or row.get('mgnMode') != 'isolated'
                or _positive(row.get('lever')) != leverage for row in rows):
            raise DemoAPIError(f'请先在OKX官方模拟盘将该合约设为逐仓{leverage}倍')
    elif flavor == 'classic':
        if leverage != 1:
            raise DemoAPIError('Bitget旧单策略执行仅支持原1倍配置')
        value = _transport(credentials, 'GET', '/api/v2/mix/account/account', params={
            'symbol': symbol, 'productType': 'USDT-FUTURES', 'marginCoin': 'USDT'})
        if (not isinstance(value, dict) or value.get('posMode') != 'one_way_mode'
                or value.get('marginMode') != 'isolated' or _positive(value.get('isolatedLongLever')) != 1
                or _positive(value.get('isolatedShortLever')) != 1):
            raise DemoAPIError('请先在Bitget Classic模拟盘将该合约设为单向、逐仓1倍')
    else:
        if leverage != 1:
            raise DemoAPIError('Bitget旧单策略执行仅支持原1倍配置')
        value = _transport(credentials, 'GET', '/api/v3/account/settings')
        rows = value.get('symbolConfigList', []) if isinstance(value, dict) else []
        selected = [row for row in rows if row.get('category') == 'USDT-FUTURES' and row.get('symbol') == symbol]
        if (not isinstance(value, dict) or value.get('holdMode') != 'one_way_mode' or len(selected) != 1
                or selected[0].get('marginMode') != 'isolated' or _positive(selected[0].get('leverage')) != 1):
            raise DemoAPIError('UTA需明确返回该合约的单向、逐仓1倍配置；请先在官方模拟盘设置')


def _positive(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError
        return result
    except (ValueError, ArithmeticError) as exc:
        raise DemoAPIError('官方模拟规则或配置缺少有效正数') from exc


def instrument(symbol):
    return symbol.removesuffix('USDT') + '-USDT-SWAP'


def rules(credentials: dict, symbol: str) -> dict:
    flavor = mode(credentials)
    if flavor == 'classic':
        value = bitget._contract_rules(symbol)
        return {'step': value['step_size'], 'minimum': value['min_qty'], 'maximum': value['max_qty'],
                'multiplier': '1', 'min_notional': value['min_notional'], 'unit': symbol.removesuffix('USDT')}
    if flavor == 'okx':
        host, path = demo_client.OKX_HOSTS[credentials['site']], '/api/v5/public/instruments'
        params = {'instType': 'SWAP', 'instId': instrument(symbol)}
    else:
        host, path = demo_client.BITGET_HOST, '/api/v3/market/instruments'
        params = {'category': 'USDT-FUTURES', 'symbol': symbol}
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get(host + path, params=params)
            response.raise_for_status()
            value = response.json()
        success = '0' if flavor == 'okx' else '00000'
        if not isinstance(value, dict) or str(value.get('code')) != success or not isinstance(value.get('data'), list):
            raise ValueError
        selected = [row for row in value['data'] if row.get('instId' if flavor == 'okx' else 'symbol') == (
            instrument(symbol) if flavor == 'okx' else symbol)]
        if len(selected) != 1:
            raise ValueError
        row = selected[0]
        if flavor == 'okx':
            if (row.get('ctType') != 'linear' or row.get('state') != 'live' or row.get('settleCcy') != 'USDT'
                    or row.get('ctValCcy') != symbol.removesuffix('USDT')):
                raise ValueError
            result = {'step': row['lotSz'], 'minimum': row['minSz'], 'maximum': row['maxMktSz'],
                      'multiplier': row['ctVal'], 'min_notional': '0', 'unit': '张', 'tick_size': row['tickSz']}
        else:
            if row.get('status') != 'online':
                raise ValueError
            result = {'step': row['quantityMultiplier'], 'minimum': row['minOrderQty'],
                      'maximum': row['maxMarketOrderQty'], 'multiplier': '1',
                      'min_notional': row['minOrderAmount'], 'unit': symbol.removesuffix('USDT')}
        for key in ('step', 'minimum', 'maximum', 'multiplier'):
            _positive(result[key])
        return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise DemoAPIError('官方合约规则暂不可用，不生成下单计划') from exc


def submit(credentials, symbol, action, quantity, client_id):
    if (symbol not in bitget.SYMBOLS or action not in ('open_long', 'open_short', 'close_long', 'close_short')
            or not re.fullmatch(r'dm[a-f0-9]{30}', client_id)):
        raise DemoAPIError('官方模拟订单参数无效')
    _positive(quantity)
    flavor = mode(credentials)
    side = 'buy' if action in ('open_long', 'close_short') else 'sell'
    reduce = action.startswith('close_')
    if flavor == 'okx':
        body = {'instId': instrument(symbol), 'tdMode': 'isolated', 'side': side, 'ordType': 'market',
                'sz': quantity, 'clOrdId': client_id, 'reduceOnly': reduce}
    elif flavor == 'classic':
        body = {'symbol': symbol, 'productType': 'USDT-FUTURES', 'marginCoin': 'USDT', 'marginMode': 'isolated',
                'side': side, 'orderType': 'market', 'size': quantity, 'clientOid': client_id,
                'reduceOnly': 'YES' if reduce else 'NO'}
    else:
        body = {'category': 'USDT-FUTURES', 'symbol': symbol, 'marginMode': 'isolated', 'side': side, 'orderType': 'market',
                'qty': quantity, 'clientOid': client_id, 'reduceOnly': 'yes' if reduce else 'no'}
    value = _transport(credentials, 'POST', ORDER_PATHS[flavor], body=body)
    if flavor == 'okx':
        if not isinstance(value, list) or len(value) != 1 or str(value[0].get('sCode')) != '0':
            raise DemoAPIError('官方模拟订单未取得明确接受结果，请查单')
        value = value[0]
    if not isinstance(value, dict):
        raise DemoAPIError('官方模拟订单响应格式无效，请查单')
    order_id = value.get('ordId' if flavor == 'okx' else 'orderId')
    if not isinstance(order_id, str) or not order_id or len(order_id) > 80:
        raise DemoAPIError('官方模拟订单未返回有效订单号，请查单')
    return order_id


def reconcile(credentials, symbol, client_id):
    flavor = mode(credentials)
    if flavor == 'okx':
        value = _transport(credentials, 'GET', '/api/v5/trade/order', params={
            'instId': instrument(symbol), 'clOrdId': client_id})
        if not isinstance(value, list) or len(value) != 1:
            raise DemoAPIError('暂未查到明确订单，请稍后继续对账')
        value = value[0]
    elif flavor == 'classic':
        value = _transport(credentials, 'GET', '/api/v2/mix/order/detail', params={
            'symbol': symbol, 'productType': 'USDT-FUTURES', 'clientOid': client_id})
    else:
        value = _transport(credentials, 'GET', '/api/v3/trade/order-info', params={'clientOid': client_id})
    if not isinstance(value, dict) or value.get('clOrdId' if flavor == 'okx' else 'clientOid') != client_id:
        raise DemoAPIError('查单标识不匹配，保留待确认状态')
    raw = value.get('state' if flavor == 'okx' else 'status' if flavor == 'classic' else 'orderStatus')
    if raw not in {'live', 'partially_filled', 'filled', 'canceled', 'cancelled',
                   'partially-filled', 'new', 'partially_filled_canceled', 'rejected'}:
        raise DemoAPIError('订单状态暂不可识别，保留待确认状态')
    return str(raw)


def snapshot(credentials):
    value = demo_accounts.snapshot(credentials)
    if value.get('stale') or value.get('error'):
        raise DemoAPIError('官方模拟账户快照过期，暂停新增指令')
    return value
