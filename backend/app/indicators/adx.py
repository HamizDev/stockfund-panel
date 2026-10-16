"""Wilder's 14-period directional movement index and average DX."""

from __future__ import annotations

import numpy as np
import polars as pl

_PERIOD = 14
_DI_PLUS = "adx_plus_di_14"
_DI_MINUS = "adx_minus_di_14"
_ADX = "adx_14"
_OUTPUT_COLUMNS = (_ADX, _DI_PLUS, _DI_MINUS)
_OHLC_COLUMNS = ("open", "high", "low", "close")


def compute_adx(df: pl.DataFrame) -> pl.DataFrame:
    """Append Wilder ADX and directional indicators to sorted OHLC bars.

    The first bar in each symbol has no previous bar, so it contributes neither
    true range nor directional movement. The first DI values use the arithmetic
    means of changes at indices 1..14. ADX is seeded by the arithmetic mean of
    DX at indices 14..27, then uses Wilder's alpha of 1/14.

    OHLC values must be finite, positive, and internally consistent. At an
    invalid bar, that symbol's indicators become null from that bar onward;
    earlier values remain causal and intact. Input rows are never removed.
    """
    missing = [column for column in _OHLC_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"ADX requires OHLC columns: {', '.join(missing)}")

    original_columns = df.columns
    prefix = "__adx14"
    while any(column == prefix or column.startswith(f"{prefix}_") for column in original_columns):
        prefix += "_"

    def temp(name: str) -> str:
        return f"{prefix}_{name}"

    group = "symbol" if "symbol" in original_columns else temp("group")
    out = df
    if group != "symbol":
        out = out.with_columns(pl.lit(0, dtype=pl.UInt8).alias(group))

    cols = {column: temp(column) for column in _OHLC_COLUMNS}
    out = out.with_columns(
        [pl.col(column).cast(pl.Float64, strict=False).alias(cols[column]) for column in _OHLC_COLUMNS]
    )

    valid_prices = [
        pl.col(cols[column]).is_not_null()
        & pl.col(cols[column]).is_finite()
        & (pl.col(cols[column]) > 0)
        for column in _OHLC_COLUMNS
    ]
    valid_ohlc = (
        pl.all_horizontal(valid_prices)
        & (pl.col(cols["low"]) <= pl.col(cols["open"]))
        & (pl.col(cols["low"]) <= pl.col(cols["close"]))
        & (pl.col(cols["high"]) >= pl.col(cols["open"]))
        & (pl.col(cols["high"]) >= pl.col(cols["close"]))
        & (pl.col(cols["low"]) <= pl.col(cols["high"]))
    )
    valid = temp("valid")
    idx = temp("index")
    valid_prefix = temp("valid_prefix")
    out = out.with_columns(valid_ohlc.fill_null(False).alias(valid)).with_columns(
        (pl.col(cols["close"]).cum_count().over(group).cast(pl.Int64) - 1).alias(idx),
        pl.col(valid).cast(pl.Int8).cum_min().over(group).eq(1).alias(valid_prefix),
    )

    previous = {
        column: temp(f"previous_{column}")
        for column in ("high", "low", "close")
    }
    out = out.with_columns(
        [pl.col(cols[column]).shift(1).over(group).alias(previous[column]) for column in previous]
    )

    true_range = temp("true_range")
    up_move = temp("up_move")
    down_move = temp("down_move")
    plus_dm = temp("plus_dm")
    minus_dm = temp("minus_dm")
    first_bar = pl.col(idx).eq(0)
    tr = pl.max_horizontal(
        pl.col(cols["high"]) - pl.col(cols["low"]),
        (pl.col(cols["high"]) - pl.col(previous["close"])).abs(),
        (pl.col(cols["low"]) - pl.col(previous["close"])).abs(),
    )
    up = pl.col(cols["high"]) - pl.col(previous["high"])
    down = pl.col(previous["low"]) - pl.col(cols["low"])
    out = out.with_columns(
        pl.when(first_bar).then(pl.lit(None, dtype=pl.Float64)).otherwise(tr).alias(true_range),
        up.alias(up_move),
        down.alias(down_move),
    ).with_columns(
        pl.when(first_bar)
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.when((pl.col(up_move) > pl.col(down_move)) & (pl.col(up_move) > 0))
                   .then(pl.col(up_move)).otherwise(0.0))
        .alias(plus_dm),
        pl.when(first_bar)
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.when((pl.col(down_move) > pl.col(up_move)) & (pl.col(down_move) > 0))
                   .then(pl.col(down_move)).otherwise(0.0))
        .alias(minus_dm),
    )

    tr_avg = temp("tr_avg")
    plus_avg = temp("plus_avg")
    minus_avg = temp("minus_avg")
    tr_seed = temp("tr_seed")
    plus_seed = temp("plus_seed")
    minus_seed = temp("minus_seed")
    seed_index = pl.col(idx).eq(_PERIOD)
    after_di_seed = pl.col(idx) > _PERIOD
    out = out.with_columns(
        pl.col(true_range).rolling_mean(_PERIOD, min_samples=_PERIOD).over(group).alias(tr_avg),
        pl.col(plus_dm).rolling_mean(_PERIOD, min_samples=_PERIOD).over(group).alias(plus_avg),
        pl.col(minus_dm).rolling_mean(_PERIOD, min_samples=_PERIOD).over(group).alias(minus_avg),
    ).with_columns(
        pl.when(seed_index).then(pl.col(tr_avg))
        .when(after_di_seed).then(pl.col(true_range)).otherwise(None).alias(tr_seed),
        pl.when(seed_index).then(pl.col(plus_avg))
        .when(after_di_seed).then(pl.col(plus_dm)).otherwise(None).alias(plus_seed),
        pl.when(seed_index).then(pl.col(minus_avg))
        .when(after_di_seed).then(pl.col(minus_dm)).otherwise(None).alias(minus_seed),
    ).with_columns(
        pl.col(tr_seed).ewm_mean(alpha=1.0 / _PERIOD, adjust=False, ignore_nulls=True)
        .over(group).alias(tr_avg),
        pl.col(plus_seed).ewm_mean(alpha=1.0 / _PERIOD, adjust=False, ignore_nulls=True)
        .over(group).alias(plus_avg),
        pl.col(minus_seed).ewm_mean(alpha=1.0 / _PERIOD, adjust=False, ignore_nulls=True)
        .over(group).alias(minus_avg),
    )

    di_plus = temp("di_plus")
    di_minus = temp("di_minus")
    di_ready = pl.col(idx) >= _PERIOD
    zero_range = pl.col(tr_avg).eq(0)
    out = out.with_columns(
        pl.when(di_ready & zero_range).then(0.0)
        .when(di_ready & (pl.col(tr_avg) > 0))
        .then(100.0 * pl.col(plus_avg) / pl.col(tr_avg))
        .otherwise(None).alias(di_plus),
        pl.when(di_ready & zero_range).then(0.0)
        .when(di_ready & (pl.col(tr_avg) > 0))
        .then(100.0 * pl.col(minus_avg) / pl.col(tr_avg))
        .otherwise(None).alias(di_minus),
    )

    dx = temp("dx")
    di_total = pl.col(di_plus) + pl.col(di_minus)
    out = out.with_columns(
        pl.when(di_ready & (di_total > 0))
        .then(100.0 * (pl.col(di_plus) - pl.col(di_minus)).abs() / di_total)
        .when(di_ready & (di_total == 0)).then(0.0)
        .otherwise(None).alias(dx)
    )

    dx_seed = temp("dx_seed")
    adx_seed = temp("adx_seed")
    adx_ready = pl.col(idx) >= (2 * _PERIOD - 1)
    dx_mean = pl.col(dx).rolling_mean(_PERIOD, min_samples=_PERIOD).over(group)
    out = out.with_columns(
        pl.when(pl.col(idx).eq(2 * _PERIOD - 1)).then(dx_mean)
        .when(adx_ready).then(pl.col(dx)).otherwise(None).alias(dx_seed)
    ).with_columns(
        pl.col(dx_seed).ewm_mean(alpha=1.0 / _PERIOD, adjust=False, ignore_nulls=True)
        .over(group).alias(adx_seed)
    )

    # Suppress the invalid bar and its tail, preserving all earlier causal rows.
    final_valid = pl.col(valid_prefix) & pl.col(valid)
    out = out.with_columns(
        pl.when(final_valid & di_ready).then(pl.col(di_plus)).otherwise(None).alias(_DI_PLUS),
        pl.when(final_valid & di_ready).then(pl.col(di_minus)).otherwise(None).alias(_DI_MINUS),
        pl.when(final_valid & adx_ready).then(pl.col(adx_seed)).otherwise(None).alias(_ADX),
    )

    kept_columns = [column for column in original_columns if column not in _OUTPUT_COLUMNS]
    return out.select([*kept_columns, *_OUTPUT_COLUMNS])


