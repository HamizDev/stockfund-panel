"""Manual THS membership snapshots. Readers never trigger a full-market crawl."""
# ruff: noqa: RUF001
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from app.plugins.fuyao.client import FuyaoError
from app.services.board_membership_store import read_snapshot
from app.services.board_membership_store import snapshot_path as _path
from app.services.fs_utils import atomic_write_text

_locks = {kind: threading.Lock() for kind in ('concept', 'industry')}
_tags = {'concept': 'cn_concept', 'industry': 'industry'}
_columns = {'concept': 'concepts', 'industry': 'industries'}


def refresh_snapshot(client, data_dir: Path, kind: str, *, progress=None, reuse_fresh: bool = False) -> dict:
    """All boards must succeed before publishing. Old cache survives every failure."""
    path = _path(data_dir, kind)
    with _locks[kind]:
        if reuse_fresh:
            current = read_snapshot(data_dir, kind)
            if current and time.time() * 1000 - current['fetched_at_ms'] < 86_400_000:
                return current
        deadline = time.monotonic() + 900
        catalog, timestamp = client.index_catalog(_tags[kind])
        if not catalog or len(catalog) > 1000:
            raise FuyaoError('板块目录为空或数量异常，保留旧快照')
        codes = set()
        symbols: dict[str, dict] = {}
        excluded_non_a_share = 0
        for index, board in enumerate(catalog):
            if time.monotonic() >= deadline:
                raise FuyaoError('板块刷新超时，保留旧快照')
            code, name = board.get('thscode'), board.get('name')
            if not isinstance(code, str) or not re.fullmatch(r'\d{6}\.TI', code) or not isinstance(name, str) or not name.strip() or code in codes:
                raise FuyaoError('板块目录字段不完整，保留旧快照')
            codes.add(code)
            if index:
                time.sleep(0.15)
            members, _ = client.index_members(code)
            # Valid empty boards are allowed; malformed responses are rejected by the client.
            for member in members:
                symbol, member_name = member.get('thscode'), member.get('name')
                # THS concept catalogs can include NEEQ / HK constituents.
                # This dataset explicitly describes SH/SZ/BJ A-share membership.
                if isinstance(symbol, str) and re.fullmatch(r'\d{5,6}\.(NQ|HK)', symbol):
                    excluded_non_a_share += 1
                    continue
                if (not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol)
                        or not isinstance(member_name, str) or not member_name.strip()):
                    error = FuyaoError('成分股字段不完整，保留旧快照')
                    error.member_shape = {'board': code, 'symbol': str(symbol)[:40], 'name_type': type(member_name).__name__}
                    raise error
                item = symbols.setdefault(symbol, {'symbol': symbol, 'name': member_name, _columns[kind]: []})
                if name not in item[_columns[kind]]:
                    item[_columns[kind]].append(name)
            if progress:
                progress(kind, index + 1, len(catalog))
        if not symbols:
            raise FuyaoError('全部成分股为空，保留旧快照')
        result = {'version': 1, 'kind': kind, 'source': 'fuyao', 'complete': True,
                  'fetched_at_ms': round(time.time() * 1000), 'source_updated_at_ms': timestamp or None,
                  'board_count': len(catalog), 'rows': [symbols[symbol] for symbol in sorted(symbols)]}
        result['scope'] = 'a_share_sh_sz_bj'
        result['excluded_non_a_share'] = excluded_non_a_share
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(result, ensure_ascii=False, allow_nan=False))
        return result
