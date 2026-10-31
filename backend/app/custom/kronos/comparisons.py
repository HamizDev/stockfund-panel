# ruff: noqa: RUF001 -- Chinese research messages and exact price-basis contract.
"""One frozen market snapshot, serial existing GPU jobs and immutable provenance."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime
from itertools import islice

from app.market_time import cn_now

from .comparison_store import ResearchStore, digest
from .evaluation import mature
from .limits import MAX_COMPARISON_RECORDS, MAX_SAVED_JOBS
from .provenance import runtime_profile
from .service import BusyError, PreparationCancelledError, validate_forecast

BATCH_ACTIVE = {'preparing', 'running'}


def validate_matrix(request):
    models, windows = request.get('models'), request.get('lookbacks')
    if (not isinstance(models, list) or models != ['base']
            or not isinstance(windows, list) or not 1 <= len(windows) <= 3
            or any(type(window) is not int or window not in (60, 120, 240, 400, 512) for window in windows)
            or len(set(windows)) != len(windows)
            or request.get('period') not in ('daily', '1m')
            or (request['period'] == '1m' and max(windows) > 240)
            or type(request.get('horizon')) is not int
            or (request['period'] == 'daily' and request['horizon'] not in (5, 10, 20))
            or (request['period'] == '1m' and not 1 <= request['horizon'] <= 20)
            or type(request.get('samples')) is not int or request['samples'] not in (1, 3, 5)):
        raise ValueError('配置矩阵无效：仅支持Base、最多三个窗口，分钟窗口最多240根。')


class ComparisonManager:
    def __init__(self, jobs, evaluator, profile_loader=runtime_profile):
        self.jobs, self.evaluator, self.profile_loader = jobs, evaluator, profile_loader
        self.store = ResearchStore(jobs.runtime, 'comparisons', limit=MAX_COMPARISON_RECORDS)
        self.lock = threading.Lock()
        self.active = None
        self.cancelled = threading.Event()
        self.thread = None

    def _save(self, batch):
        batch['updated_at'] = cn_now().isoformat()
        self.store.write(batch)

    def make_identity(self):
        for _ in range(3):
            identity = uuid.uuid4().hex
            if not self.store.path(identity).exists():
                return identity
        raise ValueError('无法分配新的比较记录编号，原记录不会覆盖。')

    def start(self, request, repo, capset, *, identity=None):
        validate_matrix(request)
        request = copy.deepcopy(request)
        request['name'] = request.get('name', '').strip() or request['symbol']
        request['models'] = sorted(request['models'])
        request['lookbacks'] = sorted(request['lookbacks'])
        identity = self.make_identity() if identity is None else identity
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity) or self.store.path(identity).exists():
            raise ValueError('比较记录编号无效或已存在，原记录不会覆盖。')
        self.jobs.reserve(identity)
        started = False
        try:
            with self.lock:
                if self.thread and self.thread.is_alive():
                    raise BusyError('上一个配置对比仍在收尾，请稍后重试。')
                count = len(request['models']) * len(request['lookbacks'])
                if len(list(islice((self.jobs.runtime / 'results').glob('*.json'), MAX_SAVED_JOBS + 1))) + count > MAX_SAVED_JOBS:
                    raise ValueError(f'预测记录接近{MAX_SAVED_JOBS}条上限，无法完整保存本次比较。')
                now = cn_now().isoformat()
                batch = {'schema_version': 1, 'id': identity, 'state': 'preparing', 'request': request,
                    'created_at': now, 'updated_at': now, 'snapshot': None, 'error': None, 'trials': []}
                for model in request['models']:
                    for window in request['lookbacks']:
                        batch['trials'].append({'id': 't' + str(len(batch['trials']) + 1).zfill(2),
                            'model': model, 'lookback': window, 'state': 'waiting', 'job_id': None,
                            'error': None, 'completed_at': None, 'timely': None, 'output_sha256': None})
                self.store.write(batch, new=True)
                self.active, self.cancelled = identity, threading.Event()
                cancel = self.cancelled
                self.thread = threading.Thread(target=self._run, args=(batch, repo, capset, cancel),
                                               name='kronos-comparison', daemon=True)
                self.thread.start()
                started = True
            return self.get(identity)
        except Exception:
            if not started:
                self.jobs.release(identity)
            else:
                self.cancelled.set()
            raise

    def _frozen(self, batch, trial):
        prepared = copy.deepcopy(batch['snapshot_data'] | {
            'history': batch['snapshot_data']['history'][-trial['lookback']:]})
        prepared['model'] = trial['model']
        prepared['comparison'] = {'id': batch['id'], 'trial_id': trial['id'],
            'snapshot_sha256': batch['snapshot']['sha256'],
            'history_sha256': digest(prepared['history']),
            'implementation_sha256': batch['snapshot']['implementation_sha256']}
        return prepared

    def _run(self, batch, repo, capset, cancel):
        try:
            request = batch['request']
            single = {key: request[key] for key in ('symbol', 'name', 'period', 'horizon', 'samples')}
            single.update(model=request['models'][0], lookback=max(request['lookbacks']))
            prepared = self.jobs.prepare(single, repo, capset, cn_now(), cancel)
            profile = self.profile_loader(self.jobs.runtime)
            batch['profile'] = profile
            batch['snapshot_data'] = prepared
            batch['snapshot'] = {'sha256': digest({key: prepared[key] for key in (
                'symbol', 'asset_type', 'period', 'history', 'future_times', 'input_as_of', 'price_basis')}),
                'input_as_of': prepared['input_as_of'], 'price_basis': prepared['price_basis'],
                'history_count': len(prepared['history']), 'future_times': prepared['future_times'],
                'captured_at': cn_now().isoformat(), 'implementation_sha256': profile['implementation_sha256']}
            if len(prepared['history']) != max(request['lookbacks']):
                raise ValueError('冻结行情窗口不完整，本次对比未启动。')
            batch['state'] = 'running'
            self._save(batch)
            for trial in batch['trials']:
                if cancel.is_set():
                    break
                if self.profile_loader(self.jobs.runtime) != profile:
                    raise ValueError('比较期间模型运行代码发生变化，未继续使用混合版本。')
                trial_request = single | {'model': trial['model'], 'lookback': trial['lookback']}
                job = self.jobs.start(trial_request, repo, capset, owner=batch['id'],
                                      prepared=self._frozen(batch, trial))
                trial.update(job_id=job['id'], state='running')
                self._save(batch)
                while not self.jobs.wait_saved(job['id']):
                    if cancel.is_set():
                        self.jobs.cancel(job['id'])
                job = self.jobs.get(job['id'], persisted=True)
                trial['state'] = job['state']
                trial['error'] = job.get('error')
                if job['state'] == 'complete':
                    if self.profile_loader(self.jobs.runtime) != profile:
                        raise ValueError('预测期间运行代码发生变化，该批次不能混合比较。')
                    self.validate_trial(batch, trial, job, sealed=False)
                    trial['completed_at'] = job['result']['completed_at']
                    trial['timely'] = not mature(prepared['future_times'][0], request['period'],
                                                  datetime.fromisoformat(trial['completed_at']))
                    trial['output_sha256'] = digest(job)
                self._save(batch)
            for trial in batch['trials']:
                if trial['state'] == 'waiting':
                    trial['state'] = 'skipped'
            completed = sum(trial['state'] == 'complete' for trial in batch['trials'])
            batch['state'] = ('cancelled' if cancel.is_set() else 'complete'
                              if completed == len(batch['trials']) else 'partial' if completed else 'failed')
        except PreparationCancelledError:
            batch['state'] = 'cancelled'
        except Exception:
            batch.update(state='failed', error='配置对比未完整完成，请核对数据、模型及剩余记录额度；已完成结果保留。')
        finally:
            for trial in batch['trials']:
                if trial['state'] == 'waiting':
                    trial['state'] = 'skipped'
                elif trial['state'] in {'preparing', 'running'} or (trial['state'] == 'complete' and not trial['output_sha256']):
                    trial.update(state='failed', error='此配置未完成或结果未通过版本及输出核验。')
            try:
                self._save(batch)
            finally:
                self.jobs.release(batch['id'])
                with self.lock:
                    if self.active == batch['id']:
                        self.active = None

    def validate_trial(self, batch, trial, job, *, sealed=True):
        result = job.get('result') or {}
        frozen = self._frozen(batch, trial)
        metadata = result.get('worker_metadata') or {}
        profile = batch['profile']
        # The single-job hash records the exact wire encoding, including object key
        # order. ResearchStore sorts its own JSON keys, so reconstruct wire order
        # from the original job history after checking its values against the snapshot.
        wire = {'model': frozen['model'], 'history': result.get('history'),
                'future_times': frozen['future_times'], 'samples': frozen['samples']}
        expected = hashlib.sha256(json.dumps(wire, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        if (job.get('state') != 'complete' or result.get('model') != trial['model']
                or job.get('id') != trial['job_id']
                or result.get('symbol') != batch['request']['symbol']
                or result.get('period') != batch['request']['period']
                or result.get('asset_type') != frozen['asset_type']
                or result.get('price_basis') != frozen['price_basis']
                or result.get('input_as_of') != frozen['input_as_of']
                or result.get('requested_at') != frozen['requested_at']
                or result.get('history') != frozen['history']
                or result.get('comparison') != frozen['comparison']
                or result.get('input_sha256') != expected
                or metadata.get('model_sha256') != profile['model_hashes'][trial['model']]
                or metadata.get('tokenizer_sha256') != profile['model_hashes']['tokenizer']
                or type(metadata.get('seed')) is not int or metadata['seed'] != 42
                or metadata.get('samples') != batch['request']['samples']
                or metadata.get('temperature') != .6 or metadata.get('top_p') != .9):
            raise ValueError('预测记录与冻结比较输入或模型指纹不一致，不参与评分。')
        validate_forecast({'forecast': result.get('forecast'), 'metrics': result.get('metrics'),
                          'forecast_mode': result.get('forecast_mode', 'ohlc'),
                          'warnings': result.get('warnings', [])}, frozen['future_times'])
        try:
            completed = datetime.fromisoformat(result['completed_at'])
            created = datetime.fromisoformat(batch['created_at'])
            requested = datetime.fromisoformat(result['requested_at'])
            if completed.tzinfo is None or completed < created or completed < requested:
                raise ValueError
            timely = not mature(frozen['future_times'][0], batch['request']['period'], completed)
        except (ValueError, KeyError, TypeError):
            raise ValueError('预测完成时间不可核验，成绩未采用。') from None
        if sealed and (trial.get('output_sha256') != digest(job)
                       or trial.get('completed_at') != result['completed_at']
                       or trial.get('timely') is not timely):
            raise ValueError('预测输出或完成时间已变化，不能作为原始事前成绩。')

    def _view(self, batch, detail):
        view = {key: copy.deepcopy(value) for key, value in batch.items()
                if key not in {'snapshot_data', 'profile', 'schema_version'}}
        with self.lock:
            active = self.active
        if view['state'] in BATCH_ACTIVE and view['id'] != active:
            view.update(state='interrupted', error='服务已重启，未重新执行旧比较；已完成记录保留。')
        if detail:
            for trial in view['trials']:
                trial.update(job=None, evaluation=None)
                if trial['job_id']:
                    try:
                        job = self.jobs.get(trial['job_id'], persisted=trial['state'] == 'complete')
                        trial['job'] = job
                        if job['state'] == 'complete':
                            self.validate_trial(batch, trial, job)
                            trial['evaluation'] = self.evaluator.get(job['id'], expected_digest=trial['output_sha256'])
                    except (ValueError, LookupError, KeyError, TypeError):
                        trial.update(job=None, evaluation=None, error='结果或核对记录不可验证，曲线与成绩未采用。')
        return view

    def get(self, identity):
        return self._view(self.store.read(identity), True)

    def list(self, symbol=None, period=None):
        return self._list_views(symbol, period, False)

    def details(self, symbol=None, period=None):
        return self._list_views(symbol, period, True)

    def iter_details(self, symbol=None, period=None):
        # Each file is read once; scores choose the earliest paired origin
        # independently of this bounded filesystem traversal order.
        for identity in self.store.identities():
            try:
                batch = self.store.read(identity)
                request = batch['request']
                if ((symbol and request['symbol'] != symbol) or (period and request['period'] != period)
                        or 'base' not in request.get('models', [])):
                    continue
                yield self._view(batch, True)
            except (ValueError, LookupError, KeyError, TypeError):
                continue

    def _list_views(self, symbol, period, detail):
        items, unavailable = [], 0
        for identity in self.store.identities():
            try:
                batch = self.store.read(identity)
                request = batch['request']
                if (symbol and request['symbol'] != symbol) or (period and request['period'] != period):
                    continue
                if 'base' not in request.get('models', []):
                    continue
                items.append(self._view(batch, detail))
            except (ValueError, LookupError, KeyError, TypeError):
                unavailable += 1
        return {'items': sorted(items, key=lambda row: row['created_at'], reverse=True),
                'unavailable': unavailable}

    def cancel(self, identity):
        self.store.read(identity)
        with self.lock:
            if self.active == identity:
                self.cancelled.set()
        return self.get(identity)

    def stop(self):
        with self.lock:
            self.cancelled.set()
