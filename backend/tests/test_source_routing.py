from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, date, datetime
from threading import Lock
from types import SimpleNamespace

import polars as pl
import pytest

from app import market_time
from app.data_providers import source_routing, tickflow_provider
from app.data_providers.source_health import SourceHealth
from app.services import preferences, trading_calendar

MARKET_NOW = datetime(2025, 6, 2, 10, 0, tzinfo=market_time.CN_TZ)
MARKET_TIMESTAMP_MS = int(MARKET_NOW.timestamp() * 1000)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def isolate_router_state(monkeypatch, clock):
    monkeypatch.setattr(source_routing, "HEALTH", SourceHealth(clock=clock))
    monkeypatch.setattr(source_routing, "_PROXIES", OrderedDict())
    monkeypatch.setattr(source_routing, "_LAST", OrderedDict())
    monkeypatch.setattr(source_routing, "_LOCK", Lock())
    monkeypatch.setattr(market_time, "cn_now", lambda: MARKET_NOW)
    monkeypatch.setattr(market_time, "in_continuous_session", lambda _now=None: True)
    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda _day, wait_seconds=0: True)
    monkeypatch.setattr(trading_calendar, "previous_trading_day", lambda _day, wait_seconds=0: None)
    monkeypatch.setattr(
        preferences,
        "load",
        lambda: {"data_source_failover_enabled": True},
    )
    for getter in (
        "get_daily_data_provider",
        "get_realtime_data_provider",
        "get_minute_data_provider",
        "get_full_minute_data_provider",
    ):
        monkeypatch.setattr(preferences, getter, lambda: "tickflow")
    return clock


def _provider(name: str, **methods):
    return SimpleNamespace(name=name, **methods)


def _quote(
    symbol: str,
    *,
    price: float = 10.0,
    server_time: str = "source-time",
    timestamp: int | float | None = MARKET_TIMESTAMP_MS,
) -> dict:
    return {
        "symbol": symbol,
        "last_price": price,
        "server_time": server_time,
        "timestamp": timestamp,
    }


def _full_market(
    count: int,
    *,
    server_time: str,
    timestamp: int | float | None = MARKET_TIMESTAMP_MS,
) -> list[dict]:
    return [
        _quote(f"{index:06d}.SH", server_time=server_time, timestamp=timestamp)
        for index in range(count)
    ]


def _daily_row(
    symbol: str,
    *,
    day: date = date(2025, 6, 2),
    open_: float = 10.0,
    high: float = 11.0,
    low: float = 9.0,
    close: float = 10.5,
    volume: float = 100.0,
    amount: float | None = None,
    adj_factor: float = 12.0,
    adj_close: float | None = None,
) -> dict:
    return {
        "symbol": symbol,
        "date": day,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "adj_factor": adj_factor,
        "adj_close": adj_close if adj_close is not None else close * adj_factor,
    }


def _daily_frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows)) if rows else pl.DataFrame()


def _minute_frame(
    symbol: str,
    timestamps: list[datetime],
    *,
    amount: float | None = None,
    source_note: str = "source",
) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol] * len(timestamps),
        "datetime": timestamps,
        "open": [10.0] * len(timestamps),
        "high": [10.2] * len(timestamps),
        "low": [9.9] * len(timestamps),
        "close": [10.1] * len(timestamps),
        "volume": [100.0] * len(timestamps),
        "amount": [amount] * len(timestamps),
        "source_note": [source_note] * len(timestamps),
    })


def test_wrap_is_opt_in_and_reuses_proxy_until_raw_provider_is_reloaded(monkeypatch):
    monkeypatch.setattr(preferences, "load", lambda: {"data_source_failover_enabled": False})
    original = _provider("stocksdk", get_instruments=lambda: "original")
    assert source_routing.wrap(original) is original

    monkeypatch.setattr(preferences, "load", lambda: {"data_source_failover_enabled": True})
    proxy = source_routing.wrap(original)
    assert proxy is not original
    assert source_routing.wrap(original) is proxy
    assert proxy.get_instruments() == "original"

    replacement = _provider("stocksdk", get_instruments=lambda: "reloaded")
    replacement_proxy = source_routing.wrap(replacement)
    assert replacement_proxy is not proxy
    assert source_routing.wrap(replacement) is replacement_proxy
    assert replacement_proxy.get_instruments() == "reloaded"


