from concurrent.futures import ThreadPoolExecutor

import polars as pl

from app.indicators import levels as levels_module


def _sample_frame() -> pl.DataFrame:
    rows = []
    for i in range(130):
        close = 0.500 + (i % 7) * 0.0003
        high = close + 0.0023
        low = close - 0.0021
        if i == 80:
            high = 0.5067
        if i == 85:
            low = 0.4937
        if i == 129:
            close, high, low = 0.503, 0.507, 0.499
        rows.append(
            {
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000.0,
                "turnover_rate": 1.0,
                "atr_14": 0.0032,
                "ma20": 0.5028,
                "ma60": 0.5027,
                "boll_upper": 0.5087,
                "boll_lower": 0.4972,
            }
        )
    return pl.DataFrame(rows)


def _by_label(points: list[dict]) -> dict[str, float]:
    return {point["label"]: point["value"] for point in points}


def test_two_decimal_default_matches_explicit_legacy_precision() -> None:
    frame = _sample_frame()

    implicit = levels_module.compute_levels(frame)
    explicit = levels_module.compute_levels(frame, price_precision=2)

    assert implicit == explicit
    assert _by_label(implicit["pivot"])["枢轴位 P"] == 0.5
    assert "当前价 0.50" in levels_module.summarize_levels(implicit, 0.503)


def test_three_decimal_precision_preserves_etf_level_values_and_summary() -> None:
    levels = levels_module.compute_levels(_sample_frame(), price_precision=3)

    pivot = _by_label(levels["pivot"])
    assert pivot["枢轴位 P"] == 0.503
    assert pivot["压力位 R1"] == 0.507
    assert pivot["支撑位 S1"] == 0.499

    extremes = _by_label(levels["extreme"])
    assert extremes["60日新高"] == 0.507
    assert extremes["60日新低"] == 0.494

    boll = _by_label(levels["boll"])
    assert boll["布林上轨"] == 0.509
    assert boll["布林下轨"] == 0.497
    assert boll["布林中轨"] == 0.503

    keltner = _by_label(levels["keltner_s"])
    assert keltner["短期通道上轨"] == 0.509
    assert keltner["短期通道下轨"] == 0.496
    assert all(
        point["value"] == round(point["value"], 3)
        for group in levels.values()
        for point in group
    )

    summary = levels_module.summarize_levels(levels, 0.503, price_precision=3)
    assert "当前价 0.503" in summary
    assert "枢轴位 P=0.503" in summary


def test_precision_is_local_to_each_call_and_default_helpers_stay_callable() -> None:
    frame = _sample_frame()
    expected_two = levels_module.compute_levels(frame)
    expected_three = levels_module.compute_levels(frame, price_precision=3)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(levels_module.compute_levels, frame, price_precision=precision)
            for precision in (2, 3) * 8
        ]
    results = [future.result() for future in futures]
    for precision, result in zip((2, 3) * 8, results, strict=True):
        assert result == (expected_two if precision == 2 else expected_three)

    assert levels_module._support_resistance(frame) == expected_two["sr"]
    assert levels_module._pivot_points(frame) == expected_two["pivot"]
    assert levels_module._extreme_levels(frame) == expected_two["extreme"]
    assert levels_module._boll_channel(frame) == expected_two["boll"]
    assert levels_module._keltner_short(frame) == expected_two["keltner_s"]
    assert levels_module._keltner_mid(frame) == expected_two["keltner_m"]
    assert levels_module._keltner_long(frame) == expected_two["keltner_l"]
    assert levels_module._atr_stops(frame) == expected_two["atr_stop"]
    assert levels_module._gap_levels(frame) == expected_two["gap"]
    assert levels_module._fibonacci_levels(frame) == expected_two["fib"]
    assert levels_module._round_numbers(frame) == expected_two["round"]
    assert levels_module._keltner_band(
        frame, "ma20", 20, 2.0, "短期", "keltner_s"
    ) == expected_two["keltner_s"]


def test_summary_precision_formats_unrounded_price_inputs() -> None:
    summary = levels_module.summarize_levels(
        {"pivot": [{"label": "触发价", "value": 0.5036}]},
        0.5034,
        price_precision=3,
    )

    assert "当前价 0.503" in summary
    assert "触发价=0.504" in summary
