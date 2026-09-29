"""盘中递推状态 (live_agg) 的窗口必须真有足够的交易日 K 线。

_build_live_agg 按「自然日 90 天」切历史再 tail(59)/tail(60) 取部分和、N 日前收盘:

- 春节/国庆长假前后, 90 个自然日只有 57~59 个交易日, 全市场的盘中 MA60、
  60 日动量用残缺窗口算出, 与盘后全量 (rolling_mean(60) / shift(60)) 不一致;
- 次新股历史不足窗口长度时, tail(N) 取到的是全部已有 K 线, 盘中 MA60 等于
  (已有收盘价之和 + 今收) / 60 这类残缺值, 而盘后全量在窗口不满时为空。
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators.pipeline import compute_enriched_today, compute_indicators
from app.tickflow.repository import DataStore, KlineRepository, _live_agg_window_start

TODAY = date(2025, 3, 3)
# 2025 春节休市 1/28~2/4 + 元旦: 2024-12-03 ~ 2025-02-28 只有 58 个交易日
CLOSED = {date(2025, 1, 1)} | {date(2025, 1, 28) + timedelta(days=i) for i in range(8)}

VALUE_COLUMNS = (
    "ma5", "ma10", "ma20", "ma30", "ma60",
    "momentum_5d", "momentum_10d", "momentum_20d", "momentum_30d", "momentum_60d",
    "vol_ma5", "vol_ma10", "vol_ratio_5d",
)
# 以下列在窗口不满时全量为空; 数值口径另有独立问题, 这里只核对空与非空
NULLNESS_COLUMNS = ("high_60d", "low_60d", "boll_upper", "boll_lower", "annual_vol_20d")


def _history_days() -> list[date]:
    days: list[date] = []
    d = TODAY - timedelta(days=299)
    while d < TODAY:
        if d.weekday() < 5 and d not in CLOSED:
            days.append(d)
        d += timedelta(days=1)
    return days


def _bars() -> pl.DataFrame:
    days = _history_days()
    rows = []
    listings = {
        "600001.SH": days,       # 老股: 约 200 个历史交易日
        "000002.SZ": days,
        "001003.SZ": days[-12:],  # 次新股: 12 个历史交易日
        "001004.SZ": days[-40:],  # 次新股: 40 个历史交易日
    }
    for n, (symbol, history) in enumerate(listings.items()):
        for i, day in enumerate([*history, TODAY]):
            close = round(10.0 + n + 0.6 * math.sin(i * 0.37 + n) + 0.004 * i, 2)
            volume = 1000.0 + 300.0 * math.cos(i * 0.51 + n)
            rows.append((symbol, day, close, close + 0.08, close - 0.08, close, volume))
    return pl.DataFrame(
        rows,
        schema=["symbol", "date", "open", "high", "low", "close", "volume"],
        orient="row",
    ).with_columns(
        (pl.col("close") * pl.col("volume")).alias("amount"),
        pl.col("close").alias("raw_close"),
        pl.col("high").alias("raw_high"),
        pl.col("low").alias("raw_low"),
    ).sort(["symbol", "date"])


def _incremental(repo: KlineRepository, bars: pl.DataFrame, latest: date) -> pl.DataFrame:
    history_indicators = compute_indicators(bars.filter(pl.col("date") < TODAY))
    repo._build_live_agg(latest)
    return compute_enriched_today(
        repo.get_live_agg(),
        history_indicators.filter(pl.col("date") == latest),
        bars.filter(pl.col("date") == TODAY).drop("raw_close", "raw_high", "raw_low"),
        None,
    ).sort("symbol")


def _assert_matches_full(incremental: pl.DataFrame, bars: pl.DataFrame) -> None:
    full = compute_indicators(bars).filter(pl.col("date") == TODAY).sort("symbol")
    assert incremental["symbol"].to_list() == full["symbol"].to_list()
    mismatches: list[str] = []
    for column in (*VALUE_COLUMNS, *NULLNESS_COLUMNS):
        pairs = zip(
            full["symbol"].to_list(), incremental[column].to_list(), full[column].to_list(),
            strict=True,
        )
        for symbol, got, want in pairs:
            if (got is None) != (want is None):
                mismatches.append(f"{symbol} {column}: 盘中={got} 全量={want}")
            elif column in VALUE_COLUMNS and got is not None and got != pytest.approx(want, rel=1e-9):
                mismatches.append(f"{symbol} {column}: 盘中={got:.6f} 全量={want:.6f}")
    assert mismatches == []


def test_fixture_holiday_window_has_fewer_than_60_trading_days():
    days = _history_days()
    latest = days[-1]
    in_90_calendar_days = [d for d in days if d >= latest - timedelta(days=90)]
    assert len(in_90_calendar_days) < 60  # 构造前提: 长假让 90 个自然日不足 60 个交易日


def test_window_start_counts_trading_days():
    days = _history_days()
    latest = days[-1]
    calendar_start = latest - timedelta(days=90)
    dates = pl.Series("date", days)

    # 长假: 自然日起点只含 58 个交易日 → 前移到第 60 个交易日
    assert _live_agg_window_start(dates, latest, calendar_start) == days[-60]
    assert _live_agg_window_start(dates, latest, calendar_start) < calendar_start
    # 平常: 自然日起点已覆盖 60 个交易日 → 保持自然日起点 (窗口不缩小)
    assert _live_agg_window_start(dates, latest, days[-65]) == days[-65]
    # 本地不足 60 个交易日 → 退回自然日起点
    assert _live_agg_window_start(dates.tail(30), latest, calendar_start) == calendar_start


def test_live_agg_from_history_cache_matches_full(tmp_path):
    bars = _bars()
    history = bars.filter(pl.col("date") < TODAY)
    repo = KlineRepository(DataStore(tmp_path))
    repo._enriched_history_cache = compute_indicators(history)

    incremental = _incremental(repo, bars, history["date"].max())

    _assert_matches_full(incremental, bars)


def test_live_agg_from_parquet_fallback_matches_full(tmp_path):
    bars = _bars()
    history = bars.filter(pl.col("date") < TODAY)
    target = tmp_path / "kline_daily_enriched" / "all"
    target.mkdir(parents=True)
    history.write_parquet(target / "part.parquet")
    repo = KlineRepository(DataStore(tmp_path))
    assert repo._enriched_history_cache is None  # 走 _build_live_agg_from_parquet 降级路径

    incremental = _incremental(repo, bars, history["date"].max())

    _assert_matches_full(incremental, bars)
