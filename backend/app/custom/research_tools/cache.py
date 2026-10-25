# ruff: noqa: RUF001 -- Localized request state.
"""Explicit bounded refresh jobs; GET never starts network or a model."""
import asyncio
import hashlib
import json
import time
from pathlib import Path

from app.services.fs_utils import atomic_write_text

MAX_JOBS = 3
MAX_FILES = 64
TTL = 6 * 3600
_jobs: dict[tuple[str,str],asyncio.Task] = {}
_errors: dict[tuple[str,str],tuple[float,str]] = {}


def _path(data_dir: Path, key: str, *, create=False) -> Path:
    root = data_dir/'research_tools'/'cache'
    for path in (data_dir,data_dir/'research_tools',root):
        if path.is_symlink():
            raise ValueError('研究缓存目录不能是链接')
    if create:
        root.mkdir(parents=True,exist_ok=True)
    target = root/(hashlib.sha256(key.encode()).hexdigest()+'.json')
    if target.is_symlink():
        raise ValueError('研究缓存文件不能是链接')
    return target


def state(data_dir: Path, key: str) -> dict:
    path = _path(data_dir,key)
    snapshot, saved = None, None
    try:
        if path.stat().st_size <= 2_000_000:
            stored = json.loads(path.read_text(encoding='utf-8'))
            if stored.get('key') == key and isinstance(stored.get('snapshot'),dict):
                snapshot,saved = stored['snapshot'],stored.get('saved_at')
    except (OSError,ValueError,TypeError):
        pass
    identity = (str(data_dir.resolve()),key)
    job = _jobs.get(identity)
    error = _errors.get(identity)
    return {'status':'loading' if job and not job.done() else ('ready' if snapshot else ('error' if error else 'idle')),
            'snapshot':snapshot,'stale':bool(error) or not isinstance(saved,(float,int)) or time.time()-saved>TTL,
            'error':error[1] if error else None,'cached_at':saved}


async def refresh(data_dir: Path, key: str, loader) -> dict:
    identity = (str(data_dir.resolve()),key)
    if identity in _jobs and not _jobs[identity].done():
        return state(data_dir,key)
    if sum(not task.done() for task in _jobs.values()) >= MAX_JOBS:
        raise ValueError('已有研究取数进行中，请等待后重试')
    error = _errors.get(identity)
    if error and time.monotonic()-error[0]<30:
        return state(data_dir,key)
    target = _path(data_dir,key,create=True)
    if not target.exists() and sum(1 for _ in target.parent.glob('*.json'))>=MAX_FILES:
        raise ValueError('研究快照已达到64份上限，请复用已查询标的')

    async def run():
        try:
            snapshot = await asyncio.to_thread(loader)
            if not isinstance(snapshot,dict):
                raise ValueError('无效资料结构')
            if snapshot.get('status')=='partial' and not snapshot.get('items'):
                raise ValueError('资料源失败且没有有效记录，不覆盖旧快照')
            if snapshot.get('status')=='partial' and snapshot.get('dataset') in {'gpu_rent','tw_revenue','memory_prices'}:
                previous=(state(data_dir,key).get('snapshot') or {}).get('items',[])
                names={row.get('name') for row in snapshot.get('items',[]) if isinstance(row,dict)}
                retained=[{**row,'stale':True,'previous_snapshot':True} for row in previous
                          if isinstance(row,dict) and row.get('name') not in names and isinstance(row.get('series'),list)]
                if retained:
                    snapshot={**snapshot,'items':[*snapshot['items'],*retained],
                              'gaps':[*snapshot.get('gaps',[]),'部分序列更新失败，已单独标记并保留上轮观测日期']}
            raw = json.dumps({'key':key,'saved_at':time.time(),'snapshot':snapshot},ensure_ascii=False,allow_nan=False)
            if len(raw.encode())>2_000_000:
                raise ValueError('资料超出缓存限制')
            atomic_write_text(target,raw)
            _errors.pop(identity,None)
        except Exception:
            _errors[identity] = (time.monotonic(),'免费资料本次未能更新，已保留上一次快照')
        finally:
            _jobs.pop(identity,None)
            # Only recent bounded failures are useful for cooldown and display.
            if len(_errors)>MAX_FILES:
                _errors.pop(next(iter(_errors)))

    _jobs[identity] = asyncio.create_task(run())
    return state(data_dir,key)


def active_jobs() -> int:
    return sum(not task.done() for task in _jobs.values())
