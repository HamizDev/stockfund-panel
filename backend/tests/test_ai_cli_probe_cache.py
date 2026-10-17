from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import ai_provider


class _Completed:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


@pytest.fixture(autouse=True)
def _clear_cli_probe_cache():
    with ai_provider._CODEX_CLI_PROBE_LOCK:
        ai_provider._CODEX_CLI_PROBE_CACHE.clear()
        ai_provider._CODEX_CLI_PROBE_INFLIGHT.clear()
    yield
    with ai_provider._CODEX_CLI_PROBE_LOCK:
        ai_provider._CODEX_CLI_PROBE_CACHE.clear()
        ai_provider._CODEX_CLI_PROBE_INFLIGHT.clear()


def _use_fake_command(monkeypatch: pytest.MonkeyPatch, command: str = "codex") -> None:
    monkeypatch.setattr(ai_provider, "_codex_base_command", lambda: [command])


def test_codex_cli_probe_uses_cached_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs))
        return _Completed(0)

    _use_fake_command(monkeypatch)
    monkeypatch.setattr(ai_provider.subprocess, "run", fake_run)

    assert ai_provider.codex_cli_available() is True
    assert ai_provider.codex_cli_available() is True
    assert len(calls) == 1
    assert calls[0][0] == ["codex", "--version"]
    assert calls[0][1]["timeout"] == 15


def test_failed_probe_is_cached_briefly_then_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    now = [100.0]

    def fake_run(args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return _Completed(0)

    _use_fake_command(monkeypatch)
    monkeypatch.setattr(ai_provider.subprocess, "run", fake_run)
    monkeypatch.setattr(ai_provider.time, "monotonic", lambda: now[0])

    assert ai_provider.codex_cli_available() is False
    assert ai_provider.codex_cli_available() is False
    assert calls == 1

    now[0] += ai_provider._CODEX_CLI_PROBE_FAILURE_TTL + 0.01
    assert ai_provider.codex_cli_available() is True
    assert calls == 2


def test_successful_probe_expires_after_success_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    now = [200.0]

    def fake_run(args, **kwargs):
        nonlocal calls
        calls += 1
        return _Completed(0)

    _use_fake_command(monkeypatch)
    monkeypatch.setattr(ai_provider.subprocess, "run", fake_run)
    monkeypatch.setattr(ai_provider.time, "monotonic", lambda: now[0])

    assert ai_provider.codex_cli_available() is True
    now[0] += ai_provider._CODEX_CLI_PROBE_SUCCESS_TTL - 0.01
    assert ai_provider.codex_cli_available() is True
    assert calls == 1

    now[0] += 0.02
    assert ai_provider.codex_cli_available() is True
    assert calls == 2


def test_concurrent_calls_share_one_inflight_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    worker_count = 8
    start = threading.Barrier(worker_count)
    probe_started = threading.Event()
    allow_probe_to_finish = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def fake_run(args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        probe_started.set()
        assert allow_probe_to_finish.wait(timeout=3)
        return _Completed(0)

    def request_probe() -> bool:
        start.wait(timeout=3)
        return ai_provider.codex_cli_available()

    _use_fake_command(monkeypatch)
    monkeypatch.setattr(ai_provider.subprocess, "run", fake_run)

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(request_probe) for _ in range(worker_count)]
        assert probe_started.wait(timeout=3)
        # Keep the owner inside the probe long enough for the other callers to
        # observe the same in-flight event instead of starting another process.
        time.sleep(0.05)
        allow_probe_to_finish.set()
        assert [future.result(timeout=3) for future in futures] == [True] * worker_count

    assert calls == 1


def test_command_and_path_changes_invalidate_probe_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    command = ["codex-first"]
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return _Completed(0)

    monkeypatch.setattr(ai_provider, "_codex_base_command", lambda: list(command))
    monkeypatch.setattr(ai_provider.subprocess, "run", fake_run)
    monkeypatch.setenv("PATH", "first-path")

    assert ai_provider.codex_cli_available() is True
    assert ai_provider.codex_cli_available() is True
    assert len(calls) == 1

    command[0] = "codex-second"
    assert ai_provider.codex_cli_available() is True
    assert len(calls) == 2

    monkeypatch.setenv("PATH", "second-path")
    assert ai_provider.codex_cli_available() is True
    assert len(calls) == 3


def test_executable_file_change_invalidates_probe_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    executable = tmp_path / "codex"
    executable.write_bytes(b"first")
    calls = 0

    def fake_run(args, **kwargs):
        nonlocal calls
        calls += 1
        return _Completed(0)

    _use_fake_command(monkeypatch, str(executable))
    monkeypatch.setattr(ai_provider.subprocess, "run", fake_run)

    assert ai_provider.codex_cli_available() is True
    executable.write_bytes(b"second-version")
    assert ai_provider.codex_cli_available() is True
    assert calls == 2


def test_probe_cache_keeps_a_bounded_number_of_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_run(args, **kwargs):
        nonlocal calls
        calls += 1
        return _Completed(0)

    _use_fake_command(monkeypatch)
    monkeypatch.setattr(ai_provider.subprocess, "run", fake_run)

    for index in range(ai_provider._CODEX_CLI_PROBE_CACHE_MAX_ENTRIES + 4):
        monkeypatch.setenv("PATH", f"path-{index}")
        assert ai_provider.codex_cli_available() is True

    assert calls == ai_provider._CODEX_CLI_PROBE_CACHE_MAX_ENTRIES + 4
    assert len(ai_provider._CODEX_CLI_PROBE_CACHE) <= ai_provider._CODEX_CLI_PROBE_CACHE_MAX_ENTRIES
