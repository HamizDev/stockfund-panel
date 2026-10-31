import json
from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.market_time import CN_TZ
from app.services import research_outcomes as outcomes
from app.services import trading_calendar

REQUESTED = datetime(2026, 10, 27, 14, 16, tzinfo=CN_TZ)
TARGET = date(2026, 10, 28)


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(outcomes, 'cn_now', lambda: REQUESTED.replace(minute=17))
    monkeypatch.setattr(outcomes, '_next_session', lambda day: TARGET)
    meta = outcomes.save_decision(tmp_path, symbol='515880.SH', asset_type='etf',
        content='原研究结论\n观察方向：上涨', requested_at=REQUESTED, horizon_sessions=1,  # noqa: RUF001
        reference_price=0.5, snapshot={'as_of': '2026-10-27'}, evidence={'id': 'fixed'},
        portfolio={'broker': {'state': 'unknown'}}, review={'enabled': False})
    return tmp_path, meta['id']


def frame(**changes):
    return pl.DataFrame([dict(symbol='515880.SH', date=TARGET, raw_close=0.51, close=0.49, quote_ts=None) | changes])


def repo_for(value):
    calls = []
    def get(*args):
        calls.append(args)
        return value
    return SimpleNamespace(get_daily_asset=get), calls


def test_empty_reads_do_not_create_data(tmp_path):
    assert outcomes.list_decisions(tmp_path)['items'] == []
    assert not (tmp_path / 'research_decisions').exists()


def test_original_decision_and_unknown_broker_remain_unchanged(saved):
    folder, identity = saved
    path = folder / 'research_decisions' / (identity + '.json')
    original = path.read_bytes()
    repo, calls = repo_for(frame())
    result = outcomes.refresh_outcome(repo, folder, identity, now=datetime(2026, 10, 28, 16, 15, tzinfo=CN_TZ))
    assert result['status'] == 'complete' and result['price_return'] == pytest.approx(.02)
    assert result['hit'] is True and path.read_bytes() == original
    assert outcomes.get_decision(folder, identity)['record']['portfolio']['broker']['state'] == 'unknown'
    assert calls == [('etf', '515880.SH', TARGET, TARGET)]
    stats = outcomes.list_decisions(folder)['statistics']
    assert stats['evaluated'] == 1 and stats['always_up_hit_rate'] == 1
    assert not (folder / 'paper').exists()


@pytest.mark.parametrize('now', [REQUESTED, datetime(2026, 10, 28, 16, 14, tzinfo=CN_TZ)])
def test_unmatured_window_does_not_read_prices(saved, now):
    folder, identity = saved
    repo, calls = repo_for(frame())
    assert outcomes.refresh_outcome(repo, folder, identity, now=now)['status'] == 'waiting_market'
    assert calls == [] and outcomes.list_decisions(folder)['statistics']['evaluated'] == 0


@pytest.mark.parametrize('value', [pl.DataFrame(), frame(symbol='600000.SH'),
    frame(date=date(2026, 10, 27)), frame(raw_close=None), frame(raw_close=float('nan')),
    frame(is_provisional=True), frame(quote_ts=int(datetime(2026, 10, 28, 14, tzinfo=CN_TZ).timestamp()*1000))])
def test_missing_or_unfinished_prices_are_not_scored(saved, value):
    folder, identity = saved
    repo, _ = repo_for(value)
    result = outcomes.refresh_outcome(repo, folder, identity, now=datetime(2026, 10, 29, 17, tzinfo=CN_TZ))
    assert result['status'] == 'waiting_data' and result['hit'] is None
    assert outcomes.list_decisions(folder)['statistics']['evaluated'] == 0


def test_unknown_calendar_never_guesses_weekdays(saved, monkeypatch):
    folder, identity = saved
    monkeypatch.setattr(outcomes, '_next_session', lambda day: None)
    repo, calls = repo_for(frame())
    result = outcomes.refresh_outcome(repo, folder, identity, now=datetime(2026, 10, 29, 17, tzinfo=CN_TZ))
    assert result['status'] == 'waiting_calendar' and not calls


def test_completed_result_is_cached_and_not_rewritten_by_later_prices(saved):
    folder, identity = saved
    repo, calls = repo_for(frame())
    now = datetime(2026, 10, 29, 17, tzinfo=CN_TZ)
    first = outcomes.refresh_outcome(repo, folder, identity, now=now)
    other, later_calls = repo_for(frame(raw_close=0.4))
    assert outcomes.refresh_outcome(other, folder, identity, now=now) == first
    assert len(calls) == 1 and later_calls == []


