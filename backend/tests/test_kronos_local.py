"""Contract tests do not load CUDA, contact providers, or mutate live data."""
import importlib.util
import json
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.custom.kronos import data, routes, service
from app.market_time import CN_TZ


def row(stamp):
    return {'symbol': '515880.SH', 'date': stamp, 'open': 0.52, 'high': 0.54,
            'low': 0.51, 'close': 0.53, 'volume': 10000.0, 'amount': 5300.0}


@pytest.fixture
def calendar(monkeypatch):
    # Oct 1-8 are intentionally excluded; verify future targets use the calendar.
    days = {date(2026, 10, day) for day in (9, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30)}
    monkeypatch.setattr(data.trading_calendar, 'month_open_days', lambda *_: days)
    monkeypatch.setattr(data.trading_calendar, 'is_trading_day', lambda day: day in days)
    monkeypatch.setattr(data.trading_calendar, 'previous_trading_day', lambda day: max(d for d in days if d < day))
    return days


def test_daily_future_uses_holidays_and_weekends(calendar):
    assert data.future_times('2026-09-30', 'daily', 3) == ['2026-10-09', '2026-10-12', '2026-10-13']


def test_minutes_skip_lunch(calendar):
    assert data.future_times('2026-10-26T11:29', '1m', 3) == ['2026-10-26T11:30', '2026-10-26T13:01', '2026-10-26T13:02']


def test_minutes_cross_session(calendar):
    assert data.future_times('2026-10-23T14:59', '1m', 3) == ['2026-10-23T15:00', '2026-10-26T09:31', '2026-10-26T09:32']


def test_unknown_calendar_blocks(monkeypatch):
    monkeypatch.setattr(data.trading_calendar, 'month_open_days', lambda *_: None)
    with pytest.raises(ValueError, match='交易日历'):
        data.future_times('2026-10-26', 'daily', 5)


@pytest.mark.parametrize(('hour', 'minute', 'expected'), [(14, 59, 23), (15, 29, 23), (15, 30, 26)])
def test_only_closed_daily(calendar, hour, minute, expected):
    assert data.closed_daily_date(datetime(2026, 10, 26, hour, minute, tzinfo=CN_TZ)) == date(2026, 10, expected)


def test_daily_provider_receives_asset_and_raw_prices(monkeypatch, calendar):
    calls = []
    def get_daily(symbols, **kwargs):
        calls.append((symbols, kwargs))
        return pl.DataFrame([row(date(2026, 10, 22)), row(date(2026, 10, 23)), row(date(2026, 10, 26))])
    monkeypatch.setattr(data.preferences, 'get_daily_data_provider', lambda: 'stocksdk')
    monkeypatch.setattr(data.custom, 'get_provider', lambda _: SimpleNamespace(get_daily=get_daily))
    rows, _ = data._daily(None, '515880.SH', 'etf', 2, datetime(2026, 10, 26, 14, tzinfo=CN_TZ))
    assert rows[-1]['time'] == '2026-10-23'
    assert rows[-1]['open'] == 0.52
    assert calls[0][1]['asset_type'] == 'etf'
    assert calls[0][1]['end_time'].date() == date(2026, 10, 23)


def test_complete_raw_daily_is_reused_without_online_or_enriched(tmp_path, monkeypatch, calendar):
    folder = tmp_path / 'kline_etf_daily/date=2026-10-23'
    folder.mkdir(parents=True)
    pl.DataFrame([row(date(2026, 10, 22)), row(date(2026, 10, 23))]).write_parquet(folder / 'part.parquet')
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(data.preferences, 'get_daily_data_provider', lambda: pytest.fail('complete raw cache needs no provider'))
    values, warnings = data._daily(repo, '515880.SH', 'etf', 2, datetime(2026, 10, 26, 14, tzinfo=CN_TZ))
    assert [value['time'] for value in values] == ['2026-10-22', '2026-10-23']
    assert any('本地原始日线' in item for item in warnings)


