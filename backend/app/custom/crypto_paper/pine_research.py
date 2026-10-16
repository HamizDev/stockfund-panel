"""Independent research adaptations of public directional and band rule ideas.

Sources and explicit differences are recorded in docs/pine-strategy-research.md.
No Pine execution, exchange credentials, network calls or ledger writes.

Additional rule references: Supertrend + EMA rebound, (c) alorse;
BB + Aroon, (c) relevantLeader16058. Original Pine sources declare MPL-2.0
(https://mozilla.org/MPL/2.0/). Formulas are independently implemented below.
"""
# ruff: noqa: RUF001 -- localized research explanations.
from __future__ import annotations

import math
import statistics

import polars as pl

from app.custom.crypto_paper import ledger, rules
from app.indicators.adx import compute_adx

IDS = frozenset({"dmi_confirm", "squeeze_release", "supertrend_pullback", "bb_aroon"})
SOURCE_ROOT = "https://github.com/Alorse/pinescript-strategies"
SOURCE_COMMIT = "8fb3837ddcd137f1450c3e82d654ff3df24d54d5"
DEFINITIONS = [
    {"id": "dmi_confirm", "name": "DMI 连续确认 · 研究改编",
     "description": "±DI连续3根同向、ADX(14)强度、EMA过滤与蜡烛方向同时确认；其余保持，沿用账户止盈止损。",
     "source_url": SOURCE_ROOT + "/blob/master/strategies/momentum/DMI%20Winner.pine",
     "adaptation": "采用看板统一ADX(14)，区别于原脚本ADX平滑13；合约允许多空，未复现原仓位或绩效。"},
    {"id": "squeeze_release", "name": "TTM 收缩释放 · 研究改编",
     "description": "布林带进入Keltner后释放，配合线性回归动量及EMA方向确认；其余保持，沿用账户止盈止损。",
     "source_url": SOURCE_ROOT + "/blob/master/strategies/momentum/TTM%20Squeeze.pine",
     "adaptation": "借鉴BB/KC和回归动量；本版用收缩释放入场，原脚本用RSI穿越入场，两者成绩不可互换。"},
    {"id": "supertrend_pullback", "name": "Supertrend + EMA 回踩 · 研究改编",
     "description": "Supertrend翻向或顺势EMA回穿给出多空候选；其余保持，沿用账户固定止盈止损。",
     "source_url": SOURCE_ROOT + "/blob/" + SOURCE_COMMIT + "/strategies/trend/Supertrend%20%2B%20EMA%20rebound%20%5BAlorse%5D.pine",
     "adaptation": "ATR采用Wilder平滑，删除原稿持仓价门槛；EMA各根独立比较，前根相等也计入穿越。最多200根窗口近似，不等于全历史Pine结果。"},
    {"id": "bb_aroon", "name": "布林带 + Aroon 强势回撤 · 研究改编",
     "description": "只做多：收盘下穿布林下轨且AroonUp>90入场；下穿上轨或AroonUp<70空仓，其余保持。",
     "source_url": SOURCE_ROOT + "/blob/" + SOURCE_COMMIT + "/strategies/mean-reversion/BB%20%2B%20Aroon.pine",
     "adaptation": "Aroon默认100根（原稿288），布林固定2倍总体标准差；跌破下轨不是反弹确认，沿用账户固定止盈止损。"},
]


def _ema(values: list[float], period: int) -> list[float]:
    result = [values[0]]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result.append(result[-1] + alpha * (value - result[-1]))
    return result


def _ohlc(bars: list[dict]) -> list[dict[str, float]]:
    if not isinstance(bars, list) or not 1 <= len(bars) <= 200:
        raise ValueError("研究计算需要1-200根已收盘K线")
    output = []
    for bar in bars:
        row = {name: float(ledger._num(bar.get(name), positive=True))
               for name in ("open", "high", "low", "close")}
        if (not all(math.isfinite(value) and value <= 1e100 for value in row.values())
                or row["high"] < max(row.values()) or row["low"] > min(row.values())):
            raise ValueError("研究K线OHLC无效")
        output.append(row)
    return output


