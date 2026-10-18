from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.custom.global_markets.analytics import daily_technicals


def daily_bars(count: int) -> list[dict]:
    start = date(2026, 1, 1)
    return [
        {
            "time": (start + timedelta(days=index)).isoformat(),
            "open": 100 + index,
            "high": 100 + index + 0.5,
            "low": 100 + index - 0.5,
            "close": 100 + index,
            "volume": 10 + index,
        }
        for index in range(count)
    ]


def test_daily_technicals_returns_fixed_values_from_normalized_bars():
    bars = daily_bars(62)

    result = daily_technicals(bars)

    assert result == {
        "date": bars[-1]["time"],
        "bars_count": 62,
        "ma5": 159,
        "above_ma5": True,
        "ma20": 151.5,
        "above_ma20": True,
        "ma60": 131.5,
        "above_ma60": True,
        "return_5d": pytest.approx(161 / 156 - 1),
        "return_20d": pytest.approx(161 / 141 - 1),
        "volume_ratio_5": pytest.approx(71 / 68),
        "new_high_60": True,
        "new_low_60": False,
        "range_position_60": pytest.approx(59.5 / 60),
    }


def test_insufficient_history_keeps_each_metric_unavailable():
    result = daily_technicals(daily_bars(5))

    assert result["bars_count"] == 5
    assert result["ma5"] == 102
    assert result["above_ma5"] is True
    for field in (
        "ma20", "above_ma20", "ma60", "above_ma60", "return_5d", "return_20d",
        "volume_ratio_5", "new_high_60", "new_low_60", "range_position_60",
    ):
        assert result[field] is None


def test_volume_ratio_requires_six_valid_nonnegative_volumes_and_nonzero_baseline():
    bars = daily_bars(6)
    bars[-1]["volume"] = 0
    assert daily_technicals(bars)["volume_ratio_5"] == 0

    bars[-1]["volume"] = None
    assert daily_technicals(bars)["volume_ratio_5"] is None

    bars[-1]["volume"] = 10
    bars[-2]["volume"] = None
    assert daily_technicals(bars)["volume_ratio_5"] is None

    for bar in bars[:-1]:
        bar["volume"] = 0
    assert daily_technicals(bars)["volume_ratio_5"] is None


def test_breakouts_compare_close_only_with_prior_sixty_bar_extremes():
    bars = daily_bars(61)
    for bar in bars:
        bar.update(open=100, high=101, low=99, close=100)
    bars[-1].update(open=101, high=1_000, low=90, close=102)

    result = daily_technicals(bars)

    assert result["new_high_60"] is True
    assert result["new_low_60"] is False

    bars[-1].update(open=99, high=101, low=1, close=98)
    result = daily_technicals(bars)
    assert result["new_high_60"] is False
    assert result["new_low_60"] is True


def test_zero_width_range_and_missing_price_remain_unavailable():
    flat = daily_bars(60)
    for bar in flat:
        bar.update(open=10, high=10, low=10, close=10)
    assert daily_technicals(flat)["range_position_60"] is None

    broken = daily_bars(62)
    broken[-1]["close"] = None
    result = daily_technicals(broken)
    assert result["ma5"] is None
    assert result["above_ma5"] is None
    assert result["return_5d"] is None
    assert result["new_high_60"] is None
    assert result["range_position_60"] is None
