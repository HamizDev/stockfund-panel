from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import httpx
import pytest

from app.custom.global_markets import market_snapshot as ms
from app.custom.global_markets.catalog import MarketDataError

STAMP = int(datetime(2026, 10, 16, 8, tzinfo=UTC).timestamp())


def raw(symbol='00700', market=128, **extra):
    return {'f12': symbol, 'f13': market, 'f14': '测试公司', 'f2': 100, 'f3': 3.66,
            'f5': 500, 'f6': 50000, 'f8': .89, 'f100': '科技', 'f124': STAMP, **extra}


@pytest.fixture(autouse=True)
def isolated_state():
    ms._STATES.clear()
    yield
    deadline = time.monotonic() + 2
    while any(row['loading'] for row in ms._STATES.values()) and time.monotonic() < deadline:
        time.sleep(.01)
    assert not any(row['loading'] for row in ms._STATES.values())
    ms._STATES.clear()


def test_explicit_market_percent_units_and_missing_source_times():
    row = ms.parse_row('hk', raw(), STAMP + 10)
    assert row['symbol'] == '00700.HK' and row['change_pct'] == pytest.approx(.0366)
    assert row['turnover_rate'] == pytest.approx(.0089)
    assert row['volume'] == 500 and row['amount'] == 50000 and row['quote_at_ms'] == STAMP * 1000
    assert ms.parse_row('us', raw(), STAMP + 10) is None
    missing = ms.parse_row('hk', raw(f3='-', f2=0, f8='-', f124=STAMP + 900), STAMP + 10)
    assert missing['price'] is None and missing['change_pct'] is None and missing['quote_at_ms'] is None
    assert ms.parse_row('us', raw('AAPL', 105), STAMP + 10)['kind'] == 'security'


def test_breadth_uses_same_quote_day_and_real_prices_only_with_equal_industry_weight():
    rows = [ms.parse_row('hk', raw(str(700 + i), f3=pct), STAMP + 10) for i, pct in enumerate([5, -3, 0])]
    rows += [ms.parse_row('hk', raw('900', f3=90, f124=STAMP - 86400), STAMP + 10),
             ms.parse_row('hk', raw('901', f3=90, f2=0), STAMP + 10)]
    result = ms.summarize('hk', rows, 5)
    assert result['date'] == '2026-10-16' and result['stats']['covered'] == 3
    assert (result['stats']['up'], result['stats']['flat'], result['stats']['down']) == (1, 1, 1)
    assert result['stats']['mean'] == pytest.approx(.02 / 3)
    assert sum(result['stats']['bins']) == 3 and result['stats']['bins'][7] == 1
    assert result['industries'][0]['change_pct'] == pytest.approx(.02 / 3)
    assert result['ranks']['gainers'][0]['symbol'] == '00700.HK'


def test_pagination_completes_before_publish_and_rejects_duplicate_or_changed_universe(monkeypatch):
    monkeypatch.setattr(ms, 'PAGE_SIZE', 2)
    monkeypatch.setattr(ms.time, 'sleep', lambda _: None)
    calls = []
    def page(_client, params):
        calls.append(params)
        return {'total': 3, 'diff': [raw('700'), raw('701')] if params['pn'] == 1 else [raw('702')]}
    monkeypatch.setattr(ms, '_read_page', page)
    result, quotes = ms.fetch('hk')
    assert result['complete'] and result['received'] == 3 and len(quotes) == 3
    assert [call['pn'] for call in calls] == [1, 2]
    assert all(call['fid'] == 'f12' and call['fs'] == 'm:128 t:3' for call in calls)
    monkeypatch.setattr(ms, '_read_page', lambda _client, params: {'total': 3, 'diff': [raw('700'), raw('701')] if params['pn'] == 1 else [raw('700')]})
    with pytest.raises(MarketDataError, match='重复'):
        ms.fetch('hk')
    monkeypatch.setattr(ms, '_read_page', lambda _client, params: {'total': 3 if params['pn'] == 1 else 4, 'diff': [raw('700'), raw('701')]})
    with pytest.raises(MarketDataError, match='总数'):
        ms.fetch('hk')


