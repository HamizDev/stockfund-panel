from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_global_markets_provider import fixture

from app.custom.global_markets import market_snapshot, overview, provider, service
from app.custom.global_markets.catalog import SEEDS, MarketDataError, normalize_symbol
from app.custom.global_markets.routes import build_router


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(market_snapshot, 'get', lambda market: {'data': None, 'loading': False, 'stale': False, 'error': None})
    overview._STATES.clear()
    service._CACHE.clear()
    service._FAILURES.clear()
    yield
    deadline = time.monotonic() + 3
    while any(state['remaining'] for state in overview._STATES.values()) and time.monotonic() < deadline:
        time.sleep(.01)
    assert not any(state['remaining'] for state in overview._STATES.values())


def wait_complete(market):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = overview.get(market)
        if not result['loading']:
            return result
        time.sleep(.01)
    pytest.fail('overview batch did not finish')


def test_indices_are_explicitly_scoped_and_require_index_type():
    assert normalize_symbol('hk', '^HSI') == '^HSI'
    assert normalize_symbol('us', '^GSPC') == '^GSPC'
    assert service.catalog('us', '^GSPC')['items'][0]['kind'] == 'index'
    assert service.catalog('hk', '恒生指数')['items'][0]['symbol'] == '^HSI'
    for market, symbol in [('us', '^HSI'), ('hk', '^GSPC'), ('us', '^UNKNOWN')]:
        with pytest.raises(ValueError):
            normalize_symbol(market, symbol)
    payload = fixture()
    payload['chart']['result'][0]['meta'].update(symbol='^GSPC', instrumentType='INDEX')
    result = provider.parse_chart('us', '^GSPC', '1d', payload)
    assert result['kind'] == 'index' and result['name'] == '标普500'
    payload['chart']['result'][0]['meta']['instrumentType'] = 'ETF'
    with pytest.raises(MarketDataError):
        provider.parse_chart('us', '^GSPC', '1d', payload)
    payload['chart']['result'][0]['meta'].update(symbol='AAPL', instrumentType='INDEX')
    with pytest.raises(MarketDataError):
        provider.parse_chart('us', 'AAPL', '1d', payload)


def test_hk_presets_keep_active_hkd_etfs_and_exclude_delisted_2833():
    # 2833 was delisted in 2022; Yahoo still serves its historical last quote.
    seeds = {symbol: kind for symbol, _, kind in SEEDS['hk']}
    assert '02833.HK' not in seeds
    assert seeds['02823.HK'] == 'etf'
    assert list(seeds.values()).count('stock') == 8
    assert list(seeds.values()).count('etf') == 4
    payload = fixture('hk')
    payload['chart']['result'][0]['meta'].update(symbol='2823.HK', instrumentType='ETF')
    result = provider.parse_chart('hk', '02823.HK', '1d', payload)
    assert result['kind'] == 'etf' and result['currency'] == 'HKD'
    assert result['name'] == '安硕富时中国A50ETF'


def test_quote_uses_short_window_and_never_treats_range_start_close_as_yesterday(monkeypatch):
    payload = fixture()
    meta = payload['chart']['result'][0]['meta']
    meta.pop('regularMarketChangePercent')
    meta['chartPreviousClose'] = 1
    calls = []
    monkeypatch.setattr(provider, '_json', lambda path, params: (calls.append(params) or payload))
    row = provider.fetch_quote('us', 'AAPL')
    assert calls[0]['range'] == '3mo'
    assert row['price'] == 343.46 and row['change_pct'] is None
    assert 'bars' not in row and row['kind'] == 'stock'
    meta['regularMarketChangePercent'] = -1.23
    assert provider.fetch_quote('us', 'AAPL')['change_pct'] == pytest.approx(-.0123)


def test_first_response_is_nonblocking_shared_bounded_and_partial(monkeypatch):
    release = threading.Event()
    calls, active = [], [0, 0]
    lock = threading.Lock()

    def fetch(market, symbol):
        with lock:
            calls.append((market, symbol))
            active[0] += 1
            active[1] = max(active)
        assert release.wait(2)
        with lock:
            active[0] -= 1
        return {'market': market, 'symbol': symbol, 'price': 10, 'change_pct': .01,
                'quote_at_ms': 100, 'fetched_at_ms': 200, 'stale': False, 'error': None}

    monkeypatch.setattr(provider, 'fetch_quote', fetch)
    started = time.monotonic()
    first = overview.get('hk')
    assert time.monotonic() - started < .5
    assert first['loading'] and first['scope'] == 'fixed_watchlist'
    assert len(first['items']) == 14 and all(row['price'] is None for row in first['items'])
    for _ in range(10):
        overview.get('hk')
    overview.get('us')
    release.set()
    hk, us = wait_complete('hk'), wait_complete('us')
    assert len(calls) == 30 and active[1] <= 2
    assert all(row['market'] == 'hk' for row in hk['items'])
    assert all(row['market'] == 'us' for row in us['items'])
    assert hk['items'][0]['kind'] == 'index'
    hk['items'][0]['price'] = 999
    assert overview.get('hk')['items'][0]['price'] == 10
    assert len(calls) == 30


