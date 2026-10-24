"""Manual AI requests use the same current, dated frame as the daily chart."""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import stock_analysis
from app.market_time import CN_TZ
from app.services import ai_provider, analysis_snapshot, stock_analyzer

SYMBOL = '515880.SH'


def history():
    dates = [date(2026, 10, 21) - timedelta(days=i) for i in reversed(range(130))]
    return pl.DataFrame({'symbol': [SYMBOL] * len(dates), 'date': dates,
                         'open': [.535] * len(dates), 'high': [.54] * len(dates),
                         'low': [.53] * len(dates), 'close': [.535] * len(dates),
                         'volume': [1000.] * len(dates), 'amount': [535.] * len(dates)})


@pytest.fixture
def snapshot(monkeypatch, tmp_path):
    repo = SimpleNamespace(resolve_asset_type=lambda symbol: 'etf',
                           store=SimpleNamespace(data_dir=tmp_path),
                           get_daily_asset=lambda *a: pl.DataFrame())
    provider = SimpleNamespace(get_daily=lambda *a, **k: history(), daily_price_basis='raw')
    monkeypatch.setattr(analysis_snapshot, 'cn_now', lambda: datetime(2026, 10, 22, 11, 35, tzinfo=CN_TZ))
    monkeypatch.setattr(stock_analyzer, 'cn_now', analysis_snapshot.cn_now)
    monkeypatch.setattr(analysis_snapshot.preferences, 'get_daily_data_provider', lambda: 'fake')
    monkeypatch.setattr(analysis_snapshot.custom, 'provider_has_dataset', lambda *a: True)
    monkeypatch.setattr(analysis_snapshot.custom, 'get_provider', lambda *a: provider)
    bar = {'symbol': SYMBOL, 'date': date(2026, 10, 22), 'open': .532, 'high': .536,
           'low': .508, 'close': .512, 'volume': 35943420., 'amount': 186000000.}
    monkeypatch.setattr(analysis_snapshot.analysis_current_session, 'load_current',
                        lambda *a, **k: (bar, '2026-10-22T11:30:00+08:00'))
    return repo, provider, bar


def test_snapshot_adds_current_raw_candle_and_recomputes_indicators(snapshot):
    repo, _, _ = snapshot
    frame, meta = analysis_snapshot.load_snapshot(repo, None, SYMBOL, 250, include_current=True)
    assert meta['as_of'] == '2026-10-22' and meta['daily_as_of'] == '2026-10-21'
    assert meta['intraday_as_of'] == '2026-10-22T11:30:00+08:00'
    assert meta['is_provisional'] and meta['price_basis'] == 'unadjusted'
    assert frame['close'][-1] == .512
    assert frame['ma5'][-1] == pytest.approx((4 * .535 + .512) / 5)


def test_daily_only_caller_does_not_fetch_current_minutes(snapshot, monkeypatch):
    repo, _, _ = snapshot
    monkeypatch.setattr(analysis_snapshot.analysis_current_session, 'load_current',
                        lambda *a, **k: pytest.fail('daily-only previous-close lookup must not recurse'))
    _, meta = analysis_snapshot.load_snapshot(repo, None, SYMBOL, 30)
    assert meta['as_of'] == '2026-10-21' and not meta['is_provisional']


def test_local_adjusted_fallback_never_mixes_raw_minutes(snapshot, monkeypatch):
    repo, provider, _ = snapshot
    provider.get_daily = lambda *a, **k: pl.DataFrame()
    repo.get_daily_asset = lambda *a: history()
    monkeypatch.setattr(analysis_snapshot, '_baostock_daily', lambda *a: pl.DataFrame())
    monkeypatch.setattr(analysis_snapshot.analysis_current_session, 'load_current',
                        lambda *a, **k: pytest.fail('must not mix raw and adjusted prices'))
    _, meta = analysis_snapshot.load_snapshot(repo, None, SYMBOL, 250, include_current=True)
    assert meta['as_of'] == '2026-10-21' and meta['price_basis'] == 'local_enriched'


