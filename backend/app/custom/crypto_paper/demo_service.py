# ruff: noqa: RUF001 -- localized Chinese UI punctuation.
"""Explicit session connections and opt-in encrypted profiles; no background trading."""
from __future__ import annotations

import copy
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.secrets_store import mask

from . import demo_accounts, demo_store
from .demo_client import DemoAPIError
from .demo_store import DemoStoreError

_LOCK = threading.RLock()
_SESSIONS: dict[tuple[str, str], dict] = {}
_GENERATIONS: dict[tuple[str, str], int] = {}
_FETCH_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_DISCONNECTED: set[tuple[str, str]] = set()
_CONNECTING: set[tuple[str, str]] = set()


class Connect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=1, max_length=256)
    secret_key: SecretStr = Field(min_length=1, max_length=512)
    passphrase: SecretStr = Field(min_length=1, max_length=256)
    site: Literal["global", "us", "eea"] = "global"
    account_mode: Literal["classic", "uta"] = "uta"
    save: bool = Field(default=False, strict=True)

    @field_validator("api_key", "secret_key", "passphrase")
    @classmethod
    def header_safe(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in raw):
            raise ValueError("Invalid credential")
        return value


def _key(data_dir: Path, exchange: str) -> tuple[str, str]:
    if exchange not in ("okx", "bitget"):
        raise DemoAPIError("不支持的模拟交易所")
    return str(data_dir.resolve()), exchange


def _status(exchange: str, entry: dict | None, saved: dict | None) -> dict:
    source = entry["credentials"] if entry else saved or {}
    verified = entry is not None and entry.get("snapshot") is not None
    has_saved_profile = bool(saved) or bool(entry and entry.get("saved"))
    return {"exchange": exchange, "site": source.get("site", "global"), "account_mode": source.get("account_mode", "classic"),
            "configured": bool(entry or saved), "saved": entry["saved"] if entry else bool(saved),
            "masked_key": mask(source["api_key"]) if entry else source.get("masked_key", ""),
            "state": "connected" if verified else "saved" if has_saved_profile else "unconfigured"}


def accounts(data_dir: Path) -> dict:
    with _LOCK:
        profiles = demo_store.profiles(data_dir)
        return {"accounts": [_status(ex, _SESSIONS.get(_key(data_dir, ex)),
                                     None if _key(data_dir, ex) in _DISCONNECTED else profiles.get(ex)) for ex in ("okx", "bitget")],
                "persistence_supported": demo_store.supported(), "readonly": True}


def connect(data_dir: Path, exchange: str, body: Connect) -> dict:
    key = _key(data_dir, exchange)
    if body.save and not demo_store.supported():
        raise DemoAPIError("当前系统仅支持会话连接，请取消本机保存")
    credentials = {"exchange": exchange, "site": body.site if exchange == "okx" else "global",
                   "account_mode": body.account_mode if exchange == "bitget" else "classic",
                   "api_key": body.api_key.get_secret_value(), "secret_key": body.secret_key.get_secret_value(),
                   "passphrase": body.passphrase.get_secret_value()}
    from .demo_bots import connection_change

    with connection_change(data_dir, exchange), _LOCK:
        generation = _GENERATIONS[key] = _GENERATIONS.get(key, 0) + 1
        _CONNECTING.add(key)
    try:
        snapshot = demo_accounts.snapshot(credentials)
        with connection_change(data_dir, exchange), _LOCK:
            if _GENERATIONS.get(key) != generation:
                raise DemoAPIError("连接配置已更新，请重新读取账户")
            if body.save:
                demo_store.save(data_dir, exchange, credentials, mask(credentials["api_key"]))
            else:
                demo_store.remove(data_dir, exchange)
            entry = {"credentials": credentials, "saved": body.save, "snapshot": snapshot, "fetched": time.monotonic()}
            _SESSIONS[key] = entry
            _DISCONNECTED.discard(key)
            return {"account": _status(exchange, entry, None), "snapshot": copy.deepcopy(snapshot)}
    finally:
        with _LOCK:
            if _GENERATIONS.get(key) == generation:
                _CONNECTING.discard(key)


def disconnect(data_dir: Path, exchange: str) -> dict:
    key = _key(data_dir, exchange)
    from .demo_bots import connection_change

    with connection_change(data_dir, exchange), _LOCK:
        _GENERATIONS[key] = _GENERATIONS.get(key, 0) + 1
        _CONNECTING.discard(key)
        _SESSIONS.pop(key, None)
        _DISCONNECTED.add(key)
        try:
            demo_store.remove(data_dir, exchange)
        except (DemoStoreError, OSError) as exc:
            raise DemoStoreError("内存会话已断开，但本机保存配置未能清除；原文件已保留，请检查后重试") from exc
    return {"account": _status(exchange, None, None)}


def snapshot(data_dir: Path, exchange: str, *, refresh: bool = False) -> dict:
    key = _key(data_dir, exchange)
    with _LOCK:
        fetch_lock = _FETCH_LOCKS.setdefault(key, threading.Lock())
    with fetch_lock:
        loaded_from_store = False
        with _LOCK:
            if key in _CONNECTING:
                raise DemoAPIError("连接配置正在验证，请稍后重试")
            if key in _DISCONNECTED:
                raise DemoAPIError("请先连接该交易所的官方模拟账户")
            entry = _SESSIONS.get(key)
            if entry is None:
                credentials = demo_store.read(data_dir, exchange)
                if credentials is None:
                    raise DemoAPIError("请先连接该交易所的官方模拟账户")
                entry = {"credentials": credentials, "saved": True, "snapshot": None, "fetched": 0}
                _SESSIONS[key] = entry
                loaded_from_store = True
            generation = _GENERATIONS.get(key, 0)
            credentials = entry["credentials"]
            previous = copy.deepcopy(entry["snapshot"])
            if previous and not refresh and time.monotonic() - entry["fetched"] < 15:
                return previous
        try:
            result = demo_accounts.snapshot(credentials)
        except DemoAPIError as exc:
            with _LOCK:
                if _GENERATIONS.get(key, 0) != generation or _SESSIONS.get(key) is not entry:
                    raise DemoAPIError("连接配置已更新，请重新读取账户") from exc
                if previous:
                    entry["snapshot"] = {**previous, "stale": True, "error": str(exc)}
                    return copy.deepcopy(entry["snapshot"])
                if loaded_from_store:
                    _SESSIONS.pop(key, None)
            raise
        except Exception:
            with _LOCK:
                if (loaded_from_store and previous is None and _GENERATIONS.get(key, 0) == generation
                        and _SESSIONS.get(key) is entry):
                    _SESSIONS.pop(key, None)
            raise
        with _LOCK:
            if _GENERATIONS.get(key, 0) != generation or _SESSIONS.get(key) is not entry:
                raise DemoAPIError("连接配置已更新，请重新读取账户")
            entry.update(snapshot=result, fetched=time.monotonic())
        return copy.deepcopy(result)
