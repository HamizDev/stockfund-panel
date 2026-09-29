"""AI 助手 get_financials 必须返回「最近 N 个报告期」, 不依赖 parquet 的物理行序。

财务表的报告期列是 period_end (与 financial_sync 的合并键、financial_analyzer 的
「按 period_end 降序取最新 N 期」一致)。_get_financials 的排序候选却是
report_date / end_date / ann_date / date —— 本地财务表一个都没有, 于是不排序,
直接 tail(N) 取物理行序的最后 N 行。首次全量同步 (_sync_table) 按数据源返回顺序
原样落盘, 数据源按新→旧返回时, 助手答「最近 4 期」拿到的是最早的 4 期。
"""
from __future__ import annotations

from datetime import date

import polars as pl

from app.custom.assistant import tools as assistant_tools

_PERIODS = [
    date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31),
    date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31),
]


def _write_metrics(data_dir, periods: list[date]) -> None:
    out = data_dir / "financials" / "metrics"
    out.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["600519.SH"] * len(periods) + ["000001.SZ"],
        "period_end": [*periods, date(2025, 12, 31)],
        "announce_date": [date(p.year + (p.month == 12), (p.month % 12) + 1, 28) for p in periods] + [date(2026, 3, 20)],
        "roe": [float(i) for i in range(len(periods))] + [9.9],
    }).write_parquet(out / "part.parquet")


async def _periods_returned(data_dir, periods: int) -> list[str]:
    ctx = assistant_tools.ToolContext.build(data_dir=data_dir)
    payload = await assistant_tools.execute_assistant_tool(
        "get_financials", {"symbol": "600519.SH", "table": "metrics", "periods": periods}, ctx,
    )
    assert payload["ok"] is True
    return [row["period_end"] for row in payload["result"]["rows"]]


async def test_latest_periods_when_file_is_newest_first(tmp_path) -> None:
    """数据源按新→旧返回、首次同步原样落盘。"""
    _write_metrics(tmp_path, list(reversed(_PERIODS)))

    assert await _periods_returned(tmp_path, 4) == [
        "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
    ]


async def test_latest_periods_when_file_is_oldest_first(tmp_path) -> None:
    """累积合并路径 (_merge_report_history) 已按 period_end 升序重写: 结果不变。"""
    _write_metrics(tmp_path, _PERIODS)

    assert await _periods_returned(tmp_path, 2) == ["2025-09-30", "2025-12-31"]