def test_closed_official_daily_does_not_fetch_or_replace_minutes(snapshot, monkeypatch):
    repo, provider, bar = snapshot
    provider.get_daily = lambda *a, **k: pl.concat([history(), pl.DataFrame([bar])])
    monkeypatch.setattr(analysis_snapshot, 'cn_now', lambda: datetime(2026, 10, 22, 16, tzinfo=CN_TZ))
    monkeypatch.setattr(analysis_snapshot.analysis_current_session, 'load_current',
                        lambda *a, **k: pytest.fail('official closed daily already available'))
    _, meta = analysis_snapshot.load_snapshot(repo, None, SYMBOL, 250, include_current=True)
    assert meta['as_of'] == '2026-10-22' and not meta['is_provisional']
    assert meta['intraday_as_of'] is None


def test_empty_stocksdk_uses_labelled_etf_fallback_without_changing_preference(snapshot, monkeypatch):
    repo, _, original = snapshot
    bar = original | {'amount': None}
    calls = []
    monkeypatch.setattr(analysis_snapshot.preferences, 'get_minute_data_provider', lambda: 'stocksdk')
    def load(*args, **kwargs):
        calls.append(kwargs.get('provider_name'))
        return (bar, '2026-10-22T11:30:00+08:00') if kwargs.get('provider_name') == 'txquote' else (None, None)
    monkeypatch.setattr(analysis_snapshot.analysis_current_session, 'load_current', load)
    frame, meta = analysis_snapshot.load_snapshot(repo, None, SYMBOL, 250, include_current=True)
    assert calls == [None, 'txquote']
    assert frame['close'][-1] == .512 and frame['amount'][-1] is None
    assert meta['as_of'] == '2026-10-22' and meta['intraday_provider'] == 'txquote'
    assert meta['freshness_error'] is None and meta['is_provisional']
    assert '腾讯' in meta['warning'] and '成交额' in meta['warning']
    assert analysis_snapshot.preferences.get_minute_data_provider() == 'stocksdk'


def test_other_selected_minute_sources_do_not_silently_use_etf_backup(snapshot, monkeypatch):
    repo, _, _ = snapshot
    calls = []
    monkeypatch.setattr(analysis_snapshot.preferences, 'get_minute_data_provider', lambda: 'another-source')
    monkeypatch.setattr(analysis_snapshot.analysis_current_session, 'load_current',
                        lambda *a, **k: (calls.append(k.get('provider_name')), (None, None))[1])
    _, meta = analysis_snapshot.load_snapshot(repo, None, SYMBOL, 250, include_current=True)
    assert calls == [None] and meta['freshness_error']


def test_today_local_frame_without_current_timestamp_is_not_fresh_intraday(snapshot, monkeypatch):
    repo, provider, bar = snapshot
    provider.get_daily = lambda *a, **k: pl.DataFrame()
    repo.get_daily_asset = lambda *a: pl.concat([history(), pl.DataFrame([bar])])
    monkeypatch.setattr(analysis_snapshot, '_baostock_daily', lambda *a: pl.DataFrame())
    _, meta = analysis_snapshot.load_snapshot(repo, None, SYMBOL, 250, include_current=True)
    assert meta['source'] == 'local' and meta['is_provisional'] and meta['freshness_error']


def test_older_backup_candidate_is_not_labelled_as_used(snapshot, monkeypatch):
    repo, provider, bar = snapshot
    provider.get_daily = lambda *_a, **_k: pl.concat([history(), pl.DataFrame([bar])])
    monkeypatch.setattr(analysis_snapshot.preferences, 'get_minute_data_provider', lambda: 'stocksdk')
    monkeypatch.setattr(analysis_snapshot.analysis_current_session, 'load_current', lambda *a, **k: (
        (bar | {'volume': bar['volume'] - 1}, '2026-10-22T11:30:00+08:00')
        if k.get('provider_name') else (None, None)))
    _, meta = analysis_snapshot.load_snapshot(repo, None, SYMBOL, 250, include_current=True)
    assert meta['intraday_provider'] is None and meta['intraday_as_of'] is None
    assert meta['freshness_error'] and not meta['warning']


