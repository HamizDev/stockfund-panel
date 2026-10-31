# ruff: noqa: RUF001 -- Chinese research messages and exact price-basis contract.
"""Bounded private research records; no account, provider or model loading."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from itertools import islice
from pathlib import Path

from .paths import read_record_bytes, research_path

MAX_BYTES = 512 * 1024


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


class ResearchStore:
    def __init__(self, runtime: Path, name: str, limit=20):
        assert name in {'comparisons', 'candidates', 'rolling'}
        self.runtime, self.folder, self.limit = runtime, runtime / name, limit
        self.lock = threading.RLock()

    def path(self, identity: str) -> Path:
        if not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise ValueError('研究记录编号无效。')
        return research_path(self.runtime, self.folder.name, identity + '.json')

    def identities(self):
        self.path('0' * 32)
        paths = sorted(islice(self.folder.glob('*.json'), self.limit + 1))
        if len(paths) > self.limit:
            raise ValueError('研究记录超过数量上限，请先核对本机保存目录。')
        return [path.stem for path in paths if re.fullmatch(r'[0-9a-f]{32}', path.stem)]

    def read(self, identity):
        with self.lock:
            return self._read(identity)

    def _read(self, identity):
        path = self.path(identity)
        if not path.is_file():
            raise LookupError('研究记录不存在。')
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('研究记录超过大小上限。')
        try:
            value = json.loads(read_record_bytes(path, MAX_BYTES))
            if (not isinstance(value, dict) or value.get('schema_version') != 1
                    or value.get('id') != identity):
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise ValueError('研究记录损坏或版本不兼容，原文件已保留。') from None
        return value

    def write(self, value, *, new=False):
        # Windows readers can deny atomic replacement while their handle is open.
        # Serialize this small record's reads/writes; no providers or scans run here.
        with self.lock:
            self._write(value, new=new)

    def _write(self, value, *, new=False):
        path = self.path(value['id'])
        raw = encoded(value)
        if len(raw) > MAX_BYTES:
            raise ValueError('研究记录超过大小上限。')
        self.folder.mkdir(exist_ok=True)
        if new and (path.exists() or len(self.identities()) >= self.limit):
            raise ValueError('研究记录已存在或达到保存上限；原记录不会覆盖。')
        temporary = self.folder / (uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(raw)
            self.path(value['id'])
            os.replace(temporary, path)
        finally:
            if temporary.is_file() and not temporary.is_symlink():
                temporary.unlink()