def test_stale_raw_daily_is_not_used(tmp_path, monkeypatch, calendar):
    folder = tmp_path / 'kline_etf_daily/date=2026-10-22'
    folder.mkdir(parents=True)
    pl.DataFrame([row(date(2026, 10, 22))]).write_parquet(folder / 'part.parquet')
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(data.preferences, 'get_daily_data_provider', lambda: 'stocksdk')
    monkeypatch.setattr(data.custom, 'get_provider', lambda _: SimpleNamespace(get_daily=lambda *_a, **_k: pl.DataFrame([row(date(2026, 10, 23))])))
    values, warnings = data._daily(repo, '515880.SH', 'etf', 1, datetime(2026, 10, 26, 14, tzinfo=CN_TZ))
    assert values[-1]['time'] == '2026-10-23'
    assert any('在线重新取数' in item for item in warnings)


def test_enriched_is_never_used_as_raw(tmp_path):
    folder = tmp_path / 'kline_etf_enriched/date=2026-10-23'
    folder.mkdir(parents=True)
    pl.DataFrame([row(date(2026, 10, 23))]).write_parquet(folder / 'part.parquet')
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    assert data._raw_daily(repo, '515880.SH', 'etf', 1, date(2026, 10, 23)).is_empty()


def test_previous_close_uses_exact_prior_raw_session(tmp_path, calendar):
    folder = tmp_path / 'kline_etf_daily/date=2026-10-23'
    folder.mkdir(parents=True)
    pl.DataFrame([row(date(2026, 10, 23))]).write_parquet(folder / 'part.parquet')
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    assert data.previous_close(repo, '515880.SH', 'etf', date(2026, 10, 26)) == {
        'prev_close': .53, 'prev_close_date': '2026-10-23'}
    pl.DataFrame([row(date(2026, 10, 23))] * 2).write_parquet(folder / 'part.parquet')
    assert data.previous_close(repo, '515880.SH', 'etf', date(2026, 10, 26))['prev_close'] is None


def test_minute_chart_context_keeps_all_observed_rows_only(monkeypatch):
    stamps = [datetime(2026, 10, 26, 9, minute) for minute in (31, 32, 33)]
    frame = pl.DataFrame([{**row(stamp.date()), 'datetime': stamp} for stamp in stamps])
    monkeypatch.setattr(data, 'current_minute_frame', lambda *_: frame)
    context = {}
    values, _ = data._minutes(None, None, '515880.SH', 'etf', 2,
                              datetime(2026, 10, 26, 9, 34, tzinfo=CN_TZ), context)
    assert len(values) == 2 and len(context['chart_context']) == 3
    assert context['chart_context'][0]['time'] == '2026-10-26T09:31'
    assert context['chart_context'][-1] == values[-1]


def test_unused_quote_timestamp_schema_drift_is_not_read(tmp_path):
    for day, stamp in [(22, None), (23, 1792742400000)]:
        folder = tmp_path / f'kline_etf_daily/date=2026-10-{day}'
        folder.mkdir(parents=True)
        values = {**row(date(2026, 10, day)), 'quote_ts': stamp}
        if day == 23:
            values['volume'] = 10000
        pl.DataFrame([values]).write_parquet(folder / 'part.parquet')
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    frame = data._raw_daily(repo, '515880.SH', 'etf', 2, date(2026, 10, 23))
    assert frame.height == 2 and 'quote_ts' not in frame.columns


def test_stale_daily_blocks(monkeypatch, calendar):
    monkeypatch.setattr(data.preferences, 'get_daily_data_provider', lambda: 'stocksdk')
    monkeypatch.setattr(data.custom, 'get_provider', lambda _: SimpleNamespace(get_daily=lambda *_a, **_k: pl.DataFrame([row(date(2026, 10, 22))])))
    with pytest.raises(ValueError, match='最近已收盘'):
        data._daily(None, '515880.SH', 'etf', 1, datetime(2026, 10, 26, 14, tzinfo=CN_TZ))


