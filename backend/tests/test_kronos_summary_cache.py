import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.custom.kronos.comparison_scores import summarize
from app.custom.kronos.comparison_store import digest
from app.custom.kronos.comparisons import ComparisonManager


@pytest.fixture
def summary_case(tmp_path):
    identity = 'a' * 32
    job_id = 'b' * 32
    jobs = type('Jobs', (), {'runtime': tmp_path, 'legacy_count': lambda *_args: 0})()
    evaluator = object()
    manager = ComparisonManager(jobs, evaluator)
    trial = {
        'id': 't01', 'model': 'base', 'lookback': 120, 'state': 'complete',
        'job_id': job_id, 'error': None, 'timely': True,
    }
    trial2 = copy.deepcopy(trial) | {'id': 't02', 'lookback': 240}
    batch = {
        'schema_version': 1,
        'id': identity, 'state': 'complete',
        'request': {'symbol': '510300.SH', 'period': '1m', 'models': ['base'],
                    'lookbacks': [120, 240], 'horizon': 20, 'samples': 3},
        'snapshot': {'input_as_of': '2026-11-09T14:30', 'price_basis': 'p',
                     'implementation_sha256': 'c' * 64},
        'created_at': '2026-11-09T14:30:05+08:00', 'trials': [copy.deepcopy(trial), trial2],
        'profile': {'model_hashes': {'base': 'd' * 64, 'tokenizer': 'e' * 64}},
    }
    manager.store.write(batch, new=True)
    evaluation = {
        'state': 'complete', 'expected': 20, 'matched': 20,
        'points': [{'time': '2026-11-09T14:31', 'actual_close': 1.0}],
        'metrics': {'mae': 0.01, 'mape': 0.01, 'baseline_mae': 0.02,
                    'direction_accuracy': 0.5},
    }
    trial_detail = copy.deepcopy(trial) | {
        'job': {'id': job_id, 'state': 'complete', 'result': {
            'worker_metadata': {'model_sha256': 'd' * 64, 'tokenizer_sha256': 'e' * 64},
            'metrics': {'elapsed_seconds': 30.0, 'cuda_peak_allocated_mb': 430.0,
                        'cuda_peak_reserved_mb': 450.0},
        }},
        'evaluation': copy.deepcopy(evaluation),
    }
    trial2_detail = copy.deepcopy(trial2) | {
        'job': {'id': job_id, 'state': 'complete', 'result': {
            'worker_metadata': {'model_sha256': 'd' * 64, 'tokenizer_sha256': 'e' * 64},
            'metrics': {'elapsed_seconds': 31.0, 'cuda_peak_allocated_mb': 440.0,
                        'cuda_peak_reserved_mb': 460.0},
        }},
        'evaluation': copy.deepcopy(evaluation),
    }
    detail = copy.deepcopy(batch) | {'trials': [trial_detail, trial2_detail]}
    # Chart arrays must never be retained in a score-only cache.
    for row in detail['trials']:
        row['job']['result']['history'] = [{'close': 123.456}] * 240
        row['job']['result']['forecast'] = [{'close': 123.456}] * 20
    for scope in ('results', 'evaluations'):
        folder = tmp_path / scope
        folder.mkdir()
        (folder / (job_id + '.json')).write_text('{}', encoding='utf-8')
    return manager, batch, detail


def test_summary_reuses_only_matching_evidence(summary_case, monkeypatch):
    manager, batch, detail = summary_case
    identity, job_id = batch['id'], detail['trials'][0]['job_id']
    calls = []

    def fake_full(_batch):
        calls.append('full')
        return copy.deepcopy(detail)

    manager._score_detail = fake_full
    first = summarize(manager)
    second = summarize(manager)
    assert first == second
    assert first['batches'] == 1
    assert manager.score_store.read(identity)['detail']['trials'][0]['evaluation']['metrics']['direction_accuracy'] == 0.5
    assert calls == ['full']
    cached = manager.score_store.read(identity)['detail']
    assert 'history' not in cached['trials'][0]['job']['result']
    assert 'forecast' not in cached['trials'][0]['job']['result']
    assert first == summarize(type('Full', (), {
        'jobs': manager.jobs, 'details': lambda *_args: {'items': [detail]},
    })())

    # Invalidate using real evidence, not only a changed mocked aggregate.
    evaluation_path = manager.jobs.runtime / 'evaluations' / (job_id + '.json')
    evaluation_path.write_text('{"state":"complete"}', encoding='utf-8')
    changed = copy.deepcopy(detail)
    changed['trials'][0]['evaluation']['metrics']['direction_accuracy'] = 0.75

    def changed_full(_batch):
        calls.append('changed')
        return copy.deepcopy(changed)

    monkeypatch.setattr(manager, '_score_detail', changed_full)
    updated = summarize(manager)

    assert updated['groups'][0]['direction_accuracy'] == 0.75
    assert calls == ['full', 'changed']


@pytest.mark.parametrize('damage', ['null', 'empty', 'truncated', 'old_version', 'changed_detail'])
def test_bad_cache_falls_back_to_originals(summary_case, monkeypatch, damage):
    manager, batch, detail = summary_case
    calls = []
    monkeypatch.setattr(manager, '_score_detail', lambda _batch: calls.append(1) or copy.deepcopy(detail))
    expected = summarize(manager)
    path = manager.score_store.path(batch['id'])
    saved = json.loads(path.read_bytes())
    if damage == 'truncated':
        path.write_text('{', encoding='utf-8')
    else:
        if damage == 'null':
            saved['detail'] = None
        elif damage == 'empty':
            saved['detail'] = {}
            saved['detail_sha256'] = digest({})
        elif damage == 'old_version':
            saved['format_version'] = 2
        else:
            saved['detail']['trials'][0]['evaluation']['metrics']['mae'] = 99
        path.write_text(json.dumps(saved), encoding='utf-8')
    assert summarize(manager) == expected
    assert len(calls) == 2


