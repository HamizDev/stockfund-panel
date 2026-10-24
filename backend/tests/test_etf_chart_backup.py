"""ETF chart backup stays separate from global sources and strategy feeds."""
from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_analysis_current_session import _minutes, _provider, _use_provider

from app.api import kline
from app.market_time import CN_TZ
from app.services import analysis_current_session, preferences
from app.services.minute_response_cache import MinuteResponseCache

DAY = date(2026, 10, 23)
SYMBOL = '515880.SH'


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    frame = _minutes(day=DAY, include_afternoon=True).with_columns(pl.lit(None, dtype=pl.Float64).alias('amount'))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    captured = []
    monkeypatch.setattr(kline, 'cn_today', lambda: date(2026, 10, 24))
    monkeypatch.setattr(kline, 'cn_now', lambda: datetime(2026, 10, 24, 0, 3, tzinfo=CN_TZ))
    monkeypatch.setattr(preferences, 'get_minute_data_provider', lambda: 'stocksdk')
    monkeypatch.setattr(kline, '_fetch_minute_fallback', lambda *_a: pl.DataFrame())
    def load(_repo, _caps, symbol, asset_type, now, **kwargs):
        captured.append((symbol, asset_type, now, kwargs))
        kwargs['on_minutes'](frame)
        return {'close': frame['close'][-1]}, '2026-10-23T15:00:00+08:00'
    monkeypatch.setattr(analysis_current_session, 'load_current', load)
    return repo, frame, captured


def test_closed_etf_chart_uses_full_dated_backup_with_provenance(inputs):
    repo, original, calls = inputs
    frame, meta = kline._fetch_chart_minute(repo, SYMBOL, DAY, 'etf', None, True, pl.DataFrame())
    assert frame.equals(original) and frame.height == 241
    assert frame['amount'].null_count() == frame.height
    assert meta['minute_provider'] == 'txquote' and '成交额' in meta['minute_warning']
    assert calls[0][2] == datetime(2026, 10, 23, 15, 30, tzinfo=CN_TZ)
    assert calls[0][3]['minute_limit'] == 300 and calls[0][3]['require_latest']
    assert preferences.get_minute_data_provider() == 'stocksdk'


def test_current_session_backup_preserves_actual_cutoff(inputs, monkeypatch):
    repo, _, calls = inputs
    now = datetime(2026, 10, 23, 14, 16, 42, tzinfo=CN_TZ)
    monkeypatch.setattr(kline, 'cn_today', lambda: DAY)
    monkeypatch.setattr(kline, 'cn_now', lambda: now)
    kline._fetch_chart_minute(repo, SYMBOL, DAY, 'etf', None, False, pl.DataFrame())
    assert calls[0][2] == now and calls[0][3]['require_latest'] is False


@pytest.mark.parametrize('asset,provider,day', [('stock', 'stocksdk', DAY),
    ('index', 'stocksdk', DAY), ('etf', 'another-source', DAY), ('etf', 'stocksdk', date(2026, 10, 25))])
def test_backup_does_not_change_other_assets_sources_or_future_dates(inputs, monkeypatch, asset, provider, day):
    repo, _, calls = inputs
    monkeypatch.setattr(preferences, 'get_minute_data_provider', lambda: provider)
    frame, _meta = kline._fetch_chart_minute(repo, SYMBOL, day, asset, None, True, pl.DataFrame())
    assert frame.is_empty() and calls == []


def test_nonempty_primary_is_not_replaced(inputs, monkeypatch):
    repo, original, calls = inputs
    monkeypatch.setattr(kline, '_fetch_minute_fallback', lambda *_a: original)
    frame, meta = kline._fetch_chart_minute(repo, SYMBOL, DAY, 'etf', None, True, pl.DataFrame())
    assert frame.equals(original) and meta == {} and calls == []


def test_failed_backup_does_not_invent_rows_or_label_it_as_used(inputs, monkeypatch):
    repo, _, _ = inputs
    monkeypatch.setattr(analysis_current_session, 'load_current', lambda *_a, **_k: (None, None))
    frame, meta = kline._fetch_chart_minute(repo, SYMBOL, DAY, 'etf', None, True, pl.DataFrame())
    assert frame.is_empty() and 'minute_provider' not in meta


def test_chart_api_exposes_backup_warning_and_real_rows(inputs, monkeypatch):
    repo, original, _ = inputs
    repo.resolve_asset_type = lambda _symbol: 'etf'
    repo.get_minute = lambda *_a, **_k: pl.DataFrame()
    monkeypatch.setattr(kline, '_get_asset_info', lambda *_a: {'name': '通信ETF国泰'})
    monkeypatch.setattr(kline, '_get_previous_closes', lambda *_a: {DAY: .503})
    monkeypatch.setattr(kline, '_get_price_limit_info', lambda *_a: None)
    monkeypatch.setattr(kline.minute_reference, 'resolve', lambda *_a: {'prev_close': .503})
    monkeypatch.setattr(kline.minute_adjust, 'minute_basis_is_raw', lambda *_a: False)
    app = FastAPI()
    app.state.repo, app.state.capabilities = repo, None
    app.include_router(kline.router)
    with TestClient(app) as client:
        response = client.get('/api/kline/minute?symbol=515880.SH&date=2026-10-23&live=1')
    body = response.json()
    assert response.status_code == 200 and body['date'] == '2026-10-23'
    assert body['source'] == 'live' and body['minute_provider'] == 'txquote'
    assert len(body['rows']) == original.height and body['rows'][-1]['datetime'].startswith('2026-10-23')
    assert body['prev_close'] == .503 and body['rows'][0]['amount'] is None


def test_shared_loader_keeps_default_sixty_but_can_return_full_chart(tmp_path, monkeypatch):
    frame = _minutes(include_afternoon=True)
    calls = []
    def get(*_a, **_k):
        calls.append(True)
        return frame
    provider = _provider(get)
    _use_provider(monkeypatch, ['stocksdk'], lambda _: provider)
    monkeypatch.setattr(analysis_current_session.trading_calendar, 'is_trading_day', lambda *_a, **_k: True)
    monkeypatch.setattr(analysis_current_session, '_CACHE', MinuteResponseCache(auto_expire=False))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    captured = []
    for options in ({}, {'minute_limit': 300}):
        analysis_current_session.load_current(repo, None, SYMBOL, 'etf',
            datetime(2026, 10, 22, 15, 30), on_minutes=captured.append, **options)
    assert [row.height for row in captured] == [60, 241] and len(calls) == 1