def test_healthy_full_market_primary_does_not_call_backup(monkeypatch):
    backup_calls = []
    primary = _provider(
        "stocksdk",
        get_realtime=lambda: _full_market(1000, server_time="sdk-time"),
    )
    backup = _provider("fuyao", get_realtime=lambda: backup_calls.append("called") or [])
    monkeypatch.setattr(source_routing, "_raw", lambda name: backup)

    rows = source_routing.wrap(primary).get_realtime()

    assert len(rows) == 1000
    assert all(row["_source_timestamp_verified"] is True for row in rows)
    assert all(row["server_time"] == "sdk-time" for row in rows)
    assert backup_calls == []
    assert source_routing.status()["active"] == [
        {"dataset": "get_realtime", "primary": "stocksdk", "active": "stocksdk"}
    ]


@pytest.mark.parametrize(
    "failure", ["empty", "exception", "under_minimum", "stale_timestamp"]
)
def test_full_market_empty_exception_and_short_response_use_fuyao_once_per_cooldown(
    monkeypatch,
    failure,
):
    calls = {"stocksdk": 0, "fuyao": 0}

    def primary_realtime():
        calls["stocksdk"] += 1
        if failure == "empty":
            return []
        if failure == "exception":
            raise RuntimeError("https://private.invalid/?token=source-secret")
        if failure == "stale_timestamp":
            return _full_market(
                1000,
                server_time="stale-sdk-time",
                timestamp=MARKET_TIMESTAMP_MS - 120_001,
            )
        return _full_market(999, server_time="short-sdk-time")

    def backup_realtime():
        calls["fuyao"] += 1
        return _full_market(1000, server_time="fuyao-server-time")

    primary = _provider("stocksdk", get_realtime=primary_realtime)
    backup = _provider("fuyao", get_realtime=backup_realtime)
    monkeypatch.setattr(source_routing, "_raw", lambda _name: backup)
    proxy = source_routing.wrap(primary)

    first = proxy.get_realtime()
    second = proxy.get_realtime()

    assert len(first) == len(second) == 1000
    assert all(row["_source_timestamp_verified"] is False for row in first + second)
    assert all(row["server_time"] == "fuyao-server-time" for row in first + second)
    assert all(row["timestamp"] is None for row in first + second)
    assert calls == {"stocksdk": 1, "fuyao": 2}


def test_explicit_index_and_etf_fallbacks_keep_their_symbol_scopes(monkeypatch):
    calls = []
    index_primary, index_missing = "000001.SH", "000002.SH"
    etf_primary, etf_missing = "510300.SH", "159919.SZ"
    primary_calls = []

    def primary_indices(symbols):
        primary_calls.append(("indices", list(symbols)))
        return [_quote(index_primary, price=31.0)]

    def primary_etfs(symbols):
        primary_calls.append(("etfs", list(symbols)))
        return [_quote(etf_primary, price=4.25)]

    primary = _provider(
        "stocksdk",
        get_realtime=lambda: pytest.fail("explicit requests must not use full-market realtime"),
        get_realtime_indices=primary_indices,
        get_realtime_etfs=primary_etfs,
    )

    def backup_indices(symbols):
        calls.append(("indices", list(symbols)))
        return [_quote(index_missing, price=32.0), _quote(index_primary, price=99.0)]

    def backup_etfs(symbols):
        calls.append(("etfs", list(symbols)))
        return [_quote(etf_missing, price=4.5), _quote(etf_primary, price=99.0)]

    backup = _provider(
        "fuyao",
        get_realtime=lambda: pytest.fail("full-market fallback must not be used"),
        get_realtime_indices=backup_indices,
        get_realtime_etfs=backup_etfs,
    )
    monkeypatch.setattr(source_routing, "_raw", lambda name: backup)
    proxy = source_routing.wrap(primary)

    index_rows = proxy.get_realtime_indices([index_primary, index_missing])
    etf_rows = proxy.get_realtime_etfs([etf_primary, etf_missing])

    assert [row["symbol"] for row in index_rows] == [index_primary, index_missing]
    assert [row["symbol"] for row in etf_rows] == [etf_primary, etf_missing]
    assert [row["last_price"] for row in index_rows] == [31.0, 32.0]
    assert [row["last_price"] for row in etf_rows] == [4.25, 4.5]
    assert [row["_source_timestamp_verified"] for row in index_rows] == [True, False]
    assert [row["_source_timestamp_verified"] for row in etf_rows] == [True, False]
    assert index_rows[0]["timestamp"] == MARKET_TIMESTAMP_MS
    assert index_rows[1]["timestamp"] is None
    assert primary_calls == [
        ("indices", [index_primary, index_missing]),
        ("etfs", [etf_primary, etf_missing]),
    ]
    assert calls == [("indices", [index_missing]), ("etfs", [etf_missing])]


