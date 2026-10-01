"""Bounded parameters for the existing research signal families."""
from __future__ import annotations


def parameters(strategy_id: str, values: dict | None = None) -> dict[str, int]:
    defaults = {"ema_trend": {"fast_period": 20, "slow_period": 60},
                "channel_breakout": {"lookback": 20}}
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
    elif not 5 <= values["lookback"] <= 120:
        raise ValueError("通道回看周期需为5-120")
    return values.copy()


def minimum_bars(strategy_id: str, values: dict | None = None) -> int:
    params = parameters(strategy_id, values)
    return max(61, params.get("slow_period", params.get("lookback", 20)) + 1)
