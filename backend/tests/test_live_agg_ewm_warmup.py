"""盘中 MACD / RSI 的递推状态与盘后全量同一暖机起点。

_build_live_agg 的 ema5~ema60、macd_dea、kdj、atr 状态取自历史缓存 (约 300 个自然日
暖机, 与盘后全量同源), 但 _ema12/_ema26 与 RSI 平均涨跌幅若只在 90 个自然日窗口切片上
重新递推, 暖机只有约 60 根: RSI24 初值残留权重 (23/24)^60 ≈ 8%, EMA26 约 1%。
盘中 macd_dif (EMA12 - EMA26) 与同一 live_agg 里的 macd_dea 状态口径不一致,
rsi_14/rsi_24 与盘后 (或盘中重启后) 数值不同, 金叉/死叉判断跟着漂移。
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators.pipeline import compute_enriched_today, compute_indicators
from app.tickflow.repository import DataStore, KlineRepository

TODAY = date(2026, 8, 3)
COLUMNS = ("macd_dif", "macd_dea", "macd_hist", "rsi_6", "rsi_14", "rsi_24")


def _bars() -> pl.DataFrame:
    days: list[date] = []
    d = TODAY - timedelta(days=299)
    while d < TODAY:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    rows = []
    for n, symbol in enumerate(("600001.SH", "000002.SZ")):
        for i, day in enumerate([*days, TODAY]):
            # 前段单边上涨/下跌、后段震荡: 暖机起点与近期状态差异大, 截断暖机的误差可见
            trend = (0.03 if n == 0 else -0.02) * min(i, 120)
            close = round(10.0 + n * 5 + trend + 0.5 * math.sin(i * 0.45 + n), 2)
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


@pytest.mark.parametrize("column", COLUMNS)
def test_intraday_macd_rsi_warmup_matches_full(tmp_path, column):
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
