"""助手回测工具的日期边界与执行顺序。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import pytest

from app.services import tool_catalog

TODAY = date(2026, 10, 1)


class _FakeLimiter:
    @contextmanager
    def slot(self, _priority: str):
        yield


def _patch_successful_worker(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
) -> None:
    from app.backtest import worker
    from app.services import heavy_job_limiter

    def fake_make_worker_task(kind, data_dir, cfg):
        captured.update(kind=kind, start=cfg.start, end=cfg.end)
        return object()

    monkeypatch.setattr(worker, "make_worker_task", fake_make_worker_task)
    monkeypatch.setattr(worker, "run_worker_task", lambda _task: {"stats": {}})
    monkeypatch.setattr(heavy_job_limiter, "shared_heavy_job_limiter", _FakeLimiter())


def _patch_execution_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.backtest import worker
    from app.services import heavy_job_limiter

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("无效日期必须在限流和 worker 执行前被拒绝")

    monkeypatch.setattr(worker, "make_worker_task", fail_if_called)
    monkeypatch.setattr(worker, "run_worker_task", fail_if_called)
    monkeypatch.setattr(
        heavy_job_limiter,
        "shared_heavy_job_limiter",
        type("ForbiddenLimiter", (), {"slot": staticmethod(fail_if_called)})(),
    )


@pytest.fixture(autouse=True)
def _beijing_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_catalog, "cn_today", lambda: TODAY)


def test_schema_documents_backtest_date_bounds() -> None:
    schema = next(
        item["function"] for item in tool_catalog.build_tool_schemas()
        if item["function"]["name"] == tool_catalog.RUN_BACKTEST
    )
    properties = schema["parameters"]["properties"]

    assert "最近 180 天" in properties["start"]["description"]
    assert "366 天" in properties["start"]["description"]
    assert "366 天" in properties["end"]["description"]
    assert "北京时间" in properties["end"]["description"]


def test_default_window_remains_180_days(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    captured: dict[str, Any] = {}
    _patch_successful_worker(monkeypatch, captured)

    result = tool_catalog.run_backtest(tmp_path, strategy_id="demo")

    assert captured["start"] == TODAY - timedelta(days=180)
    assert captured["end"] == TODAY
    assert result["start"] == (TODAY - timedelta(days=180)).isoformat()


def test_366_day_range_is_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    captured: dict[str, Any] = {}
    _patch_successful_worker(monkeypatch, captured)
    start = TODAY - timedelta(days=366)

    result = tool_catalog.run_backtest(
        tmp_path, strategy_id="demo", start=start.isoformat(), end=TODAY.isoformat(),
    )

    assert captured["start"] == start
    assert captured["end"] == TODAY
    assert result["end"] == TODAY.isoformat()


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("2026-10-02", "2026-10-01", "开始日期不能晚于结束日期"),
        ("2026-09-30", "2026-10-02", "结束日期不能晚于今天"),
        ("2025-09-29", "2026-10-01", "不能超过 366 天"),
    ],
)
def test_invalid_bounds_are_rejected_before_limiter_or_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    start: str,
    end: str,
    message: str,
) -> None:
    _patch_execution_forbidden(monkeypatch)

    with pytest.raises(ValueError, match=message):
        tool_catalog.run_backtest(
            tmp_path, strategy_id="demo", start=start, end=end,
        )
