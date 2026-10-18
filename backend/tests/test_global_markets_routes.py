from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.global_markets import routes


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
