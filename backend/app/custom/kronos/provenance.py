"""Read-only fingerprints for the local Kronos runtime and worker code."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
from pathlib import Path

_MODEL_NAMES = ("small", "base", "tokenizer")
_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_MAX_CODE_BYTES = 1024 * 1024
_MAX_MODEL_PY_FILES = 32
_MAX_MODEL_DIRECTORY_ENTRIES = 256
_MISSING_MODEL_CONFIG = b"\x00kronos-model-config-missing-v1\x00"


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _resolve_root(path: Path, label: str) -> Path:
    try:
        if _is_link(path):
            raise ValueError(f"{label} 不能是符号链接。")
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"{label} 必须是目录。")
        return resolved
    except ValueError:
        raise
    except (OSError, RuntimeError, TypeError) as exc:
        raise ValueError(f"{label} 不存在或不可读取。") from exc


def _read_code_file(root: Path, relative: Path, label: str) -> bytes:
    """Read one bounded regular file below root, rejecting links and escapes."""
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"{label} 路径无效。")

    path = root.joinpath(relative)
    current = root
    try:
        for part in relative.parts:
            current = current / part
            if _is_link(current):
                raise ValueError(f"{label} 不允许符号链接。")

        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError(f"{label} 超出允许目录。")

        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} 必须是普通文件。")
        if before.st_size > _MAX_CODE_BYTES:
            raise ValueError(f"{label} 超过 1 MiB 大小上限。")

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_dev != before.st_dev
                    or opened.st_ino != before.st_ino):
                raise ValueError(f"{label} 必须是普通文件。")
            content = stream.read(_MAX_CODE_BYTES + 1)
        if len(content) > _MAX_CODE_BYTES:
            raise ValueError(f"{label} 超过 1 MiB 大小上限。")

        after = path.lstat()
        if (_is_link(path) or before.st_dev != after.st_dev or before.st_ino != after.st_ino
                or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError(f"{label} 读取期间发生变化。")
        return content
    except ValueError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{label} 不存在或不可读取。") from exc


def _runtime_root(runtime: Path) -> Path:
    try:
        return _resolve_root(Path(runtime), "runtime")
    except ValueError:
        raise
    except (TypeError, OSError, RuntimeError) as exc:
        raise ValueError("runtime 路径无效或不可读取。") from exc


def _literal_model_hashes(source: bytes) -> dict[str, str]:
    try:
        tree = ast.parse(source.decode("utf-8-sig"), filename="runner.py", mode="exec")
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("runner.py 无法安全解析。") from exc

    assignments: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            mentions = any(
                isinstance(name, ast.Name) and name.id == "MODEL_HASHES"
                for target in node.targets
                for name in ast.walk(target)
            )
            if mentions:
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    raise ValueError("MODEL_HASHES 必须是唯一的顶层字典赋值。")
                assignments.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "MODEL_HASHES":
                if node.value is None:
                    raise ValueError("MODEL_HASHES 必须是字面量字典。")
                assignments.append(node.value)

    stored = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "MODEL_HASHES"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    imported = any(
        alias.asname == "MODEL_HASHES"
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    )
    named_definition = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == "MODEL_HASHES"
        for node in ast.walk(tree)
    )
    if len(assignments) != 1 or len(stored) != 1 or imported or named_definition:
        raise ValueError("runner.py 必须包含唯一顶层 MODEL_HASHES 字面量字典。")

    value = assignments[0]
    if not isinstance(value, ast.Dict):
        raise ValueError("MODEL_HASHES 必须是字面量字典。")
    keys = []
    for key_node, value_node in zip(value.keys, value.values, strict=True):
        if (not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str)
                or not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str)):
            raise ValueError("MODEL_HASHES 只能包含字符串键和值。")
        keys.append(key_node.value)
    if len(keys) != len(set(keys)):
        raise ValueError("MODEL_HASHES 不能包含重复键。")

    try:
        model_hashes = ast.literal_eval(value)
    except (ValueError, TypeError, MemoryError, RecursionError) as exc:
        raise ValueError("MODEL_HASHES 不是安全的字面量字典。") from exc
    if not isinstance(model_hashes, dict) or set(model_hashes) not in (
            {'base', 'tokenizer'}, set(_MODEL_NAMES)):
        raise ValueError("MODEL_HASHES 必须包含 base、tokenizer, 仅兼容旧记录的 small 摘要。")
    if any(not isinstance(model_hashes[name], str) or not _SHA256_HEX.fullmatch(model_hashes[name])
           for name in model_hashes):
        raise ValueError("MODEL_HASHES 的值必须是 64 位十六进制字符串。")
    return {name: model_hashes[name] for name in _MODEL_NAMES if name in model_hashes}


def _optional_model_directory(runtime_root: Path) -> Path | None:
    current = runtime_root
    for part in ("source", "model"):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError("runtime/source/model 不可读取。") from exc
        if _is_link(current):
            raise ValueError("runtime/source/model 不允许符号链接。")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("runtime/source/model 路径格式无效。")
        try:
            resolved = current.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("runtime/source/model 不可读取。") from exc
        if not resolved.is_relative_to(runtime_root):
            raise ValueError("runtime/source/model 超出 runtime 目录。")
    return current


def _model_sources(runtime_root: Path) -> list[tuple[str, bytes]]:
    model_dir = _optional_model_directory(runtime_root)
    if model_dir is None:
        return []

    source_names: list[str] = []
    try:
        with os.scandir(model_dir) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > _MAX_MODEL_DIRECTORY_ENTRIES:
                    raise ValueError("runtime/source/model 文件数量超过扫描上限。")
                path = model_dir / entry.name
                if _is_link(path):
                    raise ValueError("runtime/source/model 不允许符号链接。")
                if entry.is_dir(follow_symlinks=False):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError("runtime/source/model 含有无法核验的文件项。")
                if entry.name.endswith(".py"):
                    source_names.append(entry.name)
                    if len(source_names) > _MAX_MODEL_PY_FILES:
                        raise ValueError("runtime/source/model 的 Python 文件超过 32 个。")
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("runtime/source/model 不可读取。") from exc

    files = []
    for name in sorted(source_names):
        relative = Path("source") / "model" / name
        content = _read_code_file(runtime_root, relative, f"runtime/{relative.as_posix()}")
        files.append((f"runtime/{relative.as_posix()}", content))
    return files


def _model_config(runtime_root: Path, model_name: str) -> tuple[str, bytes]:
    relative = Path("models") / model_name / "config.json"
    current = runtime_root
    try:
        for part in relative.parts[:-1]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                return f"runtime/{relative.as_posix()}", _MISSING_MODEL_CONFIG
            if _is_link(current):
                raise ValueError(f"runtime/{relative.as_posix()} 路径不允许符号链接。")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError(f"runtime/{relative.as_posix()} 路径格式无效。")
            if not current.resolve(strict=True).is_relative_to(runtime_root):
                raise ValueError(f"runtime/{relative.as_posix()} 超出 runtime 目录。")

        config_path = runtime_root / relative
        try:
            config_path.lstat()
        except FileNotFoundError:
            return f"runtime/{relative.as_posix()}", _MISSING_MODEL_CONFIG
        content = _read_code_file(runtime_root, relative, f"runtime/{relative.as_posix()}")
        try:
            value = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise ValueError(f"runtime/{relative.as_posix()} 不是有效 JSON。") from exc
        if not isinstance(value, dict):
            raise ValueError(f"runtime/{relative.as_posix()} 必须是 JSON 对象。")
        return f"runtime/{relative.as_posix()}", content
    except ValueError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"runtime/{relative.as_posix()} 不存在或不可读取。") from exc


def _model_configs(runtime_root: Path, names) -> list[tuple[str, bytes]]:
    return [_model_config(runtime_root, name) for name in names]


def _panel_code_files() -> list[tuple[str, bytes]]:
    try:
        module_path = Path(__file__).absolute()
        panel_root = module_path.parents[4]
        if any(_is_link(path) for path in (module_path, *module_path.parents[:5])):
            raise ValueError("Kronos provenance 路径不能包含符号链接。")
        panel_root = _resolve_root(panel_root, "panel")
        if not module_path.resolve(strict=True).is_relative_to(panel_root):
            raise ValueError("Kronos provenance 路径超出面板目录。")
    except ValueError:
        raise
    except (OSError, RuntimeError, IndexError) as exc:
        raise ValueError("无法定位 Kronos 面板代码目录。") from exc

    files = []
    for name in ("service.py", "data.py"):
        relative = Path("backend") / "app" / "custom" / "kronos" / name
        content = _read_code_file(panel_root, relative, f"panel/{relative.as_posix()}")
        files.append((f"panel/{relative.as_posix()}", content))
    return files


def _implementation_sha256(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256(b"kronos-implementation-v1\0")
    for name, content in sorted(files, key=lambda item: item[0]):
        encoded_name = name.encode("utf-8", errors="surrogatepass")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def runtime_profile(runtime: Path) -> dict:
    """Return validated model hashes and bounded source fingerprints.

    No runner code is executed and no model weights, data, environment files,
    providers, or network resources are opened.
    """
    runtime_root = _runtime_root(runtime)
    runner_relative = Path("runner.py")
    runner_bytes = _read_code_file(runtime_root, runner_relative, "runtime/runner.py")
    model_hashes = _literal_model_hashes(runner_bytes)
    implementation_files = [("runtime/runner.py", runner_bytes)]
    implementation_files.extend(_model_sources(runtime_root))
    implementation_files.extend(_model_configs(runtime_root, model_hashes))
    implementation_files.extend(_panel_code_files())
    return {
        "model_hashes": model_hashes,
        "runner_sha256": hashlib.sha256(runner_bytes).hexdigest(),
        "implementation_sha256": _implementation_sha256(implementation_files),
    }
