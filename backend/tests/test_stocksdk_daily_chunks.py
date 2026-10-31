from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.data_providers import source_routing, tickflow_provider
from app.plugins.stocksdk import provider as stocksdk_provider
from app.services import trading_calendar


def test_stocksdk_iter_daily_is_lazy_and_emits_40_symbol_chunks(monkeypatch):
    calls = []
    progress = []

    def fake_bridge(job, timeout=None):
        calls.append(job)
        return {"rows": {}}

    monkeypatch.setattr(stocksdk_provider.bridge, "run_job", fake_bridge)
    monkeypatch.setattr(stocksdk_provider, "_closed_daily_day", lambda *_args: None)
    symbols = [f"{index:06d}.SH" for index in range(81)]
    provider = stocksdk_provider.StockSDKProvider()
    chunks = provider.iter_daily(
        symbols,
        datetime(2026, 10, 28),
        datetime(2026, 10, 29),
        asset_type="etf",
        on_chunk_done=lambda done, total: progress.append((done, total)),
    )

    assert calls == []
    assert next(chunks).is_empty()
    assert [len(job["symbols"]) for job in calls] == [40]
    assert progress == []
    assert next(chunks).is_empty()
    assert progress == [(1, 3)]
    assert next(chunks).is_empty()
    assert [len(job["symbols"]) for job in calls] == [40, 40, 1]
    assert progress == [(1, 3), (2, 3)]
    with pytest.raises(StopIteration):
        next(chunks)
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert all(job["asset_type"] == "etf" for job in calls)


def test_stocksdk_iter_daily_keeps_long_history_aggregate_path():
    calls = []

    def progress(*_args):
        return None

    class RecordingProvider(stocksdk_provider.StockSDKProvider):
        def get_daily(self, symbols, start_time, end_time, asset_type="stock", on_chunk_done=None):
            calls.append((symbols, start_time, end_time, asset_type, on_chunk_done))
            return pl.DataFrame()

    symbols = [f"{index:06d}.SH" for index in range(81)]
    provider = RecordingProvider()
    result = list(provider.iter_daily(
        symbols,
        datetime(2026, 10, 20),
        datetime(2026, 10, 29),
        asset_type="stock",
        on_chunk_done=progress,
    ))

    assert len(result) == 1
    assert calls == [(symbols, datetime(2026, 10, 20), datetime(2026, 10, 29), "stock", progress)]


@pytest.mark.parametrize(
    ("now", "start", "end", "latest", "expected", "expected_boundary"),
    [
        (
            datetime(2026, 10, 29, 16, 0),
            datetime(2026, 10, 29),
            datetime(2026, 10, 29),
            date(2026, 10, 29),
            "20261029",
            date(2026, 10, 30),
        ),
        (
            datetime(2026, 10, 30, 10, 0),
            datetime(2026, 10, 29),
            datetime(2026, 10, 29),
            date(2026, 10, 29),
            "20261029",
            date(2026, 10, 30),
        ),
        (
            datetime(2026, 10, 29, 15, 0),
            datetime(2026, 10, 29),
            datetime(2026, 10, 29, 15, 0),
            date(2026, 10, 28),
            None,
            None,
        ),
        (
            datetime(2026, 10, 30, 17, 0),
            datetime(2026, 10, 29),
            datetime(2026, 10, 29),
            None,
            None,
            date(2026, 10, 31),
        ),
        (
            datetime(2026, 10, 30, 17, 0),
            datetime(2026, 10, 29),
            datetime(2026, 10, 29),
            date(2026, 10, 28),
            None,
            date(2026, 10, 31),
        ),
        (
            datetime(2026, 10, 30, 17, 0),
            datetime(2026, 10, 28),
            datetime(2026, 10, 29),
            date(2026, 10, 29),
            None,
            None,
        ),
    ],
)
def test_daily_job_sets_closed_day_only_for_latest_completed_single_day(
    monkeypatch, now, start, end, latest, expected, expected_boundary
):
    jobs = []
    boundaries = []

    def fake_bridge(job, timeout=None):
        jobs.append(job)
        return {"rows": {}}

    monkeypatch.setattr(stocksdk_provider.bridge, "run_job", fake_bridge)
    monkeypatch.setattr(
        stocksdk_provider,
        "cn_now",
        lambda: now.replace(tzinfo=stocksdk_provider.CN_TZ),
    )

    def fake_previous_trading_day(boundary, wait_seconds=0):
        boundaries.append(boundary)
        return latest

    monkeypatch.setattr(trading_calendar, "previous_trading_day", fake_previous_trading_day)
    stocksdk_provider.StockSDKProvider().get_daily(
        ["600000.SH"], start, end, asset_type="etf"
    )

    assert len(jobs) == 1
    assert jobs[0]["asset_type"] == "etf"
    assert jobs[0].get("closed_day") == expected
    if expected_boundary is None:
        assert boundaries == []
    else:
        assert boundaries == [expected_boundary]