def _dmi(rows: list[dict], params: dict) -> dict:
    directional = compute_adx(pl.DataFrame(rows))
    adx = directional["adx_14"][-1]
    plus = directional["adx_plus_di_14"].tail(3).to_list()
    minus = directional["adx_minus_di_14"].tail(3).to_list()
    if adx is None or any(value is None for value in plus + minus):
        raise ValueError("DMI历史不足或包含坏报价")
    current = rows[-1]
    average = _ema([row["close"] for row in rows], params["ma_period"])[-1]
    strong = adx > params["adx_threshold"]
    up = all(a > b for a, b in zip(plus, minus, strict=True))
    down = all(a < b for a, b in zip(plus, minus, strict=True))
    long = strong and up and current["close"] > average and current["close"] > current["open"]
    short = strong and down and current["close"] < average and current["close"] < current["open"]
    return {"target": "long" if long else "short" if short else "hold",
            "reason": "连续方向、强度、均线及蜡烛方向已确认" if long or short else "等待连续3根方向、ADX强度、EMA及蜡烛方向同时确认",
            "metrics": {"adx_14": adx, "plus_di": plus[-1], "minus_di": minus[-1], "ema": average},
            "conditions": {"strength": strong, "three_bars_up": up, "three_bars_down": down,
                           "above_ema": current["close"] > average,
                           "bullish_candle": current["close"] > current["open"]}}


def _squeeze(rows: list[dict], params: dict) -> dict:
    length = params["lookback"]
    closes = [row["close"] for row in rows]
    centers = _ema(closes, length)
    tr = [rows[0]["high"] - rows[0]["low"]] + [
        max(row["high"] - row["low"], abs(row["high"] - closes[i - 1]),
            abs(row["low"] - closes[i - 1])) for i, row in enumerate(rows) if i]
    ranges = _ema(tr, length)
    oscillator_input = []
    squeeze = []
    for i in range(length - 1, len(rows)):
        window = rows[i - length + 1:i + 1]
        mean = statistics.mean(closes[i - length + 1:i + 1])
        half_band = 2 * statistics.pstdev(closes[i - length + 1:i + 1])
        squeeze.append(mean + half_band < centers[i] + ranges[i]
                       and mean - half_band > centers[i] - ranges[i])
        midpoint = (max(row["high"] for row in window) + min(row["low"] for row in window)) / 2
        oscillator_input.append(closes[i] - (midpoint + mean) / 2)
    values = oscillator_input[-length:]
    x_mean = (length - 1) / 2
    y_mean = statistics.mean(values)
    slope = sum((i - x_mean) * (value - y_mean) for i, value in enumerate(values)) / sum(
        (i - x_mean) ** 2 for i in range(length))
    momentum = y_mean + slope * x_mean  # endpoint of the causal least-squares line
    release = squeeze[-2] and not squeeze[-1]
    long = release and momentum > 0 and closes[-1] > centers[-1]
    short = release and momentum < 0 and closes[-1] < centers[-1]
    return {"target": "long" if long else "short" if short else "hold",
            "reason": "收缩已释放，动量和EMA方向已确认" if long or short else "等待BB/KC收缩释放与动量、EMA同向",
            "metrics": {"momentum_price": momentum, "ema": centers[-1], "kc_half_width": ranges[-1]},
            "conditions": {"previous_squeeze": squeeze[-2], "current_squeeze": squeeze[-1],
                           "release": release, "positive_momentum": momentum > 0,
                           "above_ema": closes[-1] > centers[-1]}}


def _supertrend_series(rows: list[dict], period: int, multiplier: int) -> list[dict]:
    """Causal hl2 bands; Wilder ATR seeded by the first period TR mean.

    Direction +1 means bullish here (opposite Pine ta.supertrend's sign).
    An initial bearish seed is not an actionable flip during warmup.
    """
    tr = [rows[0]["high"] - rows[0]["low"]] + [
        max(row["high"] - row["low"], abs(row["high"] - rows[i - 1]["close"]),
            abs(row["low"] - rows[i - 1]["close"])) for i, row in enumerate(rows) if i]
    atr = statistics.mean(tr[:period])
    upper = lower = 0.0
    direction = -1
    output = []
    for i in range(period - 1, len(rows)):
        if i >= period:
            atr = (atr * (period - 1) + tr[i]) / period
        center = (rows[i]["high"] + rows[i]["low"]) / 2
        basic_upper, basic_lower = center + multiplier * atr, center - multiplier * atr
        if i == period - 1:
            upper, lower = basic_upper, basic_lower
        else:
            previous_close = rows[i - 1]["close"]
            upper = basic_upper if basic_upper < upper or previous_close > upper else upper
            lower = basic_lower if basic_lower > lower or previous_close < lower else lower
            if direction == -1 and rows[i]["close"] > upper:
                direction = 1
            elif direction == 1 and rows[i]["close"] < lower:
                direction = -1
        output.append({"atr": atr, "supertrend": lower if direction == 1 else upper, "direction": direction})
    return output


