# ruff: noqa: RUF001 -- The price-basis string is the existing Chinese API contract.
"""CPU-only forecast scoring contracts; never mutates live predictions or weights."""
import hashlib
import json
import threading
from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.custom.kronos import evaluation as ev
from app.custom.kronos import evaluation_data as quotes
from app.market_time import CN_TZ


def now(hour=14, minute=20):
    return datetime(2026, 10, 26, hour, minute, tzinfo=CN_TZ)


def job(identity='a' * 32, symbol='515880.SH', period='1m'):
    stamps = ['2026-10-26T14:16', '2026-10-26T14:17', '2026-10-26T14:18']
    cutoff, requested = '2026-10-26T14:15', '2026-10-26T14:16:20+08:00'
    if period == 'daily':
        stamps = ['2026-10-26', '2026-10-27', '2026-10-28']
        cutoff, requested = '2026-10-23', '2026-10-26T09:00:00+08:00'
    return {'id': identity, 'state': 'complete', 'request': {'symbol': symbol, 'period': period},
            'result': {'symbol': symbol, 'asset_type': 'etf', 'period': period,
                       'price_basis': '原始价格（未复权）', 'input_as_of': cutoff,
                       'requested_at': requested, 'completed_at': requested, 'history': [{'time': cutoff, 'close': 100.}],
                       'forecast': [{'time': stamp, 'close': value} for stamp, value in zip(stamps, [101., 99., 100.], strict=True)]}}


def test_future_actuals_are_never_used():
    value = ev.score(job(), {'2026-10-26T14:16': 102.}, now(14, 16))
    assert value['state'] == 'pending' and value['matched'] == 0
    assert value['metrics']['direction_accuracy'] is None and value['metrics']['mae'] is None
    assert value['points'][0]['actual_close'] is None


def test_minute_is_not_final_until_next_wall_clock_minute():
    price = {'2026-10-26T14:16': 102.}
    assert ev.score(job(), price, now(14, 16).replace(second=59))['matched'] == 0
    assert ev.score(job(), price, now(14, 17))['matched'] == 1


def test_daily_waits_for_closed_session():
    price = {'2026-10-26': 102.}
    assert ev.score(job(period='daily'), price, now(15, 29))['matched'] == 0
    value = ev.score(job(period='daily'), price, now(15, 30))
    assert value['matched'] == 1 and value['pending'] == 2


def test_partial_metrics_and_baseline_use_only_matched_points():
    actual = {'2026-10-26T14:16': 102., '2026-10-26T14:17': 98., '2026-10-26T14:18': 100.}
    value = ev.score(job(), actual, now(14, 18))
    assert (value['matched'], value['pending'], value['missing']) == (2, 1, 0)
    assert value['metrics']['mae'] == 1 and value['metrics']['rmse'] == 1
    assert value['metrics']['baseline_mae'] == 2
    assert value['metrics']['mape'] == pytest.approx((1 / 102 + 1 / 98) / 2)
    assert value['metrics']['direction_accuracy'] == 1
    assert value['points'][2]['actual_close'] is None


def test_missing_quote_is_not_a_prediction_error():
    value = ev.score(job(), {'2026-10-26T14:16': 102.}, now(14, 18))
    assert (value['matched'], value['pending'], value['missing']) == (1, 1, 1)
    assert value['metrics']['direction_total'] == 1
    assert value['metrics']['direction_matches'] == 1


def test_all_due_missing_is_unavailable_not_zero_accuracy():
    value = ev.score(job(), {}, now())
    assert value['state'] == 'unavailable' and value['missing'] == 3
    assert value['metrics']['direction_accuracy'] is None


def test_direction_is_relative_to_original_close_including_flat():
    value = ev.score(job(), {'2026-10-26T14:16': 99., '2026-10-26T14:17': 101., '2026-10-26T14:18': 100.}, now())
    assert value['metrics']['direction_matches'] == 1
    assert value['metrics']['direction_accuracy'] == pytest.approx(1 / 3)
    assert value['metrics']['flat_actual_count'] == 1


@pytest.mark.parametrize('bad', [None, 0., -1., float('nan'), float('inf')])
def test_invalid_actual_prices_do_not_enter_metrics(bad):
    value = ev.score(job(), {'2026-10-26T14:16': bad}, now())
    assert value['matched'] == 0 and value['missing'] == 3


def test_unrepresentable_relative_error_is_not_serialized_as_infinity():
    value = job()
    value['result']['forecast'][0]['close'] = 1e308
    scored = ev.score(value, {'2026-10-26T14:16': 5e-324}, now())
    assert scored['matched'] == 0
    json.dumps(scored, allow_nan=False)


