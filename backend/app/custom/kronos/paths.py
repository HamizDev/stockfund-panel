"""Bounded research paths: one lstat per component, without resolve rescans."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def read_record_bytes(path: Path, limit: int) -> bytes:
    """Read at most limit+1 bytes and reject changed or non-regular records."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError('Research record is not a bounded regular file')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('Research record changed while opening')
        raw = stream.read(limit + 1)
    after = path.lstat()
    if (len(raw) > limit or len(raw) != before.st_size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)):
        raise ValueError('Research record changed or exceeds its size limit')
    return raw


def research_path(runtime: Path, *parts: str) -> Path:
    root = Path(os.path.abspath(runtime))
    relative = Path(*parts)
    if relative.is_absolute() or relative.drive or any(part in {'.', '..'} for part in relative.parts):
        raise ValueError('研究记录路径无效。')
    target = root / relative
    if target == root or not target.is_relative_to(root):
        raise ValueError('研究记录路径超出本机目录。')
    # Reject every existing ancestor, including those above runtime. With no
    # links/reparse points and no traversal, lexical containment is physical
    # containment; resolve() would only walk these components a second time.
    for path in (*reversed(target.parents), target):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise ValueError('研究记录路径不能包含链接。')
    return target
