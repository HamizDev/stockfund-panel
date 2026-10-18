from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from app.custom.global_markets import industries
from app.custom.global_markets.industry_catalog import THEMES


class PeekOnlyRepo:
    def __init__(self, frame: pl.DataFrame) -> None:
        self.frame = frame

    def peek_enriched_latest(self):
        return self.frame, None

    def get_enriched_latest(self):
        pytest.fail("industry quotes must use the cache-only peek")


def test_cn_quotes_uses_peek_raw_price_and_millisecond_quote_timestamp():
    live_symbol, daily_symbol = THEMES[0]["cn"][:2]
    quote_stamp = int(datetime(2020, 2, 3, 8, 30, tzinfo=UTC).timestamp() * 1000)
    frame = pl.DataFrame(
        {
            "symbol": [live_symbol, daily_symbol, "999999.SH"],
            "name": ["实时样本", "日线样本", "无关标的"],
            "close": [900.0, 901.0, 902.0],
            "raw_close": [12.5, 13.5, 14.5],
            "change_pct": [0.025, -0.01, 0.99],
            "quote_ts": [quote_stamp, None, quote_stamp],
            "date": [date(2026, 10, 18), date(2026, 10, 17), date(2026, 10, 18)],
        }
    )

    quotes = industries.cn_quotes(PeekOnlyRepo(frame))

    assert set(quotes) == {live_symbol, daily_symbol}
    assert quotes[live_symbol] == {
        "symbol": live_symbol,
        "name": "实时样本",
        "price": 12.5,
        "change_pct": 0.025,
        "quote_at_ms": quote_stamp,
        "quote_date": "2020-02-03",
        "basis": "source_quote",
    }
    assert quotes[daily_symbol]["price"] == 13.5
    assert quotes[daily_symbol]["quote_at_ms"] is None
    assert quotes[daily_symbol]["quote_date"] == "2026-10-17"
    assert quotes[daily_symbol]["basis"] == "daily_reference"


def test_aggregate_is_equal_weighted_and_excludes_missing_or_other_day_members():
    first_theme, second_theme = THEMES[:2]
    first, second, missing = first_theme["cn"]
    older_symbol = second_theme["cn"][0]
    latest_day = "2026-10-18"
    older_day = "2026-10-17"
    quotes = {
        first: {"name": "大涨股", "price": 1.0, "change_pct": 0.12, "quote_date": latest_day},
        second: {"name": "小跌股", "price": 100_000.0, "change_pct": -0.02, "quote_date": latest_day},
        older_symbol: {"name": "旧日期", "price": 20.0, "change_pct": 0.9, "quote_date": older_day},
    }

    themes, as_of = industries.aggregate("cn", quotes)
    by_id = {theme["id"]: theme for theme in themes}

    assert as_of == latest_day
    first_result = by_id[first_theme["id"]]
    assert first_result["change_pct"] == pytest.approx(0.05)
    assert first_result["covered"] == 2
    assert first_result["total"] == 3
    assert [member["included"] for member in first_result["members"]] == [True, True, False]
    assert first_result["members"][2]["symbol"] == missing
    assert first_result["members"][2]["change_pct"] is None

    second_result = by_id[second_theme["id"]]
    assert second_result["covered"] == 0
    assert second_result["change_pct"] is None
    assert second_result["members"][0]["included"] is False
    assert second_result["members"][0]["change_pct"] is None


def test_us_industries_request_only_the_curated_basket_without_repo_scans(monkeypatch):
    requested: list[set[str]] = []
    expected = {symbol for theme in THEMES for symbol in theme["us"]}
    sample = THEMES[0]["us"][0]

    def scoped_quotes(symbols: set[str]):
        requested.append(set(symbols))
        return (
            {
                "data": {"fetched_at_ms": 1234},
                "loading": False,
                "stale": False,
                "error": None,
            },
            {
                sample: {
                    "symbol": sample,
                    "name": "篮子成分",
                    "price": 10.0,
                    "change_pct": 0.03,
                    "quote_at_ms": None,
                    "quote_date": "2026-10-18",
                    "basis": "source_quote",
                },
                "NOT_IN_BASKET": {
                    "symbol": "NOT_IN_BASKET",
                    "name": "无关证券",
                    "price": 100.0,
                    "change_pct": 9.0,
                    "quote_at_ms": None,
                    "quote_date": "2026-10-18",
                    "basis": "source_quote",
                },
            },
        )

    class NoDiskRepo:
        def __getattribute__(self, name):
            raise AssertionError(f"US industry aggregation must not access repo.{name}")

    monkeypatch.setattr(industries.industry_quotes, "get", scoped_quotes)

    result = industries.get("us", NoDiskRepo())

    assert requested == [expected]
    assert all(not symbol.endswith((".SH", ".SZ")) for symbol in requested[0])
    assert result["market"] == "us"
    assert result["source"] == "tencent_public"
    first_theme = next(theme for theme in result["themes"] if theme["id"] == THEMES[0]["id"])
    assert first_theme["covered"] == 1
    assert first_theme["change_pct"] == pytest.approx(0.03)


def test_basket_excludes_future_dates_and_invalid_prices():
    symbols = THEMES[0]['cn']
    quotes = {
        symbols[0]: {'price': 2, 'change_pct': .2, 'quote_date': '2999-01-01'},
        symbols[1]: {'price': 0, 'change_pct': .2, 'quote_date': '2020-01-01'},
        symbols[2]: {'price': 4, 'change_pct': -.03, 'quote_date': '2020-01-01'},
    }
    themes, day = industries.aggregate('cn', quotes)
    assert day == '2020-01-01'
    assert themes[0]['covered'] == 1 and themes[0]['change_pct'] == pytest.approx(-.03)
