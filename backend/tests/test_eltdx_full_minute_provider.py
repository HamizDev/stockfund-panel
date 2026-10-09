"""Bounded full-market collection without touching real data or network."""
from datetime import datetime
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from app.plugins.eltdx_gateway.client import EltdxGatewayError
from app.plugins.eltdx_gateway.provider import EltdxGatewayProvider

NOW = datetime(2026, 10, 9, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))


def bar(day="2026-10-09", **values):
    return {"time": f"{day}T09:31:00+08:00", "open": 10, "high": 11,
            "low": 9, "close": 10.5, "volume_lots": 12, **values}


def provider(monkeypatch, reader):
    monkeypatch.setattr("app.plugins.eltdx_gateway.provider.cn_now", lambda: NOW)
    p = EltdxGatewayProvider()
    p._client = reader
    return p


def test_full_collection_chunks_deduplicates_and_keeps_only_current_day(monkeypatch):
    calls = []

    class Reader:
        def fetch_intraday_chunk(self, symbols, count=300):
            calls.append(list(symbols))
            return {s: [bar("2026-10-08"), bar()] for s in symbols}

    p = provider(monkeypatch, Reader())
    symbols = [f"{600000+i:06}.SH" for i in range(65)]
    df = p.get_intraday_batch([*symbols, symbols[0]])
    assert list(map(len, calls)) == [32, 32, 1]
    assert df.height == 65
    assert df["symbol"].n_unique() == 65
    assert df["datetime"].dtype == pl.Datetime("us")
    assert df["datetime"].min().date() == NOW.date()
    assert df["volume"].to_list() == [12] * 65
    assert df["amount"].null_count() == 65
    stats = p.get_intraday_status()
    assert stats["requested_symbols"] == stats["covered_symbols"] == 65
    assert stats["requests"] == 3  # batch attempts, excluding health probes
    assert stats["failed_symbols"] == stats["empty_symbols"] == 0
    assert stats["collection_complete"] is True
    assert stats["collecting"] is False


def test_partial_collection_retains_valid_bars_and_reports_missing(monkeypatch):
    class Reader:
        def fetch_intraday_chunk(self, symbols, count=300):
            return {symbols[0]: [bar()], symbols[1]: [bar("2026-10-08")],
                    symbols[2]: [bar(close=float("nan"))]}

    p = provider(monkeypatch, Reader())
    df = p.get_intraday_batch(["600000.SH", "000001.SZ", "920580.BJ"])
    assert df["symbol"].to_list() == ["600000.SH"]
    stats = p.get_intraday_status()
    assert stats["covered_symbols"] == 1
    assert stats["empty_symbols"] == stats["failed_symbols"] == 1
    assert stats["collection_complete"] is False


def test_failed_chunk_does_not_drop_other_chunks_or_leak_error(monkeypatch):
    class Reader:
        def fetch_intraday_chunk(self, symbols, count=300):
            if len(symbols) == 32:
                raise EltdxGatewayError("private upstream body")
            return {s: [bar()] for s in symbols}

    p = provider(monkeypatch, Reader())
    df = p.get_intraday_batch([f"{600000+i:06}.SH" for i in range(33)])
    assert df.height == 1
    assert p.get_intraday_status()["failed_symbols"] == 32
    assert "private" not in str(p.get_intraday_status())


def test_time_budget_leaves_unqueried_symbols_and_returns_available_data(monkeypatch):
    clock = iter([0, 0, 601])
    monkeypatch.setattr("app.plugins.eltdx_gateway.provider.time.monotonic", lambda: next(clock))

    class Reader:
        def fetch_intraday_chunk(self, symbols, count=300):
            return {s: [bar()] for s in symbols}

    p = provider(monkeypatch, Reader())
    df = p.get_intraday_batch([f"{600000+i:06}.SH" for i in range(33)])
    assert df.height == 32
    assert p.get_intraday_status()["unqueried_symbols"] == 1
    assert p.get_intraday_status()["collection_complete"] is False


def test_no_duplicate_collection_while_busy(monkeypatch):
    p = provider(monkeypatch, None)
    with p._intraday_lock, pytest.raises(EltdxGatewayError, match="采集"):
        p.get_intraday_batch(["600000.SH"])


@pytest.mark.parametrize("symbols,asset,count", [
    (["600000.SH"], "etf", 300), (["invalid"], "stock", 300),
    (["600000.SH"], "stock", True), (["600000.SH"], "stock", 801),
])
def test_invalid_full_collection_rejected_before_network(monkeypatch, symbols, asset, count):
    p = provider(monkeypatch, None)
    with pytest.raises((ValueError, EltdxGatewayError)):
        p.get_intraday_batch(symbols, count=count, asset_type=asset)


def test_full_minute_does_not_remove_on_demand_limit(monkeypatch):
    p = provider(monkeypatch, None)
    assert "full_minute" in p.config.datasets
    assert p.full_minute_min_interval_s == 300
    with pytest.raises(ValueError):
        p.get_minute(["600000.SH", "000001.SZ"], None, None)


def test_full_minute_trial_is_bounded_and_read_only(monkeypatch):
    calls = []

    class Reader:
        def fetch_intraday_chunk(self, symbols, count=300):
            calls.append(symbols)
            return {s: [bar()] for s in symbols}

    p = provider(monkeypatch, Reader())
    result = p.test_dataset("full_minute")
    assert calls == [["600519.SH"]]
    assert result["rows"] == 1
    assert result["observed_end"].startswith("2026-10-09")
    with pytest.raises(ValueError, match="32"):
        p.test_dataset("full_minute", [f"{600000+i:06}.SH" for i in range(33)])
    assert len(calls) == 1