def test_partial_failure_is_null_and_old_success_only_returns_as_explicit_stale(monkeypatch):
    def fetch(market, symbol):
        if symbol == '^HSI':
            raise MarketDataError('限流')
        return {'market': market, 'symbol': symbol, 'price': 10, 'change_pct': None,
                'quote_at_ms': 100, 'fetched_at_ms': 200, 'stale': False, 'error': None}

    monkeypatch.setattr(provider, 'fetch_quote', fetch)
    first = wait_complete('hk')
    bad = first['items'][0]
    assert bad['symbol'] == '^HSI' and bad['price'] is None and bad['error'] == '限流'
    assert bad['status'] == 'error' and all(row['price'] == 10 for row in first['items'][1:])
    assert all(row['change_pct'] is None for row in first['items'])
    monkeypatch.setattr(provider, 'fetch_quote', lambda *args: (_ for _ in ()).throw(MarketDataError('离线')))
    for key, (stamp, value) in list(service._CACHE.items()):
        service._CACHE[key] = (stamp - 100, value)
    overview._STATES['hk']['completed'] -= 100
    second = wait_complete('hk')
    assert all(row['stale'] and row['fetched_at_ms'] == 200 for row in second['items'][1:])
    assert all(row['error'] for row in second['items'])


def test_overview_api_is_readonly_and_rejects_unknown_market(monkeypatch):
    monkeypatch.setattr(overview, 'get', lambda market: {'market': market, 'items': [], 'loading': False})
    app = FastAPI()
    app.include_router(build_router())
    client = TestClient(app)
    root = '/api/custom/global-markets/overview'
    assert client.get(root + '?market=us').json()['market'] == 'us'
    assert client.get(root + '?market=cn').status_code == 422
    assert client.post(root + '?market=us').status_code == 405


def test_slow_completed_batch_gets_full_cache_window_instead_of_restarting_immediately(monkeypatch):
    calls = []

    def fetch(market, symbol):
        calls.append(symbol)
        if len(calls) == 14:
            # Simulate a batch that took longer than its refresh interval.
            overview._STATES[market]['started'] -= 100
        return {'market': market, 'symbol': symbol, 'price': 10, 'change_pct': .01,
                'quote_at_ms': 100, 'fetched_at_ms': 200, 'stale': False, 'error': None}

    monkeypatch.setattr(provider, 'fetch_quote', fetch)
    overview.get('hk')
    deadline = time.monotonic() + 2
    while overview._STATES['hk']['remaining'] and time.monotonic() < deadline:
        time.sleep(.01)
    assert not overview._STATES['hk']['remaining']
    assert not overview.get('hk')['loading']
    assert len(calls) == 14


def test_revalidation_keeps_complete_previous_rows_instead_of_flashing_zero_counts(monkeypatch):
    monkeypatch.setattr(provider, 'fetch_quote', lambda market, symbol: {
        'market': market, 'symbol': symbol, 'price': 10, 'change_pct': .01,
        'quote_at_ms': 100, 'fetched_at_ms': time.time() * 1000, 'stale': False, 'error': None})
    first = wait_complete('hk')
    gate = threading.Event()
    def blocked(market, symbol):
        assert gate.wait(1)
        return {'market': market, 'symbol': symbol, 'price': 20, 'change_pct': -.01,
                'quote_at_ms': 100, 'fetched_at_ms': time.time() * 1000, 'stale': False, 'error': None}
    monkeypatch.setattr(provider, 'fetch_quote', blocked)
    service._CACHE.clear()
    overview._STATES['hk']['completed'] -= 100
    try:
        refresh = overview.get('hk')
        assert refresh['loading'] and all(row['price'] == 10 and not row['stale'] for row in refresh['items'])
        assert all(row['refreshing'] for row in refresh['items'])
        assert first['items'][0]['price'] == 10
    finally:
        gate.set()
    final = wait_complete('hk')
    assert all(row['price'] == 20 and not row['refreshing'] for row in final['items'])
