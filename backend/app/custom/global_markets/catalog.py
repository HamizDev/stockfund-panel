# ruff: noqa: RUF001 -- localized Chinese market labels.
"""Explicit market identifiers; never infer a mainland instrument from these codes."""
from __future__ import annotations

import re
import unicodedata

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

# Search-only catalog rows. They resolve a requested instrument without adding
# it to the fixed observation pool used by the three-market dashboard.
SEARCH_ONLY = {
    "hk": [("07709.HK", "南方東英SK海力士每日槓桿最多(2x)產品", "etf")],
    "us": [("SKHY", "SK海力士（Nasdaq ADS）", "stock")],
}

# Common Chinese names and abbreviations for the small built-in catalog. These
# are lookup aliases only, not an assertion that the catalogs cover a market.
SEARCH_ALIASES = {
    "hk": {
        "00700.HK": ("腾讯", "騰訊", "Tencent"),
        "09988.HK": ("阿里", "阿里巴巴集团", "阿里巴巴集團", "Alibaba"),
        "03690.HK": ("美团点评", "美團", "Meituan"),
        "01810.HK": ("小米", "Xiaomi"),
        "01211.HK": ("比亚迪", "比亞迪", "BYD"),
        "00005.HK": ("汇丰", "滙豐", "HSBC"),
        "00388.HK": ("港交所", "香港聯合交易所", "HKEX"),
        "00941.HK": ("中移动", "中國移動", "China Mobile"),
        "02800.HK": ("盈富", "恒生指数ETF", "恒生指數ETF", "Tracker Fund"),
        "02828.HK": ("恒生国企ETF", "恒生國企ETF", "国企指数ETF", "國企指數ETF"),
        "03033.HK": ("恒科ETF", "Hang Seng Tech ETF"),
        "02823.HK": ("A50ETF", "富时中国A50ETF", "富時中國A50ETF", "iShares A50"),
        "07709.HK": (
            "07709", "南方海力士", "南方東英海力士", "南方两倍做多海力士",
            "南方兩倍做多海力士", "两倍做多海力士", "兩倍做多海力士",
            "南方2倍做多海力士", "2倍做多海力士", "2x做多海力士",
            "南方两倍海力士", "南方两倍做多SK海力士", "南方东英SK海力士每日杠杆最多(2x)产品",
            "南方東英SK海力士每日槓桿最多(2x)產品",
            "南方两倍做多SK海力士", "南方兩倍做多SK海力士",
            "CSOP SK Hynix Daily Leveraged 2x",
        ),
    },
    "us": {
        "SPY": ("标普500ETF", "標普500ETF", "S&P 500 ETF"),
        "QQQ": ("纳指100ETF", "納指100ETF", "纳斯达克100ETF", "納斯達克100ETF"),
        "IWM": ("罗素2000ETF", "羅素2000ETF", "Russell 2000 ETF"),
        "GLD": ("黄金ETF", "黃金ETF", "Gold ETF"),
        "TLT": ("长期美债ETF", "長期美債ETF", "长期美国国债ETF"),
        "AAPL": ("苹果公司", "蘋果公司", "Apple"),
        "MSFT": ("微软公司", "微軟公司", "Microsoft"),
        "NVDA": ("英伟达", "英偉達", "Nvidia"),
        "TSLA": ("特斯拉", "Tesla"),
        "AMZN": ("亚马逊公司", "亞馬遜公司", "Amazon"),
        "GOOGL": ("谷歌母公司", "Alphabet", "Google"),
        "META": ("Meta平台", "Meta平台公司", "脸书", "臉書", "Facebook"),
        "SKHY": ("海力士", "SK海力士", "SK Hynix", "Hynix", "SK Hynix ADS"),
    },
}

# Normalize common Hong Kong Traditional Chinese input without adding a
# runtime transliteration dependency. The aliases remain scoped to known rows.
_TRADITIONAL_TO_SIMPLIFIED = str.maketrans({
    "兩": "两", "東": "东", "槓": "杠", "桿": "杆", "產": "产",
    "證": "证", "會": "会", "國": "国", "際": "际", "聯": "联",
    "發": "发", "財": "财", "經": "经", "營": "营", "業": "业",
    "銀": "银", "豐": "丰", "滙": "汇", "數": "数", "據": "据",
    "億": "亿", "長": "长", "時": "时", "後": "后", "場": "场",
    "標": "标", "準": "准", "納": "纳", "達": "达", "羅": "罗",
    "蘋": "苹", "偉": "伟", "亞": "亚", "馬": "马", "遜": "逊",
    "臉": "脸", "書": "书", "為": "为", "這": "这", "個": "个",
    "與": "与", "擇": "择", "買": "买", "賣": "卖", "稱": "称",
    "務": "务", "萬": "万", "匯": "汇", "灣": "湾", "臺": "台",
    "島": "岛", "幣": "币",
})


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


def _search_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().translate(_TRADITIONAL_TO_SIMPLIFIED)
    value = value.replace("二倍", "2倍").replace("两倍", "2倍").replace("×", "x")
    return "".join(char for char in value if char.isalnum())


def _catalog_rows(market: str) -> list[tuple[str, str, str]]:
    return [*INDICES[market], *SEEDS[market], *SEARCH_ONLY[market]]


def local_catalog(market: str, query: str = "") -> list[dict]:
    if market not in MARKETS:
        raise ValueError("请选择港股或美股市场")
    query = query.strip()
    query_key = _search_key(query)
    if query and not query_key:
        return []
    rows = SEEDS[market] if not query else _catalog_rows(market)
    items = []
    for symbol, name, kind in rows:
        candidates = (symbol, name, *SEARCH_ALIASES[market].get(symbol, ()))
        if not query or any(
            query_key in _search_key(candidate)
            or (len(query_key) >= 4 and _search_key(candidate) in query_key)
            for candidate in candidates
        ):
            items.append({"symbol": symbol, "name": name, "market": market, "kind": kind})
    return items


def display_name(market: str, symbol: str, fallback: str) -> str:
    safe_name = fallback[:120] if isinstance(fallback, str) and fallback.strip() else symbol
    return next((row[1] for row in _catalog_rows(market) if row[0] == symbol), safe_name)