def test_changed_basis_or_retrospective_prediction_cannot_be_scored():
    changed = job()
    changed['result']['price_basis'] = '前复权'
    with pytest.raises(ValueError, match='口径'):
        ev.score(changed, {}, now())
    changed = job()
    changed['result']['requested_at'] = '2026-10-26T14:18:00+08:00'
    with pytest.raises(ValueError, match='事前'):
        ev.score(changed, {}, now())


class Jobs:
    def __init__(self, root):
        self.runtime = root
        self.busy = False
    def status(self):
        return {'busy': self.busy}
    def get(self, identity):
        raise LookupError(identity)


def save(root, value):
    folder = root / 'results'
    folder.mkdir(exist_ok=True)
    path = folder / (value['id'] + '.json')
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def test_worker_saves_only_sidecar_and_reuses_completed_actuals(tmp_path):
    original = save(tmp_path, job())
    before = original.read_bytes()
    calls = []
    def loader(*args):
        calls.append(args)
        return {row['time']: row['close'] for row in job()['result']['forecast']}
    manager = ev.EvaluationManager(Jobs(tmp_path), loader)
    manager.repo = object()
    manager.cycle(now())
    stored = json.loads((tmp_path / ('evaluations/' + 'a' * 32 + '.json')).read_text(encoding='utf-8'))
    assert stored['state'] == 'complete' and stored['matched'] == 3
    assert original.read_bytes() == before
    assert stored['forecast_sha256'] == hashlib.sha256(before).hexdigest()
    manager.cycle(now())
    assert len(calls) == 1


def test_future_and_busy_jobs_do_not_fetch_quotes(tmp_path):
    save(tmp_path, job())
    manager = ev.EvaluationManager(Jobs(tmp_path), lambda *_: pytest.fail('must not fetch'))
    manager.repo = object()
    manager.cycle(now(14, 16))
    manager.jobs.busy = True
    manager.cycle(now())
    assert not (tmp_path / 'evaluations').exists()


def test_failure_fanout_is_capped_and_other_records_can_continue(tmp_path):
    for i, symbol in enumerate(['515880.SH', '512800.SH', '510300.SH']):
        save(tmp_path, job(f'{i:032x}', symbol))
    calls = []
    def failed(*args):
        calls.append(args)
        raise RuntimeError('provider unavailable')
    manager = ev.EvaluationManager(Jobs(tmp_path), failed)
    manager.repo = object()
    manager.cycle(now())
    assert len(calls) == 2


def test_same_context_is_fetched_once_for_multiple_forecasts(tmp_path):
    for i in range(5):
        save(tmp_path, job(f'{i:032x}'))
    calls = []
    manager = ev.EvaluationManager(Jobs(tmp_path), lambda *args: calls.append(args) or {})
    manager.repo = object()
    manager.cycle(now())
    assert len(calls) == 1


def test_changed_forecast_does_not_reuse_or_overwrite_old_evaluation(tmp_path, monkeypatch):
    path = save(tmp_path, job())
    actual = {row['time']: row['close'] for row in job()['result']['forecast']}
    manager = ev.EvaluationManager(Jobs(tmp_path), lambda *_: actual)
    manager.repo = object()
    manager.cycle(now())
    scored = tmp_path / ('evaluations/' + 'a' * 32 + '.json')
    old = scored.read_bytes()
    changed = job()
    changed['result']['forecast'][0]['close'] = 120.
    path.write_text(json.dumps(changed), encoding='utf-8')
    monkeypatch.setattr(ev, 'cn_now', now)
    assert manager.get('a' * 32)['matched'] == 0
    manager.cycle(now())
    assert scored.read_bytes() == old


def test_oversize_and_path_traversal_are_rejected(tmp_path):
    manager = ev.EvaluationManager(Jobs(tmp_path))
    with pytest.raises(ValueError):
        manager.get('../outside')
    path = save(tmp_path, job())
    path.write_bytes(b' ' * (ev.MAX_BYTES + 1))
    with pytest.raises(ValueError, match='大小'):
        manager.get('a' * 32)


def test_shutdown_discards_an_inflight_fetch(tmp_path):
    save(tmp_path, job())
    entered, release = threading.Event(), threading.Event()
    def loader(*_):
        entered.set()
        release.wait(3)
        return {'2026-10-26T14:16': 101.}
    manager = ev.EvaluationManager(Jobs(tmp_path), loader, interval=0.01)
    manager.repo = object()
    thread = threading.Thread(target=manager.cycle, args=(now(),))
    thread.start()
    assert entered.wait(1)
    manager.stop()
    release.set()
    thread.join(1)
    assert not (tmp_path / 'evaluations').exists()


def test_stop_after_first_date_does_not_start_a_second_fetch(tmp_path):
    value = job(period='daily')
    save(tmp_path, value)
    calls = []
    manager = ev.EvaluationManager(Jobs(tmp_path))
    manager.repo = object()
    def loader(*args):
        calls.append(args)
        manager.stop_event.set()
        return {}
    manager.loader = loader
    manager.cycle(datetime(2026, 10, 29, 16, tzinfo=CN_TZ))
    assert len(calls) == 1
    assert not (tmp_path / 'evaluations').exists()