@pytest.mark.parametrize('field,value', [('open', None), ('high', 0.50), ('low', 0.55), ('close', float('nan'))])
def test_invalid_observed_prices_block(field, value):
    bad = row(date(2026, 10, 23))
    bad[field] = value
    with pytest.raises(ValueError):
        data._rows(pl.DataFrame([bad]), '515880.SH', 'date', 1)


def test_duplicate_days_block():
    with pytest.raises(ValueError, match='重复'):
        data._rows(pl.DataFrame([row(date(2026, 10, 23))] * 2), '515880.SH', 'date', 2)


def test_volume_and_amount_missing_are_not_fabricated():
    original = row(date(2026, 10, 23))
    del original['amount']
    rows = data._rows(pl.DataFrame([original]), '515880.SH', 'date', 1)
    assert rows[0]['amount'] is None
    assert rows[0]['volume'] == 10000


def test_minute_excludes_in_progress(monkeypatch, calendar):
    monkeypatch.setattr(data.preferences, 'get_minute_data_provider', lambda: 'txquote')
    stamps = [datetime(2026, 10, 26, 14, minute) for minute in range(14, 17)]
    frame = pl.DataFrame([{**row(stamp.date()), 'datetime': stamp, 'amount': None} for stamp in stamps])
    def load(*_args, **kwargs):
        assert kwargs['require_latest'] is True
        kwargs['on_minutes'](frame)
        return {}, stamps[-1].isoformat()
    monkeypatch.setattr(data.analysis_current_session, 'load_current', load)
    rows, _ = data._minutes(None, None, '515880.SH', 'etf', 2, datetime(2026, 10, 26, 14, 16, 40, tzinfo=CN_TZ))
    assert [item['time'] for item in rows] == ['2026-10-26T14:14', '2026-10-26T14:15']
    assert rows[0]['amount'] is None


def test_minute_price_point_stream_is_not_used(monkeypatch, calendar):
    monkeypatch.setattr(data.preferences, 'get_minute_data_provider', lambda: 'txquote')
    monkeypatch.setattr(data.analysis_current_session, 'load_current', lambda *_a, **_k: (None, None))
    with pytest.raises(ValueError, match='价格点'):
        data._minutes(None, None, '600000.SH', 'stock', 60, datetime(2026, 10, 26, 14, tzinfo=CN_TZ))


def valid_forecast():
    return {'forecast': [{'time': '2026-10-27', 'open': 1., 'high': 1.1, 'low': 0.9, 'close': 1.}],
            'metrics': {'elapsed_seconds': 1., 'cuda_peak_allocated_mb': 100., 'cuda_peak_reserved_mb': 128.}}


@pytest.mark.parametrize('kind', ['time', 'nan', 'order', 'metric', 'length'])
def test_reject_invalid_worker_results(kind):
    value = valid_forecast()
    if kind == 'time':
        value['forecast'][0]['time'] = '2026-10-26'
    if kind == 'nan':
        value['forecast'][0]['close'] = float('nan')
    if kind == 'order':
        value['forecast'][0]['high'] = 0.5
    if kind == 'metric':
        value['metrics']['elapsed_seconds'] = float('inf')
    if kind == 'length':
        value['forecast'] = []
    with pytest.raises(ValueError):
        service.validate_forecast(value, ['2026-10-27'])


def test_valid_worker_result():
    service.validate_forecast(valid_forecast(), ['2026-10-27'])


def test_close_only_worker_has_no_ohl_fields():
    value = valid_forecast()
    value.update(forecast_mode='close_only', warnings=['仅展示收盘价预测路径'])
    value['forecast'] = [{'time': '2026-10-27', 'close': 1.}]
    service.validate_forecast(value, ['2026-10-27'])
    value['forecast'][0]['open'] = 1.
    with pytest.raises(ValueError, match='开高低'):
        service.validate_forecast(value, ['2026-10-27'])