def test_aware_daily_job_dates_match_closed_day_in_china_timezone(monkeypatch):
    jobs = []
    same_moment = datetime(2026, 10, 29, 16, 0, tzinfo=UTC)
    now = datetime(2026, 10, 30, 17, 0, tzinfo=stocksdk_provider.CN_TZ)

    monkeypatch.setattr(stocksdk_provider.bridge, "run_job", lambda job, timeout=None: jobs.append(job) or {"rows": {}})
    monkeypatch.setattr(stocksdk_provider, "cn_now", lambda: now)
    monkeypatch.setattr(
        trading_calendar,
        "previous_trading_day",
        lambda boundary, wait_seconds=0: date(2026, 10, 30),
    )

    stocksdk_provider.StockSDKProvider().get_daily(
        ["600000.SH"], same_moment, same_moment, asset_type="stock"
    )

    assert jobs[0]["start"] == jobs[0]["end"] == jobs[0]["closed_day"] == "20261030"


def test_routing_daily_generator_uses_wrapper_failover_per_chunk(monkeypatch):
    expected_day = date(2026, 10, 29)
    primary_requests = []
    fallback_requests = []
    progress = []

    class Primary:
        name = "stocksdk"

        def get_daily(self, symbols, **_kwargs):
            primary_requests.append(list(symbols))
            return pl.DataFrame()

        def iter_daily(self, *_args, **_kwargs):
            raise AssertionError("raw primary iterator bypassed wrapper fallback")

    class AlwaysAttempt:
        @staticmethod
        def attempt(_key, fetch, accept):
            value = fetch()
            return SimpleNamespace(value=value, success=accept(value))

    def fallback_daily(symbols, start_time, end_time, asset_type):
        fallback_requests.append((list(symbols), asset_type))
        return pl.DataFrame(
            {
                "symbol": symbols,
                "date": [expected_day] * len(symbols),
                "open": [10.0] * len(symbols),
                "high": [11.0] * len(symbols),
                "low": [9.0] * len(symbols),
                "close": [10.5] * len(symbols),
                "volume": [1.0] * len(symbols),
            }
        )

    monkeypatch.setattr(source_routing, "enabled", lambda: True)
    monkeypatch.setattr(source_routing, "HEALTH", AlwaysAttempt())
    monkeypatch.setattr(source_routing, "_daily_session", lambda _end: expected_day)
    monkeypatch.setattr(
        tickflow_provider,
        "TickFlowProvider",
        lambda: SimpleNamespace(get_daily=fallback_daily),
    )

    symbols = [f"{index:06d}.SH" for index in range(41)]
    wrapper = source_routing.wrap(Primary())
    chunks_iter = wrapper.iter_daily(
        symbols,
        start_time=datetime(2026, 10, 29),
        end_time=datetime(2026, 10, 29),
        asset_type="stock",
        on_chunk_done=lambda done, total: progress.append((done, total)),
    )

    first = next(chunks_iter)
    assert first.height == 40
    assert progress == []
    second = next(chunks_iter)
    assert second.height == 1
    assert progress == [(1, 2)]
    with pytest.raises(StopIteration):
        next(chunks_iter)
    assert progress == [(1, 2), (2, 2)]
    assert [len(symbols) for symbols in primary_requests] == [40, 1]
    assert [len(symbols) for symbols, _asset_type in fallback_requests] == [40, 1]
    assert all(asset_type == "stock" for _symbols, asset_type in fallback_requests)