def minute_frame(rows):
    return pl.DataFrame([{'symbol': '515880.SH', 'datetime': datetime.fromisoformat(stamp), 'close': price}
                         for stamp, price in rows])


def test_actual_minute_excludes_duplicates_unfinished_and_wrong_context():
    frame = minute_frame([('2026-10-26T14:16', .5), ('2026-10-26T14:16', .51),
                          ('2026-10-26T14:17', .52), ('2026-10-26T14:18', .53)])
    assert quotes.minute_closes(frame, '515880.SH', date(2026, 10, 26), now(14, 18)) == {'2026-10-26T14:17': .52}
    assert quotes.minute_closes(frame, '512800.SH', date(2026, 10, 26), now()) == {}
    assert quotes.minute_closes(frame, '515880.SH', date(2026, 10, 23), now()) == {}


def test_nonzero_seconds_cannot_overwrite_an_exact_minute():
    frame = minute_frame([('2026-10-26T14:16:00', .5), ('2026-10-26T14:16:30', .9)])
    assert quotes.minute_closes(frame, '515880.SH', date(2026, 10, 26), now()) == {'2026-10-26T14:16': .5}


def test_provider_failure_retains_verified_local_points(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes.trading_calendar, 'is_trading_day', lambda _: True)
    local = {'2026-10-26T14:16': .5}
    monkeypatch.setattr(quotes, '_local_minute', lambda *_: local)
    monkeypatch.setattr(quotes.preferences, 'get_minute_data_provider', lambda: 'offline')
    def unavailable(*_a, **_k):
        raise RuntimeError('offline')
    monkeypatch.setattr(quotes.custom, 'get_provider', unavailable)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    assert quotes.actual_prices(repo, None, '515880.SH', 'etf', '1m', '2026-10-26', now()) == local


def test_duplicate_daily_target_is_not_silently_selected(monkeypatch):
    monkeypatch.setattr(quotes.trading_calendar, 'is_trading_day', lambda _: True)
    monkeypatch.setattr(quotes, 'closed_daily_date', lambda _: date(2026, 10, 26))
    one = {'symbol': '515880.SH', 'date': date(2026, 10, 26),
           'open': .5, 'high': .6, 'low': .4, 'close': .5}
    monkeypatch.setattr(quotes, '_raw_daily', lambda *_a, **_k: pl.DataFrame([one, one]))
    assert quotes.actual_prices(None, None, '515880.SH', 'etf', 'daily', '2026-10-26', now(16)) == {}


def test_depth_uses_actual_trading_days_and_unknown_calendar_blocks(monkeypatch):
    prior = {date(2026, 10, 9): date(2026, 9, 30), date(2026, 9, 30): date(2026, 9, 29),
             date(2026, 9, 29): date(2026, 9, 28), date(2026, 9, 28): date(2026, 9, 25)}
    monkeypatch.setattr(quotes.trading_calendar, 'is_trading_day', lambda _: True)
    monkeypatch.setattr(quotes.trading_calendar, 'previous_trading_day', prior.get)
    provider = SimpleNamespace(minute_history_days=5)
    assert quotes.supported_history(provider, date(2026, 9, 25), date(2026, 10, 9))
    assert not quotes.supported_history(provider, date(2026, 9, 24), date(2026, 10, 9))
    monkeypatch.setattr(quotes.trading_calendar, 'previous_trading_day', lambda _: None)
    assert not quotes.supported_history(provider, date(2026, 9, 25), date(2026, 10, 9))


def test_daily_exact_date_and_raw_source_only(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes.trading_calendar, 'is_trading_day', lambda _: True)
    monkeypatch.setattr(quotes, 'closed_daily_date', lambda _: date(2026, 10, 26))
    wrong = pl.DataFrame([{'symbol': '515880.SH', 'date': date(2026, 10, 23),
                          'open': .5, 'high': .6, 'low': .4, 'close': .5}])
    monkeypatch.setattr(quotes, '_raw_daily', lambda *_a, **_kw: wrong)
    assert quotes.actual_prices(None, None, '515880.SH', 'etf', 'daily', '2026-10-26', now(16)) == {}


def test_unknown_minute_price_basis_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes.trading_calendar, 'is_trading_day', lambda _: True)
    monkeypatch.setattr(quotes, '_local_minute', lambda *_: {})
    monkeypatch.setattr(quotes.preferences, 'get_minute_data_provider', lambda: 'unknown')
    monkeypatch.setattr(quotes.custom, 'get_provider', lambda *_a, **_k: SimpleNamespace(get_minute=lambda *_a, **_k: pytest.fail('unknown basis')))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    assert quotes.actual_prices(repo, None, '515880.SH', 'etf', '1m', '2026-10-26', now()) == {}