@pytest.mark.parametrize('bad', ['mode', 'unhashable', 'warning', 'weak_warning', 'close', 'time'])
def test_close_only_worker_is_fail_closed(bad):
    value = valid_forecast()
    value.update(forecast_mode='close_only', warnings=['仅展示收盘价预测路径'])
    value['forecast'] = [{'time': '2026-10-27', 'close': 1.}]
    if bad == 'mode':
        value['forecast_mode'] = 'anything'
    if bad == 'unhashable':
        value['forecast_mode'] = []
    if bad == 'warning':
        value['warnings'] = ['']
    if bad == 'weak_warning':
        value['warnings'] = ['GPU异常']
    if bad == 'close':
        value['forecast'][0]['close'] = float('nan')
    if bad == 'time':
        value['forecast'][0]['time'] = '2026-10-26'
    with pytest.raises(ValueError):
        service.validate_forecast(value, ['2026-10-27'])


def _next_runner():
    path = Path(__file__).resolve().parents[2] / 'scripts/kronos/runner.py'
    spec = importlib.util.spec_from_file_location('local_kronos_next_runner', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def test_runner_drops_invalid_ohl_without_repair():
    bars, mode, warnings = _next_runner().encode_forecast([
        {'open': 1., 'high': .9, 'low': .8, 'close': 1.1}], ['2026-10-27'])
    assert mode == 'close_only' and bars == [{'time': '2026-10-27', 'close': 1.1}]
    assert warnings and '未修正' in warnings[0]


def test_runner_keeps_valid_ohlc():
    original = {'open': 1., 'high': 1.2, 'low': .8, 'close': 1.1}
    bars, mode, warnings = _next_runner().encode_forecast([original], ['2026-10-27'])
    assert mode == 'ohlc' and bars == [{'time': '2026-10-27', **original}] and not warnings


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), 0., -1.])
def test_runner_nonfinite_prices_do_not_degrade(bad):
    with pytest.raises(ValueError):
        _next_runner().encode_forecast([{'open': 1., 'high': bad, 'low': .8, 'close': 1.}], ['2026-10-27'])


@pytest.mark.parametrize('values', [{'model': 'shell'}, {'symbol': '../../key'}, {'samples': True},
                                   {'lookback': 100000}, {'horizon': 500}, {'path': 'C:/secret'}])
def test_request_is_bounded(values):
    with pytest.raises(ValidationError):
        routes.PredictionRequest.model_validate({'symbol': '515880.SH', **values})


def test_status_is_lazy_and_single_flight(tmp_path, monkeypatch):
    manager = service.JobManager(tmp_path)
    assert manager.status()['ready'] is False
    for rel in ('.venv/Scripts/python.exe', 'runner.py', 'ready.json',
                'models/base/model.safetensors', 'models/tokenizer/model.safetensors'):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert manager.status()['ready'] is True
    assert manager.status()['models'] == ['base']
    assert not (tmp_path / 'models' / 'small').exists()
    monkeypatch.setattr(service.threading.Thread, 'start', lambda _self: None)
    job = manager.start({'symbol': '515880.SH'}, None, None)
    assert job['request']['model'] == 'base'
    assert manager.status()['busy']
    with pytest.raises(service.BusyError):
        manager.start(job['request'], None, None)
    manager.cancel(job['id'])
    assert manager.cancelled.is_set()


def test_job_path_traversal_rejected(tmp_path):
    with pytest.raises(ValueError):
        service.JobManager(tmp_path).get('../preferences')


def test_prepare_failure_never_launches_process(tmp_path, monkeypatch):
    manager = service.JobManager(tmp_path)
    (tmp_path / 'results').mkdir()
    def bad(*_a): raise ValueError('行情缺失')
    monkeypatch.setattr(service, 'load_input', bad)
    monkeypatch.setattr(service.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('must not launch'))
    job = {'id': 'a' * 32, 'request': {}, 'state': 'preparing'}
    manager._run(job, None, None, None, manager.cancelled)
    assert job['state'] == 'failed' and job['error'] == '行情缺失'
    assert (tmp_path / 'results' / ('a' * 32 + '.json')).is_file()


