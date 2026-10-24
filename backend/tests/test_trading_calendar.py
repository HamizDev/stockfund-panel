from __future__ import annotations

import calendar
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from app.services import trading_calendar as service


@pytest.fixture(autouse=True)
def _clear_calendar_cache():
    service.clear_cache()
    yield
    service.clear_cache()


def _payload(year: int, month: int, open_days: set[date] | None = None) -> dict:
    open_days = open_days or set()
    return {"data": [
        {"jyrq": date(year, month, day).isoformat(),
         "jybz": "1" if date(year, month, day) in open_days else "0"}
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
    ]}


def test_parse_requires_full_unique_calendar_month_and_string_flags():
    opens = {date(2026, 10, 8), date(2026, 10, 9)}
    assert service._parse_month(_payload(2026, 10, opens), 2026, 10) == frozenset(opens)

    incomplete = _payload(2026, 10, opens)
    incomplete["data"].pop()
    with pytest.raises(ValueError):
        service._parse_month(incomplete, 2026, 10)

    duplicate = _payload(2026, 10, opens)
    duplicate["data"][-1] = duplicate["data"][0]
    with pytest.raises(ValueError):
        service._parse_month(duplicate, 2026, 10)

    malformed = _payload(2026, 10, opens)
    malformed["data"][0]["jybz"] = True
    with pytest.raises(ValueError):
        service._parse_month(malformed, 2026, 10)

    wrong_month = _payload(2026, 10, opens)
    wrong_month["data"][0]["jyrq"] = "2026-09-01"
    with pytest.raises(ValueError):
        service._parse_month(wrong_month, 2026, 10)


def test_parse_leap_february_requires_all_29_dates():
    days = {date(2024, 2, day) for day in range(1, 30)}
    payload = _payload(2024, 2, days)
    assert service._parse_month(payload, 2024, 2) == frozenset(days)


def test_weekend_is_known_closed_without_request(monkeypatch):
    monkeypatch.setattr(service, "_fetch_month", lambda *_: pytest.fail("weekend must not fetch"))
    assert service.is_trading_day(date(2026, 10, 24)) is False
    assert service.is_trading_day(date(2026, 10, 25)) is False


def test_weekday_uses_official_calendar_or_returns_unknown(monkeypatch):
    opens = {date(2026, 10, 8), date(2026, 10, 9)}
    monkeypatch.setattr(service, "_fetch_month", lambda *_: frozenset(opens))
    assert service.is_trading_day(date(2026, 10, 8)) is True
    assert service.is_trading_day(date(2026, 10, 7)) is False

    service.clear_cache()
    monkeypatch.setattr(service, "_fetch_month", lambda *_: (_ for _ in ()).throw(TimeoutError()))
    assert service.is_trading_day(date(2026, 10, 8), wait_seconds=0.5) is None


def test_previous_session_crosses_october_national_holiday_boundary(monkeypatch):
    october_open = {date(2026, 10, day) for day in (8, 9, *range(12, 17), *range(19, 24), *range(26, 31))}
    september_open = {date(2026, 9, day) for day in range(1, 31)
                      if date(2026, 9, day).weekday() < 5}
    calls = []

    def fetch(year, month):
        calls.append((year, month))
        return october_open if (year, month) == (2026, 10) else september_open

    monkeypatch.setattr(service, "_fetch_month", fetch)
    assert service.previous_trading_day(date(2026, 10, 8)) == date(2026, 9, 30)
    assert calls == [(2026, 10), (2026, 9)]


def test_failed_adjacent_month_does_not_assume_long_holiday(monkeypatch):
    october_open = {date(2026, 10, day) for day in (8, 9, *range(12, 17), *range(19, 24), *range(26, 31))}

    def fetch(year, month):
        if (year, month) == (2026, 10):
            return october_open
        raise TimeoutError

    monkeypatch.setattr(service, "_fetch_month", fetch)
    assert service.previous_trading_day(date(2026, 10, 8), wait_seconds=0.5) is None


def test_failure_is_negative_cached(monkeypatch):
    calls = []

    def fail(*_):
        calls.append(1)
        raise OSError("upstream unavailable")

    monkeypatch.setattr(service, "_fetch_month", fail)
    for _ in range(2):
        assert service.month_open_days(2026, 10, wait_seconds=0.5) is None
    assert len(calls) == 1
    assert len(service._CACHE) == 1
    assert service._CACHE[(2026, 10)][1] is None


def test_simultaneous_month_requests_share_one_fetch(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []
    opens = frozenset({date(2026, 10, 8)})

    def fetch(*_):
        calls.append(1)
        started.set()
        assert release.wait(2)
        return opens

    monkeypatch.setattr(service, "_fetch_month", fetch)
    first_result = []
    with ThreadPoolExecutor(max_workers=1) as callers:
        first = callers.submit(service.month_open_days, 2026, 10, wait_seconds=0.01)
        assert started.wait(1)
        assert first.result(timeout=1) is None  # fetch remains in flight
        assert service.month_open_days(2026, 10, wait_seconds=0.01) is None
        assert len(service._PENDING) == 1
        assert len(calls) == 1
        release.set()
        deadline = time.monotonic() + 2
        while not service._CACHE.get((2026, 10)) and time.monotonic() < deadline:
            time.sleep(0.005)
        first_result.append(service.month_open_days(2026, 10, wait_seconds=0))
    assert first_result == [opens]
    assert len(calls) == 1


def test_wait_timeout_returns_unknown_then_completed_fetch_is_cached(monkeypatch):
    started, release = threading.Event(), threading.Event()
    opens = frozenset({date(2026, 10, 8)})

    def fetch(*_):
        started.set()
        assert release.wait(2)
        return opens

    monkeypatch.setattr(service, "_fetch_month", fetch)
    assert service.month_open_days(2026, 10, wait_seconds=0.01) is None
    assert started.wait(1)
    release.set()
    deadline = time.monotonic() + 2
    while not service._CACHE.get((2026, 10)) and time.monotonic() < deadline:
        time.sleep(0.005)
    assert service.month_open_days(2026, 10, wait_seconds=0) == opens


def test_month_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(service, "_fetch_month", lambda year, month: frozenset({date(year, month, 1)}))
    for index in range(service._MAX_CACHE_ENTRIES + 8):
        year, month = 2024 + index // 12, index % 12 + 1
        assert service.month_open_days(year, month, wait_seconds=0.5)
    assert len(service._CACHE) == service._MAX_CACHE_ENTRIES
