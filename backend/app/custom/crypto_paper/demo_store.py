# ruff: noqa: RUF001 -- localized Chinese UI punctuation.
"""Dedicated Windows user-bound encrypted storage for optional demo credentials."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import stat
import threading
import uuid
from pathlib import Path

LOCK = threading.RLock()


class DemoStoreError(ValueError):
    pass


def supported() -> bool:
    return os.name == "nt"


def _crypt(payload: bytes, *, decrypt: bool = False) -> bytes:
    if not supported():
        raise DemoStoreError("当前系统不支持本机加密保存，请使用会话连接")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    protect = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    protect.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    protect.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not protect(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise DemoStoreError("本机加密配置无法读取，请重新填写模拟盘密钥")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)


def _path(data_dir: Path) -> Path:
    root = data_dir.resolve()
    target = root / "user_data" / "crypto_demo_credentials.json"
    for path in (target, target.parent):
        if path.exists() and (path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0)
                              & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise DemoStoreError("模拟盘配置路径异常，未修改配置")
    return target


def _load(data_dir: Path) -> dict:
    target = _path(data_dir)
    if not target.exists():
        return {"schema": 1, "accounts": {}}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        if value.get("schema") != 1 or not isinstance(value.get("accounts"), dict):
            raise ValueError
        if set(value["accounts"]) - {"okx", "bitget"}:
            raise ValueError
        for exchange, row in value["accounts"].items():
            if not isinstance(row, dict) or row.get("site") not in {"global", "us", "eea"}:
                raise ValueError
            if row.get("account_mode") not in {"classic", "uta"}:
                raise ValueError
            if exchange == "okx" and row["account_mode"] != "classic":
                raise ValueError
            if exchange == "bitget" and row["site"] != "global":
                raise ValueError
            if not isinstance(row.get("masked_key"), str) or len(row["masked_key"]) > 256:
                raise ValueError
            if not isinstance(row.get("ciphertext"), str) or not 1 <= len(row["ciphertext"]) <= 16384:
                raise ValueError
        return value
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        raise DemoStoreError("模拟盘加密配置不可用，原文件已保留") from exc


def profiles(data_dir: Path) -> dict:
    with LOCK:
        return {key: {field: row.get(field) for field in ("site", "account_mode", "masked_key")}
                for key, row in _load(data_dir)["accounts"].items()}


def read(data_dir: Path, exchange: str) -> dict | None:
    with LOCK:
        row = _load(data_dir)["accounts"].get(exchange)
        if row is None:
            return None
        try:
            value = json.loads(_crypt(base64.b64decode(row["ciphertext"], validate=True), decrypt=True))
            if value["exchange"] != exchange or value["site"] != row["site"] or value["account_mode"] != row["account_mode"]:
                raise ValueError
            if any(not isinstance(value.get(k), str) or not value[k] or len(value[k]) > 512
                   or not value[k].isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value[k])
                   for k in ("api_key", "secret_key", "passphrase")):
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError) as exc:
            raise DemoStoreError("本机加密配置无法读取，请重新填写模拟盘密钥") from exc


def _write(data_dir: Path, value: dict) -> None:
    target = _path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def save(data_dir: Path, exchange: str, credentials: dict, masked_key: str) -> None:
    ciphertext = base64.b64encode(_crypt(json.dumps(credentials).encode())).decode("ascii")
    with LOCK:
        current = _load(data_dir)
        current["accounts"][exchange] = {"site": credentials["site"], "account_mode": credentials["account_mode"],
                                          "masked_key": masked_key, "ciphertext": ciphertext}
        _write(data_dir, current)


def remove(data_dir: Path, exchange: str) -> None:
    with LOCK:
        current = _load(data_dir)
        if exchange in current["accounts"]:
            del current["accounts"][exchange]
            _write(data_dir, current)
