"""ETF daily-sync resilience and instrument classification regressions."""
from __future__ import annotations

from types import SimpleNamespace

import polars as pl

from app.services import index_sync, kline_sync


def _raw_daily(symbols: list[str]) -> dict:
    ts = 1_790_208_000_000
    return {
        symbol: {
            "timestamp": [ts],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [1000.0],
            "amount": [10500.0],
        }
        for symbol in symbols
    }


class _FakeKlines:
    def __init__(self, *, fail_fetch: bool = False):
        self.calls = 0
        self.fail_fetch = fail_fetch

    def batch(self, symbols, **kwargs):
        self.calls += 1
        if self.fail_fetch:
            raise RuntimeError("temporary upstream failure")
        return _raw_daily(list(symbols))


def _install_client(monkeypatch, *, fail_fetch: bool = False):
    klines = _FakeKlines(fail_fetch=fail_fetch)
    monkeypatch.setattr(
        kline_sync,
        "get_client",
        lambda: SimpleNamespace(klines=klines),
    )
    monkeypatch.setattr(kline_sync.time, "sleep", lambda *_: None)
    monkeypatch.setattr(kline_sync, "sleep_between_batches", lambda *_: None)
    return klines


def test_daily_batch_retries_transient_conversion_failure(monkeypatch):
    klines = _install_client(monkeypatch)
    real_convert = kline_sync._compact_klines_to_df
    convert_calls = 0

    def _flaky_convert(raw, default_symbol=None):
        nonlocal convert_calls
        convert_calls += 1
        if convert_calls == 1:
            raise TypeError("float expected at most 1 argument, got 2")
        return real_convert(raw, default_symbol=default_symbol)

    monkeypatch.setattr(kline_sync, "_compact_klines_to_df", _flaky_convert)

    failed: list[str] = []
    out = kline_sync.sync_daily_batch(
        ["517360.SH", "517380.SH"],
        batch_size=100,
        failed_out=failed,
    )

    assert klines.calls == 2
    assert failed == []
    assert out["symbol"].to_list() == ["517360.SH", "517380.SH"]


def test_daily_batch_degrades_after_repeated_conversion_failure(monkeypatch):
    klines = _install_client(monkeypatch)
    real_convert = kline_sync._compact_klines_to_df

    def _large_batch_breaks(raw, default_symbol=None):
        if isinstance(raw, dict) and "timestamp" not in raw and len(raw) > 20:
            raise TypeError("issubclass() arg 1 must be a class")
        return real_convert(raw, default_symbol=default_symbol)

    monkeypatch.setattr(kline_sync, "_compact_klines_to_df", _large_batch_breaks)

    symbols = [f"{560000 + i:06d}.SH" for i in range(25)]
    failed: list[str] = []
    out = kline_sync.sync_daily_batch(
        symbols,
        batch_size=25,
        failed_out=failed,
    )

    # 3 failed attempts on the 25-symbol request, then 20 + 5 succeed.
    assert klines.calls == 5
    assert failed == []
    assert out["symbol"].n_unique() == 25


def test_daily_batch_persistent_fetch_failure_is_bounded(monkeypatch):
    klines = _install_client(monkeypatch, fail_fetch=True)
    symbols = ["517360.SH", "517380.SH"]
    failed: list[str] = []

    out = kline_sync.sync_daily_batch(
        symbols,
        batch_size=100,
        failed_out=failed,
    )

    assert klines.calls == 3
    assert out.is_empty()
    assert failed == symbols


def test_etf_instrument_annotation_filters_sse_auxiliary_codes_only():
    df = pl.DataFrame({
        "symbol": [
            "562630.SH",
            "562633.SH",
            "562634.SH",
            "159047.SZ",
            "158037.SZ",
        ],
        "name": [
            "通信ETF",
            "通信E",
            "认购款",
            "新发ETF",
            "机器人BS",
        ],
        "asset_type": ["etf"] * 5,
    })

    out = index_sync._annotate_etf_instruments(df)
    roles = dict(zip(out["symbol"].to_list(), out["instrument_role"].to_list()))

    assert roles["562630.SH"] == "market"
    assert roles["562633.SH"] == "subscription_aux"
    assert roles["562634.SH"] == "subscription_aux"
    # Shenzhen 158/159 ranges contain normal tradable ETFs. Without lifecycle
    # fields, do not exclude them merely because they currently have zero K-lines.
    assert roles["159047.SZ"] == "market"
    assert roles["158037.SZ"] == "market"

    assert index_sync._eligible_etf_symbols(out) == [
        "158037.SZ",
        "159047.SZ",
        "562630.SH",
    ]