def compute_adx_matrix(
    open: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    observed: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute ADX/+DI/-DI for a ``(time, symbol)`` NumPy matrix.

    The observed mask distinguishes an absent slot from a recorded invalid bar.
    Without a mask, all-NaN OHLC slots are treated as absent for compatibility.
    An absent slot leaves Wilder state untouched. A present bar with any invalid
    OHLC value invalidates that symbol from the bar onward. State updates are
    batched across symbols for each time step.
    """
    arrays = tuple(np.asarray(values, dtype=np.float64) for values in (open, high, low, close))
    if any(values.ndim != 2 for values in arrays):
        raise ValueError("ADX matrix inputs must have shape (time, symbol)")
    if any(values.shape != arrays[0].shape for values in arrays[1:]):
        raise ValueError("ADX matrix inputs must have identical shapes")
    if observed is not None:
        observed = np.asarray(observed)
        if observed.shape != arrays[0].shape:
            raise ValueError("ADX observed mask must match OHLC shape")

    open_values, high_values, low_values, close_values = arrays
    time_count, symbol_count = close_values.shape
    adx_values = np.full((time_count, symbol_count), np.nan, dtype=np.float32)
    plus_di_values = np.full_like(adx_values, np.nan)
    minus_di_values = np.full_like(adx_values, np.nan)

    invalid = np.zeros(symbol_count, dtype=bool)
    bar_count = np.zeros(symbol_count, dtype=np.int64)
    previous_high = np.full(symbol_count, np.nan, dtype=np.float64)
    previous_low = np.full(symbol_count, np.nan, dtype=np.float64)
    previous_close = np.full(symbol_count, np.nan, dtype=np.float64)

    tr_seed_sum = np.zeros(symbol_count, dtype=np.float64)
    plus_seed_sum = np.zeros(symbol_count, dtype=np.float64)
    minus_seed_sum = np.zeros(symbol_count, dtype=np.float64)
    tr_average = np.full(symbol_count, np.nan, dtype=np.float64)
    plus_average = np.full(symbol_count, np.nan, dtype=np.float64)
    minus_average = np.full(symbol_count, np.nan, dtype=np.float64)

    dx_seed_sum = np.zeros(symbol_count, dtype=np.float64)
    dx_seed_count = np.zeros(symbol_count, dtype=np.int64)
    adx_state = np.full(symbol_count, np.nan, dtype=np.float64)
    adx_ready = np.zeros(symbol_count, dtype=bool)

    alpha = 1.0 / _PERIOD
    retain = 1.0 - alpha

    for time_index in range(time_count):
        open_now = open_values[time_index]
        high_now = high_values[time_index]
        low_now = low_values[time_index]
        close_now = close_values[time_index]

        # Matrix gaps have no OHLC at all. A populated bar with a missing close
        # is bad data, not a halt that may be silently skipped.
        present = (np.asarray(observed[time_index], dtype=bool) if observed is not None
                   else (~np.isnan(open_now) | ~np.isnan(high_now)
                         | ~np.isnan(low_now) | ~np.isnan(close_now)))
        finite_ohlc = (
            np.isfinite(open_now)
            & np.isfinite(high_now)
            & np.isfinite(low_now)
            & np.isfinite(close_now)
        )
        valid_ohlc = (
            finite_ohlc
            & (open_now > 0)
            & (high_now > 0)
            & (low_now > 0)
            & (close_now > 0)
            & (low_now <= open_now)
            & (low_now <= close_now)
            & (high_now >= open_now)
            & (high_now >= close_now)
            & (low_now <= high_now)
        )
        invalid |= present & ~invalid & ~valid_ohlc
        valid = present & ~invalid & valid_ohlc
        has_previous = valid & (bar_count > 0)
        bar_index = bar_count

        true_range = np.maximum.reduce(
            (
                high_now - low_now,
                np.abs(high_now - previous_close),
                np.abs(low_now - previous_close),
            )
        )
        upward = high_now - previous_high
        downward = previous_low - low_now
        plus_dm = np.where((upward > downward) & (upward > 0), upward, 0.0)
        minus_dm = np.where((downward > upward) & (downward > 0), downward, 0.0)

        seed_period = has_previous & (bar_index <= _PERIOD)
        tr_seed_sum[seed_period] += true_range[seed_period]
        plus_seed_sum[seed_period] += plus_dm[seed_period]
        minus_seed_sum[seed_period] += minus_dm[seed_period]

        di_seed = has_previous & (bar_index == _PERIOD)
        if np.any(di_seed):
            tr_average[di_seed] = tr_seed_sum[di_seed] / _PERIOD
            plus_average[di_seed] = plus_seed_sum[di_seed] / _PERIOD
            minus_average[di_seed] = minus_seed_sum[di_seed] / _PERIOD

        di_update = has_previous & (bar_index > _PERIOD)
        if np.any(di_update):
            tr_average[di_update] = retain * tr_average[di_update] + alpha * true_range[di_update]
            plus_average[di_update] = retain * plus_average[di_update] + alpha * plus_dm[di_update]
            minus_average[di_update] = retain * minus_average[di_update] + alpha * minus_dm[di_update]

        di_ready = di_seed | di_update
        if np.any(di_ready):
            plus_di = np.zeros(symbol_count, dtype=np.float64)
            minus_di = np.zeros(symbol_count, dtype=np.float64)
            nonzero_range = di_ready & (tr_average > 0)
            plus_di[nonzero_range] = 100.0 * plus_average[nonzero_range] / tr_average[nonzero_range]
            minus_di[nonzero_range] = 100.0 * minus_average[nonzero_range] / tr_average[nonzero_range]

            di_total = plus_di + minus_di
            dx = np.divide(
                100.0 * np.abs(plus_di - minus_di),
                di_total,
                out=np.zeros(symbol_count, dtype=np.float64),
                where=di_total > 0,
            )
            plus_di_values[time_index, di_ready] = plus_di[di_ready].astype(np.float32)
            minus_di_values[time_index, di_ready] = minus_di[di_ready].astype(np.float32)

            adx_recur = di_ready & adx_ready
            adx_seed = di_ready & ~adx_ready
            dx_seed_sum[adx_seed] += dx[adx_seed]
            dx_seed_count[adx_seed] += 1
            adx_first = adx_seed & (dx_seed_count == _PERIOD)
            if np.any(adx_first):
                adx_state[adx_first] = dx_seed_sum[adx_first] / _PERIOD
                adx_ready[adx_first] = True
            if np.any(adx_recur):
                adx_state[adx_recur] = retain * adx_state[adx_recur] + alpha * dx[adx_recur]

            adx_available = adx_first | adx_recur
            adx_values[time_index, adx_available] = adx_state[adx_available].astype(np.float32)

        previous_high[valid] = high_now[valid]
        previous_low[valid] = low_now[valid]
        previous_close[valid] = close_now[valid]
        bar_count[valid] += 1

    return adx_values, plus_di_values, minus_di_values
