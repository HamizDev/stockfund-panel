"""Precise request-time inputs and neutral public research."""
from datetime import datetime
from types import SimpleNamespace

import polars as pl
from test_analysis_current_session import _minutes, _provider, _use_provider

from app.market_time import CN_TZ
from app.services import ai_provider, analysis_current_session, analysis_snapshot, stock_analyzer
from app.services.minute_response_cache import MinuteResponseCache


def test_public_analysis_stays_descriptive_for_position_questions():
    prompt = stock_analyzer._SYSTEM_PROMPT
    assert '不包含任何买卖或操作建议' in prompt
    assert '不下操作结论' in prompt
    focus = ai_provider.build_focus_instruction('我该持有还是加仓减仓')
    assert '不得给出相应操作结论' in focus
    holding = stock_analyzer._paper_position_part([{'account_id': 'test', 'qty': 100, 'avg_cost': .5}], .514, .514)
    assert '不提供加减仓指令' in holding
    assert '可执行参考' not in holding


def test_stock_focus_keeps_intent_and_prompt_quotes_etf_price_exactly():
    prompt = stock_analyzer._build_user_prompt([], {}, {'pivot': [
        {'value': .503, 'label': '支撑位 S1', 'side': 'support', 'type': 'pivot'}]},
        .514, '515880.SH', '我该持有还是减仓', asset_type='etf',
        snapshot_meta={'as_of': '2026-10-23', 'requested_at': '2026-10-23T14:16:42+08:00',
                       'intraday_as_of': '2026-10-23T14:16:00+08:00',
                       'minute_rows': [{'datetime': '2026-10-23T14:16:00', 'close': .514, 'amount': None}]})
    assert '不得给出相应操作结论' in prompt
    assert '当前价 0.514' in prompt and '0.503' in prompt
    assert '14:16:42' in prompt and '14:16:00' in prompt
    assert '最近60根' in prompt and '"amount": null' in prompt
    assert '不编造' in prompt


def test_strict_aggregate_rejects_previous_minute_and_excludes_future():
    all_minutes = _minutes(include_afternoon=True)
    now = datetime(2026, 10, 22, 14, 16, 42, tzinfo=CN_TZ)
    lagged = all_minutes.filter(pl.col('datetime') <= datetime(2026, 10, 22, 14, 15))
    assert analysis_current_session._aggregate(lagged, '515880.SH', now, require_latest=True) == (None, None)
    bar, stamp = analysis_current_session._aggregate(all_minutes, '515880.SH', now, require_latest=True)
    assert bar and stamp == '2026-10-22T14:16:00+08:00'


def test_strict_minute_cache_refetches_at_minute_boundary_and_callback_gets_no_future(tmp_path, monkeypatch):
    calls, captured = [], []
    def fetch(*_a, **_k):
        calls.append(True)
        return _minutes(include_afternoon=True)
    provider = _provider(fetch)
    _use_provider(monkeypatch, ['stocksdk'], lambda _: provider)
    monkeypatch.setattr(analysis_current_session.trading_calendar, 'is_trading_day', lambda *_a, **_k: True)
    monkeypatch.setattr(analysis_current_session, '_CACHE', MinuteResponseCache(auto_expire=False))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    for minute in (16, 16, 17):
        _, stamp = analysis_current_session.load_current(
            repo, None, '515880.SH', 'etf', datetime(2026, 10, 22, 14, minute, 10),
            require_latest=True, on_minutes=captured.append)
        assert stamp.endswith(f'14:{minute}:00+08:00')
    assert len(calls) == 2
    assert captured[0]['datetime'][-1] == datetime(2026, 10, 22, 14, 16)
    assert captured[-1]['datetime'][-1] == datetime(2026, 10, 22, 14, 17)


def test_strict_freshness_does_not_accept_two_minute_lag():
    meta = {'as_of': '2026-10-23', 'is_provisional': True, 'require_current_minute': True,
            'intraday_as_of': '2026-10-23T14:14:00+08:00'}
    assert analysis_snapshot.freshness_error(meta, datetime(2026, 10, 23, 14, 16, 42, tzinfo=CN_TZ))


def test_strict_old_minute_response_does_not_block_fresh_same_minute_retry(tmp_path, monkeypatch):
    full = _minutes(include_afternoon=True)
    old = full.filter(pl.col('datetime') <= datetime(2026, 10, 22, 14, 15))
    responses, calls = iter([old, full]), []
    def fetch(*_a, **_k):
        calls.append(True)
        return next(responses)
    provider = _provider(fetch)
    _use_provider(monkeypatch, ['stocksdk'], lambda _: provider)
    monkeypatch.setattr(analysis_current_session.trading_calendar, 'is_trading_day', lambda *_a, **_k: True)
    monkeypatch.setattr(analysis_current_session, '_CACHE', MinuteResponseCache(auto_expire=False))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    now = datetime(2026, 10, 22, 14, 16, 10)
    assert analysis_current_session.load_current(
        repo, None, '515880.SH', 'etf', now, require_latest=True) == (None, None)
    bar, stamp = analysis_current_session.load_current(
        repo, None, '515880.SH', 'etf', now, require_latest=True)
    assert bar and stamp.endswith('14:16:00+08:00') and len(calls) == 2


def test_index_focus_remains_descriptive():
    prompt = stock_analyzer._build_user_prompt([], {}, {}, 3000, '000001.SH', '现在加仓吗', asset_type='index')
    assert '不得给出相应操作结论' in prompt
    assert '指数点位本身不可直接下单' in stock_analyzer._INDEX_SYSTEM_PROMPT


def test_closed_daily_status_is_explicit_in_prompt():
    prompt = stock_analyzer._build_user_prompt([], {}, {}, .514, '515880.SH', '', asset_type='etf',
        snapshot_meta={'as_of': '2026-10-23', 'is_provisional': False})
    assert 'is_provisional=false' in prompt and '未使用盘中临时日K' in prompt


def test_prompt_bands_share_three_decimal_etf_precision():
    from test_levels_precision import _sample_frame

    from app.api.stock_analysis import _build_series
    bands = _build_series(_sample_frame(), price_precision=3)
    assert bands['keltner_s']['lower'][-1] == .496
    assert bands['boll']['mid'][-1] == .503
