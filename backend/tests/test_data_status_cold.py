"""Cold-load cache behaviour for the data status endpoint."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.api import data


@pytest.fixture(autouse=True)
def isolated_status_cache():
    data.invalidate_data_cache(None)
    yield
    data.invalidate_data_cache(None)


def test_concurrent_cold_table_stats_share_one_fetch() -> None:
    data.invalidate_data_cache("daily")
    calls = 0
    calls_lock = threading.Lock()
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    expected = {"rows": 12}
    callers_ready = threading.Barrier(8)

    def fetch():
        nonlocal calls
        with calls_lock:
            calls += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=3)
        return expected

    def read(fetcher):
        callers_ready.wait(timeout=2)
        return data._get_table_stats("daily", fetcher)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(read, fetch) for _ in range(8)]
        assert fetch_started.wait(timeout=1)
        time.sleep(0.1)
        release_fetch.set()
        assert [future.result(timeout=5) for future in futures] == [expected] * 8

    assert calls == 1


def test_invalidation_during_table_fetch_does_not_publish_stale_stats() -> None:
    data.invalidate_data_cache("daily")
    calls = 0
    fetch_started = threading.Event()
    release_first = threading.Event()

    def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            fetch_started.set()
            assert release_first.wait(timeout=3)
        return {"rows": calls}

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(data._get_table_stats, "daily", fetch)
        assert fetch_started.wait(timeout=1)
        data.invalidate_data_cache("daily")
        release_first.set()
        assert result.result(timeout=5) == {"rows": 2}

    assert calls == 2
    assert data._get_table_stats("daily", fetch) == {"rows": 2}
    assert calls == 2


def test_concurrent_cold_storage_stats_share_one_scan(tmp_path: Path, monkeypatch) -> None:
    data.invalidate_data_cache(None)
    calls = 0
    calls_lock = threading.Lock()
    scan_started = threading.Event()
    release_scan = threading.Event()
    expected = {"total_size_mb": 42.0}
    callers_ready = threading.Barrier(8)

    def compute(_data_dir):
        nonlocal calls
        with calls_lock:
            calls += 1
        scan_started.set()
        assert release_scan.wait(timeout=3)
        return expected

    monkeypatch.setattr(data, "_compute_storage", compute)

    def read():
        callers_ready.wait(timeout=2)
        return data._get_storage(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(read) for _ in range(8)]
        assert scan_started.wait(timeout=1)
        time.sleep(0.1)
        release_scan.set()
        assert [future.result(timeout=5) for future in futures] == [expected] * 8

    assert calls == 1


def test_full_invalidation_during_storage_scan_discards_stale_result(
    tmp_path: Path, monkeypatch,
) -> None:
    data.invalidate_data_cache(None)
    calls = 0
    scan_started = threading.Event()
    release_first = threading.Event()

    def compute(_data_dir):
        nonlocal calls
        calls += 1
        if calls == 1:
            scan_started.set()
            assert release_first.wait(timeout=3)
        return {"total_size_mb": float(calls)}

    monkeypatch.setattr(data, "_compute_storage", compute)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(data._get_storage, tmp_path)
        assert scan_started.wait(timeout=1)
        data.invalidate_data_cache(None)
        release_first.set()
        assert result.result(timeout=5) == {"total_size_mb": 2.0}

    assert calls == 2
    assert data._get_storage(tmp_path) == {"total_size_mb": 2.0}
    assert calls == 2


def test_empty_table_result_is_cached_until_invalidated() -> None:
    data.invalidate_data_cache("daily")
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return None

    assert data._get_table_stats("daily", fetch) is None
    assert data._get_table_stats("daily", fetch) is None
    assert calls == 1

    data.invalidate_data_cache("daily")
    assert data._get_table_stats("daily", fetch) is None
    assert calls == 2