def test_refresh_is_nonblocking_atomic_and_keeps_last_complete_snapshot_on_failure(monkeypatch):
    release = threading.Event()
    def fetch(_market):
        assert release.wait(1)
        return {'generation': 2, 'scope': 'us_listed_securities'}, {'AAPL': {'price': 20}}
    ms._STATES['us'] = {'data': {'generation': 1, 'scope': 'us_listed_securities'}, 'quotes': {'AAPL': {'price': 10}},
                        'error': None, 'stale': False, 'loading': False, 'completed': -float('inf')}
    monkeypatch.setattr(ms, 'fetch', fetch)
    start = time.monotonic()
    first = ms.get('us')
    assert time.monotonic() - start < .5 and first['data']['generation'] == 1 and first['loading']
    _, subset = ms.quotes('us', {'AAPL'})
    assert subset == {'AAPL': {'price': 10}}
    release.set()
    deadline = time.monotonic() + 1
    while ms.get('us')['loading'] and time.monotonic() < deadline:
        time.sleep(.01)
    result = ms.get('us')
    assert result['data']['generation'] == 2
    result['data']['generation'] = 100
    assert ms.get('us')['data']['generation'] == 2
    monkeypatch.setattr(ms, 'fetch', lambda _: (_ for _ in ()).throw(MarketDataError('分页失败')))
    ms._STATES['us']['completed'] -= 400
    ms.get('us')
    deadline = time.monotonic() + 1
    while ms.get('us')['loading'] and time.monotonic() < deadline:
        time.sleep(.01)
    failed = ms.get('us')
    assert failed['stale'] and failed['error'] == '分页失败' and failed['data']['generation'] == 2


def test_transport_disconnect_retries_are_bounded_but_http_refusal_is_not_retried(monkeypatch):
    monkeypatch.setattr(ms.time, 'sleep', lambda _: None)
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            raise httpx.RemoteProtocolError('closed', request=request)
        return httpx.Response(200, json={'data': {'total': 1, 'diff': [raw()]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert ms._read_page(client, {'pn': 1})['total'] == 1
    assert len(calls) == 3
    calls.clear()
    def refused(request):
        calls.append(request)
        return httpx.Response(429)
    with httpx.Client(transport=httpx.MockTransport(refused)) as client, pytest.raises(MarketDataError, match='限流'):
        ms._read_page(client, {'pn': 1})
    assert len(calls) == 1


def test_us_initial_full_source_failure_publishes_explicit_capped_pool_not_full_market(monkeypatch):
    from app.custom.global_markets import us_screeners
    monkeypatch.setattr(ms, 'fetch', lambda _: (_ for _ in ()).throw(MarketDataError('断连')))
    row = ms.parse_row('us', raw('AAPL', 105), STAMP + 10)
    row['source'] = 'yahoo_screener'
    monkeypatch.setattr(us_screeners, 'fetch_us_screener_pool', lambda: {'quotes': [row]})
    state = {'data': None, 'quotes': {}, 'error': None, 'stale': False, 'loading': True}
    ms._load('us', state)
    assert state['data']['source'] == 'yahoo_screener'
    assert state['data']['scope'] == 'us_screener_pool'
    assert state['data']['warnings'] and state['data']['total_reported'] == 1
    assert not state['loading'] and state['error'] is None


def test_us_pool_enrichment_keeps_quote_source_date_and_real_volume_rank(monkeypatch):
    from app.custom.global_markets import us_classification, us_screeners
    monkeypatch.setattr(ms, 'fetch', lambda _: (_ for _ in ()).throw(MarketDataError('offline')))
    row = ms.parse_row('us', raw('AAPL', 105, f100=None, f6=None, f8=None), STAMP + 10)
    row.update(source='yahoo_screener', market_cap=1e12)
    original = {key: row[key] for key in ('price', 'change_pct', 'volume', 'quote_at_ms', 'source')}
    monkeypatch.setattr(us_screeners, 'fetch_us_screener_pool', lambda: {'quotes': [row]})
    def enrich(rows):
        rows[0].update(industry='Computer Manufacturing', industry_source='nasdaq_screener')
        return {'source': 'nasdaq_screener', 'source_as_of': None, 'matched': 1, 'error': None}
    monkeypatch.setattr(us_classification, 'enrich', enrich)
    state = {'data': None, 'quotes': {}, 'error': None, 'stale': False, 'loading': True}
    ms._load('us', state)
    data = state['data']
    assert data['source'] == 'yahoo_screener' and data['scope'] == 'us_screener_pool'
    assert data['date'] == '2026-10-16' and data['industry_covered'] == 1
    assert data['industries'][0]['name'] == 'Computer Manufacturing'
    assert data['ranks']['volume'][0]['volume'] == 500
    assert data['ranks']['market_cap'][0]['market_cap'] == 1e12
    assert not data['ranks']['amount'] and not data['ranks']['turnover']
    assert {key: state['quotes']['AAPL'][key] for key in original} == original
