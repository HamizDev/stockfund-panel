"""An explicitly fresh AI request must not silently analyze an old session."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from test_stock_analysis_fresh_snapshot import history

from app.market_time import CN_TZ
from app.services import ai_provider, analysis_snapshot, stock_analyzer, trading_calendar


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    monkeypatch.setattr(trading_calendar, 'is_trading_day', lambda day, **_: day.weekday() < 5)
    from datetime import date
    monkeypatch.setattr(trading_calendar, 'previous_trading_day', lambda *_a, **_k: date(2026, 10, 22))


@pytest.mark.parametrize(('hour', 'minute'), [(9, 30), (11, 35), (14, 16), (18, 0)])
def test_yesterdays_frame_is_not_fresh_after_open(hour, minute):
    error = analysis_snapshot.freshness_error(
        {'as_of': '2026-10-22'}, datetime(2026, 10, 23, hour, minute, tzinfo=CN_TZ))
    assert '2026-10-22' in error and '2026-10-23' in error


def test_premarket_weekend_and_known_holiday_do_not_require_a_fake_today_candle(monkeypatch):
    assert analysis_snapshot.freshness_error({'as_of': '2026-10-22'}, datetime(2026, 10, 23, 9, 29)) is None
    assert analysis_snapshot.freshness_error({'as_of': '2026-10-22'}, datetime(2026, 10, 24, 14)) is None
    monkeypatch.setattr(trading_calendar, 'is_trading_day', lambda *_a, **_k: False)
    assert analysis_snapshot.freshness_error({'as_of': '2026-10-22'}, datetime(2026, 10, 23, 14)) is None


def test_missing_recent_completed_session_still_fails_before_open():
    assert analysis_snapshot.freshness_error({'as_of': '2026-10-21'}, datetime(2026, 10, 23, 8))


def test_unknown_previous_session_stops_ai_instead_of_accepting_arbitrary_history(monkeypatch):
    monkeypatch.setattr(trading_calendar, 'previous_trading_day', lambda *_a, **_k: None)
    assert '无法核验' in analysis_snapshot.freshness_error({'as_of': '2026-10-09'}, datetime(2026, 10, 24, 14))
    monkeypatch.setattr(trading_calendar, 'is_trading_day', lambda *_a, **_k: False)
    assert analysis_snapshot.freshness_error({'as_of': '2026-10-09'}, datetime(2026, 10, 23, 14))


def test_invalid_lunchtime_stamp_cannot_look_fresh():
    assert analysis_snapshot.freshness_error(
        {'as_of': '2026-10-23', 'is_provisional': True, 'intraday_as_of': '2026-10-23T12:00:00+08:00'},
        datetime(2026, 10, 23, 12, 30, tzinfo=CN_TZ))


def test_unverified_local_today_does_not_become_official_merely_because_market_closed():
    assert analysis_snapshot.freshness_error(
        {'as_of': '2026-10-23', 'source': 'local', 'is_provisional': False},
        datetime(2026, 10, 23, 18, tzinfo=CN_TZ))


def test_postclose_today_is_fresh_but_current_partial_requires_actual_time():
    assert analysis_snapshot.freshness_error({'as_of': '2026-10-23'}, datetime(2026, 10, 23, 18)) is None
    assert analysis_snapshot.freshness_error(
        {'as_of': '2026-10-23', 'is_provisional': True}, datetime(2026, 10, 23, 14, 16))
    assert analysis_snapshot.freshness_error(
        {'as_of': '2026-10-23', 'is_provisional': True, 'intraday_as_of': '2026-10-23T14:15:00+08:00'},
        datetime(2026, 10, 23, 14, 16, tzinfo=CN_TZ)) is None


@pytest.mark.parametrize('stamp', ['2026-10-23T11:30:00+08:00', '2026-10-22T14:16:00+08:00',
                                  '2026-10-23T14:20:00+08:00', 'bad'])
def test_stale_wrong_day_future_or_invalid_intraday_time_fails(stamp):
    assert analysis_snapshot.freshness_error(
        {'as_of': '2026-10-23', 'is_provisional': True, 'intraday_as_of': stamp},
        datetime(2026, 10, 23, 14, 16, tzinfo=CN_TZ))


def test_lunch_and_after_close_use_last_completed_session_time():
    for hour, stamp in [(12, '11:30'), (18, '15:00')]:
        assert analysis_snapshot.freshness_error(
            {'as_of': '2026-10-23', 'is_provisional': True,
             'intraday_as_of': f'2026-10-23T{stamp}:00+08:00'},
            datetime(2026, 10, 23, hour, tzinfo=CN_TZ)) is None


async def test_stale_input_emits_error_before_calling_model(monkeypatch, tmp_path):
    monkeypatch.setattr(analysis_snapshot, 'load_snapshot', lambda *_a, **_k: (
        history(), {'freshness_error': '数据截至2026-10-22, 未取得2026-10-23行情。'}))
    async def never(*_a, **_k):
        pytest.fail('stale input must not consume model quota')
        yield ''
    monkeypatch.setattr(ai_provider, 'stream_ai_text', never)
    repo = SimpleNamespace(resolve_asset_type=lambda _: 'etf')
    events = [json.loads(line) async for line in stock_analyzer.analyze_stock_stream(
        repo, tmp_path, '515880.SH', refresh=True)]
    assert len(events) == 1 and events[0]['type'] == 'error'
    assert '2026-10-23' in events[0]['message']
