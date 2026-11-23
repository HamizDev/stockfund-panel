"""Raw daily response validation uses mocked HTTP, never real credentials/data."""
import json
from contextlib import contextmanager

import httpx
import pytest

from app.plugins.fuyao.client import FuyaoClient, FuyaoError

STAMP = 1767283200000
SYMBOL = '600000.SH'


def bar():
    return dict(date_ms=STAMP, open_price=10, high_price=11, low_price=9,
                close_price=10.5, volume=1000, turnover=10500)


def response(bars):
    return {'code': 0, 'data': {'interval': '1d', 'adjust': 'none',
            'item': [{'thscode': SYMBOL, 'item': bars}]}}


@contextmanager
def client_for(payload):
    client = FuyaoClient('test-only-placeholder')
    client._http.close()
    client._http = httpx.Client(base_url='https://fuyao.aicubes.cn', transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=json.dumps(payload).encode('utf8'),
                                headers={'content-type': 'application/json'})))
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize(('field', 'value'), [
    ('open_price', None), ('close_price', True), ('high_price', float('inf')),
    ('low_price', float('nan')), ('volume', -1), ('turnover', -1), ('volume', None),
    ('high_price', 9.5), ('low_price', 10.2), ('date_ms', True), ('date_ms', STAMP + 1),
])
def test_batch_rejects_invalid_raw_values(field, value):
    record = bar()
    record[field] = value
    with client_for(response([record])) as client, pytest.raises(FuyaoError):
        client.historical_kline_batch([SYMBOL], STAMP, STAMP)


def test_batch_rejects_duplicate_dates():
    with client_for(response([bar(), bar()])) as client, pytest.raises(FuyaoError):
        client.historical_kline_batch([SYMBOL], STAMP, STAMP)


@pytest.mark.parametrize(('field', 'value'), [('interval', '1m'), ('adjust', 'qfq')])
def test_batch_rejects_wrong_price_basis_or_period(field, value):
    payload = response([bar()])
    payload['data'][field] = value
    with client_for(payload) as client, pytest.raises(FuyaoError):
        client.historical_kline_batch([SYMBOL], STAMP, STAMP)


def test_batch_empty_group_is_distinct_from_missing_group():
    with client_for(response([])) as client:
        assert client.historical_kline_batch([SYMBOL], STAMP, STAMP) == {SYMBOL: []}
    payload = response([])
    payload['data']['item'] = []
    with client_for(payload) as client:
        assert client.historical_kline_batch([SYMBOL], STAMP, STAMP) == {}


def test_batch_raw_units_are_not_reinterpreted_by_client():
    with client_for(response([bar()])) as client:
        row = client.historical_kline_batch([SYMBOL], STAMP, STAMP)[SYMBOL][0]
        assert row['volume'] == 1000 and row['turnover'] == 10500


@pytest.mark.parametrize('halt_close', [0, 10.5])
def test_batch_normal_halt_record_can_reach_existing_halt_filter(halt_close):
    record = {**bar(), 'open_price': 0, 'high_price': 0, 'low_price': 0,
              'close_price': halt_close, 'volume': 0, 'turnover': 0}
    with client_for(response([record])) as client:
        assert client.historical_kline_batch([SYMBOL], STAMP, STAMP)[SYMBOL] == [record]
