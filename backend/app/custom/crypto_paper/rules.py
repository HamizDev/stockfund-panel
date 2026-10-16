"""Bounded parameters for the existing research signal families."""
from __future__ import annotations

from typing import Literal

StrategyId = Literal["ema_trend", "channel_breakout", "dmi_confirm", "squeeze_release",
                     "supertrend_pullback", "bb_aroon"]


def parameters(strategy_id: str, values: dict | None = None) -> dict[str, int]:
    defaults = {"ema_trend": {"fast_period": 20, "slow_period": 60},
                "channel_breakout": {"lookback": 20},
                "dmi_confirm": {"ma_period": 55, "adx_threshold": 23},
                "squeeze_release": {"lookback": 20},
                "supertrend_pullback": {"atr_period": 10, "atr_multiplier": 3, "ema_period": 20},
                "bb_aroon": {"bb_period": 20, "aroon_period": 100}}
    if not isinstance(strategy_id, str) or strategy_id not in defaults:
        raise ValueError("未知策略")
    if values is None:
        return defaults[strategy_id].copy()
    if not isinstance(values, dict) or set(values) != set(defaults[strategy_id]):
        raise ValueError("策略规则参数格式无效")
    if any(type(value) is not int for value in values.values()):
        raise ValueError("策略周期必须是整数")
    if strategy_id == "ema_trend":
        fast, slow = values["fast_period"], values["slow_period"]
        if not 5 <= fast <= 50 or not 20 <= slow <= 120 or fast >= slow:
            raise ValueError("均线快周期需为5-50, 慢周期为20-120且大于快周期")
    elif strategy_id == "dmi_confirm":
        if not 20 <= values["ma_period"] <= 120 or not 10 <= values["adx_threshold"] <= 50:
            raise ValueError("DMI均线周期需为20-120, ADX门槛为10-50")
    elif strategy_id == "squeeze_release":
        if not 10 <= values["lookback"] <= 60:
            raise ValueError("收缩回看周期需为10-60")
    elif strategy_id == "supertrend_pullback":
        if (not 5 <= values["atr_period"] <= 30 or not 1 <= values["atr_multiplier"] <= 5
                or not 10 <= values["ema_period"] <= 120):
            raise ValueError("Supertrend ATR周期需为5-30, 倍数为1-5, EMA周期为10-120")
    elif strategy_id == "bb_aroon":
        if not 10 <= values["bb_period"] <= 60 or not 20 <= values["aroon_period"] <= 120:
            raise ValueError("布林周期需为10-60, Aroon周期为20-120")
    elif not 5 <= values["lookback"] <= 120:
        raise ValueError("通道回看周期需为5-120")
    return values.copy()


def minimum_bars(strategy_id: str, values: dict | None = None) -> int:
    params = parameters(strategy_id, values)
    if strategy_id == "dmi_confirm":
        return max(61, params["ma_period"] + 1)
    if strategy_id == "squeeze_release":
        return max(61, 2 * params["lookback"] + 1)
    if strategy_id == "supertrend_pullback":
        return max(61, params["ema_period"] + 1, 3 * params["atr_period"] + 1)
    if strategy_id == "bb_aroon":
        return max(61, params["aroon_period"] + 1, params["bb_period"] + 1)
    return max(61, params.get("slow_period", params.get("lookback", 20)) + 1)