def test_api_validation_and_lifecycle(monkeypatch, tmp_path):
    manager = service.JobManager(tmp_path)
    monkeypatch.setattr(routes, 'MANAGER', manager)
    app = FastAPI()
    app.include_router(routes.build_router())
    app.state.repo = None
    client = TestClient(app)
    assert client.get('/api/custom/kronos/status').json()['ready'] is False
    assert client.post('/api/custom/kronos/jobs', json={'symbol': 'bad'}).status_code == 422
    assert client.post('/api/custom/kronos/jobs', json={'symbol': '515880.SH', 'model': 'small'}).status_code == 422
    assert client.post('/api/custom/kronos/jobs', json={'symbol': '515880.SH'}).status_code == 400
    assert client.get('/api/custom/kronos/jobs/' + 'a' * 32).status_code == 404


def test_runtime_rejects_same_parsed_future_time():
    runner = _next_runner()
    history = []
    for index in range(30):
        day = date(2026, 9, 1) + timedelta(days=index)
        history.append({'time': day.isoformat(), 'open': 1, 'high': 1, 'low': 1, 'close': 1})
    with pytest.raises(ValueError, match='input cutoff'):
        runner.validate_input({'history': history, 'future_times': ['2026-09-30 00:00:00']})


def test_runner_defaults_to_base_rejects_small_and_declares_only_base_hash():
    runner = _next_runner()
    history = []
    for index in range(30):
        day = date(2026, 9, 1) + timedelta(days=index)
        history.append({'time': day.isoformat(), 'open': 1., 'high': 1.1, 'low': .9, 'close': 1.})
    payload = {'history': history, 'future_times': ['2026-10-01']}

    assert runner.validate_input(payload)[0] == 'base'
    assert set(runner.MODEL_HASHES) == {'base', 'tokenizer'}
    with pytest.raises(ValueError, match='Base'):
        runner.validate_input(payload | {'model': 'small'})


def _prepared():
    return {'symbol': '515880.SH', 'name': '通信ETF', 'model': 'base', 'period': 'daily',
            'history': [{'time': '2026-10-23', 'open': 1., 'high': 1.1, 'low': .9, 'close': 1.}],
            'future_times': ['2026-10-26'], 'samples': 1, 'warnings': []}


def _job():
    return {'id': 'b' * 32, 'state': 'preparing', 'request': {'horizon': 1}, 'result': None, 'error': None}


def test_cancellation_during_prepare_is_terminal_without_gpu(tmp_path, monkeypatch):
    manager = service.JobManager(tmp_path)
    (tmp_path / 'results').mkdir()
    entered, release = threading.Event(), threading.Event()
    def prepare(*_a):
        entered.set()
        release.wait(3)
        return _prepared()
    monkeypatch.setattr(service, 'load_input', prepare)
    monkeypatch.setattr(service.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('GPU must not start'))
    job = manager.job = _job()
    worker = threading.Thread(target=manager._run, args=(job, None, None, None, manager.cancelled))
    worker.start()
    assert entered.wait(1)
    manager.cancel(job['id'])
    worker.join(1)
    assert not worker.is_alive()
    assert job['state'] == 'cancelled'
    assert manager.status()['busy'] is True  # Bounded in-flight read is draining.
    release.set()
    manager.preparing_thread.join(1)
    assert manager.status()['busy'] is False


def test_prepare_timeout_is_bounded(tmp_path, monkeypatch):
    manager = service.JobManager(tmp_path, timeout=0.04)
    (tmp_path / 'results').mkdir()
    release = threading.Event()
    def prepare(*_a):
        release.wait(2)
        return _prepared()
    monkeypatch.setattr(service, 'load_input', prepare)
    job = _job()
    manager._run(job, None, None, None, manager.cancelled)
    release.set()
    manager.preparing_thread.join(1)
    assert job['state'] == 'failed' and '准备超时' in job['error']


