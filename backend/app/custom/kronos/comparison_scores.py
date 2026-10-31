# ruff: noqa: RUF001 -- Chinese research messages and exact price-basis contract.
"""Aggregate complete paired origins, keeping missing and delayed evidence explicit."""
from __future__ import annotations

import math
from itertools import islice

from .comparison_store import digest


def average(values):
    values = [value for value in values if type(value) in (int, float) and math.isfinite(value)]
    return math.fsum(values) / len(values) if values else None


def summarize(comparisons, symbol=None, period=None):
    stream = getattr(comparisons, 'iter_details', None)
    batches = stream(symbol, period) if callable(stream) else iter(sorted(
        comparisons.details(symbol, period)['items'], key=lambda row: row['created_at']))
    batch_count = 0
    groups = {}
    for batch in batches:
        batch_count += 1
        snap, request = batch['snapshot'], batch['request']
        if snap is None:
            continue
        trials = batch['trials']
        origin = (request['symbol'], request['period'], snap['input_as_of'])
        matrix = {'models': request['models'], 'lookbacks': request['lookbacks'],
                  'horizon': request['horizon'], 'samples': request['samples']}
        shared = {'symbol': request['symbol'], 'period': request['period'],
                  'price_basis': snap['price_basis'], 'implementation': snap['implementation_sha256'],
                  'matrix': matrix}
        cohort_origin = digest(shared), origin
        paired = batch['state'] == 'complete' and len(trials) >= 2
        actual_paths = []
        for trial in trials:
            evaluation = trial.get('evaluation')
            paired = paired and (trial['state'] == 'complete' and not trial.get('error')
                                 and evaluation is not None and evaluation['state'] == 'complete')
            if evaluation is not None and evaluation['state'] == 'complete':
                actual_paths.append([(point['time'], point['actual_close']) for point in evaluation['points']])
        paired = paired and bool(actual_paths) and all(path == actual_paths[0] for path in actual_paths)
        for trial in trials:
            if trial.get('model') != 'base':
                continue
            job, evaluation = trial.get('job'), trial.get('evaluation')
            if not job or job['state'] != 'complete' or trial.get('error'):
                continue
            result = job['result']
            metadata = result['worker_metadata']
            key = digest(shared | {'model': trial['model'], 'lookback': trial['lookback'],
                                   'weights': metadata['model_sha256'], 'tokenizer': metadata['tokenizer_sha256']})
            group = groups.setdefault(key, {'id': key, 'model': trial['model'], 'lookback': trial['lookback'],
                'symbol': request['symbol'], 'period': request['period'], 'horizon': request['horizon'],
                'samples': request['samples'], 'origins_set': set(),
                'coverage_by_origin': {}, 'scores': {}, 'latencies': [], 'peaks': []})
            group['origins_set'].add(origin)
            group['latencies'].append(result['metrics']['elapsed_seconds'])
            group['peaks'].append(result['metrics']['cuda_peak_reserved_mb'])
            if evaluation is not None:
                previous = group['coverage_by_origin'].get(origin, (0, evaluation['expected']))
                group['coverage_by_origin'][origin] = max(previous, (evaluation['matched'], evaluation['expected']))
            if paired:
                # Retain the same earliest valid paired record as chronological
                # aggregation, without materializing every batch's OHLC arrays.
                candidate = ((batch['created_at'], batch['id']), origin, trial['timely'], evaluation['metrics'])
                previous_score = group['scores'].get(cohort_origin)
                if previous_score is None or candidate[0] < previous_score[0]:
                    group['scores'][cohort_origin] = candidate
    output = []
    for group in groups.values():
        contributions = list(group.pop('scores').values())
        scores = [value[3] for value in contributions]
        origins = len(group.pop('origins_set'))
        complete = len({value[1] for value in contributions})
        timely = len({value[1] for value in contributions if value[2]})
        coverage = list(group.pop('coverage_by_origin').values())
        matched, expected = sum(value[0] for value in coverage), sum(value[1] for value in coverage)
        mae, baseline = average([score['mae'] for score in scores]), average([score['baseline_mae'] for score in scores])
        group.update(origins=origins, complete_origins=complete, timely_origins=timely,
            matched=matched, expected=expected, coverage=matched / expected if expected else None,
            mae=mae, mape=average([score['mape'] for score in scores]), baseline_mae=baseline,
            direction_accuracy=average([score['direction_accuracy'] for score in scores]),
            baseline_skill=1 - mae / baseline if baseline is not None and baseline > 0 and mae is not None else None,
            latency_seconds=average(group.pop('latencies')), peak_mb=max(group.pop('peaks'), default=None),
            eligible_for_ranking=False,
            reason='仅完整成对起点计入误差；重复起点不重复计样本。延后计算不视为实时预测，方向一致率不是交易胜率；不自动评冠军。')
        output.append(group)
    legacy_counter = getattr(comparisons.jobs, 'legacy_count', None)
    legacy = legacy_counter(symbol, period) if callable(legacy_counter) else 0
    legacy_paths = [] if callable(legacy_counter) else sorted(islice((comparisons.jobs.runtime / 'results').glob('*.json'), 100))
    for path in legacy_paths:
        try:
            job = comparisons.jobs.get(path.stem)
            request = job.get('request') or {}
            if ((not symbol or request.get('symbol') == symbol) and (not period or request.get('period') == period)
                    and request.get('model') == 'base'
                    and not (job.get('result') or {}).get('comparison')):
                legacy += 1
        except (ValueError, LookupError, KeyError):
            pass
    return {'groups': sorted(output, key=lambda row: (row['model'], row['lookback'], row['horizon'], row['id'])),
            'batches': batch_count, 'legacy_jobs': legacy, 'training_enabled': False, 'automatic_promotion': False}
