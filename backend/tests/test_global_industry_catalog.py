from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.custom.global_markets.industry_catalog import (
    DISCLAIMER,
    THEMES,
    industry_market,
)

EXPECTED_COMPONENTS = {
    "ai_compute": (["688041.SH", "603019.SH", "000977.SZ"], ["NVDA", "AMD", "AVGO"]),
    "cpo": (["300308.SZ", "300502.SZ", "300394.SZ"], ["COHR", "LITE", "AAOI"]),
    "pcb": (["002463.SZ", "002916.SZ", "300476.SZ"], ["TTMI", "SANM", "FLEX"]),
    "semiconductor": (["688981.SH", "002371.SZ", "688012.SH"], ["NVDA", "AMD", "TSM", "AMAT"]),
    "memory": (["603986.SH", "301308.SZ", "688525.SH"], ["MU", "WDC", "SNDK"]),
    "data_center": (["300442.SZ", "603881.SH", "300738.SZ"], ["EQIX", "DLR", "VRT"]),
    "cloud": (["688111.SH", "600845.SH", "600588.SH"], ["MSFT", "AMZN", "GOOGL", "ORCL"]),
    "space": (["600879.SH", "300762.SZ", "600118.SH"], ["RKLB", "RDW", "PL"]),
    "satellite": (["600118.SH", "002025.SZ", "300045.SZ"], ["ASTS", "IRDM", "GSAT"]),
    "robotics": (["002747.SZ", "688017.SH", "300124.SZ"], ["TER", "SYM", "ISRG"]),
    "autonomous": (["002920.SZ", "603596.SH", "600699.SH"], ["TSLA", "MBLY", "AUR"]),
    "nuclear": (["601985.SH", "003816.SZ", "601611.SH"], ["CEG", "CCJ", "SMR"]),
    "power_grid": (["600406.SH", "600312.SH", "600089.SH"], ["ETN", "PWR", "HUBB", "GEV"]),
    "defense": (["600760.SH", "600893.SH", "000768.SZ"], ["LMT", "RTX", "NOC"]),
    "new_energy": (["300750.SZ", "002594.SZ", "601012.SH"], ["ENPH", "FSLR", "TSLA"]),
    "solar": (["601012.SH", "300274.SZ", "600438.SH"], ["FSLR", "ENPH", "SEDG"]),
    "battery": (["300750.SZ", "300014.SZ", "002460.SZ"], ["ALB", "SQM", "LAC"]),
    "petroleum": (["601857.SH", "600028.SH", "600938.SH"], ["XOM", "CVX", "COP"]),
    "natural_gas": (["600803.SH", "600256.SH", "601139.SH"], ["LNG", "EQT", "CTRA"]),
    "copper": (["600362.SH", "000878.SZ", "601600.SH"], ["FCX", "SCCO", "TECK"]),
    "gold": (["601899.SH", "600547.SH", "600489.SH"], ["NEM", "AEM", "AU"]),
    "bank": (["601398.SH", "600036.SH", "601939.SH"], ["JPM", "BAC", "WFC"]),
    "biopharma": (["600276.SH", "300760.SZ", "603259.SH"], ["LLY", "JNJ", "AMGN"]),
    "consumer": (["600519.SH", "000858.SZ", "600887.SH"], ["WMT", "COST", "PG", "KO"]),
    "rare_earth": (["600111.SH", "600392.SH", "300748.SZ"], ["MP", "USAR", "UUUU"]),
}


def test_theme_catalog_has_25_unique_ids_and_canonical_unique_symbols():
    assert len(THEMES) == 25
    assert len({theme["id"] for theme in THEMES}) == 25
    assert {theme["id"] for theme in THEMES} == set(EXPECTED_COMPONENTS)

    for theme in THEMES:
        assert theme["name"]
        assert "代表股票篮子" in theme["note"]
        assert "非官方指数" in theme["note"]
        assert len(theme["cn"]) == len(set(theme["cn"]))
        assert len(theme["us"]) == len(set(theme["us"]))
        assert all(len(code) == 9 and code[6:] in {".SH", ".SZ"} and code[:6].isdigit()
                   for code in theme["cn"])
        assert all(ticker.isascii() and ticker.isupper() for ticker in theme["us"])

    assert "非官方指数" in DISCLAIMER


def test_theme_components_match_the_requested_fixed_baskets():
    for theme in THEMES:
        assert (theme["cn"], theme["us"]) == EXPECTED_COMPONENTS[theme["id"]]


def test_notes_disclose_special_basket_semantics():
    notes = {theme["id"]: theme["note"] for theme in THEMES}
    assert "电子制造" in notes["pcb"]
    assert "偏锂资源" in notes["battery"]
    assert "不是油价报价" in notes["petroleum"]
    assert "不是铜价报价" in notes["copper"]
    assert "不是黄金商品报价" in notes["gold"]


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(7, 59, "us"), (8, 0, "cn"), (19, 59, "cn"), (20, 0, "us")],
)
def test_auto_market_uses_hong_kong_hour_boundaries(hour, minute, expected):
    hk = ZoneInfo("Asia/Hong_Kong")
    now = datetime(2026, 10, 18, hour, minute, tzinfo=hk)
    assert industry_market("auto", now) == expected


def test_auto_market_converts_aware_utc_datetime_to_hong_kong_time():
    assert industry_market("auto", datetime(2026, 10, 18, 0, 0, tzinfo=UTC)) == "cn"
    assert industry_market("auto", datetime(2026, 10, 18, 11, 59, tzinfo=UTC)) == "cn"
    assert industry_market("auto", datetime(2026, 10, 18, 12, 0, tzinfo=UTC)) == "us"
    assert industry_market("auto", datetime(2026, 10, 18, 23, 59, tzinfo=UTC)) == "us"


@pytest.mark.parametrize(("mode", "expected"), [("cn", "cn"), ("us", "us")])
def test_explicit_market_mode_overrides_clock(mode, expected):
    hk = ZoneInfo("Asia/Hong_Kong")
    assert industry_market(mode, datetime(2026, 10, 18, 12, 0, tzinfo=hk)) == expected


def test_market_selection_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        industry_market("auto", datetime(2026, 10, 18, 12, 0))


def test_market_selection_rejects_invalid_mode():
    with pytest.raises(ValueError, match="mode"):
        industry_market("hk")
