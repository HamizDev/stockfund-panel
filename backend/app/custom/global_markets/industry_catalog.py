# ruff: noqa: RUF001 -- localized Chinese labels and punctuation.
"""Static representative-stock baskets for broad industry themes.

Every entry is a representative stock basket, not an official index. These
baskets are descriptive reference data and do not represent live prices.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

DISCLAIMER = "以下均为代表股票篮子，非官方指数。"
HONG_KONG_TZ = ZoneInfo("Asia/Hong_Kong")


THEMES: tuple[dict[str, str | list[str]], ...] = (
    {"id": "ai_compute", "name": "AI算力", "cn": ["688041.SH", "603019.SH", "000977.SZ"], "us": ["NVDA", "AMD", "AVGO"], "note": "代表股票篮子，非官方指数；涵盖AI芯片、互联与服务器。"},
    {"id": "cpo", "name": "CPO/光通信", "cn": ["300308.SZ", "300502.SZ", "300394.SZ"], "us": ["COHR", "LITE", "AAOI"], "note": "代表股票篮子，非官方指数；涵盖光器件与光通信。"},
    {"id": "pcb", "name": "PCB/电子制造", "cn": ["002463.SZ", "002916.SZ", "300476.SZ"], "us": ["TTMI", "SANM", "FLEX"], "note": "代表股票篮子，非官方指数；美股含PCB及电子制造商。"},
    {"id": "semiconductor", "name": "半导体", "cn": ["688981.SH", "002371.SZ", "688012.SH"], "us": ["NVDA", "AMD", "TSM", "AMAT"], "note": "代表股票篮子，非官方指数；涵盖芯片设计、制造与设备。"},
    {"id": "memory", "name": "存储", "cn": ["603986.SH", "301308.SZ", "688525.SH"], "us": ["MU", "WDC", "SNDK"], "note": "代表股票篮子，非官方指数；涵盖存储芯片与存储设备。"},
    {"id": "data_center", "name": "数据中心", "cn": ["300442.SZ", "603881.SH", "300738.SZ"], "us": ["EQIX", "DLR", "VRT"], "note": "代表股票篮子，非官方指数；涵盖机房、基础设施与温控。"},
    {"id": "cloud", "name": "云计算", "cn": ["688111.SH", "600845.SH", "600588.SH"], "us": ["MSFT", "AMZN", "GOOGL", "ORCL"], "note": "代表股票篮子，非官方指数；涵盖云平台与企业软件。"},
    {"id": "space", "name": "商业航天", "cn": ["600879.SH", "300762.SZ", "600118.SH"], "us": ["RKLB", "RDW", "PL"], "note": "代表股票篮子，非官方指数；涵盖航天制造、发射与遥感。"},
    {"id": "satellite", "name": "卫星", "cn": ["600118.SH", "002025.SZ", "300045.SZ"], "us": ["ASTS", "IRDM", "GSAT"], "note": "代表股票篮子，非官方指数；涵盖卫星制造与通信。"},
    {"id": "robotics", "name": "机器人", "cn": ["002747.SZ", "688017.SH", "300124.SZ"], "us": ["TER", "SYM", "ISRG"], "note": "代表股票篮子，非官方指数；涵盖工业、仓储与医疗机器人。"},
    {"id": "autonomous", "name": "自动驾驶", "cn": ["002920.SZ", "603596.SH", "600699.SH"], "us": ["TSLA", "MBLY", "AUR"], "note": "代表股票篮子，非官方指数；涵盖智能驾驶与自动驾驶技术。"},
    {"id": "nuclear", "name": "核电/核能", "cn": ["601985.SH", "003816.SZ", "601611.SH"], "us": ["CEG", "CCJ", "SMR"], "note": "代表股票篮子，非官方指数；涵盖核电运营、燃料与设备。"},
    {"id": "power_grid", "name": "电网", "cn": ["600406.SH", "600312.SH", "600089.SH"], "us": ["ETN", "PWR", "HUBB", "GEV"], "note": "代表股票篮子，非官方指数；涵盖输配电设备与工程。"},
    {"id": "defense", "name": "军工", "cn": ["600760.SH", "600893.SH", "000768.SZ"], "us": ["LMT", "RTX", "NOC"], "note": "代表股票篮子，非官方指数；涵盖航空、航发与防务承包商。"},
    {"id": "new_energy", "name": "新能源", "cn": ["300750.SZ", "002594.SZ", "601012.SH"], "us": ["ENPH", "FSLR", "TSLA"], "note": "代表股票篮子，非官方指数；覆盖电池、电动车与光伏。"},
    {"id": "solar", "name": "光伏", "cn": ["601012.SH", "300274.SZ", "600438.SH"], "us": ["FSLR", "ENPH", "SEDG"], "note": "代表股票篮子，非官方指数；涵盖光伏制造与逆变器。"},
    {"id": "battery", "name": "锂电/锂资源", "cn": ["300750.SZ", "300014.SZ", "002460.SZ"], "us": ["ALB", "SQM", "LAC"], "note": "代表股票篮子，非官方指数；美股侧偏锂资源企业。"},
    {"id": "petroleum", "name": "石油", "cn": ["601857.SH", "600028.SH", "600938.SH"], "us": ["XOM", "CVX", "COP"], "note": "代表股票篮子，非官方指数；石油相关公司股票，不是油价报价。"},
    {"id": "natural_gas", "name": "天然气", "cn": ["600803.SH", "600256.SH", "601139.SH"], "us": ["LNG", "EQT", "CTRA"], "note": "代表股票篮子，非官方指数；涵盖天然气生产、运输与运营。"},
    {"id": "copper", "name": "铜/有色", "cn": ["600362.SH", "000878.SZ", "601600.SH"], "us": ["FCX", "SCCO", "TECK"], "note": "代表股票篮子，非官方指数；铜及有色公司股票，不是铜价报价。"},
    {"id": "gold", "name": "黄金矿业", "cn": ["601899.SH", "600547.SH", "600489.SH"], "us": ["NEM", "AEM", "AU"], "note": "代表股票篮子，非官方指数；黄金矿业股，不是黄金商品报价。"},
    {"id": "bank", "name": "银行金融", "cn": ["601398.SH", "600036.SH", "601939.SH"], "us": ["JPM", "BAC", "WFC"], "note": "代表股票篮子，非官方指数；涵盖大型商业银行。"},
    {"id": "biopharma", "name": "生物医药", "cn": ["600276.SH", "300760.SZ", "603259.SH"], "us": ["LLY", "JNJ", "AMGN"], "note": "代表股票篮子，非官方指数；涵盖制药与生物技术企业。"},
    {"id": "consumer", "name": "消费", "cn": ["600519.SH", "000858.SZ", "600887.SH"], "us": ["WMT", "COST", "PG", "KO"], "note": "代表股票篮子，非官方指数；涵盖消费品与零售。"},
    {"id": "rare_earth", "name": "稀土/关键矿产", "cn": ["600111.SH", "600392.SH", "300748.SZ"], "us": ["MP", "USAR", "UUUU"], "note": "代表股票篮子，非官方指数；涵盖稀土及关键矿产公司。"},
)


def industry_market(mode: str, now: datetime | None = None) -> str:
    """Choose an industry-basket market; auto uses Hong Kong local clock hours.

    The 08:00-20:00 Hong Kong window is a display-selection rule only. It does
    not indicate exchange sessions, holidays, or real-time/off-hours quotes.
    """
    if mode not in {"auto", "cn", "us"}:
        raise ValueError("mode must be 'auto', 'cn', or 'us'")

    if now is not None and (
        not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
    ):
        raise ValueError("now must be a timezone-aware datetime")

    if mode in {"cn", "us"}:
        return mode

    hong_kong_now = datetime.now(HONG_KONG_TZ) if now is None else now.astimezone(HONG_KONG_TZ)
    return "cn" if 8 <= hong_kong_now.hour < 20 else "us"
