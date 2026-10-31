from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import stock_analysis
from app.market_time import CN_TZ
from app.services import research_outcomes, stock_reports


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(stock_analysis.router)
    return TestClient(app), tmp_path


def test_empty_list_is_read_only(client):
    c, folder = client
    response = c.get('/api/stock-analysis/research/records')
    assert response.status_code == 200 and response.json()['total'] == 0
    assert not (folder / 'research_decisions').exists()


def test_research_link_cannot_attach_another_report(client, monkeypatch):
    c, folder = client
    saved = research_outcomes.save_decision(folder, symbol='515880.SH', asset_type='etf',
        content='原报告', requested_at=datetime(2026, 10, 27, 14, tzinfo=CN_TZ),
        horizon_sessions=1, reference_price=.5, snapshot={}, evidence={}, portfolio={}, review={})
    writes = []
    monkeypatch.setattr(stock_reports, 'save_report', lambda record: writes.append(record) or record)
    invalid = c.post('/api/stock-analysis/reports', json=dict(symbol='515880.SH', content='伪装报告', research_id=saved['id']))
    assert invalid.status_code == 409 and writes == []
    good = c.post('/api/stock-analysis/reports', json=dict(symbol='515880.SH', content='原报告', research_id=saved['id']))
    assert good.status_code == 200 and len(writes) == 1


@pytest.mark.parametrize('horizon', [True, '1', 0, 2, 6])
def test_invalid_observation_window_fails_before_ai(client, horizon):
    c, _ = client
    assert c.post('/api/stock-analysis/analyze', json=dict(symbol='515880.SH', horizon_sessions=horizon)).status_code == 422


def test_invalid_archive_path_does_not_disclose_machine_path(client):
    c, _ = client
    response = c.get('/api/stock-analysis/research/records/not-a-record')
    assert response.status_code == 409 and 'D:' not in response.text
