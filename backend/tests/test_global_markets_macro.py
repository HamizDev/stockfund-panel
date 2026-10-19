import asyncio
from datetime import date

import pytest
from app.custom.global_markets import macro, service
from app.custom.global_markets.catalog import MarketDataError


def test_treasury_orders_by_source_date_preserves_missing_rates_and_basis_points():
    rows = macro.parse_treasury('Date,1 Mo,2 Yr,10 Yr,30 Yr\n10/15/2026,4.1,4.77,5.10,\n10/16/2026,4.12,4.74,5.09,N/A\n', date(2026, 10, 18))
    result = macro.snapshot('us', rows)
    assert result['date'] == '2026-10-16' and result['unit'] == 'percent'
    items = {item['id']: item for item in result['items']}
    assert items['10y']['value'] == 5.09
    assert items['10y']['change_bp'] == -1
    assert items['30y']['value'] is None
    assert result['spread_bp'] == 35


@pytest.mark.parametrize('text', [
    'Date,2 Yr,10 Yr\n10/16/2026,nan,5\n',
    'Date,2 Yr,10 Yr\n10/16/2026,4,5\n10/16/2026,4,5\n',
    'Date,2 Yr,10 Yr\n10/19/2026,4,5\n',
    '<html>blocked</html>',
    'Date,2 Yr,10 Yr\n10/16/2026,,\n',
])
def test_treasury_rejects_corrupt_duplicate_future_or_empty_data(text):
    with pytest.raises(MarketDataError):
        macro.parse_treasury(text, date(2026, 10, 18))


def test_china_fr_fields_are_rates_not_prices_or_hk_benchmarks():
    rows = macro.parse_fixing('\n2026-10-16,,,,,,1.32,1.4,1.42\n2026-10-15,,,,,,1.33,1.4,1.43\n', date(2026, 10, 18))
    result = macro.snapshot('cn', rows)
    assert result['market'] == 'cn' and result['currency'] == 'CNY'
    assert result['items'][0] == {'id': 'FR001', 'label': '隔夜 FR001', 'value': 1.32, 'previous_value': 1.33, 'change_bp': -1.0}
    assert result['spread_bp'] is None


@pytest.mark.parametrize('text', [
    '2026-10-16,,,,,,1.3,,1.4', '2026-10-16,x,,,,,1,2,3',
    '2026-10-16,,,,,,1,inf,3', '2026-10-19,,,,,,1,2,3',
])
def test_fixing_rejects_partial_or_corrupt_rows(text):
    with pytest.raises(MarketDataError):
        macro.parse_fixing(text, date(2026, 10, 18))


def test_macro_shared_cache_retains_truthful_date_and_warns_when_refresh_fails(monkeypatch):
    key = ('macro', 'cn')
    for cache in (service._CACHE, service._FAILURES, service._PENDING):
        cache.pop(key, None)
    clock = [100.0]
    monkeypatch.setattr(service.time, 'monotonic', lambda: clock[0])
    calls = []

    def fetch(market):
        calls.append(market)
        if len(calls) > 1:
            raise MarketDataError('官方源读取失败')
        return {'market': 'cn', 'date': '2026-10-16', 'cached': False, 'stale': False}

    monkeypatch.setattr(macro, 'fetch', fetch)
    try:
        first = macro.get('cn')
        second = macro.get('cn')
        assert first['cached'] is False and second['cached'] is True and len(calls) == 1
        clock[0] += 3601
        failed = macro.get('cn')
        assert failed['date'] == '2026-10-16' and failed['stale'] is True and failed['error']
        with pytest.raises(ValueError):
            macro.get('hk')
    finally:
        for cache in (service._CACHE, service._FAILURES, service._PENDING):
            cache.pop(key, None)


def test_total_timeout_cancels_a_slow_stream_and_closes_the_client(monkeypatch):
    closed = []
    class Response:
        status_code = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): closed.append('stream')
        async def aiter_bytes(self):
            yield b'Date,2 Yr,10 Yr\n'
            await asyncio.sleep(5)
            yield b'10/16/2026,4,5'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): closed.append('client')
        def stream(self, *args, **kwargs): return Response()
    monkeypatch.setattr(macro.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(macro, 'TOTAL_TIMEOUT_S', 0.01)
    with pytest.raises(MarketDataError):
        macro._text('https://home.treasury.gov/test')
    assert closed == ['stream', 'client']