@pytest.mark.parametrize('scope', ['results', 'evaluations', 'comparisons'])
def test_evidence_change_revalidates(summary_case, monkeypatch, scope):
    manager, batch, detail = summary_case
    calls = []
    monkeypatch.setattr(manager, '_score_detail', lambda _batch: calls.append(1) or copy.deepcopy(detail))
    summarize(manager)
    identity = batch['id'] if scope == 'comparisons' else detail['trials'][0]['job_id']
    path = manager.jobs.runtime / scope / (identity + '.json')
    # Whitespace leaves decoded content identical but changes the actual evidence.
    path.write_bytes(path.read_bytes() + b' ')
    summarize(manager)
    # Comparisons are decoded canonically; changing its real fields invalidates.
    if scope == 'comparisons':
        changed = manager.store.read(identity) | {'updated_at': '2026-11-09T14:40:00+08:00'}
        manager.store.write(changed)
        summarize(manager)
    assert len(calls) == 2


def test_changed_evidence_during_validation_is_not_cached(summary_case, monkeypatch):
    manager, batch, detail = summary_case
    def reading(_batch):
        path = manager.jobs.runtime / 'evaluations' / (detail['trials'][0]['job_id'] + '.json')
        path.write_bytes(path.read_bytes() + b' ')
        return copy.deepcopy(detail)
    monkeypatch.setattr(manager, '_score_detail', reading)
    assert summarize(manager)['batches'] == 1
    assert not manager.score_store.path(batch['id']).exists()


@pytest.mark.parametrize('changed_path', [False, True])
def test_projection_preserves_paired_origins_and_timeliness(summary_case, changed_path):
    manager, _batch, detail = summary_case
    detail['trials'][1]['timely'] = False
    if changed_path:
        detail['trials'][1]['evaluation']['points'][0]['actual_close'] = 2.0
    duplicate = copy.deepcopy(detail) | {'id': 'f' * 32, 'created_at': '2026-11-09T14:32:00+08:00'}
    compact = [manager._compact_score_detail(row) for row in (duplicate, detail)]
    def wrapper(rows):
        return type('Rows', (), {'jobs': manager.jobs, 'iter_details': lambda *_args: iter(rows)})()
    original = summarize(wrapper([detail, duplicate]))
    assert summarize(wrapper(compact)) == original
    assert original['groups'][0]['complete_origins'] == (0 if changed_path else 1)
    assert original['groups'][1]['timely_origins'] == 0


def test_view_validates_against_sealed_snapshot(summary_case, monkeypatch):
    manager, batch, detail = summary_case
    batch['snapshot_data'] = {'history': [{'close': 1.0}]}
    seen = []
    monkeypatch.setattr(manager.jobs, 'get', lambda *_args, **_kwargs: detail['trials'][0]['job'], raising=False)
    manager.evaluator = type('Evaluator', (), {'get': lambda *_args, **_kwargs: detail['trials'][0]['evaluation']})()
    def validate(frozen_batch, _trial, _job):
        seen.append(frozen_batch['snapshot_data'])
    monkeypatch.setattr(manager, 'validate_trial', validate)
    for trial in batch['trials']:
        trial['output_sha256'] = 'e' * 64
    view = manager._view(batch, True)
    assert len(seen) == 2
    assert 'snapshot_data' not in view
    assert all(trial['evaluation']['state'] == 'complete' for trial in view['trials'])


@pytest.mark.parametrize('state', ['pending', 'partial', 'unavailable'])
def test_unfinished_evaluation_always_reloads_originals(summary_case, monkeypatch, state):
    manager, batch, detail = summary_case
    detail['trials'][0]['evaluation']['state'] = state
    calls = []
    monkeypatch.setattr(manager, '_score_detail', lambda _batch: calls.append(1) or copy.deepcopy(detail))
    first = summarize(manager)
    assert summarize(manager) == first
    assert all(group['complete_origins'] == 0 for group in first['groups'])
    assert len(calls) == 2
    assert not manager.score_store.path(batch['id']).exists()


def test_concurrent_summary_requests_share_cache_build(summary_case, monkeypatch):
    manager, _batch, detail = summary_case
    calls = []
    monkeypatch.setattr(manager, '_score_detail', lambda _batch: calls.append(1) or copy.deepcopy(detail))
    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = [executor.submit(summarize, manager) for _ in range(2)]
        first, second = [request.result(timeout=10) for request in requests]
    assert first == second
    assert len(calls) == 1


def test_summary_uses_iterable_batches_without_legacy_fallback(tmp_path):
    jobs = type('Jobs', (), {'runtime': tmp_path, 'legacy_count': lambda *_args: 0})()
    manager = ComparisonManager(jobs, object())
    batch = {
        'schema_version': 1,
        'id': 'a' * 32, 'state': 'failed', 'snapshot': None,
            'request': {'symbol': '510300.SH', 'period': '1m', 'models': ['base']}, 'trials': [],
        'created_at': '2026-11-09T14:30:05+08:00',
    }
    manager.store.write(batch, new=True)

    result = summarize(manager)

    assert result['batches'] == 1
    assert result['groups'] == []
    assert result['legacy_jobs'] == 0