def test_non_stocksdk_wrapper_preserves_native_iterator_or_aggregate_path(monkeypatch):
    monkeypatch.setattr(source_routing, "enabled", lambda: True)
    symbols = [f"{index:06d}.SH" for index in range(81)]
    start = datetime(2026, 10, 28)
    end = datetime(2026, 10, 29)
    native_calls = []

    class NativeProvider:
        name = "fuyao"

        def iter_daily(self, got_symbols, **kwargs):
            native_calls.append((got_symbols, kwargs))
            yield pl.DataFrame({"source": ["native"]})

        def get_daily(self, *_args, **_kwargs):
            raise AssertionError("native iterator should be preserved")

    native_frames = list(source_routing.wrap(NativeProvider()).iter_daily(
        symbols, start_time=start, end_time=end, asset_type="stock"
    ))
    assert native_frames[0]["source"][0] == "native"
    assert native_calls == [(symbols, {
        "start_time": start,
        "end_time": end,
        "asset_type": "stock",
        "on_chunk_done": None,
    })]

    aggregate_calls = []

    class AggregateProvider:
        name = "txquote"

        def get_daily(self, got_symbols, **kwargs):
            aggregate_calls.append((got_symbols, kwargs))
            return pl.DataFrame({"source": ["aggregate"]})

    aggregate_frames = list(source_routing.wrap(AggregateProvider()).iter_daily(
        symbols, start_time=start, end_time=end, asset_type="stock"
    ))
    assert aggregate_frames[0]["source"][0] == "aggregate"
    assert aggregate_calls == [(symbols, {
        "start_time": start,
        "end_time": end,
        "asset_type": "stock",
        "on_chunk_done": None,
    })]


def test_routing_daily_filter_uses_china_date_for_aware_utc_request(monkeypatch):
    local_day = date(2026, 10, 30)
    utc_midnight_crossing = datetime(2026, 10, 29, 16, 0, tzinfo=UTC)
    primary_requests = []

    class Primary:
        name = "stocksdk"

        def get_daily(self, symbols, **_kwargs):
            primary_requests.append(list(symbols))
            return pl.DataFrame(
                {
                    "symbol": symbols,
                    "date": [local_day] * len(symbols),
                    "open": [10.0] * len(symbols),
                    "high": [11.0] * len(symbols),
                    "low": [9.0] * len(symbols),
                    "close": [10.5] * len(symbols),
                    "volume": [1.0] * len(symbols),
                }
            )

        def iter_daily(self, *_args, **_kwargs):
            raise AssertionError("wrapper should own the iterator")

    class AlwaysAttempt:
        @staticmethod
        def attempt(_key, fetch, accept):
            value = fetch()
            return SimpleNamespace(value=value, success=accept(value))

    monkeypatch.setattr(source_routing, "enabled", lambda: True)
    monkeypatch.setattr(source_routing, "HEALTH", AlwaysAttempt())
    monkeypatch.setattr(source_routing, "_daily_session", lambda _end: local_day)
    frames = list(source_routing.wrap(Primary()).iter_daily(
        ["600000.SH"],
        start_time=utc_midnight_crossing,
        end_time=utc_midnight_crossing,
        asset_type="stock",
    ))

    assert primary_requests == [["600000.SH"]]
    assert len(frames) == 1
    assert frames[0]["date"].to_list() == [local_day]


def test_wrapper_and_stocksdk_short_range_use_china_dates(monkeypatch):
    # UTC dates differ by eight days, while the requested Beijing dates are exactly seven days apart.
    start = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)  # Beijing 2026-10-02
    end = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)  # Beijing 2026-10-09
    symbols = [f"{index:06d}.SH" for index in range(41)]
    wrapper_calls = []
    raw_calls = []

    class Primary:
        name = "stocksdk"

    wrapper = source_routing._Provider(Primary())

    def wrapper_get_daily(got_symbols, **_kwargs):
        wrapper_calls.append(list(got_symbols))
        return pl.DataFrame()

    monkeypatch.setattr(wrapper, "get_daily", wrapper_get_daily)
    list(wrapper.iter_daily(symbols, start_time=start, end_time=end))

    class RecordingProvider(stocksdk_provider.StockSDKProvider):
        def get_daily(self, got_symbols, **_kwargs):
            raw_calls.append(list(got_symbols))
            return pl.DataFrame()

    list(RecordingProvider().iter_daily(symbols, start_time=start, end_time=end))

    assert [len(chunk) for chunk in wrapper_calls] == [40, 1]
    assert [len(chunk) for chunk in raw_calls] == [40, 1]
