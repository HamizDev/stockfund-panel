from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from datetime import time as clock_time

import polars as pl
import pytest
from app.services.minute_response_cache import MinuteFetchKey, MinuteResponseCache


def _key(**changes) -> MinuteFetchKey:
    fields = {
        "data_dir": "D:/data-a",
        "provider": "stocksdk",
        "asset_type": "etf",
        "symbol": "515880.SH",
        "trade_date": date(2026, 10, 16),
        "frequency": "1m",
        "raw_basis": True,
        "adjustment_context": (),
    }
    fields.update(changes)
    return MinuteFetchKey(**fields)


def _frame(rows: int = 1, *, symbol: str = "515880.SH", day: date = date(2026, 10, 16)) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol] * rows,
        "datetime": [datetime.combine(day, datetime.min.time()).replace(hour=9, minute=30 + i) for i in range(rows)],
        "close": [1.0 + i for i in range(rows)],
    })


def test_concurrent_identical_reads_share_one_fetch_and_return_independent_frames():
    cache = MinuteResponseCache(ttl_seconds=2, max_entries=8, max_rows=100, wait_timeout_seconds=1)
    calls = 0
    calls_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def loader() -> pl.DataFrame:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return _frame(3)

    def read() -> pl.DataFrame:
        barrier.wait()
        return cache.get_or_fetch(_key(), loader)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: read(), range(8)))

    assert calls == 1
    assert [result.height for result in results] == [3] * 8
    assert cache.entry_count == 1
    results[0].replace_column(2, pl.Series("close", [999.0] * 3))
    assert results[1]["close"].to_list() == [1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    "change",
    [
        {"data_dir": "D:/data-b"},
        {"provider": "tickflow"},
        {"asset_type": "stock"},
        {"symbol": "512800.SH"},
        {"trade_date": date(2026, 10, 15)},
        {"frequency": "5m"},
        {"raw_basis": False},
        {"adjustment_context": (("adj_factor_etf/all.parquet", 2, 99),)},
        {"local_partition_context": ("new-local-generation",)},
    ],
)
def test_result_isolated_by_every_fetch_and_price_context(change):
    cache = MinuteResponseCache(ttl_seconds=2)
    calls = 0

    def matching_loader(key: MinuteFetchKey):
        nonlocal calls
        def load() -> pl.DataFrame:
            nonlocal calls
            calls += 1
            return _frame(symbol=key.symbol, day=key.trade_date)
        return load

    first_key = _key()
    second_key = _key(**change)
    cache.get_or_fetch(first_key, matching_loader(first_key))
    cache.get_or_fetch(second_key, matching_loader(second_key))

    assert calls == 2
    assert cache.entry_count == 2


def test_expired_result_is_fetched_again():
    now = [0.0]
    cache = MinuteResponseCache(ttl_seconds=5, clock=lambda: now[0])
    calls = 0

    def loader() -> pl.DataFrame:
        nonlocal calls
        calls += 1
        return _frame()

    cache.get_or_fetch(_key(), loader)
    now[0] = 5.01
    cache.get_or_fetch(_key(), loader)

    assert calls == 2
    assert cache.entry_count == 1


def test_empty_and_failed_fetches_are_not_cached_and_pending_state_is_cleared():
    cache = MinuteResponseCache(ttl_seconds=2)
    calls = 0

    def empty_loader() -> pl.DataFrame:
        nonlocal calls
        calls += 1
        return pl.DataFrame()

    assert cache.get_or_fetch(_key(), empty_loader).is_empty()
    assert cache.get_or_fetch(_key(), empty_loader).is_empty()
    assert calls == 2
    assert cache.entry_count == 0
    assert cache.pending_count == 0

    def failed_loader() -> pl.DataFrame:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("upstream unavailable")
        return _frame()

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        cache.get_or_fetch(_key(symbol="600000.SH"), failed_loader)
    assert cache.pending_count == 0
    assert cache.get_or_fetch(_key(symbol="600000.SH"), failed_loader).height == 1
    assert cache.pending_count == 0


