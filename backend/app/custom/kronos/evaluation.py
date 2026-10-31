# ruff: noqa: RUF001 -- Local Chinese evaluation status messages.
"""Bounded, CPU-only verification of immutable forecasts against completed bars."""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from contextlib import suppress
from datetime import datetime, time, timedelta
from itertools import islice

from app.market_time import CN_TZ, cn_now
from app.services.fs_utils import atomic_write_text_exclusive

from .comparison_store import digest
from .limits import MAX_SAVED_JOBS
from .paths import read_record_bytes, research_path

MAX_BYTES = 512 * 1024
STATES = {'pending', 'partial', 'complete', 'unavailable', 'not_applicable'}


def valid_price(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def target_time(value: str, period: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is not None:
        raise ValueError('预测时间必须为北京时间墙钟。')
    if period == 'daily':
        if len(value) != 10:
            raise ValueError('日K预测时间格式无效。')
    elif len(value) != 16 or not (time(9, 31) <= stamp.time() <= time(11, 30)
                                  or time(13, 1) <= stamp.time() <= time(15)):
        raise ValueError('分钟预测时间不在连续交易时段。')
    return stamp


def mature(value: str, period: str, now: datetime) -> bool:
    stamp = target_time(value, period)
    wall = now.astimezone(CN_TZ).replace(tzinfo=None)
    cutoff = datetime.combine(stamp.date(), time(15, 30)) if period == 'daily' else stamp + timedelta(minutes=1)
    return wall >= cutoff


def payload(job):
    if job.get('state') != 'complete' or not isinstance(job.get('result'), dict):
        raise ValueError('预测尚未完成，暂不能核对。')
    result = job['result']
    if (result.get('period') not in {'daily', '1m'}
            or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', str(result.get('symbol', '')))
            or result.get('asset_type') not in {'stock', 'etf'}
            or result.get('price_basis') != '原始价格（未复权）'):
        raise ValueError('预测价格或资产口径无法核验。')
    request = job.get('request', {})
    if result['symbol'] != request.get('symbol') or result['period'] != request.get('period'):
        raise ValueError('预测上下文不匹配。')
    history, forecast = result.get('history'), result.get('forecast')
    if not isinstance(history, list) or not history or len(history) > 512:
        raise ValueError('缺少生成预测时的真实末价。')
    if not isinstance(forecast, list) or not 1 <= len(forecast) <= 20:
        raise ValueError('预测窗口无效。')
    reference = history[-1].get('close')
    if not valid_price(reference) or history[-1].get('time') != result.get('input_as_of'):
        raise ValueError('预测参考末价或日期无效。')
    prior = target_time(result['input_as_of'], result['period'])
    requested = datetime.fromisoformat(result['requested_at'])
    if requested.tzinfo is None:
        raise ValueError('预测生成时间缺少时区。')
    requested = requested.astimezone(CN_TZ).replace(tzinfo=None, second=0, microsecond=0)
    completed = datetime.fromisoformat(result.get('completed_at') or '')
    if completed.tzinfo is None:
        raise ValueError('预测完成时间缺失或缺少时区，不能核验事前成绩。')
    completed = completed.astimezone(CN_TZ).replace(tzinfo=None)
    first = target_time(forecast[0]['time'], result['period'])
    first_close = (datetime.combine(first.date(), time(15)) if result['period'] == 'daily'
                   else first + timedelta(minutes=1))
    if completed >= first_close:
        raise ValueError('预测完成晚于首个目标收盘，原路径保留，不计事前方向成绩。')
    for row in forecast:
        stamp = target_time(row['time'], result['period'])
        if (stamp <= prior or not valid_price(row.get('close'))
                or (result['period'] == 'daily' and stamp.date() < requested.date())
                or (result['period'] == '1m' and stamp < requested)):
            raise ValueError('预测含已过去或无效时点，不能作为事前成绩。')
        prior = stamp
    return result, float(reference)


def direction(value: float, reference: float) -> int:
    delta = value - reference
    epsilon = max(abs(reference) * 1e-9, 1e-12)
    return 0 if abs(delta) <= epsilon else 1 if delta > 0 else -1


def score(job, actuals: dict, now: datetime, signature=None, evaluated_at=None):
    result, reference = payload(job)
    points, errors, relative, baseline = [], [], [], []
    matches = flat = 0
    for row in result['forecast']:
        actual = actuals.get(row['time']) if mature(row['time'], result['period'], now) else None
        actual = float(actual) if valid_price(actual) else None
        status = ('matched' if actual is not None else 'unavailable') if mature(row['time'], result['period'], now) else 'pending'
        error = row['close'] - actual if actual is not None else None
        if actual is not None and not math.isfinite(abs(error) / actual):
            actual, error, status = None, None, 'unavailable'
        same = None
        if actual is not None:
            same = direction(row['close'], reference) == direction(actual, reference)
            matches += int(same)
            flat += int(direction(actual, reference) == 0)
            errors.append(error)
            relative.append(abs(error) / actual)
            baseline.append(abs(reference - actual))
        points.append({'time': row['time'], 'predicted_close': row['close'], 'actual_close': actual,
                       'status': status, 'error': error, 'direction_match': same})
    count = len(errors)
    pending = sum(point['status'] == 'pending' for point in points)
    missing = len(points) - pending - count
    scale = max((abs(error) for error in errors), default=0)
    metrics = {'mae': math.fsum(abs(error) / count for error in errors) if count else None,
        'rmse': scale * math.sqrt(math.fsum((error / scale) ** 2 / count for error in errors)) if scale else 0. if count else None,
        'mape': math.fsum(value / count for value in relative) if count else None,
        'baseline_mae': math.fsum(value / count for value in baseline) if count else None,
        'direction_matches': matches, 'direction_total': count,
        'direction_accuracy': matches / count if count else None, 'flat_actual_count': flat}
    state = 'complete' if count == len(points) else 'partial' if count else 'pending' if pending == len(points) else 'unavailable'
    message = ('全部预测时点已对照完成。' if state == 'complete' else '等待对应真实行情完成。'
               if state == 'pending' else '仅统计已核对时点；缺报价不计为预测错误。')
    return {'schema_version': 1, 'job_id': job['id'], 'state': state, 'expected': len(points),
        'matched': count, 'pending': pending, 'missing': missing, 'evaluated_at': evaluated_at,
        'message': message, 'reference_close': reference, 'forecast_sha256': signature,
        'points': points, 'metrics': metrics}


class EvaluationManager:
    """One daemon, at most two quote contexts per minute; never loads a model."""
    def __init__(self, jobs, loader=None, interval=60):
        self.jobs = jobs
        self.folder = jobs.runtime / 'evaluations'
        self.loader = loader
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None
        self.cursor = 0
        self.repo = self.capset = None

    def _load(self, identity):
        if not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise ValueError('预测记录编号无效。')
        path = research_path(self.jobs.runtime, 'results', identity + '.json')
        if not path.is_file():
            return self.jobs.get(identity), None
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('预测记录大小或路径无效。')
        raw = read_record_bytes(path, MAX_BYTES)
        job = json.loads(raw)
        if job.get('id') != identity:
            raise ValueError('预测记录编号不匹配。')
        return job, hashlib.sha256(raw).hexdigest()

    def _previous(self, identity, signature):
        try:
            path = research_path(self.jobs.runtime, 'evaluations', identity + '.json')
        except ValueError:
            return None
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        try:
            saved = json.loads(read_record_bytes(path, MAX_BYTES))
            return saved if saved.get('forecast_sha256') == signature and saved.get('job_id') == identity else None
        except (OSError, ValueError):
            return None

    def get(self, identity, *, expected_digest=None):
        job, signature = self._load(identity)
        if expected_digest is not None and digest(job) != expected_digest:
            raise ValueError('核对时预测文件已变化，不能引用其他版本的成绩。')
        previous = self._previous(identity, signature)
        actuals = {point['time']: point['actual_close'] for point in previous.get('points', [])
                   if point.get('status') == 'matched'} if previous else {}
        return score(job, actuals, cn_now(), signature, previous.get('evaluated_at') if previous else None)

    def cycle(self, now=None):
        now = now or cn_now()
        if self.repo is None or self._busy():
            return
        paths = list(islice(research_path(self.jobs.runtime, 'results').glob('*.json'), MAX_SAVED_JOBS + 1))
        if len(paths) > MAX_SAVED_JOBS:
            return
        paths.sort(key=lambda path: path.lstat().st_mtime_ns, reverse=True)
        if not paths:
            return
        # Recent predictions stay responsive, while the older tail rotates too;
        # no lexical first-100 prefix can permanently exclude a saved forecast.
        recent, older = paths[:32], paths[32:]
        turn = self.cursor // 2 if older else self.cursor
        recent_cursor = turn % len(recent)
        recent_window = recent[recent_cursor:] + recent[:recent_cursor]
        selected = recent_window
        if older:
            tail_cursor = turn % len(older)
            older_window = (older[tail_cursor:] + older[:tail_cursor])[:68]
            # The two-context budget must not always be spent on recent rows.
            # Alternate queue priority; advance each first position one at a time
            # so permanently unavailable contexts cannot starve their neighbors.
            selected = older_window + recent_window if self.cursor % 2 else recent_window + older_window
        self.cursor += 1
        paths = selected
        quote_cache = {}
        for path in paths:
            if self.stop_event.is_set() or self._busy():
                return
            try:
                job, signature = self._load(path.stem)
                if signature is None:
                    continue
                result, _ = payload(job)
                previous = self._previous(path.stem, signature)
                actuals = {point['time']: point['actual_close'] for point in previous.get('points', [])
                           if point.get('status') == 'matched'} if previous else {}
                due = [row['time'] for row in result['forecast']
                       if row['time'] not in actuals and mature(row['time'], result['period'], now)]
                if not due:
                    continue
                checked = False
                for day in sorted({stamp[:10] for stamp in due}):
                    if self.stop_event.is_set() or self._busy():
                        return
                    key = (result['symbol'], result['asset_type'], result['period'], day)
                    if key not in quote_cache:
                        if len(quote_cache) >= 2:
                            continue
                        quote_cache[key] = {}
                        # Count failed contexts too; do not fan out retries.
                        with suppress(Exception):
                            quote_cache[key] = self.loader(self.repo, self.capset, *key, now)
                    checked = True
                    for stamp, price in quote_cache[key].items():
                        if stamp in due and valid_price(price):
                            actuals[stamp] = price
                if self.stop_event.is_set():
                    return
                if not checked:
                    continue
                saved = score(job, actuals, now, signature, now.isoformat())
                self._save(path.stem, signature, saved)
            except Exception:
                continue

    def _save(self, identity, signature, saved):
        # Never rewrite a forecast, or overwrite an evaluation for a changed forecast.
        _, current = self._load(identity)
        if current != signature or self.stop_event.is_set():
            return
        try:
            target = research_path(self.jobs.runtime, 'evaluations', identity + '.json')
        except ValueError:
            return
        if target.exists() and self._previous(identity, signature) is None:
            return
        raw = json.dumps(saved, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(raw) > MAX_BYTES:
            return
        self.folder.mkdir(exist_ok=True)
        atomic_write_text_exclusive(target, raw.decode('utf-8'))

    def _busy(self):
        probe = getattr(self.jobs, 'is_busy', None)
        return probe() if callable(probe) else self.jobs.status()['busy']

    def start(self, repo, capset=None):
        if self.thread is not None and self.thread.is_alive():
            return
        self.repo, self.capset = repo, capset
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name='kronos-actual-evaluation', daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_event.is_set():
            # Optional CPU scoring must not affect the panel lifecycle.
            with suppress(Exception):
                self.cycle()
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
