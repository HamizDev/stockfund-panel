from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.global_markets import routes
from app.custom.global_markets.catalog import MarketDataError


def test_industries_route_is_read_only_and_passes_validated_mode_and_repository(monkeypatch):
    app = FastAPI()
    sentinel = object()
    app.state.repo = sentinel
    calls = []

    def get(mode, repo):
        calls.append((mode, repo))
        return {'mode': mode, 'basis': 'equal_weight_representative_basket', 'themes': []}

    monkeypatch.setattr(routes.industries, 'get', get)
    app.include_router(routes.build_router())
    with TestClient(app) as client:
        for mode in ['auto', 'cn', 'us']:
            response = client.get('/api/custom/global-markets/industries', params={'mode': mode})
            assert response.status_code == 200 and response.json()['mode'] == mode
        assert client.get('/api/custom/global-markets/industries?mode=hk').status_code == 422
        assert client.post('/api/custom/global-markets/industries').status_code == 405
    assert calls == [(mode, sentinel) for mode in ['auto', 'cn', 'us']]


def test_macro_route_validates_region_is_get_only_and_sanitizes_provider_failure(monkeypatch):
    app = FastAPI()
    app.include_router(routes.build_router())
    monkeypatch.setattr(routes.macro, 'get', lambda market: {'market': market, 'unit': 'percent'})
    with TestClient(app) as client:
        assert client.get('/api/custom/global-markets/macro?market=us').json()['market'] == 'us'
        assert client.get('/api/custom/global-markets/macro?market=hk').status_code == 422
        assert client.post('/api/custom/global-markets/macro?market=us').status_code == 405
        def fail(market):
            raise MarketDataError('官方利率源读取失败')
        monkeypatch.setattr(routes.macro, 'get', fail)
        assert client.get('/api/custom/global-markets/macro?market=cn').status_code == 503


def test_etf_shares_route_is_read_only_and_rejects_noncanonical_symbols(monkeypatch):
    app = FastAPI()
    app.include_router(routes.build_router())
    calls = []
    def get(symbol, day):
        calls.append((symbol, day))
        if symbol != '515880.SH':
            raise ValueError('ETF代码格式无效')
        return {'symbol': symbol, 'date': day, 'shares_10k': 1234}
    monkeypatch.setattr(routes.etf_shares, 'get', get)
    with TestClient(app) as client:
        assert client.get('/api/custom/global-markets/etf-shares?symbol=515880.SH&date=2026-10-16').json()['shares_10k'] == 1234
        assert client.get('/api/custom/global-markets/etf-shares?symbol=foo').status_code == 422
        assert client.post('/api/custom/global-markets/etf-shares?symbol=515880.SH').status_code == 405
    assert calls[0] == ('515880.SH', '2026-10-16')
