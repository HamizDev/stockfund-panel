from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from app.indicators.adx import compute_adx, compute_adx_matrix

OUTPUT_COLUMNS = ["adx_14", "adx_plus_di_14", "adx_minus_di_14"]


def _bars(symbol: str, closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol] * len(closes),
            "date": list(range(len(closes))),
            "open": closes,
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
        }
    )


def _rising_bars(symbol: str = "UP", count: int = 40) -> pl.DataFrame:
    return _bars(symbol, [100.0 + index for index in range(count)])


def test_strict_wilder_seeds_for_trend_and_flat_market() -> None:
    trend = compute_adx(_rising_bars(count=32))
    assert trend["adx_plus_di_14"][13] is None
    assert trend["adx_plus_di_14"][14] == pytest.approx(50.0)
    assert trend["adx_minus_di_14"][14] == pytest.approx(0.0)
    assert trend["adx_14"][26] is None
    assert trend["adx_14"][27] == pytest.approx(100.0)

    flat_close = [50.0] * 32
    flat = pl.DataFrame(
        {
            "symbol": ["FLAT"] * len(flat_close),
            "date": list(range(len(flat_close))),
            "open": flat_close,
            "high": flat_close,
            "low": flat_close,
            "close": flat_close,
        }
    )
    result = compute_adx(flat)
    assert result["adx_plus_di_14"][14] == pytest.approx(0.0)
    assert result["adx_minus_di_14"][14] == pytest.approx(0.0)
    assert result["adx_14"][27] == pytest.approx(0.0)


def test_reversal_has_fixed_di_and_adx_values() -> None:
    # Fourteen upward changes establish the DI seed; later bars reverse direction.
    closes = [100.0 + index for index in range(15)]
    reversal_start = closes[-1]
    closes.extend(reversal_start - index for index in range(1, 29))
    result = compute_adx(_bars("REVERSAL", closes))

    assert result["adx_plus_di_14"][14] == pytest.approx(50.0)
    assert result["adx_minus_di_14"][14] == pytest.approx(0.0)
    assert result["adx_plus_di_14"][15] == pytest.approx(46.42857142857143)
    assert result["adx_minus_di_14"][15] == pytest.approx(3.571428571428571)
    assert result["adx_14"][27] == pytest.approx(37.37030184281297)
    assert result["adx_14"][28] == pytest.approx(36.78191870804263)


def test_symbols_are_smoothed_independently() -> None:
    source = pl.concat([_rising_bars("UP", 32), _bars("DOWN", [100.0 - i for i in range(32)])])
    result = compute_adx(source)
    up = result.filter(pl.col("symbol") == "UP")
    down = result.filter(pl.col("symbol") == "DOWN")

    assert up["adx_plus_di_14"][14] == pytest.approx(50.0)
    assert up["adx_minus_di_14"][14] == pytest.approx(0.0)
    assert down["adx_plus_di_14"][14] == pytest.approx(0.0)
    assert down["adx_minus_di_14"][14] == pytest.approx(50.0)
    assert up["adx_14"][27] == pytest.approx(100.0)
    assert down["adx_14"][27] == pytest.approx(100.0)


def test_short_history_and_empty_input_keep_warmup_nulls() -> None:
    short = compute_adx(_rising_bars(count=27))
    assert short["adx_plus_di_14"][13] is None
    assert short["adx_plus_di_14"][14] == pytest.approx(50.0)
    assert short["adx_14"].null_count() == 27

    empty = pl.DataFrame(
        schema={
            "symbol": pl.String,
            "date": pl.Int64,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
        }
    )
    result = compute_adx(empty)
    assert result.height == 0
    assert result.columns == [*empty.columns, *OUTPUT_COLUMNS]


def test_future_bars_do_not_change_existing_prefix() -> None:
    closes = [100.0 + index for index in range(31)]
    future_start = closes[-1]
    closes.extend(future_start - 20.0 * step for step in range(1, 10))
    prefix = compute_adx(_bars("PREFIX", closes[:31]))
    extended = compute_adx(_bars("PREFIX", closes)).head(31)
    assert prefix.select(OUTPUT_COLUMNS).equals(extended.select(OUTPUT_COLUMNS))


@pytest.mark.parametrize(
    ("column", "bad_value"),
    [
        ("close", None),
        ("high", float("nan")),
        ("low", float("inf")),
        ("high", 120.0),  # The close at this row is 135, so high < close.
    ],
)
def test_invalid_ohlc_makes_current_and_later_values_null(column: str, bad_value: float | None) -> None:
    source = _rising_bars(count=40)
    values = source[column].to_list()
    values[35] = bad_value
    source = source.with_columns(pl.Series(column, values, dtype=pl.Float64))

    result = compute_adx(source)
    valid_prefix = compute_adx(_rising_bars(count=35))
    assert result.height == source.height
    for name in OUTPUT_COLUMNS:
        assert result[name][:35].equals(valid_prefix[name])
        assert result[name][35:].null_count() == result.height - 35


def test_output_appends_only_the_three_adx_columns() -> None:
    source = _rising_bars(count=30).with_columns(pl.lit("kept").alias("extra"))
    result = compute_adx(source)
    assert result.columns == [*source.columns, *OUTPUT_COLUMNS]
    assert result.height == source.height
    assert result["extra"].to_list() == ["kept"] * source.height