def test_cancellation_never_publishes_a_result(saved):
    folder, identity = saved
    repo, _ = repo_for(frame())
    outcomes.refresh_outcome(repo, folder, identity, now=datetime(2026, 10, 29, 17, tzinfo=CN_TZ), cancelled=lambda: True)
    assert outcomes.get_decision(folder, identity)['outcome'] is None


def test_late_conclusion_is_not_a_prospective_hit(saved, monkeypatch):
    folder, identity = saved
    path = folder / 'research_decisions' / (identity + '.json')
    value = outcomes.get_decision(folder, identity)['record']
    value['created_at'] = datetime(2026, 10, 28, 15, tzinfo=CN_TZ).isoformat()
    outcomes._write(path, value)
    repo, calls = repo_for(frame())
    result = outcomes.refresh_outcome(repo, folder, identity, now=datetime(2026, 10, 29, 17, tzinfo=CN_TZ))
    assert result['status'] == 'unscorable' and not calls


def test_corrupt_records_and_path_traversal_fail_closed(saved):
    folder, identity = saved
    with pytest.raises(ValueError):
        outcomes.get_decision(folder, '../paper/accounts/default')
    path = folder / 'research_decisions' / (identity + '.json')
    value = json.loads(path.read_text(encoding='utf-8'))
    value['content'] = '修改后的结论'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='校验失败'):
        outcomes.get_decision(folder, identity)


@pytest.mark.parametrize(('text', 'expected'), [('观察方向：上涨', 'up'),  # noqa: RUF001
    ('**观察方向：** 下跌', 'down'), ('观察方向：未判断', 'unknown'),  # noqa: RUF001
    ('上涨\n观察方向：上涨\n观察方向：下跌', 'unknown'), ('看涨但没有检验标签', 'unknown')])  # noqa: RUF001
def test_only_one_explicit_label_is_counted(text, expected):
    assert outcomes.observation_direction(text) == expected


def test_bounded_index_retains_older_original_files(tmp_path, monkeypatch):
    monkeypatch.setattr(outcomes, 'MAX_RECENT', 2)
    monkeypatch.setattr(outcomes, 'cn_now', lambda: REQUESTED)
    ids = [outcomes.save_decision(tmp_path, symbol='515880.SH', asset_type='etf', content='观察方向：上涨',  # noqa: RUF001
        requested_at=REQUESTED, horizon_sessions=1, reference_price=.5, snapshot={}, evidence={},
        portfolio={}, review={})['id'] for _ in range(3)]
    assert outcomes.list_decisions(tmp_path)['total'] == 3
    assert len(outcomes.list_decisions(tmp_path)['items']) == 2
    assert outcomes.get_decision(tmp_path, ids[0])['record']['id'] == ids[0]


def test_background_rotation_does_not_starve_older_pending_records(tmp_path):
    service = outcomes.OutcomeService(None, tmp_path)
    items = [dict(id=str(i), status='pending') for i in range(45)]
    visited = {row['id'] for _ in range(3) for row in service.pending_batch(items)}
    assert visited == {str(i) for i in range(45)}


def test_forward_calendar_uses_official_sessions_across_months(monkeypatch):
    monkeypatch.setattr(trading_calendar, 'month_open_days', lambda year, month:
        frozenset({date(2026, 10, 30)}) if month == 10 else frozenset({date(2026, 11, 2)}))
    assert outcomes._next_session(date(2026, 10, 30)) == date(2026, 11, 2)
    monkeypatch.setattr(trading_calendar, 'month_open_days', lambda *a: None)
    assert outcomes._next_session(date(2026, 10, 30)) is None


def test_growing_file_read_is_bounded(tmp_path, monkeypatch):
    import io
    path = tmp_path / 'record.json'
    path.write_text('{}', encoding='utf-8')
    reads = []
    class Bounded(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)
    monkeypatch.setattr(type(path), 'open', lambda *a, **kw: Bounded(b'x' * (outcomes.MAX_BYTES + 10)))
    with pytest.raises(ValueError):
        outcomes._read(path)
    assert reads == [outcomes.MAX_BYTES + 1]
