# ruff: noqa: RUF001 -- Chinese fixture punctuation is intentional.
from app.data_providers.news import NewsRecord
from app.services.news_rules import associate, classify, normalize_news


def record(source="cls", title="【公司回购】", date="2026-10-07", summary="公司回购公告"):
    return NewsRecord(source, "123", title, summary, date + "T10:00:00+08:00", None)


def test_same_headline_two_sources_preserves_provenance():
    rows = normalize_news([record(), record("eastmoney", "公司回购", summary="公司回购公告，具体内容更长")])
    assert len(rows) == 1
    assert len(rows[0]["origins"]) == 2
    assert "更长" in rows[0]["summary"]
    assert rows[0]["id"] == normalize_news([record("eastmoney", "公司回购")])[0]["id"]


def test_date_and_different_update_not_fuzzy_merged():
    assert len(normalize_news([record(), record(date="2026-10-06"), record(title="公司增加回购金额")])) == 3


def test_reproducible_score_and_risk_precedence():
    result = classify("半导体材料国产替代取得突破，但公司被立案处罚")
    assert result["classifications"]["serenity"] == {
        "category": "risk", "label": "风险·负面", "score": 41, "matched": ["立案", "处罚"],
    }
    assert result["classifications"]["wojianshan"]["category"] == "risk"
    assert result["sentiment"]["direction"] == "mixed"


def test_neutral_is_not_fake_positive():
    result = classify("今天举办新闻发布会")
    assert result["sentiment"]["direction"] == "neutral"
    assert result["classifications"]["serenity"]["score"] == 0
    assert result["classifications"]["serenity"]["matched"] == []


INSTRUMENTS = [
    {"symbol": "600029.SH", "name": "南方航空", "asset_type": "stock"},
    {"symbol": "601398.SH", "name": "工商银行", "asset_type": "stock"},
    {"symbol": "000001.SZ", "name": "平安", "asset_type": "stock"},
    {"symbol": "510300.SH", "name": "沪深300ETF", "asset_type": "etf"},
]


def test_foreign_and_partial_name_dont_match_domestic_stock():
    assert associate("美国西南航空与苹果合作，中国银行业观察，祝你平安。", INSTRUMENTS) == []


def test_exact_name_and_qualified_code_match_with_evidence():
    rows = associate("工商银行公告，股票代码：000001，沪深300ETF 510300.SH", INSTRUMENTS)
    assert {row["symbol"] for row in rows} == {"601398.SH", "000001.SZ", "510300.SH"}
    assert all(row["direct"] and row["basis"] for row in rows)
    assert associate("这里有数字000001但不一定是股票", INSTRUMENTS) == []


def test_index_excluded():
    assert associate("上证指数000001.SH", [{"symbol": "000001.SH", "name": "上证指数", "asset_type": "index"}]) == []


def test_fund_code_and_long_number_do_not_match_same_code_stock():
    assert associate("基金代码：000001，股票代码：1000001", INSTRUMENTS) == []
    assert associate("股票代码：000001ABC，ETF代码：510300XYZ", INSTRUMENTS) == []
    assert associate("ETF代码：510300", INSTRUMENTS)[0]["asset_type"] == "etf"


def test_same_generic_title_conflicting_summaries_are_not_merged():
    rows = normalize_news([record(summary="收到新增订单"), record("eastmoney", summary="公司被立案调查")])
    assert len(rows) == 2
    assert len({row["id"] for row in rows}) == 2
    assert {row["summary"] for row in rows} == {"收到新增订单", "公司被立案调查"}


def test_attribution_after_headline_can_merge_identical_body_prefix():
    rows = normalize_news([record(summary="【公司回购】财联社10月7日电，公司回购公告，下一步注销"),
                           record("eastmoney", summary="【公司回购】公司回购公告")])
    assert len(rows) == 1
    assert len(rows[0]["origins"]) == 2


def test_observed_negative_announcements_are_risks_not_positive_announcements():
    for text in ("公司公告试验未达主要终点", "公司公告被禁止采购，存在违规和失信行为"):
        assert classify(text)["classifications"]["wojianshan"]["category"] == "risk"
        assert classify(text)["sentiment"]["direction"] == "negative"


def test_macro_and_explicit_denials_do_not_match_equity_words():
    for text in ("隔夜逆回购协议规模变化", "美国国债中标利率变化", "公司未被立案，未受处罚"):
        result = classify(text)
        assert result["classifications"]["wojianshan"]["category"] == "other"
        assert result["sentiment"]["direction"] == "neutral"
