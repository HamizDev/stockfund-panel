"""Overview misses share work without reviving invalidated or failed results."""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest

from app.api import overview


def request(repo=None, quotes=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=repo if repo is not None else object(), quote_service=quotes)))


@pytest.fixture(autouse=True)
def clear_cache():
    overview.invalidate_overview_cache()
    yield
    overview.invalidate_overview_cache()


def test_slow_build_gets_full_cache_window(monkeypatch):
    clock = [100.]
    calls = []
    monkeypatch.setattr(overview.time, 'time', lambda: clock[0])
    monkeypatch.setattr(overview.time, 'monotonic', lambda: clock[0])
    def build(*_):
        calls.append(1)
        clock[0] += 6
        return {'generation': len(calls)}
    monkeypatch.setattr(overview, '_build_overview', build)
    req = request()
    assert overview.market_overview(req) == {'generation': 1}
    assert overview.market_overview(req) == {'generation': 1}
    assert len(calls) == 1
    clock[0] += 5.01
    assert overview.market_overview(req) == {'generation': 2}


def test_concurrent_misses_share_one_build(monkeypatch):
    calls, lock, barrier = [], Lock(), Barrier(8)
    def build(*_):
        with lock:
            calls.append(1)
        time.sleep(.06)
        return {'total': 42}
    monkeypatch.setattr(overview, '_build_overview', build)
    req = request()
    def read(_):
        barrier.wait(timeout=3)
        return overview.market_overview(req)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read, range(8)))
    assert results == [{'total': 42}] * 8
    assert len(calls) == 1


def test_invalidated_inflight_result_cannot_overwrite_new_snapshot(monkeypatch):
    entered, release, calls = Event(), Event(), []
    def build(*_):
        calls.append(1)
        revision = len(calls)
        if revision == 1:
            entered.set()
            assert release.wait(3)
        return {'revision': revision}
    monkeypatch.setattr(overview, '_build_overview', build)
    req = request()
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(overview.market_overview, req)
        try:
            assert entered.wait(2)
            overview.invalidate_overview_cache()
            assert overview.market_overview(req) == {'revision': 2}
        finally:
            release.set()
        assert old.result(timeout=2) == {'revision': 1}
    assert overview.market_overview(req) == {'revision': 2}
    assert len(calls) == 2


def test_repository_and_quote_context_do_not_share_cached_results(monkeypatch):
    calls = []
    def build(req, _):
        calls.append(1)
        return {'repository': id(req.app.state.repo), 'quote': id(req.app.state.quote_service)}
    monkeypatch.setattr(overview, '_build_overview', build)
    first = request()
    second = request(quotes=object())
    assert overview.market_overview(first) != overview.market_overview(second)
    assert len(calls) == 2


def test_historical_date_uses_a_distinct_cache_key(monkeypatch):
    calls = []
    def build(_, day):
        calls.append(day)
        return {'date': day.isoformat() if day else 'latest'}
    monkeypatch.setattr(overview, '_build_overview', build)
    req = request()
    assert overview.market_overview(req, date(2026, 10, 22))['date'] == '2026-10-22'
    assert overview.market_overview(req)['date'] == 'latest'
    assert calls == [date(2026, 10, 22), None]


def test_failed_build_is_not_cached_and_can_be_retried(monkeypatch):
    calls = []
    def build(*_):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError('synthetic read failed')
        return {'ok': True}
    monkeypatch.setattr(overview, '_build_overview', build)
    req = request()
    with pytest.raises(ValueError, match='synthetic read failed'):
        overview.market_overview(req)
    assert overview.market_overview(req) == {'ok': True}
    assert len(calls) == 2
