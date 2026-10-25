# ruff: noqa: RUF001 -- Native Chinese research text.
"""Pure portfolio concentration and historical risk calculations.

Historical ``close`` values are assumed to be consistently adjusted by the
caller. This module does not load data, infer an industry from a name, convert
currencies, fill gaps, or make trading recommendations.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime
from itertools import pairwise
from typing import Any

_PERIODS_PER_YEAR = 252
_UNCLASSIFIED = "未分类"
_PRICE_BASIS = "调用方提供的同口径历史复权收盘价；按日期交集计算，不前填缺失日期"
_STATIC_NOTE = "基于当前持仓权重的静态组合历史压力试算，不代表账户真实净值"


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _day(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc
    raise ValueError(f"{field} must be a date or ISO date string")


def _finite_sum(values: Any, field: str) -> float:
    try:
        result = math.fsum(values)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} is not finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} is not finite")
    return result


def _check_finite_result(value: Any, path: str = "result") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} contains a non-finite value")
    if isinstance(value, dict):
        for key, child in value.items():
            _check_finite_result(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_finite_result(child, f"{path}[{index}]")


def _parse_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(positions, list):
        raise ValueError("positions must be a list")
    if len(positions) > 30:
        raise ValueError("at most 30 positions are supported")

    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(positions):
        if not isinstance(raw, dict):
            raise ValueError(f"positions[{index}] must be an object")

        symbol = _text(raw.get("symbol"), f"positions[{index}].symbol")
        if symbol in seen:
            raise ValueError(f"duplicate symbol: {symbol}")
        seen.add(symbol)

        name = _text(raw.get("name"), f"positions[{index}].name")
        market = _text(raw.get("market"), f"positions[{index}].market")
        currency = _text(raw.get("currency"), f"positions[{index}].currency")
        quantity = _number(raw.get("qty"), f"positions[{index}].qty")
        price = _number(raw.get("price"), f"positions[{index}].price")
        if quantity < 0 or price < 0:
            raise ValueError(f"positions[{index}].qty and price must be non-negative")
        price_date = _day(raw.get("price_date"), f"positions[{index}].price_date")

        industry = raw.get("industry")
        if industry is None or (isinstance(industry, str) and not industry.strip()):
            industry = _UNCLASSIFIED
        elif not isinstance(industry, str):
            raise ValueError(f"positions[{index}].industry must be a string when provided")
        else:
            industry = industry.strip()

        value = quantity * price
        if not math.isfinite(value):
            raise ValueError(f"positions[{index}] value is not finite")
        parsed.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "currency": currency,
                "qty": quantity,
                "price": price,
                "price_date": price_date.isoformat(),
                "_price_date": price_date,
                "_value": value,
                "industry": industry,
            }
        )
    return parsed


def _parse_history(rows: list[dict[str, Any]], symbol: str) -> dict[date, float]:
    if not isinstance(rows, list):
        raise ValueError(f"histories[{symbol!r}] must be a list")
    parsed: dict[date, float] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"histories[{symbol!r}][{index}] must be an object")
        day = _day(row.get("date"), f"histories[{symbol!r}][{index}].date")
        close = _number(row.get("close"), f"histories[{symbol!r}][{index}].close")
        if close <= 0:
            raise ValueError(f"histories[{symbol!r}][{index}].close must be positive")
        if day in parsed:
            raise ValueError(f"duplicate history date for {symbol}: {day.isoformat()}")
        parsed[day] = close
    return parsed


def _sample_variance(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = _finite_sum(values, "historical mean") / len(values)
    return _finite_sum(
        ((value - mean) ** 2 for value in values), "historical variance"
    ) / (len(values) - 1)


def _risk_result(
    symbols: list[str],
    weights: list[float],
    histories: dict[str, dict[date, float]],
    dates: list[date],
    min_observations: int,
) -> tuple[dict[str, Any], int, list[dict[str, Any]], date | None, date | None]:
    observations = max(0, len(dates) - 1)
    returns: dict[str, list[float]] = {symbol: [] for symbol in symbols}
    for symbol in symbols:
        prices = histories[symbol]
        for previous_day, current_day in pairwise(dates):
            current_day_close = prices[current_day]
            previous = prices[previous_day]
            change = (current_day_close - previous) / previous
            if not math.isfinite(change):
                raise ValueError(f"non-finite historical return for {symbol}")
            returns[symbol].append(change)

    required_observations = max(min_observations, 2)
    gaps: list[dict[str, Any]] = []
    if observations < required_observations:
        gaps.append(
            {
                "code": "insufficient_common_observations",
                "available": observations,
                "required": required_observations,
                "required_prices": required_observations + 1,
                "symbols": symbols,
            }
        )
        risk = {
            "annualized_volatility": None,
            "max_drawdown": None,
            "risk_contributions": None,
            "correlations": None,
        }
        return risk, observations, gaps, dates[0] if dates else None, dates[-1] if dates else None

    portfolio_returns = [
        _finite_sum(
            (weight * returns[symbol][index] for symbol, weight in zip(symbols, weights, strict=True)),
            "historical portfolio return",
        )
        for index in range(observations)
    ]
    portfolio_variance = _sample_variance(portfolio_returns)
    assert portfolio_variance is not None
    annualized_volatility = math.sqrt(portfolio_variance * _PERIODS_PER_YEAR)

    wealth = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for change in portfolio_returns:
        wealth *= 1.0 + change
        if not math.isfinite(wealth):
            raise ValueError("historical portfolio wealth is not finite")
        peak = max(peak, wealth)
        max_drawdown = min(max_drawdown, (wealth - peak) / peak)

    means = {
        symbol: _finite_sum(returns[symbol], f"historical mean for {symbol}") / observations
        for symbol in symbols
    }
    covariance: dict[tuple[str, str], float] = {}
    variances: dict[str, float] = {}
    for left in symbols:
        for right in symbols:
            cov = _finite_sum(
                (
                    (left_return - means[left]) * (right_return - means[right])
                    for left_return, right_return in zip(returns[left], returns[right], strict=True)
                ),
                f"historical covariance for {left} and {right}",
            ) / (observations - 1)
            covariance[left, right] = cov
        variances[left] = covariance[left, left]

    weighted_covariance = {
        symbol: _finite_sum(
            (
                covariance[symbol, other] * weights[index]
                for index, other in enumerate(symbols)
            ),
            f"weighted covariance for {symbol}",
        )
        for symbol in symbols
    }
    risk_contributions: dict[str, float] | None = None
    if portfolio_variance > 0:
        risk_contributions = {
            symbol: weights[index] * weighted_covariance[symbol] / portfolio_variance
            for index, symbol in enumerate(symbols)
        }

    correlation_values: list[list[float | None]] = []
    for left in symbols:
        row: list[float | None] = []
        for right in symbols:
            denominator = math.sqrt(variances[left] * variances[right])
            if denominator <= 0:
                row.append(None)
                continue
            correlation = covariance[left, right] / denominator
            row.append(min(1.0, max(-1.0, correlation)))
        correlation_values.append(row)

    risk = {
        "annualized_volatility": annualized_volatility,
        "max_drawdown": max_drawdown,
        "risk_contributions": risk_contributions,
        "correlations": {"symbols": symbols, "values": correlation_values},
    }
    return risk, observations, gaps, dates[0] if dates else None, dates[-1] if dates else None


def calculate(
    positions: list[dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
    min_observations: int = 20,
    *,
    as_of: date | str | None = None,
) -> dict[str, Any]:
    """Summarize current holdings and calculate trailing, currency-local risk.

    Weights use ``qty * price`` within each currency and remain static across
    the historical return window. A group's risk is calculated only from
    positive-weight holdings with history. Their weights are re-normalized
    for that calculation; ``risk.included_weight`` reports the share of the
    original group represented by those holdings. The effective cutoff is
    the earlier of ``as_of`` (when supplied) and the group's earliest
    ``price_date``. The default cutoff is that earliest ``price_date``.

    ``histories[symbol]`` must contain daily ``date`` and positive ``close``
    values in one consistently adjusted-price basis. Missing dates are
    intersected across included holdings and never forward-filled. The
    returned risk figures are a historical static-portfolio stress test, not
    the account's realized net asset value or a trading recommendation.

    Raises:
        ValueError: If inputs contain invalid numbers, duplicate symbols or
            dates, negative quantities/prices, or more than 30 positions.
    """
    if isinstance(min_observations, bool) or not isinstance(min_observations, int):
        raise ValueError("min_observations must be a positive integer")
    if min_observations < 1:
        raise ValueError("min_observations must be a positive integer")
    if not isinstance(histories, Mapping):
        raise ValueError("histories must be a mapping")
    as_of_day = None if as_of is None else _day(as_of, "as_of")
    parsed_positions = _parse_positions(positions)

    groups: dict[str, list[dict[str, Any]]] = {}
    for position in parsed_positions:
        groups.setdefault(position["currency"], []).append(position)

    result: list[dict[str, Any]] = []
    for currency, group_positions in groups.items():
        try:
            total_value = math.fsum(position["_value"] for position in group_positions)
        except OverflowError as exc:
            raise ValueError(f"total value is not finite for currency {currency}") from exc
        if not math.isfinite(total_value):
            raise ValueError(f"total value is not finite for currency {currency}")

        for position in group_positions:
            position["weight"] = position["_value"] / total_value if total_value > 0 else 0.0
        sorted_weights = sorted((position["weight"] for position in group_positions), reverse=True)
        concentration = {
            "top1": sorted_weights[0] if sorted_weights else 0.0,
            "top3": math.fsum(sorted_weights[:3]),
            "hhi": math.fsum(weight * weight for weight in sorted_weights),
        }

        current_cutoff = min(position["_price_date"] for position in group_positions)
        cutoff = min(current_cutoff, as_of_day) if as_of_day is not None else current_cutoff
        gaps: list[dict[str, Any]] = []
        included_positions: list[dict[str, Any]] = []
        parsed_histories: dict[str, dict[date, float]] = {}
        for position in group_positions:
            if position["weight"] <= 0:
                continue
            symbol = position["symbol"]
            if symbol not in histories:
                gaps.append({"code": "history_missing", "symbol": symbol, "weight": position["weight"]})
                continue
            prices = _parse_history(histories[symbol], symbol)
            usable_prices = {day: close for day, close in prices.items() if day <= cutoff}
            if not usable_prices:
                gaps.append(
                    {
                        "code": "no_history_before_cutoff",
                        "symbol": symbol,
                        "cutoff": cutoff.isoformat(),
                        "weight": position["weight"],
                    }
                )
                continue
            included_positions.append(position)
            parsed_histories[symbol] = usable_prices

        included_weight = math.fsum(position["weight"] for position in included_positions)
        risk_symbols = [position["symbol"] for position in included_positions]
        normalized_weights = (
            [position["weight"] / included_weight for position in included_positions]
            if included_weight > 0
            else []
        )

        if risk_symbols:
            common_dates = set(parsed_histories[risk_symbols[0]])
            for symbol in risk_symbols[1:]:
                common_dates.intersection_update(parsed_histories[symbol])
            aligned_dates = sorted(common_dates)
        else:
            aligned_dates = []
            gaps.append({"code": "no_historical_positions", "message": "没有有权重且截止日前有历史数据的持仓"})

        risk, observations, risk_gaps, first_date, last_date = _risk_result(
            risk_symbols,
            normalized_weights,
            parsed_histories,
            aligned_dates,
            min_observations,
        )
        gaps.extend(risk_gaps)
        risk.update(
            {
                "included_weight": included_weight,
                "price_basis": _PRICE_BASIS,
                "weight_basis": "当前持仓 qty×price 权重；仅有历史的标的按 included_weight 重归一",
                "as_of": cutoff.isoformat(),
                "interpretation": _STATIC_NOTE,
            }
        )

        result.append(
            {
                "currency": currency,
                "total_value": total_value,
                "positions": [
                    {
                        key: position[key]
                        for key in (
                            "symbol",
                            "name",
                            "market",
                            "currency",
                            "qty",
                            "price",
                            "price_date",
                            "industry",
                            "weight",
                        )
                    }
                    for position in group_positions
                ],
                "concentration": concentration,
                "risk": risk,
                "observations": observations,
                "coverage": {
                    "position_count": len(group_positions),
                    "history_count": len(included_positions),
                    "included_symbols": risk_symbols,
                    "included_weight": included_weight,
                    "common_price_dates": len(aligned_dates),
                    "first_common_date": first_date.isoformat() if first_date else None,
                    "last_common_date": last_date.isoformat() if last_date else None,
                },
                "gaps": gaps,
            }
        )

    payload = {"groups": result}
    _check_finite_result(payload)
    return payload
