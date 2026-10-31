# ruff: noqa: RUF001 -- Chinese research UI and prompt punctuation.
"""Immutable research decisions and bounded, price-only outcome checks.

No AI, price-provider requests or trading writes occur here. Official calendar
lookups reuse the existing bounded calendar service. A direction score is not
a trading win rate; the saved conclusion and its original evidence never change.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import stat
import threading
import uuid
from datetime import datetime, time
from pathlib import Path

from app.market_time import CN_TZ, cn_now
from app.services.fs_utils import atomic_write_text_exclusive as atomic_write_text

_LOCK = threading.RLock()
_ID = re.compile(r'[0-9a-f]{32}')
_SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ)')
MAX_BYTES = 512 * 1024
MAX_RECENT = 200
logger = logging.getLogger(__name__)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _root(data_dir):
    root = Path(data_dir) / 'research_decisions'
    if _linked(root):
        raise ValueError('研究记录目录不可用')
    return root


def _linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def _path(data_dir, identity, *, outcome=False):
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        raise ValueError('研究记录编号无效')
    root = _root(data_dir)
    folder = root / 'outcomes' if outcome else root
    if _linked(folder):
        raise ValueError('研究记录目录不可用')
    path = folder / (identity + '.json')
    if _linked(path):
        raise ValueError('研究记录路径不可用')
    return path


def _read(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if _linked(path) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError('研究记录路径不可用')
    if metadata.st_size > MAX_BYTES:
        raise ValueError('研究记录超过大小限制')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or len(raw) != metadata.st_size:
        raise ValueError('研究记录超过大小限制')
    value = json.loads(raw)
    if not isinstance(value, dict) or value.pop('sha256', None) != fingerprint(value):
        raise ValueError('研究记录校验失败，原件保留')
    return value


def _write(path, value):
    text = json.dumps(value | {'sha256': fingerprint(value)}, ensure_ascii=False, allow_nan=False)
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('研究记录超过大小限制')
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text, mode=0o600)


def _index(data_dir):
    path = _root(data_dir) / 'index.json'
    if _linked(path):
        raise ValueError('研究索引路径不可用')
    value = _read(path) or {'version': 1, 'total': 0, 'items': []}
    if (value.get('version') != 1 or not isinstance(value.get('items'), list)
            or len(value['items']) > MAX_RECENT or type(value.get('total')) is not int
            or value['total'] < len(value['items'])):
        raise ValueError('研究索引校验失败')
    ids = set()
    for row in value['items']:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                or not _ID.fullmatch(row['id']) or row['id'] in ids
                or row.get('direction') not in {'up', 'down', 'unknown'}
                or row.get('status') not in {'pending', 'waiting_calendar', 'waiting_market', 'waiting_data', 'unscorable', 'complete'}
                or (row.get('hit') is not None and type(row['hit']) is not bool)
                or (row.get('price_return') is not None and
                    (type(row['price_return']) not in (int, float) or not math.isfinite(row['price_return'])))):
            raise ValueError('研究索引校验失败')
        ids.add(row['id'])
    return value


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def observation_direction(content):
    values = re.findall(r'^\s*(?:[-*]\s*)?观察方向\s*[：:]\s*(上涨|下跌|未判断)\s*$',
                        content.replace('**', ''), re.MULTILINE)
    return {'上涨': 'up', '下跌': 'down', '未判断': 'unknown'}.get(values[0], 'unknown') if len(values) == 1 else 'unknown'


def save_decision(data_dir, *, symbol, asset_type, content, requested_at, horizon_sessions,
                  reference_price, snapshot, evidence, portfolio, review, name=''):
    if not _SYMBOL.fullmatch(symbol) or asset_type not in {'stock', 'etf', 'index'}:
        raise ValueError('暂只记录A股、ETF和指数研究')
    if not isinstance(requested_at, datetime) or requested_at.tzinfo is None:
        raise ValueError('研究时间必须含时区')
    if type(horizon_sessions) is not int or horizon_sessions not in (1, 3, 5):
        raise ValueError('观察期限无效')
    if not isinstance(content, str) or not content.strip() or len(content) > 60_000:
        raise ValueError('研究结论为空或过长')
    now = cn_now()
    identity = uuid.uuid4().hex
    record = dict(version=1, id=identity, symbol=symbol, name=str(name)[:100], asset_type=asset_type,
        requested_at=requested_at.astimezone(CN_TZ).isoformat(), created_at=now.isoformat(),
        content=content, content_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(),
        horizon_sessions=horizon_sessions, direction=observation_direction(content),
        reference_price=float(reference_price) if _positive(reference_price) else None,
        price_basis='unadjusted' if _positive(reference_price) else 'unverified', snapshot=snapshot, evidence=evidence,
        portfolio=portfolio, review=review,
        score_definition='提问日之后第N个实际交易日收盘的原始价格变化；不是成交收益或OOS。')
    meta = {key: record[key] for key in ('id', 'symbol', 'name', 'requested_at', 'created_at',
        'horizon_sessions', 'direction', 'reference_price')}
    meta.update(status='pending', target_date=None, price_return=None, hit=None)
    with _LOCK:
        index = _index(data_dir)
        path = _path(data_dir, identity)
        if path.exists():
            raise ValueError('研究记录已存在，拒绝覆盖')
        _write(path, record)
        index['total'] += 1
        index['items'] = [meta, *index['items']][:MAX_RECENT]
        _write(_root(data_dir) / 'index.json', index)
    return meta


def get_decision(data_dir, identity):
    with _LOCK:
        record = _read(_path(data_dir, identity))
        if record is None:
            raise LookupError('研究记录不存在')
        if record.get('id') != identity:
            raise ValueError('研究记录编号不匹配')
        outcome = _read(_path(data_dir, identity, outcome=True))
    if outcome and (outcome.get('id') != identity or outcome.get('decision_sha256') != fingerprint(record)):
        raise ValueError('评分与原研究记录不一致')
    return {'record': record, 'outcome': outcome}


def list_decisions(data_dir):
    with _LOCK:
        index = _index(data_dir)
    items = index['items']
    scored = [row for row in items if row.get('status') == 'complete' and row.get('hit') is not None]
    count = len(scored)
    return dict(items=items, total=index['total'], recent_limit=MAX_RECENT,
        statistics=dict(evaluated=count,
            direction_hit_rate=sum(row['hit'] for row in scored) / count if count else None,
            always_up_hit_rate=sum(row['price_return'] > 0 for row in scored) / count if count else None,
            always_down_hit_rate=sum(row['price_return'] < 0 for row in scored) / count if count else None),
        limits='仅统计此页最近记录中方向明确且目标价格非持平的研究；缺价不计分。价格变化不等于费用后成交收益。')


def _next_session(day):
    from app.services import trading_calendar
    year, month = day.year, day.month
    for _ in range(2):
        days = trading_calendar.month_open_days(year, month)
        if days is None:
            return None
        later = [value for value in days if value > day]
        if later:
            return min(later)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


def _calculate(repo, record, now):
    requested = datetime.fromisoformat(record['requested_at']).astimezone(CN_TZ)
    target = requested.date()
    for _ in range(record['horizon_sessions']):
        target = _next_session(target)
        if target is None:
            return {'status': 'waiting_calendar', 'message': '实际交易日历尚未确认'}
    result = dict(status='waiting_market', target_date=target.isoformat(), price_return=None, hit=None,
        message='等待观察期收盘后形成真实行情')
    if now.date() < target or (now.date() == target and now.time().replace(tzinfo=None) < time(16, 15)):
        return result
    completed = datetime.fromisoformat(record['created_at']).astimezone(CN_TZ)
    if completed >= datetime.combine(target, time(15), CN_TZ):
        return result | {'status': 'unscorable', 'message': '结论形成晚于目标收盘，不计事前方向成绩'}
    if not _positive(record['reference_price']):
        return result | {'status': 'unscorable', 'message': '原始价格基准缺失，不计分'}
    frame = repo.get_daily_asset(record['asset_type'], record['symbol'], target, target)
    if frame.is_empty() or frame.height != 1:
        return result | {'status': 'waiting_data', 'message': '缺少目标交易日完整报价'}
    row = frame.row(0, named=True)
    if str(row.get('date'))[:10] != target.isoformat() or row.get('symbol') != record['symbol']:
        return result | {'status': 'waiting_data', 'message': '目标报价日期或标的不匹配'}
    price = row.get('raw_close')
    if record['asset_type'] == 'index' and price is None:
        price = row.get('close')
    if not _positive(price) or row.get('is_provisional') is True:
        return result | {'status': 'waiting_data', 'message': '目标原始收盘价格尚不可核验'}
    quote_ts = row.get('quote_ts')
    if quote_ts is not None:
        try:
            stamp = datetime.fromtimestamp(float(quote_ts) / 1000, CN_TZ)
            valid_clock = stamp.date() == target and stamp.time().replace(tzinfo=None) >= time(15)
        except (TypeError, ValueError, OverflowError, OSError):
            valid_clock = False
        if not valid_clock:
            return result | {'status': 'waiting_data', 'message': '目标报价仍是未收盘快照'}
    change = float(price) / record['reference_price'] - 1
    hit = None if abs(change) < 1e-10 or record['direction'] == 'unknown' else (
        (change > 0) == (record['direction'] == 'up'))
    return result | dict(status='complete', actual_price=float(price), price_return=change, hit=hit,
        message='已对照目标原始收盘价；未计分红、费用和成交约束，不作为交易收益。')


def refresh_outcome(repo, data_dir, identity, *, now=None, cancelled=None):
    current = get_decision(data_dir, identity)
    if current['outcome'] and current['outcome']['status'] in {'complete', 'unscorable'}:
        return current['outcome']
    now = now or cn_now()
    if now.tzinfo is None:
        raise ValueError('评分时间必须含时区')
    now = now.astimezone(CN_TZ)
    calculated = _calculate(repo, current['record'], now)
    result = calculated | dict(id=identity, checked_at=now.isoformat(),
                              decision_sha256=fingerprint(current['record']))
    if cancelled and cancelled():
        return result
    with _LOCK:
        if cancelled and cancelled():
            return result
        cached = _read(_path(data_dir, identity, outcome=True))
        if cached and cached['status'] in {'complete', 'unscorable'}:
            return cached
        _write(_path(data_dir, identity, outcome=True), result)
        index = _index(data_dir)
        for row in index['items']:
            if row['id'] == identity:
                row.update({key: result.get(key) for key in ('status', 'target_date', 'price_return', 'hit')})
        _write(_root(data_dir) / 'index.json', index)
    return result


class OutcomeService:
    def __init__(self, repo, data_dir):
        self.repo, self.data_dir = repo, data_dir
        self.stop_event = threading.Event()
        self.thread = None
        self.cursor = 0

    def pending_batch(self, items):
        pending = [row for row in items if row['status'] not in {'complete', 'unscorable'}]
        if not pending:
            self.cursor = 0
            return []
        offset = self.cursor % len(pending)
        selected = (pending[offset:] + pending[:offset])[:20]
        self.cursor = (offset + len(selected)) % len(pending)
        return selected

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name='research-outcomes', daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_event.wait(60):
            try:
                items = list_decisions(self.data_dir)['items']
                for row in self.pending_batch(items):
                    if self.stop_event.is_set():
                        return
                    try:
                        refresh_outcome(self.repo, self.data_dir, row['id'], cancelled=self.stop_event.is_set)
                    except Exception:  # One unavailable quote must not stop checks for other records.
                        logger.warning('研究结果核对暂不可用，保留原记录')
                        continue
            except Exception:  # Keep the background service isolated from malformed local records.
                logger.warning('研究索引暂不可用，未改原记录')
                continue

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
