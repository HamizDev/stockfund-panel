from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.services import analysis_current_session as service
from app.services.minute_response_cache import MinuteResponseCache

DAY = date(2026, 10, 22)  # Thursday; deterministic A-share session.
SYMBOL = "515880.SH"


@pytest.fixture(autouse=True)
def _known_weekday_calendar(monkeypatch):
    monkeypatch.setattr(service.trading_calendar, "is_trading_day", lambda *_args, **_kwargs: True)


def _session_times(day: date = DAY, *, include_afternoon: bool = False) -> list[datetime]:
    values: list[datetime] = []
    current = datetime.combine(day, time(9, 30))
    morning_end = datetime.combine(day, time(11, 30))
    while current <= morning_end:
        values.append(current)
        current += timedelta(minutes=1)
    if include_afternoon:
        current = datetime.combine(day, time(13, 1))
        afternoon_end = datetime.combine(day, time(15, 0))
        while current <= afternoon_end:
            values.append(current)
            current += timedelta(minutes=1)
    return values


def _minutes(
    *,
    symbol: str = SYMBOL,
    day: date = DAY,
    include_afternoon: bool = False,
    base: float = 10.0,
    times: list[datetime] | None = None,
) -> pl.DataFrame:
    datetimes = times if times is not None else _session_times(day, include_afternoon=include_afternoon)
    return pl.DataFrame({
        "symbol": [symbol] * len(datetimes),
        "datetime": datetimes,
        "open": [base] * len(datetimes),
        "high": [base + 0.02] * len(datetimes),
        "low": [base - 0.02] * len(datetimes),
        "close": [base + 0.01] * len(datetimes),
        "volume": [1.0] * len(datetimes),
        "amount": [base + 0.01] * len(datetimes),
    })


def _provider(get_frame):
    """Build a raw, declared custom provider for the on-demand path tests."""
    return SimpleNamespace(
        name="stocksdk",
        minute_price_basis="raw",
        get_minute=get_frame,
    )


def _use_provider(monkeypatch, selected, provider_for):
    monkeypatch.setattr(service.preferences, "get_minute_data_provider", lambda: selected[0])
    monkeypatch.setattr(
        service.kline_sync,
        "_resolve_minute_provider",
        lambda name, *, asset_type: (provider_for(name), False, None),
    )


def test_aggregates_complete_121_bar_morning_session() -> None:
    frame = _minutes()

    bar, minute_time = service._aggregate(frame, SYMBOL, datetime(2026, 10, 22, 11, 30))

    assert frame.height == 121
    assert bar == {
        "symbol": SYMBOL,
        "date": DAY,
        "open": 10.0,
        "high": 10.02,
        "low": 9.98,
        "close": 10.01,
        "volume": 121.0,
        "amount": pytest.approx(1211.21),
    }
    assert minute_time is not None and minute_time.startswith("2026-10-22T11:30:00+08:00")


def test_lunch_break_is_not_treated_as_missing_minutes() -> None:
    frame = _minutes()

    bar, minute_time = service._aggregate(frame, SYMBOL, datetime(2026, 10, 22, 12, 30))

    assert bar is not None and bar["volume"] == 121.0
    assert minute_time is not None and minute_time.startswith("2026-10-22T11:30:00+08:00")


def test_stale_morning_data_does_not_look_like_a_full_session() -> None:
    frame = _minutes()

    assert service._aggregate(frame, SYMBOL, datetime(2026, 10, 22, 13, 34)) == (None, None)


def test_full_session_aggregates_across_lunch_without_lunch_bars() -> None:
    frame = _minutes(include_afternoon=True)

    bar, minute_time = service._aggregate(frame, SYMBOL, datetime(2026, 10, 22, 15, 0))

    # 09:30 is an optional opening label; the expected continuous-session bars
    # are 09:31-11:30 and 13:01-15:00 (240 expected plus optional 09:30).
    assert frame.height == 241
    assert bar is not None and bar["volume"] == 241.0
    assert minute_time is not None and minute_time.startswith("2026-10-22T15:00:00+08:00")


def test_rejects_missing_opening_or_middle_minute() -> None:
    all_times = _session_times()
    missing_open = _minutes(times=all_times[2:])  # begins at 09:32, not a session open
    missing_middle = _minutes(times=[t for t in all_times if t != datetime(2026, 10, 22, 10, 1)])

    assert service._aggregate(missing_open, SYMBOL, datetime(2026, 10, 22, 11, 30)) == (None, None)
    assert service._aggregate(missing_middle, SYMBOL, datetime(2026, 10, 22, 11, 30)) == (None, None)


