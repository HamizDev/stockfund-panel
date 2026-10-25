

import base64
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.research_tools import routes


def client(tmp_path):
    app=FastAPI()
    app.state.repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(routes.build_router())
    return TestClient(app)


def test_snapshot_get_never_creates_files_or_fetches(tmp_path,monkeypatch):
    monkeypatch.setattr(routes.industry,'fetch',lambda *_: (_ for _ in ()).throw(AssertionError('GET must not fetch')))
    with client(tmp_path) as session:
        result=session.get('/api/custom/research-tools/snapshot?dataset=gpu_rent')
        assert result.status_code==200 and result.json()['status']=='idle'
    assert not (tmp_path/'research_tools').exists()


def test_library_wire_shape_and_search_citation(tmp_path):
    with client(tmp_path) as session:
        payload={'name':'研究笔记.md','content':base64.b64encode('通信ETF的行业风险核对'.encode()).decode()}
        added=session.post('/api/custom/research-tools/library',json=payload)
        assert added.status_code==200
        assert 'id' in added.json() and added.json()['duplicate'] is False
        assert session.post('/api/custom/research-tools/library',json=payload).json()['duplicate'] is True
        assert session.get('/api/custom/research-tools/library').json()['items'][0]['name']=='研究笔记.md'
        hit=session.get('/api/custom/research-tools/library/search',params={'q':'通信ETF'}).json()['items'][0]
        assert '通信ETF' in hit['snippet'] and '研究笔记' in hit['citation']


def test_upload_validation_does_not_echo_document_or_invalid_content(tmp_path):
    with client(tmp_path) as session:
        response=session.post('/api/custom/research-tools/library',json={'name':'synthetic-private-name','content':'<notbase64>'})
        assert response.status_code==422 and 'synthetic-private-name' not in response.text
        assert session.get('/api/custom/research-tools/snapshot',params={'dataset':'dividends','market':'us','symbol':'AAPL'}).status_code==422
        assert session.get('/api/custom/research-tools/library/search',params={'q':'x'*121}).status_code==422
