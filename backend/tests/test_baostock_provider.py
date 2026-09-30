"""BaoStock provider contract tests; no SDK package or network is required."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import date, datetime

import polars as pl
import pytest

from app.plugins.baostock import provider as bp

_FIELDS = ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]


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


def test_provider_declares_only_adjustment_factors():
    provider = bp.BaoStockProvider()
    assert provider.name == "baostock"
    assert set(provider.config.datasets) == {"adj_factor"}
    assert set(bp._DATASETS) == {"adj_factor"}


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
