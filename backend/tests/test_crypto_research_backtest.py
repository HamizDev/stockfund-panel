from __future__ import annotations

from decimal import Decimal

import pytest

from app.custom.crypto_paper import research_backtest as backtest
from app.custom.crypto_paper import rules

HOUR_MS = 3_600_000
BASE_MS = 1_700_000_000_000 // HOUR_MS * HOUR_MS


def _bars(count: int = 250, interval: str = "1h") -> list[dict]:
    period = HOUR_MS if interval == "1h" else 4 * HOUR_MS
    start = BASE_MS // period * period
    return [
        {
            "open_time_ms": start + index * period,
            "close_time_ms": start + (index + 1) * period - 1,
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100",
            "volume": "ignored",
        }
        for index in range(count)
    ]


def _config(count: int = 250, **overrides) -> dict:
    return {
        "strategy_id": "ema_trend",
        "strategy_params": {"fast_period": 5, "slow_period": 20},
        "symbol": "BTCUSDT",
        "interval": "1h",
        "bars": count,
        "initial_cash": "10000",
        "allocation_pct": 50,
        "stop_loss_pct": 10,
        "take_profit_pct": 10,
        **overrides,
    }


def _contract(**overrides) -> dict:
    return {
        "step_size": "0.001",
        "min_qty": "0.001",
        "min_notional": "5",
        "max_qty": "1000",
        "max_leverage": "20",
        "taker_fee_rate": "0.0006",
        **overrides,
    }


def _signals(monkeypatch, targets: list[str]):
    calls: list[list[dict]] = []

    def signal(_strategy_id, prefix, _market, _params):
        calls.append(prefix)
        index = len(calls) - 1
        return targets[index] if index < len(targets) else "hold"

    monkeypatch.setattr(backtest.rules, "minimum_bars", lambda *_args: 1)
    monkeypatch.setattr(backtest.auto, "signal", signal)
    return calls


def test_no_signal_has_null_win_rate_and_only_causal_ohlc_prefixes(monkeypatch):
    calls = _signals(monkeypatch, [])
    result = backtest.run(_config(), _bars(), _contract())

    assert result["trades"] == []
    assert result["metrics"]["closed_trades"] == 0
    assert result["metrics"]["win_rate_pct"] is None
    assert len(result["nav"]) == 251
    assert len(calls) == 250
    assert [len(calls[index]) for index in (0, 1, 199, 200, 249)] == [1, 2, 200, 200, 200]
    for index, prefix in enumerate(calls):
        assert prefix[-1]["close_time_ms"] == _bars()[index]["close_time_ms"]
        assert len(prefix) <= 200
        assert all(set(bar) == {"open_time_ms", "close_time_ms", "open", "high", "low", "close"}
                   for bar in prefix)
    assert "资金费未计入" in " ".join(result["warnings"])
    assert "手续费后、资金费前" in result["method"]


def test_signal_fills_next_open_with_adverse_slippage_and_both_fees(monkeypatch):
    _signals(monkeypatch, ["long"])
    result = backtest.run(
        _config(allocation_pct=100, stop_loss_pct=10, take_profit_pct=10),
        _bars(), _contract(taker_fee_rate="0.0006"),
    )

    opening, closing = result["trades"]
    source = _bars()
    assert opening["action"] == "open_long"
    assert opening["signal_time_ms"] == source[0]["close_time_ms"]
    assert opening["fill_time_ms"] == source[1]["open_time_ms"]
    assert opening["price"] == pytest.approx(100 * 1.0005)
    assert Decimal(str(opening["quantity"])) % Decimal("0.001") == 0
    assert opening["quantity"] >= 0.001
    assert opening["fee"] == pytest.approx(opening["price"] * opening["quantity"] * 0.0006)
    assert closing["action"] == "close_long"
    assert closing["reason"] == "end_of_test_estimate"
    assert closing["fill_time_ms"] == source[-1]["close_time_ms"]
    assert closing["price"] == pytest.approx(100 * 0.9995)
    assert closing["fee"] == pytest.approx(closing["price"] * closing["quantity"] * 0.0006)
    assert result["metrics"]["fees_paid"] == pytest.approx(opening["fee"] + closing["fee"])
    assert result["metrics"]["closed_trades"] == 1
    assert result["metrics"]["win_rate_pct"] == 0
    assert opening["realized_pnl"] + closing["realized_pnl"] < 0
    assert any("期末估算平仓" in warning for warning in result["warnings"])


def test_long_hold_short_flat_targets_keep_existing_meanings(monkeypatch):
    _signals(monkeypatch, ["long", "hold", "short", "flat", "hold"])
    source = _bars()
    result = backtest.run(_config(), source, _contract())

    assert [trade["action"] for trade in result["trades"]] == [
        "open_long", "close_long", "open_short", "close_short",
    ]
    assert [trade["signal_time_ms"] for trade in result["trades"]] == [
        source[0]["close_time_ms"], source[2]["close_time_ms"],
        source[2]["close_time_ms"], source[3]["close_time_ms"],
    ]
    assert [trade["fill_time_ms"] for trade in result["trades"]] == [
        source[1]["open_time_ms"], source[3]["open_time_ms"],
        source[3]["open_time_ms"], source[4]["open_time_ms"],
    ]
    assert result["metrics"]["closed_trades"] == 2