async def test_ai_prompt_meta_and_levels_use_current_snapshot(snapshot, monkeypatch, tmp_path):
    repo, _, _ = snapshot
    monkeypatch.setattr(stock_analyzer, '_load_kline', lambda *a: pytest.fail('stale local loader'))
    from app.strategy import paper
    monkeypatch.setattr(paper, 'list_account_ids', lambda *a: [])
    captured = {}

    async def stream(messages, **kwargs):
        captured['prompt'] = messages[1]['content']
        yield '测试报告'

    monkeypatch.setattr(ai_provider, 'stream_ai_text', stream)
    events = [json.loads(line) async for line in stock_analyzer.analyze_stock_stream(
        repo, tmp_path, SYMBOL, refresh=True)]
    meta = events[0]
    assert meta['type'] == 'meta' and meta['as_of'] == '2026-10-22'
    assert meta['close'] == .512 and meta['is_provisional']
    assert meta['intraday_as_of'] in captured['prompt']
    assert '未收盘日K' in captured['prompt'] and '不能当作收盘确认信号' in captured['prompt']
    assert '"date": "2026-10-22"' in captured['prompt']
    assert '"ma5": 0.5304' in captured['prompt']
    assert events[-1]['type'] == 'done'


async def test_ai_uses_pinned_current_minute_and_preserves_request_time(snapshot, monkeypatch, tmp_path):
    repo, _, _ = snapshot
    from app.strategy import paper
    monkeypatch.setattr(paper, 'list_account_ids', lambda *a: [])
    monkeypatch.setattr(stock_analyzer, 'cn_now', lambda: datetime(2026, 10, 22, 11, 30, 42, tzinfo=CN_TZ))
    captured = {}
    async def stream(messages, **kwargs):
        captured['prompt'] = messages[1]['content']
        yield '测试报告'
    monkeypatch.setattr(ai_provider, 'stream_ai_text', stream)
    events = [json.loads(line) async for line in stock_analyzer.analyze_stock_stream(
        repo, tmp_path, SYMBOL, refresh=True)]
    meta = events[0]
    assert meta['type'] == 'meta' and meta['as_of'] == '2026-10-22'
    assert meta['close'] == .512 and meta['is_provisional']
    assert meta['requested_at'] == '2026-10-22T11:30:42+08:00'
    assert meta['intraday_as_of'] in captured['prompt']
    assert '未收盘日K' in captured['prompt'] and '不能当作收盘确认信号' in captured['prompt']
    assert '"date": "2026-10-22"' in captured['prompt']
    assert '"ma5": 0.5304' in captured['prompt']
    assert events[-1]['type'] == 'done'


def test_manual_route_requests_refresh_and_report_preserves_timestamps(snapshot, monkeypatch):
    repo, _, _ = snapshot
    captured = {}
    requested = datetime(2026, 10, 22, 11, 30, 42, tzinfo=CN_TZ)
    monkeypatch.setattr(stock_analysis, 'cn_now', lambda: requested)

    async def analyze(*args, **kwargs):
        captured.update(kwargs)
        yield json.dumps({'type': 'done'})

    monkeypatch.setattr(stock_analysis, 'analyze_stock_stream', analyze)
    monkeypatch.setattr(stock_analysis.stock_reports, 'save_report', lambda data: data)
    app = FastAPI()
    app.state.repo = repo
    app.state.capabilities = 'caps'
    app.include_router(stock_analysis.router)
    with TestClient(app) as client:
        assert client.post('/api/stock-analysis/analyze', json={'symbol': SYMBOL}).status_code == 200
        saved = client.post('/api/stock-analysis/reports', json={
            'symbol': SYMBOL, 'content': 'report', 'as_of': '2026-10-22',
            'daily_as_of': '2026-10-21', 'intraday_as_of': '2026-10-22T11:30:00+08:00',
            'requested_at': requested.isoformat(),
            'is_provisional': True, 'price_basis': 'unadjusted'}).json()['report']
    assert captured['refresh'] and captured['capset'] == 'caps'
    assert captured['request_time'] == requested
    assert saved['requested_at'] == requested.isoformat()
    assert saved['is_provisional'] and saved['intraday_as_of'] == '2026-10-22T11:30:00+08:00'
