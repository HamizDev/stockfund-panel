"""Small, explicit mainland ETF research pools and low frequency signals.

The commission is the user's supplied broker rate, not a broker API quote.
Only verified pool members receive T+0 / 20% rules; unknown ETFs stay T+1.
"""
from __future__ import annotations

import numpy as np

from app.backtest.matrix import make_signal_matrix, matrix_feature

COMMISSION_PCT = 0.0005
ENTRY_PCT = 80
SLIPPAGE_BPS = 5
BUDGETS = (5_000, 20_000)
# Exchange rules: https://etf.sse.com.cn/fund/quertion/
# 20%: https://www.sse.com.cn/assortment/fund/star/ and SZSE 2020-08-24 fund list.
POOL = (
    ("510050.SH", "上证50ETF", "宽基", False, .10),
    ("510300.SH", "沪深300ETF", "宽基", False, .10),
    ("510500.SH", "中证500ETF", "宽基", False, .10),
    ("512100.SH", "中证1000ETF", "宽基", False, .10),
    ("159915.SZ", "创业板ETF", "宽基", False, .20),
    ("588000.SH", "科创50ETF", "宽基", False, .20),
    ("512000.SH", "券商ETF", "行业", False, .10),
    ("512480.SH", "半导体ETF", "行业", False, .10),
    ("512010.SH", "医药ETF", "行业", False, .10),
    ("512800.SH", "银行ETF", "行业", False, .10),
    ("159928.SZ", "消费ETF", "行业", False, .10),
    ("511010.SH", "国债ETF", "债券", True, .10),
    ("511260.SH", "十年国债ETF", "债券", True, .10),
    ("511360.SH", "短融ETF", "债券", True, .10),
    ("518880.SH", "黄金ETF", "黄金", True, .10),
)
FAMILIES = {
    "core": {"strategy_id": "etf_core_trend", "name": "ETF 宽基配置趋势",
             "description": "宽基、债券、黄金小池；MA20高于MA60、价格高于MA60且20日动量为正时，选择动量最强的一只；跌破MA60退出。"},
    "sector": {"strategy_id": "etf_sector_trend", "name": "ETF 行业趋势",
               "description": "券商、半导体、医药、银行、消费小池；使用同一趋势门槛，选择动量最强的一只；跌破MA60退出。"},
}


def symbols_for(family: str) -> tuple[str, ...]:
    return tuple(symbol for symbol, _, category, _, _ in POOL
                 if (category == "行业") == (family == "sector"))


def trading_rule(symbol: str) -> tuple[bool, float]:
    return next(((t0, limit) for code, _, _, t0, limit in POOL if code == symbol), (False, .10))


class ETFTrendStrategy:
    def __init__(self, family: str):
        self.family = family

    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "volume", "raw_close"})

    def required_warmup_bars(self, params: dict) -> int:
        return 60

    def compute_signals(self, market, params: dict):
        pool = np.array([symbol in symbols_for(self.family) for symbol in market.symbols])
        ma20 = matrix_feature(market, "ma20")
        ma60 = matrix_feature(market, "ma60")
        momentum = matrix_feature(market, "momentum_20d")
        valid = pool[None, :] & market.tradable & np.isfinite(momentum)
        entry = valid & (market.close > ma60) & (ma20 > ma60) & (momentum > 0)
        ticket = float(params.get("max_ticket", 0))
        if ticket > 0:
            # Raw execution price; never size lots using an adjusted signal price.
            raw = market.fields.get("raw_close")
            if raw is None:
                entry[:] = False
            else:
                one_lot = np.round(raw * (1 + SLIPPAGE_BPS / 10_000), 4) * 100
                cost = np.round(one_lot + np.maximum(np.round(one_lot * COMMISSION_PCT, 2), 5), 2)
                entry &= np.isfinite(cost) & (cost <= ticket)
        # A small deterministic pool, one strongest eligible entry per bar.
        score = np.where(entry, momentum, -np.inf)
        if market.shape[1]:
            top = np.argmax(score, axis=1)
            keep = np.zeros(market.shape, dtype=bool)
            keep[np.arange(market.shape[0]), top] = np.any(entry, axis=1)
            entry &= keep
        # Exits are not restricted by affordability or the current entry ranking.
        exit_ = pool[None, :] & market.tradable & (market.close < ma60)
        return make_signal_matrix(
            market.shape, entry=entry.astype(np.uint8), exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_etf_trend_entry",),
            exit_signal_ids=("signal_etf_trend_exit",),
        )


def strategy_meta(family: str) -> dict:
    return {"id": FAMILIES[family]["strategy_id"], "name": FAMILIES[family]["name"],
            "description": FAMILIES[family]["description"], "tags": ["ETF", "日线", "小资金"],
            "asset_types": ["etf"], "timeframes": ["1d"],
            "params": [{"id": "max_ticket", "label": "单笔预算上限（元，0不限）", "type": "float",
                        "default": 0, "min": 0, "max": 1_000_000, "step": 100}],
            "scoring": {"momentum_20d": 1.0}, "order_by": "score", "descending": True, "limit": 1}
