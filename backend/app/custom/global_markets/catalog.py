# ruff: noqa: RUF001 -- localized Chinese market labels.
"""Explicit market identifiers; never infer a mainland instrument from these codes."""
from __future__ import annotations

import re

MARKETS = {
    "hk": {"name": "港股", "currency": "HKD", "timezone": "Asia/Hong_Kong"},
    "us": {"name": "美股", "currency": "USD", "timezone": "America/New_York"},
}

# Only these verified index identifiers bypass stock-code syntax. They are
# genuine indices, not ETFs or a proxy for whole-market breadth.
INDICES = {
    'hk': [('^HSI', '恒生指数', 'index'), ('^HSCE', '恒生中国企业指数', 'index')],
    'us': [('^GSPC', '标普500', 'index'), ('^DJI', '道琼斯', 'index'),
           ('^IXIC', '纳斯达克综合', 'index'), ('^RUT', '罗素2000', 'index')],
}

# Observation shortcuts, not a ranking, recommendation, or automatically traded universe.
SEEDS = {
    "hk": [
        ("00700.HK", "腾讯控股", "stock"), ("09988.HK", "阿里巴巴", "stock"),
        ("03690.HK", "美团", "stock"), ("01810.HK", "小米集团", "stock"),
        ("01211.HK", "比亚迪股份", "stock"), ("00005.HK", "汇丰控股", "stock"),
        ("00388.HK", "香港交易所", "stock"), ("00941.HK", "中国移动", "stock"),
        ("02800.HK", "盈富基金", "etf"), ("02828.HK", "恒生中国企业ETF", "etf"),
        ("03033.HK", "南方恒生科技ETF", "etf"), ("02823.HK", "安硕富时中国A50ETF", "etf"),
    ],
    "us": [
        ("SPY", "标普500ETF", "etf"), ("QQQ", "纳斯达克100ETF", "etf"),
        ("IWM", "罗素2000ETF", "etf"), ("GLD", "黄金ETF", "etf"),
        ("TLT", "长期美国国债ETF", "etf"), ("AAPL", "苹果", "stock"),
        ("MSFT", "微软", "stock"), ("NVDA", "英伟达", "stock"),
        ("TSLA", "特斯拉", "stock"), ("AMZN", "亚马逊", "stock"),
        ("GOOGL", "谷歌", "stock"), ("META", "Meta平台", "stock"),
    ],
}


class MarketDataError(Exception):
    """Safe user-facing upstream failure, without response bodies or credentials."""


def normalize_symbol(market: str, value: str) -> str:
    if market not in MARKETS:
        raise ValueError("请选择港股或美股市场")
    if not isinstance(value, str):
        raise ValueError("代码格式无效")
    value = value.strip().upper()
    if any(row[0] == value for row in INDICES[market]):
        return value
    if market == "hk":
        value = value.removesuffix(".HK")
        if not re.fullmatch(r"[0-9]{1,5}", value) or int(value) == 0:
            raise ValueError("港股代码应为1至5位数字，可带.HK后缀")
        return value.zfill(5) + ".HK"
    if (not re.fullmatch(r"[A-Z]{1,8}(?:[-.][A-Z]{1,2})?", value)
            or value.endswith((".HK", ".SH", ".SZ", ".BJ", ".SS"))):
        raise ValueError("美股代码格式无效，请勿输入A股或港股代码")
    return value.replace(".", "-")


def yahoo_symbol(market: str, symbol: str) -> str:
    canonical = normalize_symbol(market, symbol)
    if canonical.startswith('^'):
        return canonical
    if market == "hk":
        return canonical.removesuffix(".HK").lstrip("0").zfill(4) + ".HK"
    return canonical


def local_catalog(market: str, query: str = "") -> list[dict]:
    if market not in MARKETS:
        raise ValueError("请选择港股或美股市场")
    query = query.strip().casefold()
    return [{"symbol": symbol, "name": name, "market": market, "kind": kind}
            for symbol, name, kind in ([*INDICES[market], *SEEDS[market]] if query else SEEDS[market])
            if not query or query in symbol.casefold() or query in name.casefold()]


def display_name(market: str, symbol: str, fallback: str) -> str:
    safe_name = fallback[:120] if isinstance(fallback, str) and fallback.strip() else symbol
    return next((row[1] for row in [*INDICES[market], *SEEDS[market]] if row[0] == symbol), safe_name)