def test_same_bar_stop_and_take_triggers_choose_stop_and_do_not_reopen(monkeypatch):
    _signals(monkeypatch, ["long"])
    source = _bars()
    source[1].update(high="105", low="95")
    result = backtest.run(
        _config(stop_loss_pct=1, take_profit_pct=1), source, _contract())

    assert [trade["action"] for trade in result["trades"]] == ["open_long", "close_long"]
    opening, closing = result["trades"]
    expected_stop = opening["price"] * 0.99
    assert closing["reason"] == "stop_loss"
    assert closing["price"] == pytest.approx(expected_stop * 0.9995)
    assert closing["fill_time_ms"] == source[1]["close_time_ms"]
    assert result["metrics"]["closed_trades"] == 1


def test_gap_through_stop_fills_at_worse_open(monkeypatch):
    _signals(monkeypatch, ["long", "hold"])
    source = _bars()
    source[1].update(low="99.5", high="100.5")
    source[2].update(open="98", high="99", low="97", close="98")
    result = backtest.run(_config(stop_loss_pct=1, take_profit_pct=10), source, _contract())

    closing = next(trade for trade in result["trades"] if trade["action"] == "close_long")
    assert closing["reason"] == "stop_loss_gap"
    assert closing["fill_time_ms"] == source[2]["open_time_ms"]
    assert closing["price"] == pytest.approx(98 * 0.9995)


def test_gap_through_take_profit_uses_the_less_favorable_target_price(monkeypatch):
    _signals(monkeypatch, ["long", "hold"])
    source = _bars()
    source[1].update(low="99.5", high="100.5")
    source[2].update(open="103", high="104", low="102", close="103")
    result = backtest.run(_config(stop_loss_pct=10, take_profit_pct=1), source, _contract())

    closing = next(trade for trade in result["trades"] if trade["action"] == "close_long")
    expected_take = result["trades"][0]["price"] * 1.01
    assert closing["reason"] == "take_profit_gap"
    assert closing["price"] == pytest.approx(expected_take * 0.9995)
    assert closing["price"] < 103


def test_insufficient_cash_skips_entry_and_discloses_it(monkeypatch):
    _signals(monkeypatch, ["long"])
    result = backtest.run(
        _config(initial_cash=100, allocation_pct=1), _bars(),
        _contract(step_size="0.1", min_qty="0.1", min_notional="50"),
    )

    assert result["trades"] == []
    assert result["metrics"]["ending_equity"] == 100
    assert any("资金不足" in warning for warning in result["warnings"])


@pytest.mark.parametrize("count", [249, 1001])
def test_rejects_bar_count_outside_supported_range(count):
    bars = _bars(count)
    config = _config(min(count, 1000))
    with pytest.raises(ValueError, match="250-1000"):
        backtest.run(config, bars, _contract())


@pytest.mark.parametrize("mutation, message", [
    ("duplicate", "连续且无重复"),
    ("gap", "连续且无重复"),
    ("close_time", "时间戳"),
    ("period_alignment", "时间戳"),
    ("nonfinite", "无效"),
    ("nonpositive", "无效"),
])
def test_rejects_duplicate_gapped_or_invalid_closed_bars(mutation, message):
    bars = _bars()
    index = 100
    if mutation == "duplicate":
        bars[index]["open_time_ms"] = bars[index - 1]["open_time_ms"]
        bars[index]["close_time_ms"] = bars[index]["open_time_ms"] + HOUR_MS - 1
    elif mutation == "gap":
        bars[index]["open_time_ms"] += HOUR_MS
        bars[index]["close_time_ms"] += HOUR_MS
    elif mutation == "close_time":
        bars[index]["close_time_ms"] += 1
    elif mutation == "period_alignment":
        bars[index]["open_time_ms"] += 1
        bars[index]["close_time_ms"] += 1
    elif mutation == "nonfinite":
        bars[index]["high"] = "NaN"
    else:
        bars[index]["close"] = "0"
    with pytest.raises(ValueError, match=message):
        backtest.run(_config(), bars, _contract())


def test_rejects_rules_requiring_more_than_200_bars(monkeypatch):
    monkeypatch.setattr(rules, "minimum_bars", lambda *_args: 201)
    with pytest.raises(ValueError, match=r"超过.*200"):
        backtest.run(_config(), _bars(), _contract())


def test_configured_bar_count_selects_latest_contiguous_window(monkeypatch):
    calls = _signals(monkeypatch, [])
    result = backtest.run(_config(bars=250), _bars(300), _contract())

    assert len(result["nav"]) == 251
    assert calls[0][0]["open_time_ms"] == _bars(300)[50]["open_time_ms"]


def test_accepts_four_hour_bars_with_matching_close_boundaries(monkeypatch):
    calls = _signals(monkeypatch, [])
    source = _bars(interval="4h")
    result = backtest.run(_config(interval="4h"), source, _contract())

    assert len(result["nav"]) == 251
    assert calls[0][0]["close_time_ms"] == source[0]["close_time_ms"]


def test_output_values_are_json_friendly_and_fee_basis_is_explicit(monkeypatch):
    _signals(monkeypatch, ["hold"])
    result = backtest.run(_config(), _bars(), _contract())

    assert isinstance(result["metrics"]["ending_equity"], float)
    assert all(isinstance(point["equity"], float) for point in result["nav"])
    assert "资金费前" in result["method"]
    assert "前向轮询" in result["method"]
    assert Decimal(str(result["metrics"]["fees_paid"])) == 0