@pytest.mark.parametrize("sdk_response", ["empty", "bad_ohlc"])
def test_daily_empty_or_invalid_stocksdk_uses_raw_tickflow_without_adjustment_mix(
    monkeypatch,
    sdk_response,
):
    requested = "000001.SZ"
    tickflow_calls = []
    sdk_frame = (
        pl.DataFrame()
        if sdk_response == "empty"
        else _daily_frame(_daily_row(requested, high=9.0, close=10.5))
    )
    primary = _provider(
        "stocksdk",
        get_daily=lambda *_args, **_kwargs: sdk_frame,
    )

    class FakeTickFlow:
        def get_daily(self, symbols, start_time, end_time, asset_type):
            tickflow_calls.append((list(symbols), start_time, end_time, asset_type))
            return _daily_frame(
                _daily_row(
                    requested,
                    open_=8.0,
                    high=9.0,
                    low=8.0,
                    close=8.25,
                    adj_factor=20.0,
                    adj_close=165.0,
                ),
                _daily_row("600000.SH", close=44.0),
            )

    monkeypatch.setattr(tickflow_provider, "TickFlowProvider", FakeTickFlow)
    start = datetime(2025, 1, 1)
    end = datetime(2025, 12, 31)

    result = source_routing.wrap(primary).get_daily(
        [requested], start_time=start, end_time=end, asset_type="stock"
    )

    assert result["symbol"].to_list() == [requested]
    assert result["close"].to_list() == [8.25]
    assert "adj_factor" not in result.columns
    assert "adj_close" not in result.columns
    assert tickflow_calls == [([requested], start, end, "stock")]


def test_daily_only_requests_missing_symbols_from_tickflow_and_discards_unrequested_rows(
    monkeypatch,
):
    first, missing, unrelated = "000001.SZ", "000002.SZ", "600000.SH"
    tickflow_requests = []
    primary = _provider(
        "stocksdk",
        get_daily=lambda *_args, **_kwargs: _daily_frame(
            _daily_row(first, close=10.25),
            _daily_row(missing, high=9.0, close=10.5),
            _daily_row(unrelated, close=99.0),
        ),
    )

    class FakeTickFlow:
        def get_daily(self, symbols, start_time, end_time, asset_type):
            tickflow_requests.append(list(symbols))
            return _daily_frame(
                _daily_row(
                    missing,
                    open_=20.0,
                    high=21.0,
                    low=19.0,
                    close=20.5,
                ),
                _daily_row(unrelated, close=88.0),
            )

    monkeypatch.setattr(tickflow_provider, "TickFlowProvider", FakeTickFlow)

    result = source_routing.wrap(primary).get_daily([first, missing], asset_type="stock")

    assert tickflow_requests == [[missing]]
    assert result["symbol"].to_list() == [first, missing]
    assert result["close"].to_list() == [10.25, 20.5]
    assert unrelated not in result["symbol"].to_list()


def test_daily_returns_empty_when_stocksdk_and_tickflow_both_fail(monkeypatch):
    requested = "000001.SZ"
    primary = _provider(
        "stocksdk",
        get_daily=lambda *_args, **_kwargs: _daily_frame(
            _daily_row(requested, high=9.0, close=10.5)
        ),
    )

    class FailedTickFlow:
        def get_daily(self, symbols, start_time, end_time, asset_type):
            raise RuntimeError("https://tickflow.invalid/?secret=private")

    monkeypatch.setattr(tickflow_provider, "TickFlowProvider", FailedTickFlow)

    result = source_routing.wrap(primary).get_daily([requested], asset_type="stock")

    assert result.is_empty()
    assert "tickflow.invalid" not in repr(source_routing.status())
    assert "private" not in repr(source_routing.status())


@pytest.mark.parametrize(
    ("asset_type", "symbol"),
    [("stock", "000001.SZ"), ("etf", "510300.SH")],
)
def test_txquote_1m_stock_and_etf_can_fall_back_to_stocksdk(monkeypatch, asset_type, symbol):
    calls = []
    local_time = datetime(2025, 6, 2, 9, 35)
    primary = _provider(
        "txquote",
        get_minute=lambda symbols, **kwargs: pl.DataFrame(),
    )
    backup = _provider(
        "stocksdk",
        get_minute=lambda symbols, **kwargs: calls.append((list(symbols), kwargs))
        or _minute_frame(symbol, [local_time], source_note="stocksdk"),
    )
    monkeypatch.setattr(source_routing, "_raw", lambda _name: backup)

    result = source_routing.wrap(primary).get_minute(
        [symbol],
        start_time=datetime(2025, 6, 2, 9, 30),
        end_time=datetime(2025, 6, 2, 9, 40),
        asset_type=asset_type,
        freq="1m",
    )

    assert result["symbol"].to_list() == [symbol]
    assert result["source_note"].to_list() == ["stocksdk"]
    assert calls[0][0] == [symbol]
    assert calls[0][1]["asset_type"] == asset_type
    assert calls[0][1]["freq"] == "1m"


