



"""Contract tests for the portfolio research math module."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest

from app.custom.research_tools.portfolio import calculate


def _position(
    symbol: str,
    *,
    currency: str = "USD",
    qty: float = 1,
    price: float = 100,
    price_date: str = "2025-01-03",
    name: str | None = None,
    **extra,
) -> dict:
    return {
        "symbol": symbol,
        "name": name or symbol,
        "market": "US",
        "currency": currency,
        "qty": qty,
        "price": price,
        "price_date": price_date,
        **extra,
    }


def _history(closes: list[float], start: date = date(2025, 1, 1)) -> list[dict]:
    return [
        {"date": (start + timedelta(days=index)).isoformat(), "close": close}
        for index, close in enumerate(closes)
    ]


def _group(result: dict, currency: str = "USD") -> dict:
    return next(group for group in result["groups"] if group["currency"] == currency)


def test_known_concentration_volatility_drawdown_risk_contribution_and_correlation():
    start = date(2025, 1, 1)
    result = calculate(
        [_position("A", price_date="2025-01-03"), _position("B", price_date="2025-01-03")],
        {
            "A": _history([100, 110, 99], start),
            "B": _history([100, 100, 110], start),
        },
        min_observations=2,
    )

    group = _group(result)
    assert group["total_value"] == 200
    assert [position["weight"] for position in group["positions"]] == [0.5, 0.5]
    assert group["concentration"] == {"top1": 0.5, "top3": 1.0, "hhi": 0.5}
    assert group["observations"] == 2
    assert group["coverage"]["common_price_dates"] == 3

    risk = group["risk"]
    assert risk["annualized_volatility"] == pytest.approx(math.sqrt(0.00125 * 252))
    assert risk["max_drawdown"] == pytest.approx(0.0)
    assert risk["risk_contributions"]["A"] == pytest.approx(2.0)
    assert risk["risk_contributions"]["B"] == pytest.approx(-1.0)
    assert risk["correlations"]["values"] == [[1.0, -1.0], [-1.0, 1.0]]
    assert risk["included_weight"] == pytest.approx(1.0)
    assert "复权收盘价" in risk["price_basis"]
    assert "静态组合历史压力试算" in risk["interpretation"]
    json.dumps(result, allow_nan=False)


def test_max_drawdown_uses_static_portfolio_wealth_from_initial_nav():
    result = calculate(
        [_position("A", price_date="2025-01-04")],
        {"A": _history([100, 80, 100, 90])},
        min_observations=3,
    )

    assert _group(result)["risk"]["max_drawdown"] == pytest.approx(-0.2)


def test_currencies_are_kept_in_separate_groups():
    result = calculate(
        [
            _position("US", currency="USD", price_date="2025-01-03"),
            _position("HK", currency="HKD", market="HK", price_date="2025-01-03"),
        ],
        {"US": _history([100, 101, 102]), "HK": _history([100, 90, 95])},
        min_observations=2,
    )

    assert {group["currency"] for group in result["groups"]} == {"USD", "HKD"}
    assert _group(result, "USD")["risk"]["correlations"]["symbols"] == ["US"]
    assert _group(result, "HKD")["risk"]["correlations"]["symbols"] == ["HK"]


def test_missing_history_is_a_gap_and_history_weights_are_reported():
    result = calculate(
        [
            _position("A", price=75, price_date="2025-01-03"),
            _position("ETF", price=25, price_date="2025-01-03", name="科技ETF"),
        ],
        {"A": _history([100, 110, 100])},
        min_observations=2,
    )

    group = _group(result)
    assert group["risk"]["included_weight"] == pytest.approx(0.75)
    assert group["coverage"]["included_symbols"] == ["A"]
    assert group["risk"]["risk_contributions"] == {"A": pytest.approx(1.0)}
    assert group["positions"][1]["industry"] == "未分类"
    assert {gap["code"] for gap in group["gaps"]} == {"history_missing"}


def test_date_intersection_does_not_fill_and_short_window_returns_null_risk():
    result = calculate(
        [
            _position("A", price_date="2025-01-05"),
            _position("B", price_date="2025-01-05"),
        ],
        {
            "A": _history([100, 110, 120], date(2025, 1, 1)),
            "B": _history([100, 110, 120], date(2025, 1, 2)),
        },
        min_observations=2,
    )

    group = _group(result)
    assert group["observations"] == 1
    assert group["coverage"]["common_price_dates"] == 2
    assert group["coverage"]["first_common_date"] == "2025-01-02"
    assert group["coverage"]["last_common_date"] == "2025-01-03"
    assert group["risk"]["annualized_volatility"] is None
    assert group["risk"]["max_drawdown"] is None
    assert group["risk"]["risk_contributions"] is None
    assert group["risk"]["correlations"] is None
    assert any(gap["code"] == "insufficient_common_observations" for gap in group["gaps"])


def test_default_minimum_requires_twenty_returns_and_twenty_one_prices():
    twenty_one_prices = [100 + index for index in range(21)]
    passed = calculate(
        [_position("A", price_date="2025-01-21")],
        {"A": _history(twenty_one_prices)},
    )
    short = calculate(
        [_position("A", price_date="2025-01-20")],
        {"A": _history(twenty_one_prices[:20])},
    )

    assert _group(passed)["observations"] == 20
    assert _group(passed)["risk"]["annualized_volatility"] is not None
    assert _group(short)["observations"] == 19
    assert _group(short)["risk"]["annualized_volatility"] is None


def test_constant_prices_have_zero_volatility_and_undefined_covariance_outputs():
    result = calculate(
        [_position("A", price_date="2025-01-03")],
        {"A": _history([5, 5, 5])},
        min_observations=2,
    )

    risk = _group(result)["risk"]
    assert risk["annualized_volatility"] == 0
    assert risk["max_drawdown"] == 0
    assert risk["risk_contributions"] is None
    assert risk["correlations"]["values"] == [[None]]
    json.dumps(result, allow_nan=False)


def test_as_of_and_position_price_date_both_prevent_lookahead():
    result = calculate(
        [_position("A", price_date="2025-01-04")],
        {"A": _history([100, 80, 50, 10])},
        min_observations=2,
        as_of="2025-01-03",
    )

    group = _group(result)
    assert group["observations"] == 2
    assert group["coverage"]["last_common_date"] == "2025-01-03"
    assert group["risk"]["as_of"] == "2025-01-03"
    assert group["risk"]["max_drawdown"] == pytest.approx(-0.5)


@pytest.mark.parametrize(
    ("updates", "histories"),
    [
        ({"qty": True}, {}),
        ({"qty": -1}, {}),
        ({"price": -1}, {}),
        ({"price": math.inf}, {}),
        ({"qty": math.nan}, {}),
        ({}, {"A": [{"date": "2025-01-01", "close": True}]}),
        ({}, {"A": [{"date": "2025-01-01", "close": -1}]}),
        ({}, {"A": [{"date": "2025-01-01", "close": math.nan}]}),
    ],
)
def test_invalid_or_negative_numeric_inputs_are_rejected(updates: dict, histories: dict):
    position = _position("A", price_date="2025-01-01", **updates)
    with pytest.raises(ValueError):
        calculate([position], histories, min_observations=1)


def test_duplicate_symbols_and_history_dates_are_rejected():
    position = _position("A")
    with pytest.raises(ValueError, match="duplicate symbol"):
        calculate([position, position], {})
    with pytest.raises(ValueError, match="duplicate history date"):
        calculate(
            [_position("A", price_date="2025-01-01")],
            {"A": [{"date": "2025-01-01", "close": 1}, {"date": "2025-01-01", "close": 2}]},
        )


def test_more_than_thirty_positions_is_rejected():
    positions = [_position(f"S{index}") for index in range(31)]
    with pytest.raises(ValueError, match="at most 30"):
        calculate(positions, {})


def test_boolean_minimum_and_nonpositive_close_are_rejected():
    with pytest.raises(ValueError, match="min_observations"):
        calculate([], {}, min_observations=True)
    with pytest.raises(ValueError, match="must be positive"):
        calculate(
            [_position("A")],
            {"A": [{"date": "2025-01-01", "close": 0}]},
        )
