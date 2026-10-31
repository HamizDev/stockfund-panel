# ruff: noqa: RUF001 -- Chinese research messages and exact price-basis contract.
"""Synthetic end-to-end comparison, scoring and candidate persistence."""
import copy
import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.custom.kronos import comparisons, routes
from app.custom.kronos.candidates import CandidateCatalog
from app.custom.kronos.comparison_scores import summarize
from app.custom.kronos.comparison_store import ResearchStore
from app.custom.kronos.evaluation import EvaluationManager, score
from app.market_time import CN_TZ

NOW = datetime(2026, 10, 26, 22, tzinfo=CN_TZ)
PROFILE = {'model_hashes': {'base': 'b' * 64, 'tokenizer': 'c' * 64},
           'implementation_sha256': 'd' * 64, 'runner_sha256': 'e' * 64}
REQUEST = {'symbol': '515880.SH', 'name': '通信ETF', 'period': 'daily',
           'models': ['base'], 'lookbacks': [60, 120], 'horizon': 5, 'samples': 1}


class FakeJobs:
    def __init__(self, runtime):
        self.runtime = runtime
        (runtime / 'results').mkdir()
        self.records, self.inputs = {}, []
        self.prepare_count, self.owner = 0, None
        self.fail_model = None
        self.block = None

    def reserve(self, owner):
        assert self.owner is None
        self.owner = owner

    def release(self, owner):
        assert self.owner == owner
        self.owner = None

    def prepare(self, request, _repo, _capset, _now, _cancel):
        self.prepare_count += 1
        if self.block:
            self.block.wait(3)
        rows = []
        for offset in range(request['lookback'] - 1, -1, -1):
            stamp = (NOW.date() - timedelta(days=offset)).isoformat()
            rows.append({'time': stamp, 'open': 1., 'high': 1.1, 'low': .9,
                         'close': 1., 'volume': 100., 'amount': 100.})
        return {'symbol': request['symbol'], 'name': request['name'], 'model': request['model'],
                'period': request['period'], 'asset_type': 'etf', 'history': rows,
                'future_times': ['2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-11-02'],
                'samples': request['samples'], 'input_as_of': rows[-1]['time'],
                'requested_at': NOW.isoformat(), 'price_basis': '原始价格（未复权）', 'warnings': []}

    def start(self, request, _repo, _capset, *, owner, prepared):
        assert owner == self.owner
        self.inputs.append(copy.deepcopy(prepared))
        wire = {key: prepared[key] for key in ('model', 'history', 'future_times', 'samples')}
        result = {key: copy.deepcopy(value) for key, value in prepared.items() if key not in {'future_times', 'samples'}}
        result.update(forecast=[{'time': stamp, 'open': 1., 'high': 1.1, 'low': .9, 'close': 1.02}
                                for stamp in prepared['future_times']], forecast_mode='ohlc',
                      completed_at=(NOW + timedelta(seconds=5)).isoformat(),
                      input_sha256=hashlib.sha256(json.dumps(wire, ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
                      metrics={'elapsed_seconds': 5., 'cuda_peak_allocated_mb': 100., 'cuda_peak_reserved_mb': 120.},
                      worker_metadata={'model_sha256': PROFILE['model_hashes'][request['model']],
                          'tokenizer_sha256': PROFILE['model_hashes']['tokenizer'], 'seed': 42,
                          'temperature': .6, 'top_p': .9, 'samples': request['samples']})
        identity = uuid.uuid4().hex
        job = {'id': identity, 'state': 'failed' if request['model'] == self.fail_model else 'complete',
               'request': copy.deepcopy(request), 'result': result, 'error': None,
               'requested_at': NOW.isoformat(), 'message': ''}
        self.records[identity] = job
        (self.runtime / 'results' / (identity + '.json')).write_text(json.dumps(job), encoding='utf-8')
        return copy.deepcopy(job)

    def wait_saved(self, *_):
        return True

    def get(self, identity, *, persisted=False):
        if persisted:
            return json.loads((self.runtime / 'results' / (identity + '.json')).read_text())
        return copy.deepcopy(self.records[identity])

    def overwrite(self, identity):
        (self.runtime / 'results' / (identity + '.json')).write_text(json.dumps(self.records[identity]), encoding='utf-8')


class Evaluator:
    def __init__(self, jobs):
        self.jobs = jobs
        self.complete = True
        self.inconsistent = False

    def get(self, identity, *, expected_digest=None):
        job = self.jobs.get(identity)
        prices = {row['time']: 1.03 for row in job['result']['forecast']} if self.complete else {}
        if self.inconsistent and job['request']['model'] == 'base' and job['request']['lookback'] == 60:
            prices[job['result']['forecast'][0]['time']] = 1.04
        return score(job, prices, datetime(2026, 11, 5, tzinfo=CN_TZ))


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(comparisons, 'cn_now', lambda: NOW)
    jobs = FakeJobs(tmp_path)
    return comparisons.ComparisonManager(jobs, Evaluator(jobs), lambda _: copy.deepcopy(PROFILE))


def run(manager):
    batch = manager.start(copy.deepcopy(REQUEST), None, None)
    manager.thread.join(4)
    assert not manager.thread.is_alive()
    return manager.get(batch['id'])


def test_all_configurations_share_one_immutable_snapshot(manager):
    batch = run(manager)
    assert batch['state'] == 'complete'
    assert manager.jobs.prepare_count == 1
    assert len(manager.jobs.inputs) == 2
    assert {item['model'] for item in manager.jobs.inputs} == {'base'}
    assert {len(item['history']) for item in manager.jobs.inputs} == {60, 120}
    assert len({item['comparison']['snapshot_sha256'] for item in manager.jobs.inputs}) == 1
    assert len({item['history'][-1]['time'] for item in manager.jobs.inputs}) == 1
    assert len({tuple(item['future_times']) for item in manager.jobs.inputs}) == 1
    saved = manager.store.read(batch['id'])['snapshot_data']
    assert len(saved['history']) == 120 and saved['model'] == 'base'
    assert all(trial['timely'] is True for trial in batch['trials'])
    assert manager.jobs.owner is None


def test_complete_pairs_have_scores_but_no_automatic_winner(manager):
    run(manager)
    groups = summarize(manager)['groups']
    assert len(groups) == 2
    assert all(row['origins'] == row['complete_origins'] == 1 for row in groups)
    assert all(row['direction_accuracy'] == 1 and row['mae'] == pytest.approx(.01) for row in groups)
    assert all(row['eligible_for_ranking'] is False for row in groups)


@pytest.mark.parametrize('reason', ['missing', 'failed', 'inconsistent'])
def test_unpaired_or_unverified_actuals_never_produce_scores(manager, reason):
    if reason == 'missing':
        manager.evaluator.complete = False
    elif reason == 'failed':
        manager.jobs.fail_model = 'base'
    else:
        manager.evaluator.inconsistent = True
    run(manager)
    groups = summarize(manager)['groups']
    if reason == 'failed':
        assert groups == []
        return
    assert len(groups) == 2
    assert all(row['complete_origins'] == 0 and row['mae'] is None and row['direction_accuracy'] is None for row in groups)


def test_duplicate_origins_do_not_inflate_sample_count(manager):
    run(manager)
    run(manager)
    summary = summarize(manager)
    assert summary['batches'] == 2
    assert all(row['origins'] == row['complete_origins'] == 1 for row in summary['groups'])


def test_cancelled_fetch_never_starts_gpu_trials(manager):
    manager.jobs.block = threading.Event()
    batch = manager.start(copy.deepcopy(REQUEST), None, None)
    manager.cancel(batch['id'])
    manager.jobs.block.set()
    manager.thread.join(3)
    assert manager.get(batch['id'])['state'] == 'cancelled'
    assert manager.jobs.inputs == [] and manager.jobs.owner is None


def test_restart_displays_interrupted_and_never_replays(manager):
    batch = run(manager)
    raw = manager.store.read(batch['id'])
    raw['state'] = 'running'
    manager.store.write(raw)
    restarted = comparisons.ComparisonManager(manager.jobs, manager.evaluator, lambda _: PROFILE)
    assert restarted.get(batch['id'])['state'] == 'interrupted'
    assert manager.jobs.prepare_count == 1
    assert manager.store.read(batch['id'])['state'] == 'running'


def test_tampered_history_is_not_scored_or_saved_as_candidate(manager):
    batch = run(manager)
    trial = batch['trials'][0]
    manager.jobs.records[trial['job_id']]['result']['history'][-1]['close'] = .8
    manager.jobs.overwrite(trial['job_id'])
    view = manager.get(batch['id'])
    assert view['trials'][0]['error'] and view['trials'][0]['evaluation'] is None
    assert all(row['mae'] is None for row in summarize(manager)['groups'])
    with pytest.raises(ValueError):
        CandidateCatalog(manager).save(batch['id'], trial['id'], 'test')


def test_candidate_archiving_never_changes_baseline_or_prediction(manager):
    batch = run(manager)
    prior = copy.deepcopy(manager.jobs.records)
    catalog = CandidateCatalog(manager)
    candidate = catalog.save(batch['id'], batch['trials'][0]['id'], '基线配置候选')
    assert candidate['seed'] == 42 and candidate['state'] == 'registered'
    assert catalog.retire(candidate['id'])['state'] == 'retired'
    assert manager.jobs.records == prior
    with pytest.raises(ValueError, match='已经保存'):
        catalog.save(batch['id'], batch['trials'][0]['id'], 'duplicate')
    assert catalog.list()['training_enabled'] is False
    assert catalog.list()['automatic_promotion'] is False


@pytest.mark.parametrize('field', ['forecast', 'completed_at', 'timely'])
def test_output_is_sealed_before_first_mature_evaluation(manager, field):
    manager.evaluator.complete = False
    batch = run(manager)
    trial = batch['trials'][0]
    result = manager.jobs.records[trial['job_id']]['result']
    if field == 'forecast':
        result['forecast'][0]['close'] = 1.03
    elif field == 'completed_at':
        result['completed_at'] = '2026-11-05T22:00:00+08:00'
    else:
        raw = manager.store.read(batch['id'])
        raw['trials'][0]['timely'] = False
        manager.store.write(raw)
    manager.jobs.overwrite(trial['job_id'])
    manager.evaluator.complete = True
    checked = manager.get(batch['id'])
    assert checked['trials'][0]['error']
    assert checked['trials'][0]['job'] is None
    assert checked['trials'][0]['evaluation'] is None
    assert all(row['mae'] is None for row in summarize(manager)['groups'])
    with pytest.raises(ValueError):
        CandidateCatalog(manager).save(batch['id'], trial['id'], 'changed')


def test_disk_only_tampering_before_real_evaluation_is_rejected(manager):
    batch = run(manager)
    trial = batch['trials'][0]
    memory_original = manager.jobs.get(trial['job_id'])
    altered = copy.deepcopy(memory_original)
    altered['result']['forecast'][0]['close'] = 1.03
    altered['result']['completed_at'] = '2026-11-05T22:00:00+08:00'
    path = manager.jobs.runtime / 'results' / (trial['job_id'] + '.json')
    path.write_text(json.dumps(altered), encoding='utf-8')
    assert manager.jobs.get(trial['job_id']) == memory_original
    manager.jobs.status = lambda: {'busy': False}
    real = EvaluationManager(manager.jobs, lambda *_: {row['time']: 1.03 for row in altered['result']['forecast']})
    real.repo = object()
    for _ in range(3):
        real.cycle(datetime(2026, 11, 5, tzinfo=CN_TZ))
    manager.evaluator = real
    view = manager.get(batch['id'])
    assert view['trials'][0]['error'] and view['trials'][0]['evaluation'] is None
    assert all(row['mae'] is None for row in summarize(manager)['groups'])
    with pytest.raises(ValueError):
        CandidateCatalog(manager).save(batch['id'], trial['id'], 'changed disk only')


def test_evaluation_rechecks_seal_if_file_changes_between_two_reads(manager):
    batch = run(manager)
    real = EvaluationManager(manager.jobs)
    class RacingEvaluator:
        def get(self, identity, *, expected_digest=None):
            path = manager.jobs.runtime / 'results' / (identity + '.json')
            altered = json.loads(path.read_text())
            altered['result']['forecast'][0]['close'] = 1.03
            path.write_text(json.dumps(altered), encoding='utf-8')
            return real.get(identity, expected_digest=expected_digest)
    manager.evaluator = RacingEvaluator()
    view = manager.get(batch['id'])
    assert all(trial['evaluation'] is None and trial['error'] for trial in view['trials'])


@pytest.mark.parametrize('change', [{'models': ['small']}, {'models': ['small', 'base']},
                                  {'models': ['base', 'base']}, {'lookbacks': [True]},
                                  {'lookbacks': [60, 60]}, {'period': '1m', 'lookbacks': [400]},
                                  {'samples': True}, {'horizon': True}, {'lookbacks': [60, 120, 240, 400]}])
def test_matrix_validation_rejects_ambiguous_or_unbounded_requests(change):
    with pytest.raises(ValidationError):
        routes.ComparisonRequest.model_validate(REQUEST | change)


def test_store_rejects_traversal_newer_schema_and_oversize(tmp_path):
    store = ResearchStore(tmp_path, 'comparisons')
    with pytest.raises(ValueError):
        store.read('../outside')
    identity = '1' * 32
    store.write({'schema_version': 2, 'id': identity}, new=True)
    with pytest.raises(ValueError, match='版本不兼容'):
        store.read(identity)
    with pytest.raises(ValueError, match='大小上限'):
        store.write({'schema_version': 1, 'id': '2' * 32, 'text': 'x' * (512 * 1024)}, new=True)


def test_api_routes_are_bounded_and_summary_is_not_identity(manager, monkeypatch):
    monkeypatch.setattr(routes, 'COMPARISONS', manager)
    monkeypatch.setattr(routes, 'CANDIDATES', CandidateCatalog(manager))
    app = FastAPI()
    app.state.repo = None
    app.include_router(routes.build_router())
    with TestClient(app) as client:
        assert client.get('/api/custom/kronos/comparisons/summary').json()['groups'] == []
        assert client.post('/api/custom/kronos/comparisons', json=REQUEST | {'path': '../'}).status_code == 422
        response = client.post('/api/custom/kronos/comparisons', json=REQUEST)
        assert response.status_code == 202
        manager.thread.join(3)
        identity = response.json()['id']
        assert client.get('/api/custom/kronos/comparisons/' + identity).json()['state'] == 'complete'
        assert client.get('/api/custom/kronos/comparisons?symbol=600000.SH').json()['items'] == []


def test_retired_small_history_is_hidden_from_new_views_without_mutating_records(manager):
    base_batch = run(manager)
    mixed = manager.store.read(base_batch['id'])
    mixed['request']['models'] = ['base', 'small']
    small_trials = []
    for index, base_trial in enumerate(mixed['trials'], start=3):
        small_trials.append({
            'id': f't{index:02d}', 'model': 'small', 'lookback': base_trial['lookback'],
            'state': 'complete', 'job_id': 'f' * 31 + str(index - 2),
            'error': None, 'completed_at': base_trial['completed_at'],
            'timely': base_trial['timely'], 'output_sha256': None,
        })
    mixed['trials'].extend(small_trials)
    manager.store.write(mixed)
    mixed_path = manager.store.path(mixed['id'])
    mixed_bytes = mixed_path.read_bytes()

    small_only = copy.deepcopy(mixed)
    small_only['id'] = 'e' * 32
    small_only['request']['models'] = ['small']
    small_only['trials'] = copy.deepcopy(small_trials)
    manager.store.write(small_only, new=True)
    small_only_path = manager.store.path(small_only['id'])
    small_only_bytes = small_only_path.read_bytes()

    for trial in small_trials:
        job = {'id': trial['job_id'], 'state': 'complete',
               'request': {'symbol': '515880.SH', 'model': 'small', 'period': 'daily'},
               'result': {}}
        manager.jobs.records[job['id']] = job
        (manager.jobs.runtime / 'results' / f"{job['id']}.json").write_text(
            json.dumps(job), encoding='utf-8')

    catalog = CandidateCatalog(manager)
    legacy_small = {'schema_version': 1, 'id': 'a' * 32, 'model': 'small',
                    'label': 'retired Small', 'created_at': NOW.isoformat(), 'state': 'registered'}
    legacy_base = {'schema_version': 1, 'id': 'b' * 32, 'model': 'base',
                   'label': 'Base candidate', 'created_at': NOW.isoformat(), 'state': 'registered'}
    catalog.store.write(legacy_small, new=True)
    catalog.store.write(legacy_base, new=True)
    legacy_small_path = catalog.store.path(legacy_small['id'])
    legacy_small_bytes = legacy_small_path.read_bytes()

    for identity, model in (('c' * 32, 'base'), ('d' * 32, 'small')):
        job = {'id': identity, 'state': 'complete',
               'request': {'symbol': '515880.SH', 'model': model, 'period': 'daily'},
               'result': {}}
        manager.jobs.records[identity] = job
        (manager.jobs.runtime / 'results' / f'{identity}.json').write_text(json.dumps(job), encoding='utf-8')

    listed = manager.list()
    assert [row['id'] for row in listed['items']] == [mixed['id']]
    assert {trial['model'] for trial in listed['items'][0]['trials']} == {'base', 'small'}
    summary = summarize(manager)
    assert summary['batches'] == 1
    assert summary['legacy_jobs'] == 1
    assert len(summary['groups']) == 2 and {row['model'] for row in summary['groups']} == {'base'}
    assert all(row['complete_origins'] == 0 for row in summary['groups'])

    choices = catalog.list()
    assert [row['id'] for row in choices['baselines']] == ['base']
    assert [row['id'] for row in choices['items']] == [legacy_base['id']]
    with pytest.raises(ValueError, match='Small已停用'):
        catalog.save(mixed['id'], small_trials[0]['id'], 'rejected Small candidate')

    assert mixed_path.read_bytes() == mixed_bytes
    assert small_only_path.read_bytes() == small_only_bytes
    assert legacy_small_path.read_bytes() == legacy_small_bytes