def test_txquote_keeps_beijing_wallclock_fields_and_null_amount(monkeypatch):
    symbol = "000001.SZ"
    quote_time = datetime(2025, 6, 2, 1, 35, tzinfo=UTC)
    start = datetime(2025, 6, 2, 1, 35, tzinfo=UTC)
    end = datetime(2025, 6, 2, 1, 36, tzinfo=UTC)
    backup_calls = []
    primary = _provider(
        "txquote",
        get_minute=lambda _symbols, **_kwargs: _minute_frame(
            symbol, [quote_time], amount=None, source_note="txquote"
        ),
    )
    monkeypatch.setattr(
        source_routing,
        "_raw",
        lambda name: backup_calls.append(name) or _provider(name),
    )

    result = source_routing.wrap(primary).get_minute(
        [symbol], start_time=start, end_time=end, asset_type="stock", freq="1m"
    )

    assert result.height == 1
    assert result["datetime"].dtype.time_zone is None
    assert result["datetime"].item() == datetime(2025, 6, 2, 9, 35)
    assert result["amount"].item() is None
    assert result["source_note"].item() == "txquote"
    assert result["close"].item() == 10.1
    assert backup_calls == []


def test_txquote_discards_out_of_window_rows_before_trying_stocksdk(monkeypatch):
    symbol = "000001.SZ"
    start = datetime(2025, 6, 2, 1, 35, tzinfo=UTC)
    end = datetime(2025, 6, 2, 1, 36, tzinfo=UTC)
    sdk_time = datetime(2025, 6, 2, 9, 35)
    calls = []
    primary = _provider(
        "txquote",
        get_minute=lambda _symbols, **_kwargs: _minute_frame(
            symbol,
            [datetime(2025, 6, 2, 1, 34, tzinfo=UTC)],
            source_note="out-of-window-txquote",
        ),
    )
    backup = _provider(
        "stocksdk",
        get_minute=lambda symbols, **kwargs: calls.append((list(symbols), kwargs))
        or _minute_frame(symbol, [sdk_time], source_note="in-window-stocksdk"),
    )
    monkeypatch.setattr(source_routing, "_raw", lambda _name: backup)

    result = source_routing.wrap(primary).get_minute(
        [symbol], start_time=start, end_time=end, asset_type="stock", freq="1m"
    )

    assert calls and calls[0][0] == [symbol]
    assert result["source_note"].to_list() == ["in-window-stocksdk"]
    assert result["datetime"].item() == datetime(2025, 6, 2, 9, 35)


@pytest.mark.parametrize(
    ("name", "asset_type", "freq"),
    [
        ("txquote", "stock", "5m"),
        ("txquote", "index", "1m"),
        ("eltdx_gateway", "stock", "1m"),
    ],
)
def test_noneligible_minute_routes_never_switch_sources(monkeypatch, name, asset_type, freq):
    calls = []
    symbol = "000001.SZ"
    response = _minute_frame(symbol, [datetime(2025, 6, 2, 9, 35)], source_note="primary")

    def get_minute(symbols, **kwargs):
        calls.append((list(symbols), kwargs))
        return response

    primary = _provider(name, get_minute=get_minute)
    monkeypatch.setattr(
        source_routing,
        "_raw",
        lambda _name: pytest.fail("noneligible routes must not resolve a backup"),
    )

    wrapped = source_routing.wrap(primary)
    result = wrapped.get_minute([symbol], asset_type=asset_type, freq=freq)

    assert result is response
    expected_kwargs = {"asset_type": asset_type, "freq": freq}
    if name == "txquote":
        expected_kwargs = {
            "start_time": None,
            "end_time": None,
            **expected_kwargs,
            "on_chunk_done": None,
        }
    assert calls == [([symbol], expected_kwargs)]
    if name == "eltdx_gateway":
        assert wrapped is primary


def test_status_health_snapshot_never_exposes_exception_text(monkeypatch):
    secrets = "https://private.invalid/path?token=hidden API_KEY=secret"
    primary = _provider("stocksdk", get_realtime=lambda: (_ for _ in ()).throw(RuntimeError(secrets)))
    backup = _provider("fuyao", get_realtime=lambda: (_ for _ in ()).throw(ValueError(secrets)))
    monkeypatch.setattr(source_routing, "_raw", lambda _name: backup)

    assert source_routing.wrap(primary).get_realtime() == []
    snapshot = source_routing.status()

    assert "private.invalid" not in repr(snapshot)
    assert "hidden" not in repr(snapshot)
    assert "secret" not in repr(snapshot)
    assert all(set(row) == {"key", "failures", "retry_after_seconds", "probing"}
               for row in snapshot["health"])