def _supertrend(rows: list[dict], params: dict) -> dict:
    series = _supertrend_series(rows, params["atr_period"], params["atr_multiplier"])
    previous, current = series[-2:]
    closes = [row["close"] for row in rows]
    averages = _ema(closes, params["ema_period"])
    flip = previous["direction"] != current["direction"]
    rebound_up = closes[-2] <= averages[-2] and closes[-1] > averages[-1]
    rebound_down = closes[-2] >= averages[-2] and closes[-1] < averages[-1]
    up = current["direction"] == 1
    long = up and (flip or rebound_up)
    short = not up and (flip or rebound_down)
    return {"target": "long" if long else "short" if short else "hold",
            "reason": "Supertrend翻向或顺势EMA回穿已确认" if long or short else "等待Supertrend翻向或顺势EMA回穿",
            "metrics": {"atr": current["atr"], "supertrend": current["supertrend"], "ema": averages[-1]},
            "conditions": {"trend_up": up, "trend_flip": flip, "rebound_up": rebound_up, "rebound_down": rebound_down}}


def _bb_aroon(rows: list[dict], params: dict) -> dict:
    length, aroon_length = params["bb_period"], params["aroon_period"]
    closes = [row["close"] for row in rows]
    previous = closes[-length - 1:-1]
    current = closes[-length:]
    mean, prior_mean = statistics.mean(current), statistics.mean(previous)
    deviation, prior_deviation = 2 * statistics.pstdev(current), 2 * statistics.pstdev(previous)
    upper, lower = mean + deviation, mean - deviation
    # L+1 observations, newest equal high wins: a high L bars ago yields 0.
    highs = [row["high"] for row in rows[-aroon_length - 1:]]
    latest_high = max(range(len(highs)), key=lambda i: (highs[i], i))
    aroon_up = 100 * latest_high / aroon_length
    crossed_lower = closes[-2] >= prior_mean - prior_deviation and closes[-1] < lower
    crossed_upper = closes[-2] >= prior_mean + prior_deviation and closes[-1] < upper
    entry = aroon_up > 90
    exit_signal = aroon_up < 70
    # Degenerate zero-width bands can cross both rails on one bar: exit wins.
    target = "flat" if crossed_upper or exit_signal else "long" if crossed_lower and entry else "hold"
    return {"target": target,
            "reason": ("强势背景下收盘跌破下轨；仍可能继续下跌" if target == "long" else
                       "下穿上轨或Aroon强度转弱，退出多头" if target == "flat" else "等待下轨穿越与Aroon强度确认"),
            "metrics": {"bb_upper": upper, "bb_middle": mean, "bb_lower": lower, "aroon_up": aroon_up},
            "conditions": {"crossed_below_lower": crossed_lower, "crossed_below_upper": crossed_upper,
                           "aroon_entry": entry, "aroon_exit": exit_signal}}


def analyse(strategy_id: str, bars: list[dict], values: dict | None = None) -> dict:
    params = rules.parameters(strategy_id, values)
    if strategy_id not in IDS:
        raise ValueError("不是新增研究策略")
    if len(bars) < rules.minimum_bars(strategy_id, params):
        raise ValueError("研究策略的已收盘历史不足")
    rows = _ohlc(bars)
    calculation = {"dmi_confirm": _dmi, "squeeze_release": _squeeze,
                   "supertrend_pullback": _supertrend, "bb_aroon": _bb_aroon}[strategy_id]
    result = calculation(rows, params)
    if not all(math.isfinite(value) for value in result["metrics"].values()):
        raise ValueError("研究统计超出可计算范围")
    return result