def test_ignores_future_minutes_and_rejects_future_only_or_wrong_symbol_rows() -> None:
    all_times = _session_times()
    now = datetime(2026, 10, 22, 11, 0)
    current_and_future = _minutes(times=all_times)
    future_only = _minutes(day=DAY + timedelta(days=1))
    historical_only = _minutes(day=DAY - timedelta(days=1))
    wrong_symbol = _minutes(symbol="510300.SH")

    bar, minute_time = service._aggregate(current_and_future, SYMBOL, now)
    assert bar is not None and bar["volume"] == 91.0
    assert minute_time is not None and minute_time.startswith("2026-10-22T11:00:00+08:00")
    assert service._aggregate(future_only, SYMBOL, now) == (None, None)
    assert service._aggregate(historical_only, SYMBOL, now) == (None, None)
    assert service._aggregate(wrong_symbol, SYMBOL, datetime(2026, 10, 22, 11, 30)) == (None, None)


@pytest.mark.parametrize(
    ("column", "bad_value"),
    [("high", 9.0), ("low", 11.0), ("close", float("nan"))],
)
def test_rejects_invalid_ohlc(column: str, bad_value: float) -> None:
    at = datetime(2026, 10, 22, 10, 0)
    frame = _minutes().with_columns(
        pl.when(pl.col("datetime") == at).then(pl.lit(bad_value)).otherwise(pl.col(column)).alias(column)
    )

    assert service._aggregate(frame, SYMBOL, datetime(2026, 10, 22, 11, 30)) == (None, None)


def test_cache_is_reused_for_same_provider_and_isolated_when_provider_changes(tmp_path, monkeypatch) -> None:
    selected_provider = ["stocksdk"]
    calls: list[str] = []
    now = datetime(2026, 10, 22, 11, 30)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))

    monkeypatch.setattr(service, "_CACHE", MinuteResponseCache(
        ttl_seconds=20, max_entries=8, max_rows=2_000, max_rows_per_entry=300,
        max_inflight=2, auto_expire=False,
    ))
    monkeypatch.setattr(service, "_PENDING", {})
    monkeypatch.setattr(service, "_LOCK", threading.Lock())
    providers = {}
    for name, base in (("stocksdk", 10.0), ("eltdx_gateway", 20.0)):
        def fetch(symbols, *, start_time, end_time, asset_type, freq, _name=name, _base=base):
            assert asset_type == "etf" and freq == "1m"
            assert start_time.date() == DAY and end_time.date() == DAY
            calls.append(_name)
            return _minutes(symbol=symbols[0], base=_base)
        providers[name] = _provider(fetch)
        providers[name].name = name
    _use_provider(monkeypatch, selected_provider, providers.__getitem__)

    first, _ = service.load_current(repo, None, SYMBOL, "etf", now)
    same_provider, _ = service.load_current(repo, None, SYMBOL, "etf", now)
    selected_provider[0] = "eltdx_gateway"
    changed_provider, _ = service.load_current(repo, None, SYMBOL, "etf", now)

    assert first is not None and first["close"] == 10.01
    assert same_provider == first
    assert changed_provider is not None and changed_provider["close"] == 20.01
    assert calls == ["stocksdk", "eltdx_gateway"]


def test_provider_context_change_during_fetch_discards_result_and_does_not_cache(tmp_path, monkeypatch) -> None:
    selected_provider = ["stocksdk"]
    now = datetime(2026, 10, 22, 11, 30)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    cache = MinuteResponseCache(
        ttl_seconds=20, max_entries=8, max_rows=2_000, max_rows_per_entry=300,
        max_inflight=2, auto_expire=False,
    )
    monkeypatch.setattr(service, "_CACHE", cache)
    monkeypatch.setattr(service, "_PENDING", {})
    monkeypatch.setattr(service, "_LOCK", threading.Lock())

    def fetch(symbols, *, start_time, end_time, asset_type, freq):
        selected_provider[0] = "eltdx_gateway"
        return _minutes(symbol=symbols[0])

    providers = {
        "stocksdk": _provider(fetch),
        "eltdx_gateway": _provider(lambda *args, **kwargs: _minutes()),
    }
    providers["eltdx_gateway"].name = "eltdx_gateway"
    _use_provider(monkeypatch, selected_provider, providers.__getitem__)

    result = service.load_current(repo, None, SYMBOL, "etf", now)

    assert result == (None, None)
    assert cache.entry_count == 0


def test_custom_provider_resolution_failure_does_not_fall_back_to_native(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 10, 22, 11, 30)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(service.preferences, "get_minute_data_provider", lambda: "custom_source")
    monkeypatch.setattr(
        service.kline_sync,
        "_resolve_minute_provider",
        lambda name, *, asset_type: (None, True, None),
    )
    monkeypatch.setattr(
        service.kline_sync,
        "fetch_minute_single",
        lambda *args, **kwargs: pytest.fail("custom provider failure must not silently use native data"),
    )
    native_capset = SimpleNamespace(has=lambda _cap: True)

    assert service.load_current(repo, native_capset, SYMBOL, "etf", now) == (None, None)