def _matrix_ohlc(close: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    close = np.asarray(close, dtype=np.float64)
    return close.copy(), close + 1.0, close - 1.0, close.copy()


def test_matrix_reversal_matches_fixed_values_and_polars() -> None:
    closes = [100.0 + index for index in range(15)]
    reversal_start = closes[-1]
    closes.extend(reversal_start - index for index in range(1, 29))
    arrays = _matrix_ohlc(np.asarray(closes, dtype=np.float64)[:, None])
    matrix_results = compute_adx_matrix(*arrays)
    polars_result = compute_adx(_bars("MATRIX", closes))

    assert all(values.dtype == np.float32 for values in matrix_results)
    assert matrix_results[1][14, 0] == pytest.approx(50.0)
    assert matrix_results[2][14, 0] == pytest.approx(0.0)
    assert matrix_results[1][15, 0] == pytest.approx(46.42857142857143)
    assert matrix_results[2][15, 0] == pytest.approx(3.571428571428571)
    assert matrix_results[0][27, 0] == pytest.approx(37.37030184281297)
    assert matrix_results[0][28, 0] == pytest.approx(36.78191870804263)

    for matrix_values, column in zip(matrix_results, OUTPUT_COLUMNS, strict=True):
        expected = polars_result[column].fill_null(float("nan")).to_numpy()
        np.testing.assert_allclose(matrix_values[:, 0], expected, rtol=1e-6, atol=1e-6, equal_nan=True)


def test_matrix_skips_ipo_and_halt_slots_and_matches_polars() -> None:
    close = np.full((52, 2), np.nan, dtype=np.float64)
    listings = [
        [index for index in range(5, 52) if index not in (22, 23)],
        [index for index in range(52) if index not in (18,)],
    ]
    for symbol_index, rows in enumerate(listings):
        start = 100.0 if symbol_index == 0 else 200.0
        direction = 1.0 if symbol_index == 0 else -1.0
        close[rows, symbol_index] = start + direction * np.arange(len(rows))
    arrays = _matrix_ohlc(close)
    matrix_results = compute_adx_matrix(*arrays)

    assert all(np.isnan(values[:5, 0]).all() for values in matrix_results)
    assert all(np.isnan(values[[22, 23], 0]).all() for values in matrix_results)
    assert all(np.isnan(values[18, 1]) for values in matrix_results)
    assert np.isfinite(matrix_results[0][-1]).all()

    for symbol_index, rows in enumerate(listings):
        source = pl.DataFrame(
            {
                "symbol": [f"S{symbol_index}"] * len(rows),
                "date": rows,
                "open": arrays[0][rows, symbol_index],
                "high": arrays[1][rows, symbol_index],
                "low": arrays[2][rows, symbol_index],
                "close": arrays[3][rows, symbol_index],
            }
        )
        polars_result = compute_adx(source)
        for matrix_values, column in zip(matrix_results, OUTPUT_COLUMNS, strict=True):
            expected = polars_result[column].fill_null(float("nan")).to_numpy()
            np.testing.assert_allclose(
                matrix_values[rows, symbol_index], expected, rtol=1e-6, atol=1e-6, equal_nan=True
            )


@pytest.mark.parametrize(
    ("column", "bad_value"),
    [("open", np.nan), ("high", np.inf), ("low", 150.0), ("close", np.nan), ("close", np.inf)],
)
def test_matrix_bad_ohlc_invalidates_only_that_symbol_tail(column: str, bad_value: float) -> None:
    close = np.column_stack((np.arange(100.0, 140.0), np.arange(200.0, 240.0)))
    clean_inputs = _matrix_ohlc(close)
    expected = compute_adx_matrix(*clean_inputs)
    damaged_inputs = [values.copy() for values in clean_inputs]
    column_index = {"open": 0, "high": 1, "low": 2, "close": 3}[column]
    damaged_inputs[column_index][35, 0] = bad_value

    result = compute_adx_matrix(*damaged_inputs)
    for actual, clean in zip(result, expected, strict=True):
        np.testing.assert_array_equal(actual[:35, 0], clean[:35, 0])
        assert np.isnan(actual[35:, 0]).all()
        np.testing.assert_array_equal(actual[:, 1], clean[:, 1])


def test_matrix_future_rows_do_not_change_existing_prefix() -> None:
    prefix_close = np.column_stack((np.arange(100.0, 132.0), np.arange(200.0, 232.0)))
    future_steps = np.arange(1.0, 9.0)
    future_close = np.column_stack(
        (prefix_close[-1, 0] - 20.0 * future_steps, prefix_close[-1, 1] - 15.0 * future_steps)
    )
    prefix_inputs = _matrix_ohlc(prefix_close)
    full_inputs = _matrix_ohlc(np.concatenate((prefix_close, future_close), axis=0))
    prefix_results = compute_adx_matrix(*prefix_inputs)
    full_results = compute_adx_matrix(*full_inputs)

    for prefix, full in zip(prefix_results, full_results, strict=True):
        np.testing.assert_array_equal(prefix, full[: prefix.shape[0]])
