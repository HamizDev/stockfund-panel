import json
from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.market_time import CN_TZ
from app.services import (
    ai_provider,
    analysis_snapshot,
    research_context,
    research_outcomes,
    stock_analyzer,
)

NOW = datetime(2026, 10, 27, 14, 16, tzinfo=CN_TZ)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    frame = pl.DataFrame([dict(symbol='515880.SH', date=date(2026, 10, 27),
        open=.5, high=.51, low=.49, close=.5, raw_close=.5, volume=100)])
    snapshot = dict(as_of='2026-10-27', daily_as_of='2026-10-26', price_basis='unadjusted',
        intraday_as_of='2026-10-27T14:16', requested_at=NOW.isoformat(), minute_rows=[], is_provisional=True,
        raw_reference_verified=True)
    monkeypatch.setattr(analysis_snapshot, 'load_snapshot', lambda *a, **kw: (frame, snapshot))
    monkeypatch.setattr(stock_analyzer, '_load_kline', lambda *a: frame)
    monkeypatch.setattr(stock_analyzer, 'compute_levels', lambda *a, **kw: {})
    monkeypatch.setattr(stock_analyzer, 'cn_today', lambda: NOW.date())
    monkeypatch.setattr(research_outcomes, 'cn_now', lambda: NOW.replace(minute=17))
    context = dict(broker={'state': 'unknown', 'cash': None, 'available_qty': None},
        paper_accounts_state='available', paper_accounts=[dict(id='one', state='held', qty=100,
            available_qty=0, avg_cost=.51, cash=1000, fees={'commission_pct': .00005})])
    monkeypatch.setattr(research_context, 'build_portfolio_context', lambda *a, **kw: context)
    return tmp_path, SimpleNamespace(resolve_asset_type=lambda symbol: 'etf'), snapshot


async def run(inputs, **options):
    folder, repo, _ = inputs
    return [json.loads(event) async for event in stock_analyzer.analyze_stock_stream(
        repo, folder, '515880.SH', refresh=True, request_time=NOW, archive=True,
        name='通信ETF国泰', **options)]


@pytest.mark.asyncio
async def test_default_uses_one_ai_call_and_saves_server_original(inputs, monkeypatch):
    inputs[2]['rows'] = [{'unused': 'x' * 10_000}] * 250
    calls = []
    async def fake(messages, **options):
        calls.append(messages)
        yield '技术状态：趋势证据偏强\n观察方向：上涨'  # noqa: RUF001
    monkeypatch.setattr(ai_provider, 'stream_ai_text', fake)
    events = await run(inputs)
    assert len(calls) == 1 and events[-1]['type'] == 'done'
    saved = next(event['research'] for event in events if event['type'] == 'research')
    original = research_outcomes.get_decision(inputs[0], saved['id'])['record']
    assert original['name'] == '通信ETF国泰' and original['direction'] == 'up'
    assert original['reference_price'] == .5 and original['portfolio']['broker']['state'] == 'unknown'
    assert original['review']['calls'] == 1 and not original['review']['enabled']
    assert 'rows' not in original['snapshot'] and 'minute_rows' not in original['snapshot']
    assert '不包含任何买卖或操作建议' in calls[0][0]['content']
    assert 'allow_action_plan' not in str(calls) and '当前动作' not in calls[0][0]['content']
    assert 'available_qty' in calls[0][1]['content'] and '实盘' in calls[0][1]['content']


@pytest.mark.asyncio
async def test_one_optional_review_reuses_evidence_and_draft_is_not_streamed(inputs, monkeypatch):
    calls = []
    async def fake(messages, **options):
        calls.append(list(messages))
        yield '这是待复核的初稿' if len(calls) == 1 else '反方核对后的最终报告\n观察方向：下跌'  # noqa: RUF001
    monkeypatch.setattr(ai_provider, 'stream_ai_text', fake)
    events = await run(inputs, review=True, horizon_sessions=3)
    text = ''.join(event.get('content', '') for event in events if event['type'] == 'delta')
    assert len(calls) == 2 and '待复核的初稿' not in text and '最终报告' in text
    assert calls[0][1] == calls[1][1]
    saved = next(event['research'] for event in events if event['type'] == 'research')
    record = research_outcomes.get_decision(inputs[0], saved['id'])['record']
    assert record['review']['completed'] and record['review']['calls'] == 2
    assert record['snapshot']['evidence_bundle_id'] == record['review']['evidence_bundle_id']
    assert record['horizon_sessions'] == 3


@pytest.mark.asyncio
async def test_failed_review_is_not_archived_or_exposed_as_completed(inputs, monkeypatch):
    calls = []
    async def fake(messages, **options):
        calls.append(messages)
        if len(calls) == 1:
            yield '待复核报告'
        else:
            raise RuntimeError('api_key=PRIVATE_TEST_DO_NOT_EXPOSE')
    monkeypatch.setattr(ai_provider, 'stream_ai_text', fake)
    events = await run(inputs, review=True)
    assert len(calls) == 2 and events[-1]['type'] == 'error'
    assert not any(event['type'] in {'delta', 'research', 'done'} for event in events)
    assert 'PRIVATE_TEST' not in str(events)
    assert research_outcomes.list_decisions(inputs[0])['total'] == 0


@pytest.mark.asyncio
async def test_incomplete_market_data_stops_before_any_ai_call(inputs, monkeypatch):
    inputs[2]['freshness_error'] = '分钟行情尚未齐全'
    async def forbidden(*a, **kw):
        raise AssertionError('must not call AI')
        yield ''
    monkeypatch.setattr(ai_provider, 'stream_ai_text', forbidden)
    events = await run(inputs)
    assert events == [{'type': 'error', 'message': '分钟行情尚未齐全'}]


@pytest.mark.asyncio
async def test_duplicate_direction_labels_are_not_given_a_score(inputs, monkeypatch):
    async def fake(*a, **kw):
        yield '观察方向：上涨\n观察方向：下跌'  # noqa: RUF001
    monkeypatch.setattr(ai_provider, 'stream_ai_text', fake)
    events = await run(inputs)
    saved = next(event['research'] for event in events if event['type'] == 'research')
    assert saved['direction'] == 'unknown'


@pytest.mark.asyncio
async def test_unverified_provider_price_is_never_saved_as_raw_scoring_reference(inputs, monkeypatch):
    inputs[2]['raw_reference_verified'] = False
    inputs[2]['price_basis'] = 'provider_unverified'
    async def fake(*a, **kw):
        yield '观察方向：上涨'  # noqa: RUF001
    monkeypatch.setattr(ai_provider, 'stream_ai_text', fake)
    events = await run(inputs)
    saved = next(event['research'] for event in events if event['type'] == 'research')
    original = research_outcomes.get_decision(inputs[0], saved['id'])['record']
    assert original['reference_price'] is None and original['price_basis'] == 'unverified'