def test_waiter_stops_waiting_after_configured_timeout():
    cache = MinuteResponseCache(ttl_seconds=2, wait_timeout_seconds=0.03)
    owner_started = threading.Event()
    release_owner = threading.Event()
    calls = 0

    def slow_loader() -> pl.DataFrame:
        nonlocal calls
        calls += 1
        owner_started.set()
        release_owner.wait(1)
        return _frame()

    def owner_read() -> pl.DataFrame:
        return cache.get_or_fetch(_key(), slow_loader)

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(owner_read)
        assert owner_started.wait(0.5)
        started = time.monotonic()
        waiter_result = cache.get_or_fetch(_key(), _frame)
        elapsed = time.monotonic() - started
        release_owner.set()
        owner.result(timeout=1)

    assert elapsed < 0.2
    assert waiter_result.height == 1
    assert calls == 1
    assert cache.pending_count == 0


def test_invalid_symbol_date_and_oversized_frames_are_not_cached():
    cache = MinuteResponseCache(ttl_seconds=2, max_rows=2)
    calls = 0

    def loader() -> pl.DataFrame:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _frame(symbol="512800.SH")
        if calls == 2:
            return _frame(day=date(2026, 10, 15))
        return _frame(3)

    for _ in range(2):
        cache.get_or_fetch(_key(), loader)
    assert calls == 2
    assert cache.entry_count == 0

    for _ in range(2):
        cache.get_or_fetch(_key(), loader)
    assert calls == 4
    assert cache.entry_count == 0


def test_entry_and_row_limits_evict_oldest_cache_entries():
    cache = MinuteResponseCache(ttl_seconds=20, max_entries=2, max_rows=3)
    calls = 0

    def loader() -> pl.DataFrame:
        nonlocal calls
        calls += 1
        return _frame(2, symbol=current_symbol[0])

    current_symbol = ["515880.SH"]
    cache.get_or_fetch(_key(symbol="515880.SH"), loader)
    current_symbol[0] = "512800.SH"
    cache.get_or_fetch(_key(symbol="512800.SH"), loader)
    assert cache.entry_count == 1
    assert cache.cached_rows == 2

    current_symbol[0] = "510300.SH"
    cache.get_or_fetch(_key(symbol="510300.SH"), loader)
    assert cache.entry_count == 1
    assert cache.cached_rows == 2
    current_symbol[0] = "515880.SH"
    cache.get_or_fetch(_key(symbol="515880.SH"), loader)
    assert calls == 4


def test_adjustment_context_fingerprint_changes_when_factor_partition_changes(tmp_path):
    from app.services.minute_response_cache import adjustment_context

    first = adjustment_context(tmp_path, "etf", raw_basis=False)
    factor_dir = tmp_path / "adj_factor_etf"
    factor_dir.mkdir()
    (factor_dir / "all.parquet").write_bytes(b"factor-a")
    second = adjustment_context(tmp_path, "etf", raw_basis=False)
    raw = adjustment_context(tmp_path, "etf", raw_basis=True)

    assert first != second
    assert raw == ("raw-fetch",)


@pytest.mark.parametrize(
    "hour_minute,expected",
    [((15, 29), False), ((15, 30), True), ((16, 0), True)],
)
def test_current_date_cache_opens_only_after_close(hour_minute, expected):
    from app.services.minute_response_cache import is_closed_minute_date

    now = datetime.combine(date(2026, 10, 19), clock_time(*hour_minute))

    assert is_closed_minute_date(date(2026, 10, 19), date(2026, 10, 19), now) is expected
    assert is_closed_minute_date(date(2026, 10, 16), date(2026, 10, 19), now) is True
    assert is_closed_minute_date(date(2026, 10, 20), date(2026, 10, 19), now) is False
