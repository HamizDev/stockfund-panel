"""Pure technical summaries for normalized daily global-market bars.

The source may include an unfinished current-session bar. Results describe the
provided history for research only and are not execution signals.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any


def _number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    if not math.isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        return None
    return result


def _field_values(rows: Sequence[Mapping[str, Any]], field: str, *, positive: bool = False,
                  nonnegative: bool = False) -> list[float] | None:
    values: list[float] = []
    for row in rows:
        value = _number(row.get(field), positive=positive, nonnegative=nonnegative)
        if value is None:
            return None
        values.append(value)
    return values


def _mean(values: Sequence[float]) -> float | None:
    try:
        result = math.fsum(values) / len(values)
    except (OverflowError, ZeroDivisionError):
        return None
    return result if math.isfinite(result) else None


def _date_of_last_bar(rows: Sequence[Mapping[str, Any]]) -> str | None:
    if not rows:
        return None
    value = rows[-1].get("time")
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.isoformat() == value else None


def _return(rows: Sequence[Mapping[str, Any]], days: int) -> float | None:
    window = rows[-(days + 1):]
    if len(window) != days + 1:
        return None
    closes = _field_values(window, "close", positive=True)
    if closes is None:
        return None
    value = closes[-1] / closes[0] - 1
    return value if math.isfinite(value) else None


def _average_close(rows: Sequence[Mapping[str, Any]], period: int) -> float | None:
    window = rows[-period:]
    if len(window) != period:
        return None
    closes = _field_values(window, "close", positive=True)
    return _mean(closes) if closes is not None else None


def daily_technicals(bars: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Summarize daily bars in provider order; unavailable metrics are ``None``.

    Returns and volume ratios use decimal ratios (``0.05`` means 5%). Moving
    averages are simple arithmetic means of the trailing closes. Breakouts
    compare the last close with the *preceding* 60 bars, excluding the last
    bar's high/low. The range position uses the latest 60 bars, including the
    last bar. A requested period counts rows supplied, which represent trading
    sessions; this function does not infer missing exchange sessions.
    """
    rows = list(bars) if isinstance(bars, Sequence) and not isinstance(bars, (str, bytes)) else []
    # Ignore malformed containers on a row-by-row basis without turning an
    # unavailable metric into a fabricated zero.
    normalized = [row if isinstance(row, Mapping) else {} for row in rows]
    count = len(normalized)
    last_close = _number(normalized[-1].get("close"), positive=True) if normalized else None

    metrics: dict[str, Any] = {
        "date": _date_of_last_bar(normalized),
        "bars_count": count,
    }
    for period in (5, 20, 60):
        average = _average_close(normalized, period)
        metrics[f"ma{period}"] = average
        metrics[f"above_ma{period}"] = last_close > average if last_close is not None and average is not None else None

    metrics["return_5d"] = _return(normalized, 5)
    metrics["return_20d"] = _return(normalized, 20)

    volume_window = normalized[-6:]
    if len(volume_window) == 6:
        previous_volumes = _field_values(volume_window[:-1], "volume", nonnegative=True)
        last_volume = _number(volume_window[-1].get("volume"), nonnegative=True)
        previous_average = _mean(previous_volumes) if previous_volumes is not None else None
        metrics["volume_ratio_5"] = (
            last_volume / previous_average
            if last_volume is not None and previous_average is not None and previous_average > 0
            else None
        )
        if metrics["volume_ratio_5"] is not None and not math.isfinite(metrics["volume_ratio_5"]):
            metrics["volume_ratio_5"] = None
    else:
        metrics["volume_ratio_5"] = None

    breakout_window = normalized[-61:]
    prior = breakout_window[:-1]
    if len(breakout_window) == 61 and last_close is not None:
        highs = _field_values(prior, "high", positive=True)
        lows = _field_values(prior, "low", positive=True)
        metrics["new_high_60"] = last_close > max(highs) if highs is not None else None
        metrics["new_low_60"] = last_close < min(lows) if lows is not None else None
    else:
        metrics["new_high_60"] = None
        metrics["new_low_60"] = None

    range_window = normalized[-60:]
    if len(range_window) == 60 and last_close is not None:
        highs = _field_values(range_window, "high", positive=True)
        lows = _field_values(range_window, "low", positive=True)
        if highs is not None and lows is not None:
            highest = max(highs)
            lowest = min(lows)
            width = highest - lowest
            position = (last_close - lowest) / width if math.isfinite(width) and width > 0 else None
            metrics["range_position_60"] = position if position is not None and 0 <= position <= 1 else None
        else:
            metrics["range_position_60"] = None
    else:
        metrics["range_position_60"] = None

    return metrics
