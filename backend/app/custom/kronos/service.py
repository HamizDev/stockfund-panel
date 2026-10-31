# ruff: noqa: RUF001 -- Local Chinese prediction status messages.
"""Single-flight, bounded local jobs. No exchange/account or trading imports."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime
from itertools import islice
from pathlib import Path

from app.config import settings
from app.market_time import cn_now
from app.services.fs_utils import atomic_write_text_exclusive

from .data import load_input
from .limits import MAX_CATALOG_ITEMS, MAX_SAVED_JOBS
from .paths import read_record_bytes, research_path

_DEFAULT_RUNTIME = settings.data_dir / 'kronos-runtime'
RUNTIME = Path(os.environ.get('KRONOS_RUNTIME_DIR', str(_DEFAULT_RUNTIME))).expanduser()
ACTIVE = {'preparing', 'running'}


class BusyError(ValueError):
    pass


class PreparationCancelledError(ValueError):
    pass


def validate_forecast(value: dict, targets: list[str]) -> None:
    if (not isinstance(value, dict) or not isinstance(value.get('forecast'), list)
            or len(value['forecast']) != len(targets)):
        raise ValueError('预测结果的时间窗口不匹配，结果未采用。')
    mode = value.get('forecast_mode', 'ohlc')
    if not isinstance(mode, str) or mode not in {'ohlc', 'close_only'}:
        raise ValueError('预测结果模式无效。')
    if mode == 'close_only' and (not isinstance(value.get('warnings'), list)
            or not any(isinstance(item, str) and '仅展示收盘价预测路径' in item for item in value['warnings'])):
        raise ValueError('仅收盘价预测缺少说明，结果未采用。')
    for row, target in zip(value['forecast'], targets, strict=True):
        if not isinstance(row, dict) or row.get('time') != target:
            raise ValueError('预测结果时间不匹配。')
        if mode == 'close_only':
            if set(row) != {'time', 'close'}:
                raise ValueError('仅收盘价模式不得包含开高低价。')
            close = row['close']
            if type(close) not in (int, float) or not math.isfinite(close) or close <= 0:
                raise ValueError('预测收盘价无效，结果未采用。')
            continue
        prices = [row.get(key) for key in ('open', 'high', 'low', 'close')]
        if not all(type(price) in (int, float) and math.isfinite(price) and price > 0 for price in prices):
            raise ValueError('预测价格无效，结果未采用。')
        op, hi, lo, cl = prices
        if hi < max(op, cl) or lo > min(op, cl) or lo > hi:
            raise ValueError('预测高低价关系异常，结果未采用。')
    metrics = value.get('metrics')
    if not isinstance(metrics, dict) or any(
        type(metrics.get(key)) not in (int, float) or not math.isfinite(metrics[key]) or metrics[key] < 0
        for key in ('elapsed_seconds', 'cuda_peak_allocated_mb', 'cuda_peak_reserved_mb')
    ):
        raise ValueError('预测资源统计无效，结果未采用。')
    warnings = value.get('warnings', [])
    if (not isinstance(warnings, list) or len(warnings) > 8
            or any(not isinstance(item, str) or len(item) > 300 for item in warnings)):
        raise ValueError('预测说明无效，结果未采用。')


def saved_job_summary(job: dict) -> dict | None:
    request = job.get('request')
    if not isinstance(request, dict):
        raise ValueError('预测记录参数无效。')
    if request.get('model') == 'small':
        return None
    identity, requested = job.get('id'), job.get('requested_at')
    if (not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity)
            or not isinstance(requested, str) or datetime.fromisoformat(requested).tzinfo is None
            or request.get('model') != 'base'
            or not isinstance(request.get('symbol'), str)
            or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', request['symbol'])
            or not isinstance(request.get('name'), str) or not request['name'].strip()
            or not isinstance(request.get('period'), str) or request['period'] not in {'daily', '1m'}
            or type(request.get('lookback')) is not int or request['lookback'] not in {60, 120, 240, 400, 512}
            or (request['period'] == '1m' and request['lookback'] > 240)
            or type(request.get('horizon')) is not int or not 1 <= request['horizon'] <= 20
            or (request['period'] == 'daily' and request['horizon'] not in {5, 10, 20})
            or type(request.get('samples')) is not int or request['samples'] not in {1, 3, 5}
            or not isinstance(job.get('state'), str) or job['state'] not in ACTIVE | {'complete', 'failed', 'cancelled'}):
        raise ValueError('预测记录摘要无效。')
    return {'id': identity, 'state': job['state'], 'requested_at': requested,
            'request': {key: request[key] for key in ('symbol', 'name', 'model', 'period', 'lookback', 'horizon', 'samples')}}


class JobManager:
    def __init__(self, runtime: Path = RUNTIME, *, timeout: float = 180, record_scope: str = 'results'):
        if not isinstance(record_scope, str) or record_scope not in {'results', 'historical_results'}:
            raise ValueError('预测记录范围无效。')
        self.runtime = runtime
        self.timeout = timeout
        self.record_scope = record_scope
        self.lock = threading.Lock()
        self.job = None
        self.persisted_job_id = None
        self.cancelled = threading.Event()
        self.process = None
        self.preparing_thread = None
        self.stopping = False
        self.owner = None
        self.finished = threading.Event()
        self.finished.set()
        self._catalog_cache = {}

    def reserve(self, owner: str) -> None:
        with self.lock:
            if (self.stopping or self.owner is not None
                    or (self.job and self.job['state'] in ACTIVE)
                    or (self.preparing_thread and self.preparing_thread.is_alive())):
                raise BusyError('已有预测或配置对比正在运行，请等待结束。')
            self.owner = owner

    def release(self, owner: str) -> None:
        with self.lock:
            if self.owner == owner:
                self.owner = None

    def status(self) -> dict:
        ready = all((self.runtime / rel).is_file() for rel in (
            ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python'), 'runner.py', 'ready.json',
            'models/base/model.safetensors',
            'models/tokenizer/model.safetensors'))
        with self.lock:
            active = self.job is not None and self.job['state'] in ACTIVE
            draining = self.preparing_thread is not None and self.preparing_thread.is_alive()
            busy = active or draining or self.owner is not None
            identity = self.job['id'] if active else None
        return {'ready': ready, 'busy': busy, 'active_job_id': identity,
                'device': 'cuda', 'models': ['base'],
                'message': ('上次行情请求正在结束，暂不能开始新预测' if draining and not active
                            else '按需加载，任务结束释放GPU显存' if ready else '本机推理环境尚未通过检查')}

    def is_busy(self) -> bool:
        with self.lock:
            return bool((self.job and self.job['state'] in ACTIVE) or self.owner is not None
                        or (self.preparing_thread and self.preparing_thread.is_alive()))

    def start(self, request: dict, repo, capset, *, owner=None, prepared=None) -> dict:
        if request.get('model', 'base') != 'base' or (prepared is not None and prepared.get('model') != 'base'):
            raise ValueError('Small已停用，新预测仅支持Base；历史原件保留。')
        if not self.status()['ready']:
            raise ValueError('本机预测运行环境未就绪。')
        folder = research_path(self.runtime, self.record_scope)
        folder.mkdir(exist_ok=True)
        if sum(1 for _ in islice(folder.glob('*.json'), MAX_SAVED_JOBS)) >= MAX_SAVED_JOBS:
            raise ValueError(f'已保存{MAX_SAVED_JOBS}次预测，请先整理本机预测记录后再继续。')
        with self.lock:
            if (self.stopping or (self.owner is not None and owner != self.owner)
                    or (self.job and self.job['state'] in ACTIVE)
                    or (self.preparing_thread is not None and self.preparing_thread.is_alive())):
                raise BusyError('已有预测任务正在运行，请等待完成或取消。')
            now = cn_now()
            normalized_request = dict(request)
            normalized_request['model'] = 'base'
            normalized_request['name'] = request.get('name', '').strip() or request['symbol']
            job = {'id': uuid.uuid4().hex, 'state': 'preparing', 'request': normalized_request,
                   'requested_at': now.isoformat(), 'message': '正在获取并核验指定标的行情',
                   'error': None, 'result': None}
            self.job = job
            self.cancelled = threading.Event()
            cancel = self.cancelled
            done = self.finished = threading.Event()
            worker = threading.Thread(target=self._run, args=(job, repo, capset, now, cancel, prepared, done),
                                      name='kronos-one-job', daemon=True)
            worker.start()
            return copy.deepcopy(job)

    def wait_saved(self, identity: str, timeout: float = .2) -> bool:
        with self.lock:
            if self.job is None or self.job['id'] != identity:
                raise ValueError('预测任务上下文已改变。')
            done = self.finished
        return done.wait(timeout)

    def prepare(self, request, repo, capset, now, cancel, started=None):
        started = started if started is not None else time.monotonic()
        channel = queue.Queue(maxsize=1)
        def fetch():
            try:
                channel.put((True, load_input(repo, capset, request, now)))
            except Exception as exc:
                channel.put((False, exc))
        preparation = threading.Thread(target=fetch, name='kronos-data-only', daemon=True)
        with self.lock:
            self.preparing_thread = preparation
        preparation.start()
        while True:
            if cancel.is_set():
                raise PreparationCancelledError('任务已取消；在途行情请求仅收尾，不会启动GPU预测')
            if time.monotonic() - started > min(60, self.timeout):
                raise ValueError('行情准备超时，预测未启动；在途请求结束前不会重复取数。')
            try:
                succeeded, prepared = channel.get(timeout=.2)
                if not succeeded:
                    raise prepared
                preparation.join(timeout=1)
                if preparation.is_alive():
                    raise ValueError('行情读取线程尚未结束，预测未启动。')
                return prepared
            except queue.Empty:
                pass

    def get(self, identity: str, *, persisted=False) -> dict:
        if not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise ValueError('预测记录编号无效。')
        with self.lock:
            if not persisted and self.job and self.job['id'] == identity and self.persisted_job_id != identity:
                return copy.deepcopy(self.job)
        path = research_path(self.runtime, self.record_scope, identity + '.json')
        if not path.is_file() or path.stat().st_size > 512 * 1024:
            raise LookupError('预测记录不存在；服务重启不会重发旧任务。')
        value = json.loads(read_record_bytes(path, 512 * 1024))
        if not isinstance(value, dict) or value.get('id') != identity:
            raise ValueError('预测记录编号不匹配。')
        return value

    def cancel(self, identity: str) -> dict:
        with self.lock:
            if self.job and self.job['id'] == identity and self.job['state'] in ACTIVE:
                self.cancelled.set()
                self.job['message'] = '正在取消并释放预测进程'
        return self.get(identity)

    def list_saved(self) -> dict:
        """Discover saved runs without retaining or returning their price arrays."""
        folder = research_path(self.runtime, self.record_scope)
        paths = list(islice(folder.glob('*.json'), MAX_SAVED_JOBS + 1))
        if len(paths) > MAX_SAVED_JOBS:
            raise ValueError(f'预测记录超过{MAX_SAVED_JOBS}条，请先整理本机记录。')
        items, skipped = [], 0
        with self.lock:
            active = copy.deepcopy(self.job) if self.job and self.job['state'] in ACTIVE else None
            previous_cache = dict(self._catalog_cache)
        updated_cache = {}
        for path in paths:
            if active and path.stem == active['id']:
                continue
            try:
                safe_path = research_path(self.runtime, self.record_scope, path.name)
                metadata = safe_path.stat()
                signature = (metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size)
                cached = previous_cache.get(path.stem)
                if cached and cached[0] == signature:
                    summary, comparison_job = cached[1], cached[2]
                else:
                    job = self.get(path.stem, persisted=True)
                    summary = saved_job_summary(job)
                    result = job.get('result')
                    if result is not None and not isinstance(result, dict):
                        raise ValueError('预测结果结构无效。')
                    comparison_job = bool(result and result.get('comparison'))
                updated_cache[path.stem] = (signature, summary, comparison_job)
                if summary is not None:
                    items.append(summary)
            except (ValueError, LookupError, OSError):
                skipped += 1
        if active:
            summary = saved_job_summary(active)
            if summary is not None:
                items.append(summary)
        items.sort(key=lambda item: (datetime.fromisoformat(item['requested_at']), item['id']), reverse=True)
        with self.lock:
            self._catalog_cache = updated_cache
        return {'items': copy.deepcopy(items[:MAX_CATALOG_ITEMS]), 'skipped': skipped,
                'total': len(items), 'limit': MAX_SAVED_JOBS, 'truncated': len(items) > MAX_CATALOG_ITEMS}

    def legacy_count(self, symbol=None, period=None) -> int:
        self.list_saved()
        with self.lock:
            summaries = list(self._catalog_cache.values())
        return sum(1 for _signature, summary, comparison_job in summaries
                   if summary is not None and not comparison_job
                   and (not symbol or summary['request']['symbol'] == symbol)
                   and (not period or summary['request']['period'] == period))

    def stop(self) -> None:
        with self.lock:
            self.stopping = True
            self.cancelled.set()
            process = self.process
        if process is not None and process.poll() is None:
            process.kill()

    def _update(self, job, **updates):
        with self.lock:
            job.update(updates)

    def _finish(self, job):
        with self.lock:
            saved = copy.deepcopy(job)
        target = research_path(self.runtime, self.record_scope, job['id'] + '.json')
        data = json.dumps(saved, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(data) > 512 * 1024:
            raise ValueError('预测记录超出大小上限。')
        atomic_write_text_exclusive(target, data.decode('utf-8'))
        with self.lock:
            if self.job is job:
                # The terminal result is available on disk. Retain only status
                # metadata in RAM; get() loads the saved result on demand.
                self.job = {key: value for key, value in job.items() if key != 'result'}
                self.job['result'] = None
                self.persisted_job_id = job['id']

    def _run(self, job, repo, capset, now, cancel, frozen=None, done=None):
        process = None
        writer = None
        started = time.monotonic()
        try:
            prepared = copy.deepcopy(frozen) if frozen is not None else self.prepare(
                job['request'], repo, capset, now, cancel, started)
            if cancel.is_set():
                self._update(job, state='cancelled', message='任务已取消')
                return
            wire = {key: prepared[key] for key in ('model', 'history', 'future_times', 'samples')}
            encoded = json.dumps(wire, ensure_ascii=False, allow_nan=False).encode('utf-8')
            fingerprint = hashlib.sha256(encoded).hexdigest()
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP',
                                      'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}}
            env.update(PYTHONNOUSERSITE='1', PYTHONUTF8='1', HF_HUB_OFFLINE='1')
            temp_dir = research_path(self.runtime, self.record_scope)
            with tempfile.TemporaryFile(dir=temp_dir) as out, tempfile.TemporaryFile(dir=temp_dir) as err:
                with self.lock:
                    if cancel.is_set():
                        job.update(state='cancelled', message='任务已取消，GPU预测未启动')
                        return
                    process = subprocess.Popen([str(self.runtime / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')),
                                                str(self.runtime / 'runner.py')], cwd=self.runtime,
                        stdin=subprocess.PIPE, stdout=out, stderr=err, env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    self.process = process
                    job.update(state='running', message='模型正在GPU预测，结束后自动释放显存')
                input_error = threading.Event()
                def send_input():
                    try:
                        process.stdin.write(encoded)
                        process.stdin.close()
                    except (OSError, ValueError):
                        input_error.set()
                writer = threading.Thread(target=send_input, name='kronos-input-only', daemon=True)
                writer.start()
                while process.poll() is None:
                    if cancel.is_set() or time.monotonic() - started > self.timeout:
                        process.kill()
                        process.wait()
                        if cancel.is_set():
                            self._update(job, state='cancelled', message='任务已取消，预测进程已退出')
                            return
                        raise ValueError('预测超过本机时限，进程已退出，请缩短窗口或减少采样。')
                    if input_error.is_set():
                        process.kill()
                        process.wait()
                        raise ValueError('无法向模型提交完整行情，预测进程已退出。')
                    if os.fstat(out.fileno()).st_size > 256 * 1024 or os.fstat(err.fileno()).st_size > 64 * 1024:
                        process.kill()
                        process.wait()
                        raise ValueError('模型输出超过安全限额，预测进程已退出。')
                    cancel.wait(0.15)
                out.seek(0)
                err.seek(0)
                output, errors = out.read(256 * 1024 + 1), err.read(64 * 1024)
            if cancel.is_set():
                self._update(job, state='cancelled', message='任务已取消，预测进程已退出')
                return
            if process.returncode != 0 or len(output) > 256 * 1024:
                if errors:
                    research_path(self.runtime, self.record_scope, job['id'] + '.stderr.log').write_bytes(
                        errors[-20000:])
                raise ValueError('本地模型未产生有效预测，已保留诊断记录；不会使用失败结果。')
            forecast = json.loads(output)
            validate_forecast(forecast, prepared['future_times'])
            result = {key: value for key, value in prepared.items() if key not in {'future_times', 'samples'}}
            result.update(forecast=forecast['forecast'], forecast_mode=forecast.get('forecast_mode', 'ohlc'),
                          metrics=forecast['metrics'])
            result['warnings'] = prepared['warnings'] + forecast.get('warnings', [])
            result['input_sha256'] = fingerprint
            result['worker_metadata'] = {key: forecast[key] for key in (
                'model_sha256', 'tokenizer_sha256', 'seed', 'temperature', 'top_p', 'samples', 'path_method') if key in forecast}
            result['completed_at'] = cn_now().isoformat()
            with self.lock:
                if cancel.is_set():
                    job.update(state='cancelled', message='任务已取消，预测结果未采用')
                else:
                    job.update(state='complete', message='预测完成，GPU进程已退出', result=result)
        except PreparationCancelledError as exc:
            self._update(job, state='cancelled', message=str(exc))
        except ValueError as exc:
            self._update(job, state='failed', message='预测未完成', error=str(exc)[:300])
        except Exception:
            self._update(job, state='failed', message='预测未完成', error='行情读取或本地预测异常，请检查数据源和本机环境后重试。')
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            if writer is not None:
                writer.join(timeout=1)
            with self.lock:
                if self.process is process:
                    self.process = None
            try:
                self._finish(job)
            except Exception:
                self._update(job, state='failed', result=None, error='预测记录保存失败，结果未采用。')
            finally:
                if done is not None:
                    done.set()


MANAGER = JobManager()
