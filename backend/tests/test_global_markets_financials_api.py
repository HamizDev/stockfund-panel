from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.global_markets import financials
from app.custom.global_markets.catalog import MarketDataError
from app.custom.global_markets.routes import build_router


def test_financials_route_validates_context_and_safe_upstream_errors(monkeypatch):
    app = FastAPI()
    app.include_router(build_router())
    calls = []

    def get(*args):
        calls.append(args)
        return {'market': args[0], 'symbol': args[1], 'table': args[2], 'items': []}

    monkeypatch.setattr(financials, 'get', get)
    root = '/api/custom/global-markets/financials'
    with TestClient(app) as client:
        response = client.get(root, params={'market': 'hk', 'symbol': '00700.HK', 'table': 'cashflow'})
        assert response.status_code == 200
        assert response.json()['items'] == []
        assert calls == [('hk', '00700.HK', 'cashflow')]
        for query in ({'market': 'cn', 'symbol': '600519.SH'},
                      {'market': 'us', 'symbol': 'AAPL', 'table': 'all'},
                      {'market': 'us', 'symbol': 'A' * 21}):
            assert client.get(root, params=query).status_code == 422
        monkeypatch.setattr(financials, 'get', lambda *a: (_ for _ in ()).throw(MarketDataError('财务暂时不可用')))
        response = client.get(root, params={'market': 'us', 'symbol': 'AAPL'})
        assert response.status_code == 503 and response.json()['detail'] == '财务暂时不可用'


def test_cross_market_financial_identifier_is_rejected():
    app = FastAPI()
    app.include_router(build_router())
    with TestClient(app) as client:
        assert client.get('/api/custom/global-markets/financials?market=us&symbol=00700.HK').status_code == 422