def test_selected_tickflow_uses_native_minutes_when_capability_is_present(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 10, 22, 11, 30)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    native_capset = SimpleNamespace(has=lambda _cap: True)
    monkeypatch.setattr(service, "_CACHE", MinuteResponseCache(
        ttl_seconds=20, max_entries=8, max_rows=2_000, max_rows_per_entry=300,
        max_inflight=2, auto_expire=False,
    ))
    monkeypatch.setattr(service, "_PENDING", {})
    monkeypatch.setattr(service, "_LOCK", threading.Lock())
    monkeypatch.setattr(service.preferences, "get_minute_data_provider", lambda: "tickflow")
    monkeypatch.setattr(service.kline_sync, "_resolve_minute_provider",
                        lambda name, *, asset_type: (None, True, None))

    def fetch(symbol, trade_date, *, asset_type, capset, raw_basis):
        assert symbol == SYMBOL and trade_date == DAY
        assert asset_type == "etf" and capset is native_capset and raw_basis is True
        return _minutes()

    monkeypatch.setattr(service.kline_sync, "fetch_minute_single", fetch)

    bar, minute_time = service.load_current(repo, native_capset, SYMBOL, "etf", now)

    assert bar is not None and bar["close"] == 10.01
    assert minute_time is not None and minute_time.startswith("2026-10-22T11:30:00+08:00")


def test_known_exchange_holiday_skips_current_minute_pull(tmp_path, monkeypatch):
    now = datetime(2026, 10, 7, 10, 30)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(service.trading_calendar, "is_trading_day", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(service, "_source_context", lambda *_: pytest.fail("holiday must stop before resolving provider"))

    assert service.load_current(repo, None, SYMBOL, "etf", now) == (None, None)


def test_drops_third_distinct_fetch_when_two_are_pending(tmp_path, monkeypatch) -> None:
    started = threading.Barrier(3)
    release = threading.Event()
    now = datetime(2026, 10, 22, 11, 30)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))

    monkeypatch.setattr(service, "_CACHE", MinuteResponseCache(
        ttl_seconds=20, max_entries=8, max_rows=2_000, max_rows_per_entry=300,
        max_inflight=2, auto_expire=False,
    ))
    monkeypatch.setattr(service, "_PENDING", {})
    monkeypatch.setattr(service, "_LOCK", threading.Lock())
    selected_provider = ["stocksdk"]

    def fetch(symbols, *, start_time, end_time, asset_type, freq):
        started.wait(timeout=3)
        release.wait(timeout=3)
        return _minutes(symbol=symbols[0])

    provider = _provider(fetch)
    _use_provider(monkeypatch, selected_provider, lambda _name: provider)

    with ThreadPoolExecutor(max_workers=2) as callers:
        first = callers.submit(service.load_current, repo, None, "510300.SH", "etf", now)
        second = callers.submit(service.load_current, repo, None, "512800.SH", "etf", now)
        try:
            started.wait(timeout=3)  # both provider workers are blocked in fetch
            third = service.load_current(repo, None, "515880.SH", "etf", now)
        finally:
            release.set()

        assert third == (None, None)
        assert first.result(timeout=4)[0] is not None
        assert second.result(timeout=4)[0] is not None


def test_explicit_etf_backup_is_cache_isolated_and_has_real_ohlcv_without_amount(tmp_path, monkeypatch):
    selected = ['stocksdk']
    calls = []
    def provider_for(name):
        def fetch(*args, **kwargs):
            calls.append(name)
            return (_minutes().with_columns(pl.lit(None).alias('amount'))
                    if name == 'txquote' else pl.DataFrame())
        provider = _provider(fetch)
        provider.name = name
        provider.minute_daily_aggregation = ('etf',) if name == 'txquote' else True
        return provider
    providers = {name: provider_for(name) for name in ('stocksdk', 'txquote')}
    _use_provider(monkeypatch, selected, providers.__getitem__)
    monkeypatch.setattr(service, '_CACHE', MinuteResponseCache(auto_expire=False))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    now = datetime(2026, 10, 22, 11, 30)
    assert service.load_current(repo, None, SYMBOL, 'etf', now) == (None, None)
    bar, stamp = service.load_current(repo, None, SYMBOL, 'etf', now, provider_name='txquote')
    assert bar is not None and bar['close'] == 10.01 and bar['amount'] is None
    assert stamp == '2026-10-22T11:30:00+08:00'
    assert service.load_current(repo, None, SYMBOL, 'stock', now, provider_name='txquote') == (None, None)
    assert selected == ['stocksdk'] and calls == ['stocksdk', 'txquote']
