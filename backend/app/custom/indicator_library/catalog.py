# ruff: noqa: RUF001 -- Chinese research labels.
"""Research descriptions are definitions, not measured strategy performance."""

TOPICS = [
    {"id": "supertrend", "name": "超级趋势", "aliases": ["supertrend", "趋势跟踪", "超级趋势"]},
    {"id": "scalping", "name": "日内短线", "aliases": ["scalping", "日内", "短线", "做t"]},
    {"id": "mean-reversion", "name": "均值回归", "aliases": ["mean reversion", "均值回归", "回归"]},
    {"id": "volume", "name": "量价", "aliases": ["volume", "成交量", "量价"]},
    {"id": "vwap", "name": "成交量加权均价", "aliases": ["vwap", "均价", "成交量加权均价"]},
    {"id": "bollingerbands", "name": "布林带", "aliases": ["bollinger bands", "布林带", "布林"]},
    {"id": "rsi", "name": "相对强弱", "aliases": ["rsi", "相对强弱", "超买超卖"]},
    {"id": "macd", "name": "MACD", "aliases": ["macd", "指数平滑异同均线"]},
    {"id": "adx", "name": "趋势强度", "aliases": ["adx", "dmi", "趋势强度", "趋向"]},
    {"id": "squeeze", "name": "波动收缩", "aliases": ["squeeze", "ttm", "波动收缩", "挤压"]},
    {"id": "supportandresistance", "name": "支撑压力", "aliases": ["support resistance", "支撑压力", "支撑", "压力"]},
    {"id": "breakout", "name": "突破", "aliases": ["breakout", "突破", "通道突破"]},
]

RECIPES = [
    {"id": "dmi_confirm", "name": "DMI 连续确认", "kind": "趋势确认",
     "description": "DI 方向、ADX 强度和连续确认组合，过滤一次交叉。",
     "inputs": "OHLC、DI/ADX 周期、确认根数、ADX 门槛",
     "entry": "只用已收盘 K 线确认 DI 方向和趋势强度；下一根才考虑成交。",
     "exit": "方向反转或原策略的退出条件；与费用及止损一起检验。",
     "caution": "横盘反复交叉；ADX 表示强度，不直接表示上涨。"},
    {"id": "squeeze_release", "name": "TTM 收缩释放", "kind": "波动突破",
     "description": "观察布林带与 Keltner 通道的收缩及动量释放。",
     "inputs": "OHLC、布林带/Keltner 周期与倍数、动量周期",
     "entry": "已收盘的收缩释放与动量同向确认，避免把收缩本身当成买点。",
     "exit": "动量反转或原策略的退出条件。",
     "caution": "假突破和跳空会改变成交；不能按图上信号价直接填单。"},
    {"id": "supertrend_pullback", "name": "Supertrend + EMA 回踩", "kind": "趋势回踩",
     "description": "用 ATR 趋势带定方向，再观察 EMA 附近的回踩。",
     "inputs": "OHLC、ATR 周期/倍数、EMA 周期、回踩范围",
     "entry": "收盘保持趋势方向并满足 EMA 回踩条件。",
     "exit": "趋势带翻向或原策略退出条件。",
     "caution": "不同 Supertrend 实现的初值和翻转顺序可能不同，先核对数值。"},
    {"id": "bb_aroon", "name": "布林带 + Aroon 强势回撤", "kind": "强势回撤",
     "description": "结合价格位置与近期高低点的新鲜程度观察回撤。",
     "inputs": "OHLC、布林带周期/倍数、Aroon 周期与门槛",
     "entry": "已收盘的回撤及 Aroon 方向同时满足原研究规则。",
     "exit": "强势失效或原策略退出条件。",
     "caution": "Aroon 依赖最高最低点的时间位置；不代表后续收益概率。"},
]