def test_worker_cannot_replace_input_metadata_or_receive_keys(tmp_path, monkeypatch):
    manager = service.JobManager(tmp_path)
    (tmp_path / 'results').mkdir()
    monkeypatch.setattr(service, 'load_input', lambda *_a: _prepared())
    monkeypatch.setenv('PRIVATE_API_KEY', 'test-only-secret')
    original = subprocess.Popen
    result = valid_forecast()
    result.update(symbol='WRONG', history=[], price_basis='incorrect')
    result['forecast'][0]['time'] = '2026-10-26'
    def popen(_args, **kwargs):
        assert 'PRIVATE_API_KEY' not in kwargs['env']
        assert kwargs['env']['HF_HUB_OFFLINE'] == '1'
        return original([sys.executable, '-c', 'import sys,json;json.load(sys.stdin);print(' + repr(json.dumps(result)) + ')'], **kwargs)
    monkeypatch.setattr(service.subprocess, 'Popen', popen)
    job = _job()
    manager._run(job, None, None, None, manager.cancelled)
    assert job['state'] == 'complete', job.get('error')
    assert job['result']['symbol'] == '515880.SH'
    assert job['result']['history'][0]['time'] == '2026-10-23'
    assert job['result'].get('price_basis') != 'incorrect'


def test_cancellation_kills_running_worker(tmp_path, monkeypatch):
    manager = service.JobManager(tmp_path)
    (tmp_path / 'results').mkdir()
    monkeypatch.setattr(service, 'load_input', lambda *_a: _prepared())
    original, children = subprocess.Popen, []
    def popen(_args, **kwargs):
        child = original([sys.executable, '-c', 'import sys,time;sys.stdin.read();time.sleep(5)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(service.subprocess, 'Popen', popen)
    job = manager.job = _job()
    worker = threading.Thread(target=manager._run, args=(job, None, None, None, manager.cancelled))
    worker.start()
    deadline = time.monotonic() + 3
    while manager.process is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.process is not None
    manager.cancel(job['id'])
    worker.join(2)
    assert job['state'] == 'cancelled' and job['result'] is None
    assert children[0].poll() is not None


def test_runtime_timeout_kills_worker(tmp_path, monkeypatch):
    manager = service.JobManager(tmp_path, timeout=0.2)
    (tmp_path / 'results').mkdir()
    monkeypatch.setattr(service, 'load_input', lambda *_a: _prepared())
    original, children = subprocess.Popen, []
    def popen(_args, **kwargs):
        child = original([sys.executable, '-c', 'import sys,time;sys.stdin.read();time.sleep(5)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(service.subprocess, 'Popen', popen)
    job = _job()
    manager._run(job, None, None, None, manager.cancelled)
    assert job['state'] == 'failed' and '时限' in job['error']
    assert children[0].poll() is not None


@pytest.mark.parametrize('cancel_requested', [True, False])
def test_unread_large_stdin_is_still_cancellable_and_timed_out(tmp_path, monkeypatch, cancel_requested):
    manager = service.JobManager(tmp_path, timeout=2 if cancel_requested else 0.3)
    (tmp_path / 'results').mkdir()
    prepared = _prepared()
    prepared['history'] *= 512  # Larger than the Windows pipe capacity.
    monkeypatch.setattr(service, 'load_input', lambda *_a: prepared)
    original, children = subprocess.Popen, []
    def popen(_args, **kwargs):
        child = original([sys.executable, '-c', 'import time;time.sleep(5)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(service.subprocess, 'Popen', popen)
    job = manager.job = _job()
    worker = threading.Thread(target=manager._run, args=(job, None, None, None, manager.cancelled))
    worker.start()
    deadline = time.monotonic() + 2
    while manager.process is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.process is not None
    if cancel_requested:
        manager.cancel(job['id'])
    worker.join(2)
    assert not worker.is_alive()
    assert job['state'] == ('cancelled' if cancel_requested else 'failed')
    assert children[0].poll() is not None
