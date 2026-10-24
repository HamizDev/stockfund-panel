from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.custom.global_markets import hk_sina_snapshot as sina
from app.custom.global_markets import market_snapshot as ms
from app.custom.global_markets import service
from app.custom.global_markets.catalog import MarketDataError

NOW = datetime(2026, 10, 20, tzinfo=UTC).timestamp()
DIRECTORY = {'00700.HK': {'symbol': '00700.HK', 'currency': 'HKD', 'kind': 'stock', 'name': '腾讯控股'},
             '02800.HK': {'symbol': '02800.HK', 'currency': 'HKD', 'kind': 'etf', 'name': '盈富基金'}}


def raw(code='00700', **extra):
    return {'symbol': code, 'name': 'ignored provider name', 'lasttrade': '417.8',
            'changepercent': '-1.5', 'amount': '12345', 'volume': '999',
            'ticktime': '2026/10/16 16:08:20', **extra}


def test_joins_verified_hkd_identity_and_keeps_real_date_units_and_missing_fields():
    row = sina.parse_row(raw(), DIRECTORY, NOW)
    assert row['name'] == '腾讯控股' and row['currency'] == 'HKD' and row['kind'] == 'stock'
    assert row['price'] == 417.8 and row['change_pct'] == -.015 and row['amount'] == 12345
    assert row['quote_at_ms'] == int(datetime(2026, 10, 16, 8, 8, 20, tzinfo=UTC).timestamp() * 1000)
    assert row['volume'] is None and row['industry'] is None and row['turnover_rate'] is None
    assert row['source'] == 'sina_hk_public'
    assert sina.parse_row(raw('02800'), DIRECTORY, NOW)['kind'] == 'etf'
    assert sina.parse_row(raw('80700'), DIRECTORY, NOW) is None
    assert sina.parse_row(raw('00700'), {'00700.HK': {**DIRECTORY['00700.HK'], 'currency': 'CNY'}}, NOW) is None


@pytest.mark.parametrize('extra', [
    {'ticktime': '16:08:20'}, {'ticktime': '2026/02/30 16:00:00'},
    {'ticktime': '2027/01/01 16:00:00'}, {'lasttrade': 'NaN'}, {'lasttrade': True},
    {'changepercent': '-'}, {'symbol': 'AAPL'}, {'symbol': '700'},
])
def test_rejects_missing_or_future_dates_invalid_prices_and_ambiguous_symbols(extra):
    assert sina.parse_row(raw(**extra), DIRECTORY, NOW) is None


def test_gbk_response_and_http_refusal_without_retry():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=json.dumps([raw(name='腾讯')], ensure_ascii=False).encode('gbk'),
                              headers={'Content-Type': 'application/json; charset=gbk'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert sina._read_page(client, 1)[0]['name'] == '腾讯'
    assert len(calls) == 1 and calls[0].url.params['node'] == 'qbgg_hk'
    calls.clear()
    def refused(request):
        calls.append(request)
        return httpx.Response(429)
    with httpx.Client(transport=httpx.MockTransport(refused)) as client, pytest.raises(MarketDataError):
        sina._read_page(client, 1)
    assert len(calls) == 1


def test_atomic_pagination_requires_terminal_page_and_rejects_repeats_or_gaps(monkeypatch):
    monkeypatch.setattr(service, 'hk_directory', lambda: list(DIRECTORY.values()))
    monkeypatch.setattr(sina, 'PAGE_SIZE', 2)
    monkeypatch.setattr(sina.time, 'sleep', lambda _: None)
    monkeypatch.setattr(sina, '_read_page', lambda _, page: [raw(), raw('02800')] if page == 1 else [])
    rows, total = sina.fetch_pool()
    assert len(rows) == total == 2
    monkeypatch.setattr(sina, '_read_page', lambda _, page: [raw()] if page == 1 else [raw('02800')])
    with pytest.raises(MarketDataError, match='缺记录'):
        sina.fetch_pool()
    monkeypatch.setattr(sina, '_read_page', lambda _, page: [raw(), raw('02800')])
    with pytest.raises(MarketDataError, match='重复'):
        sina.fetch_pool()
    monkeypatch.setattr(sina, '_read_page', lambda _, page: [raw(), raw('02800')] if page == 1 else (_ for _ in ()).throw(httpx.ReadTimeout('offline')))
    with pytest.raises(MarketDataError, match='未发布部分'):
        sina.fetch_pool()


def test_hk_source_failure_fills_only_explicit_verified_pool(monkeypatch):
    from app.custom.global_markets import hk_tencent_etf as tencent

    monkeypatch.setattr(ms, 'fetch', lambda _: (_ for _ in ()).throw(MarketDataError('offline')))
    row = sina.parse_row(raw(), DIRECTORY, NOW)
    monkeypatch.setattr(sina, 'fetch_pool', lambda: ([row], 3))
    monkeypatch.setattr(tencent, 'fetch_etf_pool', lambda: (_ for _ in ()).throw(MarketDataError('offline')))
    state = {'data': None, 'quotes': {}, 'error': None, 'stale': False, 'loading': True}
    ms._load('hk', state)
    assert state['error'] is None and not state['loading']
    data = state['data']
    assert data['scope'] == 'hk_sina_hkd_pool' and data['source'] == 'sina_hk_public'
    assert data['date'] == '2026-10-16' and data['skipped'] == 2 and data['stats']['covered'] == 1
    assert data['industry_covered'] == 0 and not data['ranks']['turnover'] and data['warnings']
    assert data['ranks']['amount'][0]['amount'] == 12345
    # Preserve a previously complete main-board universe instead of silently
    # switching the meaning of its time series to the smaller backup pool.
    state['data'] = {'scope': 'hk_mainboard', 'generation': 1}
    ms._load('hk', state)
    assert state['stale'] and state['data']['generation'] == 1


def test_directory_success_cache_is_shared_between_consumers(monkeypatch):
    service._CACHE.clear()
    service._FAILURES.clear()
    calls = []
    monkeypatch.setattr(service.hk_symbol_directory, 'load_rows', lambda: calls.append(1) or list(DIRECTORY.values()))
    try:
        service.hk_directory()[0]['name'] = 'mutated'
        assert service.hk_directory()[0]['name'] == '腾讯控股' and len(calls) == 1
    finally:
        service._CACHE.clear()
        service._FAILURES.clear()
