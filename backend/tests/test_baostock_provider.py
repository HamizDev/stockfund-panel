"""BaoStock provider contract tests; no SDK package or network is required."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import date, datetime
from typing import ClassVar

import polars as pl
import pytest
from app.plugins.baostock import provider as bp

_FIELDS = ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]
_DAILY_FIELDS = ["date", "code", "open", "high", "low", "close", "volume", "amount"]


def _raw(symbol: str, values: list[tuple[str, float]]) -> dict:
    code = bp._baostock_code(symbol)
    return {
        "symbol": symbol,
        "code": code,
        "fields": _FIELDS,
        "rows": [
            {
                "code": code,
                "dividOperateDate": day,
                "foreAdjustFactor": "1",
                "backAdjustFactor": str(factor),
                "adjustFactor": str(factor),
            }
            for day, factor in values
        ],
    }


def _raw_daily(symbol: str, values: list[tuple[str, str, str, str, str, str, str]]) -> dict:
    code = bp._baostock_daily_code(symbol, "etf" if symbol.startswith(("5", "15")) else "stock")
    return {
        "symbol": symbol,
        "code": code,
        "fields": _DAILY_FIELDS,
        "rows": [
            {
                "date": day,
                "code": code,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume_shares,
                "amount": amount,
            }
            for day, open_price, high, low, close, volume_shares, amount in values
        ],
    }


def test_provider_declares_factors_and_keeps_selected_daily_helper_internal():
    provider = bp.BaoStockProvider()
    assert provider.name == "baostock"
    assert set(provider.config.datasets) == {"adj_factor"}
    assert set(bp._DATASETS) == {"adj_factor"}
    assert callable(provider.get_daily)


@pytest.mark.parametrize(
    ("symbol", "asset_type", "expected"),
    [
        ("600000.SH", "stock", "sh.600000"),
        ("000001.SZ", "stock", "sz.000001"),
        ("515880.SH", "etf", "sh.515880"),
        ("159919.SZ", "etf", "sz.159919"),
    ],
)
def test_daily_code_mapping_for_supported_sh_sz_assets(symbol, asset_type, expected):
    assert bp._baostock_daily_code(symbol, asset_type) == expected


@pytest.mark.parametrize(
    ("symbol", "asset_type"),
    [
        ("430001.BJ", "stock"),
        ("510300.SH", "stock"),
        ("600000.SH", "etf"),
        ("160119.SZ", "etf"),
        ("515880.SH", "index"),
    ],
)
def test_daily_rejects_unsupported_exchange_or_asset_type(symbol, asset_type):
    with pytest.raises(ValueError):
        bp._baostock_daily_code(symbol, asset_type)


def test_daily_normalization_filters_dates_and_converts_share_volume_to_hands():
    raw = _raw_daily(
        "515880.SH",
        [
            ("2026-10-13", "0.51", "0.52", "0.50", "0.515", "200", "102"),
            ("2026-10-16", "0.52", "0.53", "0.51", "0.522", "673797031", "358285629"),
            ("2026-10-19", "0.52", "0.53", "0.51", "0.525", "100", "52"),
        ],
    )

    rows = bp._normalize_daily_rows(
        "515880.SH", "sh.515880", raw["fields"], raw["rows"],
        date(2026, 10, 14), date(2026, 10, 16),
    )

    assert len(rows) == 1
    assert rows[0] == {
        "symbol": "515880.SH", "date": date(2026, 10, 16),
        "open": 0.52, "high": 0.53, "low": 0.51, "close": 0.522,
        "volume": 6_737_970.31, "amount": 358_285_629.0,
    }


def test_daily_provider_uses_raw_daily_rows_and_reports_progress(monkeypatch):
    provider = bp.BaoStockProvider()
    raw = _raw_daily(
        "600000.SH",
        [("2026-10-16", "9.7", "9.8", "9.6", "9.73", "10000", "97300")],
    )
    captured = {}

    def query(symbols, start_date, end_date):
        captured.update(symbols=symbols, start=start_date, end=end_date)
        return [raw]

    progress = []
    monkeypatch.setattr(bp, "_query_daily_batch", query)
    frame = provider.get_daily(
        ["600000.SH"], datetime(2026, 10, 1), datetime(2026, 10, 16),
        on_chunk_done=lambda current, total: progress.append((current, total)),
    )

    assert captured == {
        "symbols": [("600000.SH", "sh.600000")],
        "start": date(2026, 10, 1), "end": date(2026, 10, 16),
    }
    assert frame.columns == list(bp._DAILY_SCHEMA)
    assert frame.schema == bp._DAILY_SCHEMA
    assert frame["close"].to_list() == [9.73]
    assert frame["volume"].to_list() == [100.0]
    assert progress == [(1, 1)]


def test_daily_empty_response_has_fixed_schema(monkeypatch):
    raw = _raw_daily("600000.SH", [])
    monkeypatch.setattr(bp, "_query_daily_batch", lambda *_args: [raw])
    frame = bp.BaoStockProvider().get_daily(
        ["600000.SH"], datetime(2026, 10, 1), datetime(2026, 10, 16)
    )
    assert frame.is_empty()
    assert frame.schema == bp._DAILY_SCHEMA


def test_daily_over_limit_is_rejected_before_starting_worker(monkeypatch):
    symbols = [f"{600000 + i:06}.SH" for i in range(101)]
    monkeypatch.setattr(
        bp, "_query_daily_batch",
        lambda *_args: pytest.fail("oversized request must be rejected before the worker"),
    )
    with pytest.raises(ValueError, match="不支持全市场日线同步"):
        bp.BaoStockProvider().get_daily(symbols, None, None)


def test_daily_worker_batches_are_isolated_bounded_and_order_checked(monkeypatch):
    symbols = [(f"{600000 + i:06}.SH", f"sh.{600000 + i:06}") for i in range(45)]
    calls = []

    def fake_worker(request, timeout_s, **kwargs):
        decoded = json.loads(request)
        calls.append((decoded, timeout_s, kwargs))
        results = [
            {
                "symbol": item["symbol"], "code": item["code"],
                "fields": _DAILY_FIELDS, "rows": [],
            }
            for item in decoded["symbols"]
        ]
        return subprocess.CompletedProcess(
            ["mock"], 0, json.dumps({"results": results}), ""
        )

    monkeypatch.setattr(bp, "_run_worker_subprocess", fake_worker)
    results = bp._query_daily_batch(symbols, date(2026, 10, 1), date(2026, 10, 16))

    assert [len(request["symbols"]) for request, _, _ in calls] == [40, 5]
    assert [timeout for _, timeout, _ in calls] == [20, 20]
    assert [kwargs for _, _, kwargs in calls] == [
        {"lock_timeout_s": bp._DAILY_LOCK_WAIT_S},
        {"lock_timeout_s": bp._DAILY_LOCK_WAIT_S},
    ]
    assert all(request["operation"] == "daily" for request, _, _ in calls)
    assert calls[0][0]["start_date"] == "2026-10-01"
    assert calls[0][0]["end_date"] == "2026-10-16"
    assert [row["symbol"] for row in results] == [symbol for symbol, _ in symbols]


def test_daily_worker_fails_fast_when_factor_query_holds_global_lock(monkeypatch):
    request = json.dumps(
        {
            "operation": "daily",
            "symbols": [{"symbol": "600000.SH", "code": "sh.600000"}],
            "start_date": "2026-10-01",
            "end_date": "2026-10-16",
        }
    )
    assert bp._BAOSTOCK_LOCK.acquire(blocking=False)
    try:
        monkeypatch.setattr(
            bp.subprocess,
            "run",
            lambda *_args, **_kwargs: pytest.fail("daily worker must not queue"),
        )
        with pytest.raises(bp.BaoStockError, match="查询繁忙"):
            bp._run_worker_subprocess(request, 20, lock_timeout_s=0.01)
    finally:
        bp._BAOSTOCK_LOCK.release()
    assert not bp._BAOSTOCK_LOCK.locked()


def test_daily_query_pins_frequency_and_unadjusted_flag():
    class Response:
        error_code = "0"
        error_msg = ""
        fields = _DAILY_FIELDS

        def __init__(self):
            self.rows = [["2026-10-16", "sh.600000", "9.7", "9.8", "9.6", "9.73", "10000", "97300"]]

        def next(self):
            return bool(self.rows)

        def get_row_data(self):
            return self.rows.pop(0)

    class SDK:
        called = None

        @classmethod
        def query_history_k_data_plus(cls, *args, **kwargs):
            cls.called = (args, kwargs)
            return Response()

    fields, rows = bp._query_daily_rows(
        SDK, "sh.600000", "2026-10-01", "2026-10-16"
    )

    assert fields == _DAILY_FIELDS
    assert rows[0]["close"] == "9.73"
    assert SDK.called == (
        ("sh.600000", ",".join(_DAILY_FIELDS)),
        {
            "start_date": "2026-10-01", "end_date": "2026-10-16",
            "frequency": "d", "adjustflag": "3",
        },
    )


def test_daily_query_rejects_error_and_missing_fields():
    class ErrorResponse:
        error_code = "1001"
        error_msg = "query rejected"
        fields = _DAILY_FIELDS

    class ErrorSDK:
        @staticmethod
        def query_history_k_data_plus(*_args, **_kwargs):
            return ErrorResponse()

    with pytest.raises(bp.BaoStockError, match="query rejected"):
        bp._query_daily_rows(ErrorSDK, "sh.600000", "2026-10-01", "2026-10-16")

    class MissingFieldsResponse:
        error_code = "0"
        error_msg = ""
        fields: ClassVar[list[str]] = ["date", "code", "close"]

    class MissingFieldsSDK:
        @staticmethod
        def query_history_k_data_plus(*_args, **_kwargs):
            return MissingFieldsResponse()

    with pytest.raises(bp.BaoStockError, match="缺少字段"):
        bp._query_daily_rows(MissingFieldsSDK, "sh.600000", "2026-10-01", "2026-10-16")


def test_daily_normalization_rejects_wrong_symbol_and_invalid_values():
    raw = _raw_daily(
        "600000.SH",
        [("2026-10-16", "9.7", "9.8", "9.6", "9.73", "10000", "97300")],
    )
    raw["rows"][0]["code"] = "sz.000001"
    with pytest.raises(bp.BaoStockError, match="代码不匹配"):
        bp._normalize_daily_rows(
            "600000.SH", "sh.600000", raw["fields"], raw["rows"],
            date(2026, 10, 1), date(2026, 10, 16),
        )
    raw = _raw_daily(
        "600000.SH",
        [("2026-10-16", "nan", "9.8", "9.6", "9.73", "10000", "97300")],
    )
    with pytest.raises(bp.BaoStockError, match="有限数"):
        bp._normalize_daily_rows(
            "600000.SH", "sh.600000", raw["fields"], raw["rows"],
            date(2026, 10, 1), date(2026, 10, 16),
        )


def test_availability_reports_optional_missing_dependency(monkeypatch):
    monkeypatch.setattr(bp.importlib.util, "find_spec", lambda name: None)
    available, reason = bp.availability()
    assert available is False
    assert "baostock" in reason and "安装" in reason


def test_adj_factor_converts_cumulative_values_to_adjacent_event_ratios(monkeypatch):
    provider = bp.BaoStockProvider()
    result = _raw(
        "600519.SH",
        [("2018-06-01", 2.0), ("2020-06-01", 3.0), ("2019-06-01", 2.4)],
    )
    monkeypatch.setattr(bp, "_query_adjust_factor_batch", lambda symbols, end: [result])

    frame = provider.get_adj_factors(["600519.SH"], None, datetime(2020, 12, 31))

    assert frame.columns == ["symbol", "trade_date", "ex_factor"]
    assert frame.schema == {"symbol": pl.String, "trade_date": pl.Date, "ex_factor": pl.Float64}
    assert frame["trade_date"].to_list() == [date(2018, 6, 1), date(2019, 6, 1), date(2020, 6, 1)]
    assert frame["ex_factor"].to_list() == pytest.approx([2.0, 1.2, 1.25])


def test_back_adjust_level_wins_over_inconsistent_adjust_factor():
    raw = _raw(
        "600519.SH",
        [("2022-12-27", 6.785630), ("2023-06-30", 6.889798)],
    )
    raw["rows"][1]["adjustFactor"] = "0.988591"

    normalized = bp._normalize_factor_rows(
        "600519.SH", "sh.600519", raw["fields"], raw["rows"]
    )

    assert normalized[-1]["trade_date"] == date(2023, 6, 30)
    assert normalized[-1]["ex_factor"] == pytest.approx(6.889798 / 6.785630)


def test_listing_level_of_one_is_not_emitted_as_an_adjustment_event():
    raw = _raw("600519.SH", [("2001-08-27", 1.0)])

    normalized = bp._normalize_factor_rows(
        "600519.SH", "sh.600519", raw["fields"], raw["rows"]
    )

    assert normalized == []


def test_window_filter_keeps_predecessor_for_first_event_ratio(monkeypatch):
    provider = bp.BaoStockProvider()
    captured = {}

    def query(symbols, end):
        captured["symbols"] = symbols
        captured["end"] = end
        return [_raw("600519.SH", [("2018-06-01", 2.0), ("2020-06-01", 3.0)])]

    monkeypatch.setattr(bp, "_query_adjust_factor_batch", query)
    frame = provider.get_adj_factors(
        ["600519.SH"], datetime(2019, 1, 1), datetime(2020, 12, 31)
    )

    assert captured == {"symbols": ["600519.SH"], "end": date(2020, 12, 31)}
    assert frame["trade_date"].to_list() == [date(2020, 6, 1)]
    assert frame["ex_factor"].to_list() == pytest.approx([1.5])


def test_query_starts_in_1990_even_when_window_starts_later(monkeypatch):
    class Response:
        error_code = "0"
        error_msg = ""
        fields = _FIELDS

        def __init__(self):
            self.rows = [["sh.600519", "2018-06-01", "1", "2", "2"]]

        def next(self):
            return bool(self.rows)

        def get_row_data(self):
            return self.rows.pop(0)

    class SDK:
        called = None

        @classmethod
        def query_adjust_factor(cls, **kwargs):
            cls.called = kwargs
            return Response()

    fields, rows = bp._query_adjust_factor_rows(
        SDK, "sh.600519", "1990-01-01", "2020-12-31"
    )
    assert fields == _FIELDS
    assert rows[0]["code"] == "sh.600519"
    assert SDK.called == {
        "code": "sh.600519",
        "start_date": "1990-01-01",
        "end_date": "2020-12-31",
    }


def test_wrong_response_symbol_is_an_error():
    raw = _raw("600519.SH", [("2020-06-01", 2.0)])
    raw["rows"][0]["code"] = "sz.000001"
    with pytest.raises(bp.BaoStockError, match="代码不匹配"):
        bp._normalize_factor_rows("600519.SH", "sh.600519", raw["fields"], raw["rows"])


def test_error_response_is_not_treated_as_no_events():
    class Response:
        error_code = "1001"
        error_msg = "query rejected"
        fields = _FIELDS

    class SDK:
        @staticmethod
        def query_adjust_factor(**kwargs):
            return Response()

    with pytest.raises(bp.BaoStockError, match="query rejected"):
        bp._query_adjust_factor_rows(SDK, "sh.600519", "1990-01-01", "2020-12-31")


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "not-a-number"])
def test_invalid_factors_fail_closed(value):
    raw = _raw("600519.SH", [("2020-06-01", 2.0)])
    raw["rows"][0]["backAdjustFactor"] = value
    with pytest.raises(bp.BaoStockError, match=r"有限正数|不是数值"):
        bp._normalize_factor_rows("600519.SH", "sh.600519", raw["fields"], raw["rows"])


def test_conflicting_duplicate_date_is_an_error_but_identical_duplicate_is_unique():
    rows = _raw("600519.SH", [("2020-06-01", 2.0), ("2020-06-01", 2.0)])
    normalized = bp._normalize_factor_rows(
        "600519.SH", "sh.600519", rows["fields"], rows["rows"]
    )
    assert len(normalized) == 1
    rows["rows"][1]["backAdjustFactor"] = "2.1"
    with pytest.raises(bp.BaoStockError, match="冲突的重复"):
        bp._normalize_factor_rows("600519.SH", "sh.600519", rows["fields"], rows["rows"])


def test_empty_success_has_typed_empty_frame(monkeypatch):
    provider = bp.BaoStockProvider()
    monkeypatch.setattr(
        bp,
        "_query_adjust_factor_batch",
        lambda symbols, end: [_raw("600519.SH", [])],
    )
    frame = provider.get_adj_factors(["600519.SH"], None, datetime(2020, 12, 31))
    assert frame.is_empty()
    assert frame.schema == {"symbol": pl.String, "trade_date": pl.Date, "ex_factor": pl.Float64}


@pytest.mark.parametrize(
    ("symbol", "asset_type"),
    [("510300.SH", "etf"), ("159919.SZ", "etf"), ("430001.BJ", "stock")],
)
def test_unsupported_etf_and_bj_symbols_are_rejected(symbol, asset_type):
    with pytest.raises(ValueError):
        bp.BaoStockProvider().get_adj_factors([symbol], None, None, asset_type=asset_type)


def test_failed_query_logs_out_and_parent_lock_is_released(monkeypatch):
    class Login:
        error_code = "0"
        error_msg = ""

    class Response:
        error_code = "1002"
        error_msg = "socket failed"
        fields = _FIELDS

    class SDK:
        logout_calls = 0

        @staticmethod
        def login():
            return Login()

        @staticmethod
        def query_adjust_factor(**kwargs):
            return Response()

        @classmethod
        def logout(cls):
            cls.logout_calls += 1

    with pytest.raises(bp.BaoStockError, match="socket failed"):
        bp._fetch_with_sdk_session(SDK, ["600519.SH"], "2020-12-31")
    assert SDK.logout_calls == 1

    def fail_once(*args, **kwargs):
        if fail_once.calls == 0:
            fail_once.calls += 1
            raise subprocess.TimeoutExpired(args[0], timeout=30)
        results = [_raw("600519.SH", [])]
        return subprocess.CompletedProcess(
            args[0], 0, json.dumps({"results": results}), ""
        )

    fail_once.calls = 0
    monkeypatch.setattr(bp.subprocess, "run", fail_once)
    with pytest.raises(bp.BaoStockError, match="超时"):
        bp._query_adjust_factor_batch(["600519.SH"], date(2020, 12, 31))
    assert not bp._BAOSTOCK_LOCK.locked()
    assert bp._query_adjust_factor_batch(["600519.SH"], date(2020, 12, 31)) == [
        _raw("600519.SH", [])
    ]
    assert not bp._BAOSTOCK_LOCK.locked()


def test_query_batch_splits_at_40_and_concatenates_results(monkeypatch):
    symbols = [f"{code:06d}.SZ" for code in range(1, 86)]
    calls = []

    def fake_worker(request, timeout_s):
        decoded = json.loads(request)
        calls.append((decoded, timeout_s))
        results = [_raw(symbol, []) for symbol in decoded["symbols"]]
        return subprocess.CompletedProcess(
            ["mock"], 0, json.dumps({"results": results}), ""
        )

    monkeypatch.setattr(bp, "_run_worker_subprocess", fake_worker)
    results = bp._query_adjust_factor_batch(symbols, date(2020, 12, 31))

    assert [len(request["symbols"]) for request, _ in calls] == [40, 40, 5]
    assert [timeout for _, timeout in calls] == [300, 300, 50]
    assert [result["symbol"] for result in results] == symbols


def test_query_batch_rejects_more_than_100_symbols_before_starting_process(monkeypatch):
    symbols = [f"{code:06d}.SZ" for code in range(1, 102)]
    monkeypatch.setattr(
        bp,
        "_run_worker_subprocess",
        lambda *_args, **_kwargs: pytest.fail("process must not start"),
    )
    with pytest.raises(ValueError, match="最多查询 100"):
        bp._query_adjust_factor_batch(symbols, date(2020, 12, 31))


def test_real_subprocess_timeout_terminates_child_and_releases_lock():
    started = time.monotonic()
    with pytest.raises(bp.BaoStockError, match="子进程超时"):
        bp._run_worker_subprocess(
            "{}",
            timeout_s=0.5,
            launch_code="import time; time.sleep(30)",
        )
    assert time.monotonic() - started < 10
    assert not bp._BAOSTOCK_LOCK.locked()
