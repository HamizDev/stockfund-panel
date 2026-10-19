from app.custom.global_markets import provider, service
from app.custom.global_markets.catalog import display_name, local_catalog


def symbols(market: str, query: str) -> list[str]:
    return [row["symbol"] for row in local_catalog(market, query)]


def test_chinese_hynix_name_resolves_to_us_nasdaq_ads_only():
    result = local_catalog("us", "海力士")
    assert [(row["symbol"], row["kind"]) for row in result] == [("SKHY", "stock")]
    assert result[0]["name"] == "SK海力士（Nasdaq ADS）"  # noqa: RUF001
    assert "000660.KS" not in symbols("us", "海力士")


def test_hk_hynix_leveraged_product_accepts_common_name_and_multiplier_forms():
    for query in (
        "南方两倍做多海力士",
        "南方兩倍做多海力士",
        "南方２倍做多海力士",
        "两倍做多海力士",
        "南方东英SK海力士每日杠杆最多(2x)产品",
        "07709",
    ):
        result = local_catalog("hk", query)
        assert [(row["symbol"], row["kind"]) for row in result] == [("07709.HK", "etf")]


def test_hynix_market_aliases_are_isolated_and_do_not_expand_seed_pool():
    assert symbols("us", "SK Hynix") == ["SKHY"]
    assert symbols("hk", "海力士") == ["07709.HK"]
    assert len(local_catalog("hk")) == 12
    assert len(local_catalog("us")) == 12
    assert "07709.HK" not in symbols("hk", "")
    assert "SKHY" not in symbols("us", "")


def test_common_hk_us_chinese_aliases_use_canonical_catalog_rows():
    assert symbols("hk", "騰訊") == ["00700.HK"]
    assert symbols("hk", "HKEX") == ["00388.HK"]
    assert symbols("us", "英偉達") == ["NVDA"]
    assert symbols("us", "Google") == ["GOOGL"]


def test_punctuation_only_search_does_not_return_the_whole_catalog():
    assert local_catalog("us", "!!!") == []


def test_display_names_cover_search_only_rows():
    assert display_name("hk", "07709.HK", "fallback") == "南方東英SK海力士每日槓桿最多(2x)產品"
    assert display_name("us", "SKHY", "fallback") == "SK海力士（Nasdaq ADS）"  # noqa: RUF001


def test_service_resolves_known_chinese_names_without_remote_search(monkeypatch):
    monkeypatch.setattr(provider, "search", lambda *args: (_ for _ in ()).throw(
        AssertionError("known aliases should resolve locally")))
    us = service.catalog("us", "海力士")
    hk = service.catalog("hk", "南方兩倍做多海力士")
    assert [row["symbol"] for row in us["items"]] == ["SKHY"]
    assert [row["symbol"] for row in hk["items"]] == ["07709.HK"]
    assert not us["warnings"] and not hk["warnings"]
