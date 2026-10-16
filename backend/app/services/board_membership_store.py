"""Read-only normalized membership cache, independent of account availability."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

_lock = threading.Lock()
_cache: dict[Path, tuple[int, int, dict]] = {}


def snapshot_path(data_dir: Path, kind: str, source: str = 'fuyao') -> Path:
    if kind not in {'concept', 'industry'} or not re.fullmatch(r'[a-z0-9_]+', source):
        raise ValueError('Unknown membership dimension or source')
    return data_dir / 'cache' / source / f'board-membership-{kind}.json'


def read_snapshot(data_dir: Path, kind: str, source: str = 'fuyao') -> dict | None:
    path = snapshot_path(data_dir, kind, source)
    column = 'concepts' if kind == 'concept' else 'industries'
    try:
        stat = path.stat()
        if stat.st_size > 8_000_000:
            return None
        with _lock:
            cached = _cache.get(path)
            if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                return cached[2]
        data = json.loads(path.read_text(encoding='utf-8'))
        if (data.get('version') == 1 and data.get('kind') == kind and data.get('source') == source
                and data.get('complete') is True and isinstance(data.get('rows'), list)
                and data['rows'] and isinstance(data.get('fetched_at_ms'), int)
                and all(isinstance(row, dict) and isinstance(row.get('symbol'), str)
                        and isinstance(row.get(column), list) for row in data['rows'])):
            with _lock:
                _cache[path] = (stat.st_mtime_ns, stat.st_size, data)
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return None
