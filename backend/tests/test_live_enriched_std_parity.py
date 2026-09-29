"""盘中增量 enriched 的 BOLL 带宽与 20 日年化波动率用样本标准差, 与盘后全量一致。

盘后全量 compute_indicators 用 polars rolling_std(20) (ddof=1), 回测矩阵的
boll_upper/boll_lower/annual_vol_20d 也显式传 ddof=1; 盘中增量
compute_enriched_today 用部分和递推时若按总体方差 (除以 20) 计算, BOLL 带宽
与年化波动率会系统性偏小 sqrt(19/20) 倍 (年化波动率约 -2.5%)。
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators.pipeline import compute_enriched_today, compute_indicators
from app.tickflow.repository import DataStore, KlineRepository

TODAY = date(2026, 8, 3)


def _bars() -> pl.DataFrame:
    days: list[date] = []
    d = TODAY - timedelta(days=1)
    while len(days) < 80:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days = [*sorted(days), TODAY]

    rows = []
    for symbol, scale in (("600001.SH", 0.8), ("000002.SZ", 0.3)):
        for i, day in enumerate(days):
            close = round(10.0 + scale * math.sin(i * 0.7) + 0.01 * i, 2)
            rows.append((symbol, day, close, close + 0.1, close - 0.1, close))
    return pl.DataFrame(
        rows,
        schema=["symbol", "date", "open", "high", "low", "close"],
        orient="row",
    ).with_columns(
        pl.lit(1000.0).alias("volume"),
        (pl.col("close") * 1000.0).alias("amount"),
        pl.col("close").alias("raw_close"),
        pl.col("high").alias("raw_high"),
        pl.col("low").alias("raw_low"),
    ).sort(["symbol", "date"])


@pytest.mark.parametrize("column", ["boll_upper", "boll_lower", "annual_vol_20d"])
def test_intraday_boll_and_annual_vol_use_sample_std_like_full(tmp_path, column):
    bars = _bars()
    history = bars.filter(pl.col("date") < TODAY)
    history_indicators = compute_indicators(history)
    latest = history["date"].max()

    repo = KlineRepository(DataStore(tmp_path))
    repo._enriched_history_cache = history_indicators
    repo._build_live_agg(latest)

    incremental = compute_enriched_today(
        repo.get_live_agg(),
        history_indicators.filter(pl.col("date") == latest),
        bars.filter(pl.col("date") == TODAY).drop("raw_close", "raw_high", "raw_low"),
        None,
    ).sort("symbol")
    full = compute_indicators(bars).filter(pl.col("date") == TODAY).sort("symbol")

    assert incremental["symbol"].to_list() == full["symbol"].to_list()
    assert incremental[column].to_list() == pytest.approx(full[column].to_list(), rel=1e-9, abs=1e-12)
