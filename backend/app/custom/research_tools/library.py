# ruff: noqa: RUF001 -- Chinese user-facing messages.
"""Bounded, local-only document storage and text search."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import threading
import unicodedata
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_LIBRARY_FILES = 50
MAX_LIBRARY_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_CHARS = 500_000
MAX_PDF_PAGES = 500
MAX_SEARCH_RESULTS = 50
MAX_QUERY_CHARS = 200

_SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}
_INDEX_NAME = "index.json"
_LOCK_NAME = ".lock"
_OBJECTS_NAME = "objects"
_INDEX_MAX_BYTES = 8 * 1024 * 1024
_DOCX_MAX_ENTRIES = 512
_DOCX_MAX_EXPANDED_BYTES = 64 * 1024 * 1024
_DOCX_MAX_COMPRESSION_RATIO = 200
_DOCX_MAX_PARAGRAPHS = 100_000
_TEXT_MAX_LINES = 100_000
_SEGMENT_CHARS = 2_000
_ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_thread_locks: dict[str, threading.RLock] = {}
_thread_locks_guard = threading.Lock()


class _InputError(ValueError):
    """A safe, user-facing validation error for document parsing."""


def add_report(data_dir: Path, name: str, content: bytes) -> dict:
    """Store one TXT, MD, DOCX, or PDF report under the supplied data directory."""
    safe_name, extension = _validate_name(name)
    if not isinstance(content, bytes):
        raise ValueError("文件内容无效")
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("文件大小必须在 1 字节至 10 MiB 之间")

    report_id = hashlib.sha256(content).hexdigest()
    segments, page_count = _extract(extension, content)
    text_chars = sum(len(segment["text"]) for segment in segments)
    if text_chars > MAX_EXTRACTED_CHARS:
        raise ValueError("可提取文字超过 50 万字上限")

    with _locked_library(data_dir, create=True) as root:
        if root is None:
            raise ValueError("资料库数据目录不可用")
        objects_dir = _ensure_child_directory(root, _OBJECTS_NAME)
        index = _read_index(root)
        inventory = _inventory_objects(objects_dir)
        existing = index["items"].get(report_id)
        if existing is not None:
            if report_id not in inventory:
                raise ValueError("资料库文件校验失败")
            _verify_object(objects_dir / f"{report_id}.blob", report_id, existing["size"])
            return {"item": _public_item(existing), "duplicate": True}

        if report_id in inventory:
            _verify_object(objects_dir / f"{report_id}.blob", report_id, len(content))
        elif len(inventory) >= MAX_LIBRARY_FILES:
            raise ValueError("资料库最多保存 50 份文件")
        if sum(inventory.values()) + (0 if report_id in inventory else len(content)) > MAX_LIBRARY_BYTES:
            raise ValueError("资料库文件总量不能超过 100 MiB")
        if len(index["items"]) >= MAX_LIBRARY_FILES:
            raise ValueError("资料库最多保存 50 份文件")
        if sum(item["text_chars"] for item in index["items"].values()) + text_chars > MAX_EXTRACTED_CHARS:
            raise ValueError("资料库可检索文字总量不能超过 50 万字")
        pdf_pages = sum(item["page_count"] or 0 for item in index["items"].values())
        if pdf_pages + (page_count or 0) > MAX_PDF_PAGES:
            raise ValueError("资料库 PDF 总页数不能超过 500 页")

        record = {
            "id": report_id,
            "name": safe_name,
            "format": extension[1:],
            "size": len(content),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "page_count": page_count,
            "text_chars": text_chars,
            "segments": segments,
        }
        new_object = report_id not in inventory
        object_path = objects_dir / f"{report_id}.blob"
        object_committed = False
        try:
            if new_object:
                _write_atomic(object_path, content)
                object_committed = True
            index["items"][report_id] = record
            _write_atomic(root / _INDEX_NAME, _encode_index(index))
        except Exception:
            if object_committed:
                with contextlib.suppress(OSError):
                    _unlink_new_object(object_path)
            raise ValueError("资料库写入失败") from None
        return {"item": _public_item(record), "duplicate": False}


def list_reports(data_dir: Path) -> dict:
    """Return report metadata without extracted text or original file contents."""
    with _locked_library(data_dir, create=False) as root:
        if root is None:
            return {"items": []}
        index = _read_index(root)
        items = sorted(
            index["items"].values(),
            key=lambda item: (item["created_at"], item["id"]),
            reverse=True,
        )
        return {"items": [_public_item(item) for item in items]}


def search_reports(data_dir: Path, query: str, limit: int = 8) -> dict:
    """Search only extracted local text and return bounded, attributable snippets."""
    if not isinstance(query, str):
        raise ValueError("请输入查询文字")
    query = " ".join(query.casefold().split())
    if not query:
        raise ValueError("查询文字不能为空")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError("查询文字不能超过 200 字")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_RESULTS:
        raise ValueError("结果数量必须在 1 至 50 之间")

    terms = query.split()
    with _locked_library(data_dir, create=False) as root:
        if root is None:
            return {"items": []}
        index = _read_index(root)
        matches = []
        for report in index["items"].values():
            for segment in report["segments"]:
                text = segment["text"]
                normalized = " ".join(text.casefold().split())
                phrase_count = normalized.count(query)
                counts = [normalized.count(term) for term in terms]
                if not phrase_count and (not counts or any(count == 0 for count in counts)):
                    continue
                score = phrase_count * 100 + sum(counts)
                matches.append(
                    {
                        "id": report["id"],
                        "name": report["name"],
                        "page": segment["page"] if segment["kind"] == "pdf" else None,
                        "snippet": _snippet(text, terms),
                        "citation": _citation(report, segment, terms, query),
                        "score": score,
                    }
                )
        matches.sort(key=lambda item: (-item["score"], item["name"].casefold(), item["id"]))
        return {"items": matches[:limit]}


@contextlib.contextmanager
def _locked_library(data_dir: Path, *, create: bool) -> Iterator[Path | None]:
    root = _prepare_library_root(data_dir, create=create)
    if root is None:
        yield None
        return
    key = os.path.normcase(str(root))
    with _thread_locks_guard:
        thread_lock = _thread_locks.setdefault(key, threading.RLock())
    thread_lock.acquire()
    try:
        try:
            with _cross_process_lock(root / _LOCK_NAME):
                yield root
        except OSError:
            raise ValueError("资料库暂不可用") from None
    finally:
        thread_lock.release()


def _prepare_library_root(data_dir: Path, *, create: bool) -> Path | None:
    try:
        raw_path = os.fspath(data_dir)
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError
        base = Path(os.path.abspath(raw_path))
        if not _ensure_directory_chain(base, create=False):
            if create:
                raise ValueError("资料库数据目录不可用")
            return None
        root = base / "research_tools" / "library"
        if not _ensure_directory_chain(root, create=create):
            return None
        return root
    except ValueError:
        raise ValueError("资料库目录无效或包含符号链接") from None
    except OSError:
        raise ValueError("资料库目录不可用") from None


def _ensure_directory_chain(path: Path, *, create: bool) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        info = _lstat_optional(current)
        if info is None:
            if not create:
                return False
            with contextlib.suppress(FileExistsError):
                current.mkdir()
            info = current.lstat()
        if _is_linklike(current, info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("资料库目录无效或包含符号链接")
    return True


def _ensure_child_directory(root: Path, name: str) -> Path:
    child = root / name
    info = _lstat_optional(child)
    if info is None:
        with contextlib.suppress(FileExistsError):
            child.mkdir()
        info = child.lstat()
    if _is_linklike(child, info) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("资料库目录无效或包含符号链接")
    return child


def _cross_process_lock(path: Path):
    @contextlib.contextmanager
    def acquire() -> Iterator[None]:
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        before = _lstat_optional(path)
        if before is not None and _is_linklike(path, before):
            raise ValueError("资料库目录无效或包含符号链接")
        try:
            fd = os.open(path, flags, 0o600)
        except OSError:
            raise ValueError("资料库暂不可用") from None
        try:
            opened = os.fstat(fd)
            after = path.lstat()
            if (
                _is_linklike(path, after)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (before is not None and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino))
                or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ValueError("资料库目录无效或包含符号链接")
            if opened.st_size == 0:
                os.write(fd, b"\0")
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    return acquire()


def _lstat_optional(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _is_linklike(path: Path, info: os.stat_result) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return stat.S_ISLNK(info.st_mode) or bool(is_junction and is_junction())


def _read_regular(path: Path, *, max_bytes: int) -> bytes | None:
    before = _lstat_optional(path)
    if before is None:
        return None
    if _is_linklike(path, before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("资料库文件无效")
    if before.st_size > max_bytes:
        raise ValueError("资料库索引超出大小上限")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise ValueError("资料库文件不可用") from None
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ValueError("资料库文件无效")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("资料库文件超出大小上限")
        return data
    finally:
        os.close(fd)


def _read_index(root: Path) -> dict:
    try:
        raw = _read_regular(root / _INDEX_NAME, max_bytes=_INDEX_MAX_BYTES)
    except ValueError:
        raise
    except Exception:
        raise ValueError("资料库索引损坏或不可用") from None
    if raw is None:
        return {"version": 1, "items": {}}
    try:
        index = json.loads(raw)
        _validate_index(index)
        return index
    except Exception:
        raise ValueError("资料库索引损坏或不可用") from None


def _validate_index(index: object) -> None:
    if not isinstance(index, dict) or index.get("version") != 1:
        raise ValueError("资料库索引损坏或不可用")
    items = index.get("items")
    if not isinstance(items, dict) or len(items) > MAX_LIBRARY_FILES:
        raise ValueError("资料库索引损坏或不可用")
    total_bytes = total_chars = total_pages = 0
    for report_id, item in items.items():
        if (
            not isinstance(report_id, str)
            or not _ID_PATTERN.fullmatch(report_id)
            or not isinstance(item, dict)
            or item.get("id") != report_id
            or not isinstance(item.get("name"), str)
            or item.get("format") not in {extension[1:] for extension in _SUPPORTED_EXTENSIONS}
            or isinstance(item.get("size"), bool)
            or not isinstance(item.get("size"), int)
            or not 1 <= item["size"] <= MAX_FILE_BYTES
            or not isinstance(item.get("created_at"), str)
            or isinstance(item.get("text_chars"), bool)
            or not isinstance(item.get("text_chars"), int)
            or item["text_chars"] < 0
            or not isinstance(item.get("segments"), list)
            or len(item["segments"]) > MAX_EXTRACTED_CHARS
        ):
            raise ValueError("资料库索引损坏或不可用")
        page_count = item.get("page_count")
        if (item["format"] == "pdf") != (page_count is not None):
            raise ValueError("资料库索引损坏或不可用")
        if page_count is not None and (
            isinstance(page_count, bool) or not isinstance(page_count, int) or not 1 <= page_count <= MAX_PDF_PAGES
        ):
            raise ValueError("资料库索引损坏或不可用")
        segment_chars = 0
        for segment in item["segments"]:
            if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
                raise ValueError("资料库索引损坏或不可用")
            kind = segment.get("kind")
            if kind == "pdf":
                if (
                    item["format"] != "pdf"
                    or len(segment["text"]) > _SEGMENT_CHARS
                    or page_count is None
                    or isinstance(segment.get("page"), bool)
                    or not isinstance(segment.get("page"), int)
                    or not 1 <= segment["page"] <= page_count
                ):
                    raise ValueError("资料库索引损坏或不可用")
            elif kind == "docx":
                if (
                    item["format"] != "docx"
                    or len(segment["text"]) > _SEGMENT_CHARS
                    or isinstance(segment.get("paragraph"), bool)
                    or not isinstance(segment.get("paragraph"), int)
                    or segment["paragraph"] < 1
                ):
                    raise ValueError("资料库索引损坏或不可用")
            elif kind == "text":
                if (
                    item["format"] not in {"txt", "md"}
                    or len(segment["text"]) > _SEGMENT_CHARS
                    or isinstance(segment.get("line_start"), bool)
                    or not isinstance(segment.get("line_start"), int)
                    or isinstance(segment.get("line_end"), bool)
                    or not isinstance(segment.get("line_end"), int)
                    or segment["line_start"] < 1
                    or segment["line_end"] < segment["line_start"]
                ):
                    raise ValueError("资料库索引损坏或不可用")
            else:
                raise ValueError("资料库索引损坏或不可用")
            segment_chars += len(segment["text"])
        if segment_chars != item["text_chars"]:
            raise ValueError("资料库索引损坏或不可用")
        total_bytes += item["size"]
        total_chars += segment_chars
        total_pages += page_count or 0
    if total_bytes > MAX_LIBRARY_BYTES or total_chars > MAX_EXTRACTED_CHARS or total_pages > MAX_PDF_PAGES:
        raise ValueError("资料库索引损坏或不可用")


def _inventory_objects(objects_dir: Path) -> dict[str, int]:
    inventory: dict[str, int] = {}
    try:
        for path in objects_dir.iterdir():
            info = path.lstat()
            match = re.fullmatch(r"([0-9a-f]{64})\.blob", path.name)
            if (
                not match
                or _is_linklike(path, info)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > MAX_FILE_BYTES
            ):
                raise ValueError("资料库文件无效")
            inventory[match.group(1)] = info.st_size
            if len(inventory) > MAX_LIBRARY_FILES:
                raise ValueError("资料库最多保存 50 份文件")
    except OSError:
        raise ValueError("资料库文件不可用") from None
    if sum(inventory.values()) > MAX_LIBRARY_BYTES:
        raise ValueError("资料库文件总量不能超过 100 MiB")
    return inventory


def _verify_object(path: Path, report_id: str, size: int) -> None:
    raw = _read_regular(path, max_bytes=MAX_FILE_BYTES)
    if raw is None or len(raw) != size or hashlib.sha256(raw).hexdigest() != report_id:
        raise ValueError("资料库文件校验失败")


def _encode_index(index: dict) -> bytes:
    return json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _write_atomic(path: Path, payload: bytes) -> None:
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".research-", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)
        raise


def _unlink_new_object(path: Path) -> None:
    info = _lstat_optional(path)
    if info is None:
        return
    if _is_linklike(path, info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError("object no longer identifies the newly written file")
    path.unlink()


def _validate_name(name: str) -> tuple[str, str]:
    if not isinstance(name, str):
        raise ValueError("文件名无效")
    safe_name = name.strip()
    if (
        not safe_name
        or len(safe_name) > 200
        or "/" in safe_name
        or "\\" in safe_name
        or ":" in safe_name
        or any(unicodedata.category(char) == "Cc" for char in safe_name)
    ):
        raise ValueError("请提供不含路径的有效文件名")
    try:
        safe_name.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("文件名无效") from None
    extension = Path(safe_name).suffix.casefold()
    if extension not in _SUPPORTED_EXTENSIONS:
        raise ValueError("仅支持 TXT、MD、DOCX 和 PDF 文件")
    return safe_name, extension


def _extract(extension: str, content: bytes) -> tuple[list[dict], int | None]:
    if extension in {".txt", ".md"}:
        try:
            text = content.decode("utf-8-sig", errors="strict")
        except UnicodeError:
            raise ValueError("TXT 和 MD 文件必须使用 UTF-8 编码") from None
        segments = _plain_text_segments(text)
        if sum(len(segment["text"]) for segment in segments) > MAX_EXTRACTED_CHARS:
            raise ValueError("可提取文字超过 50 万字上限")
        return segments, None
    if extension == ".docx":
        return _extract_docx(content), None
    if extension == ".pdf":
        return _extract_pdf(content)
    raise ValueError("仅支持 TXT、MD、DOCX 和 PDF 文件")


def _plain_text_segments(text: str) -> list[dict]:
    segments = []
    buffer: list[str] = []
    buffer_chars = 0
    line_start = 0
    lines = 0

    def flush(line_end: int) -> None:
        nonlocal buffer, buffer_chars, line_start
        if buffer:
            segments.append(
                {
                    "kind": "text",
                    "line_start": line_start,
                    "line_end": line_end,
                    "text": "\n".join(buffer),
                }
            )
        buffer = []
        buffer_chars = 0
        line_start = 0

    for line_number, raw_line in enumerate(io.StringIO(text), start=1):
        lines = line_number
        if lines > _TEXT_MAX_LINES:
            raise ValueError("文本行数超过支持上限")
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            flush(line_number - 1)
            continue
        if len(line) > _SEGMENT_CHARS:
            flush(line_number - 1)
            for offset in range(0, len(line), _SEGMENT_CHARS):
                segments.append(
                    {
                        "kind": "text",
                        "line_start": line_number,
                        "line_end": line_number,
                        "text": line[offset : offset + _SEGMENT_CHARS],
                    }
                )
            continue
        added_chars = len(line) + (1 if buffer else 0)
        if buffer and buffer_chars + added_chars > _SEGMENT_CHARS:
            flush(line_number - 1)
        if not buffer:
            line_start = line_number
        buffer.append(line)
        buffer_chars += len(line) + (1 if len(buffer) > 1 else 0)
    flush(lines)
    return segments


def _extract_docx(content: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > _DOCX_MAX_ENTRIES:
                raise ValueError("DOCX 文件包含过多压缩条目")
            total_size = 0
            names = set()
            for info in infos:
                member_name = info.filename
                pure_path = PurePosixPath(member_name)
                if (
                    not member_name
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or "\\" in member_name
                    or "\x00" in member_name
                    or pure_path.is_absolute()
                    or ".." in pure_path.parts
                    or re.match(r"^[A-Za-z]:", member_name)
                    or member_name in names
                ):
                    raise _InputError("DOCX 压缩包包含无效路径")
                names.add(member_name)
                total_size += info.file_size
                if total_size > _DOCX_MAX_EXPANDED_BYTES:
                    raise _InputError("DOCX 解压后超过安全上限")
                if info.file_size and (
                    not info.compress_size
                    or info.file_size / info.compress_size > _DOCX_MAX_COMPRESSION_RATIO
                ):
                    raise _InputError("DOCX 压缩比例超过安全上限")
            document_infos = [info for info in infos if info.filename == "word/document.xml"]
            if len(document_infos) != 1 or document_infos[0].file_size > _DOCX_MAX_EXPANDED_BYTES:
                raise _InputError("DOCX 文档结构无效")
            document_xml = archive.read(document_infos[0])
        root = ElementTree.fromstring(document_xml)
    except _InputError:
        raise
    except Exception:
        raise ValueError("无法读取 DOCX 文档") from None

    segments = []
    char_count = 0
    paragraphs = 0
    for paragraph in root.iter(f"{_W_NS}p"):
        paragraphs += 1
        if paragraphs > _DOCX_MAX_PARAGRAPHS:
            raise _InputError("DOCX 段落数量超过安全上限")
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{_W_NS}t":
                parts.append(node.text or "")
            elif node.tag in {f"{_W_NS}tab", f"{_W_NS}ptab"}:
                parts.append("\t")
            elif node.tag in {f"{_W_NS}br", f"{_W_NS}cr"}:
                parts.append("\n")
        text = "".join(parts).strip()
        if not text:
            continue
        char_count += len(text)
        if char_count > MAX_EXTRACTED_CHARS:
            raise _InputError("可提取文字超过 50 万字上限")
        for offset in range(0, len(text), _SEGMENT_CHARS):
            segments.append(
                {
                    "kind": "docx",
                    "paragraph": paragraphs,
                    "text": text[offset : offset + _SEGMENT_CHARS],
                }
            )
    return segments


def _extract_pdf(content: bytes) -> tuple[list[dict], int]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("读取 PDF 需要安装可选依赖 pypdf") from None
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise _InputError("暂不支持加密 PDF")
        page_count = len(reader.pages)
        if not 1 <= page_count <= MAX_PDF_PAGES:
            raise _InputError("单份 PDF 页数必须在 1 至 500 页之间")
        segments = []
        char_count = 0
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            char_count += len(text)
            if char_count > MAX_EXTRACTED_CHARS:
                raise _InputError("可提取文字超过 50 万字上限")
            for offset in range(0, len(text), _SEGMENT_CHARS):
                segments.append(
                    {
                        "kind": "pdf",
                        "page": page_number,
                        "text": text[offset : offset + _SEGMENT_CHARS],
                    }
                )
        return segments, page_count
    except _InputError:
        raise
    except Exception:
        raise ValueError("无法读取 PDF 文档") from None


def _public_item(item: dict) -> dict:
    return {
        "id": item["id"],
        "name": item["name"],
        "format": item["format"],
        "size": item["size"],
        "created_at": item["created_at"],
        "page_count": item["page_count"],
        "text_chars": item["text_chars"],
        "searchable": item["text_chars"] > 0,
    }


def _citation(report: dict, segment: dict, terms: list[str], query: str) -> str:
    if segment["kind"] == "pdf":
        return f"{report['name']}，第 {segment['page']} 页"
    if segment["kind"] == "docx":
        return f"{report['name']}，第 {segment['paragraph']} 段"
    text = segment["text"]
    normalized = text.casefold()
    match_at = normalized.find(query)
    if match_at < 0:
        positions = [normalized.find(term) for term in terms if term]
        positions = [position for position in positions if position >= 0]
        match_at = min(positions, default=0)
    line = segment["line_start"] + text.count("\n", 0, match_at)
    return f"{report['name']}，第 {line} 行"


def _snippet(text: str, terms: list[str], *, max_chars: int = 240) -> str:
    normalized = text.casefold()
    positions = [normalized.find(term) for term in terms if term]
    positions = [position for position in positions if position >= 0]
    match_at = min(positions, default=0)
    start = max(0, match_at - max_chars // 3)
    end = min(len(text), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)
    snippet = text[start:end].strip()
    if start:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return snippet
