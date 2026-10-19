import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.custom.global_markets import (
    hk_symbol_directory,
    provider,
    service,
    symbol_directory,
    symbol_search,
)
from app.custom.global_markets.catalog import MarketDataError, display_name, local_catalog


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
    monkeypatch.setattr(symbol_search, "search", lambda *args: (_ for _ in ()).throw(
        AssertionError("known aliases must not reach Tencent")))
    monkeypatch.setattr(symbol_directory, "search", lambda *args: (_ for _ in ()).throw(
        AssertionError("known aliases must not reach Nasdaq Trader")))
    monkeypatch.setattr(hk_symbol_directory, "load_rows", lambda *args: (_ for _ in ()).throw(
        AssertionError("known aliases must not reach HKEX")))
    us = service.catalog("us", "海力士")
    hk = service.catalog("hk", "南方兩倍做多海力士")
    assert [row["symbol"] for row in us["items"]] == ["SKHY"]
    assert [row["symbol"] for row in hk["items"]] == ["07709.HK"]
    assert not us["warnings"] and not hk["warnings"]


def test_service_uses_tencent_after_yahoo_empty_and_marks_candidates(monkeypatch):
    fallback = [{"symbol": "00981.HK", "name": "中芯国际", "market": "hk", "kind": "stock"}]
    calls = []
    monkeypatch.setattr(provider, "search", lambda *_: [])
    monkeypatch.setattr(symbol_search, "search", lambda market, query: calls.append((market, query)) or fallback)
    monkeypatch.setattr(symbol_directory, "search", lambda *_: pytest.fail("HK must not use US directory"))
    monkeypatch.setattr(hk_symbol_directory, "load_rows", lambda *_: pytest.fail("Tencent result should win"))

    result = service.catalog("hk", "中芯国际")

    assert calls == [("hk", "中芯国际")]
    assert result["items"] == fallback
    assert any("腾讯" in warning and "行情" in warning for warning in result["warnings"])
    assert "quote" not in result["items"][0]


def test_hkex_directory_is_last_hk_search_fallback_and_preserves_source_metadata(monkeypatch):
    query = "陌生ZXQTESTCORP2026"
    fallback = [{
        "symbol": "00981.HK", "name": query, "name_en": "HKEX ONLY",
        "market": "hk", "kind": "stock", "category": "Equity",
        "subcategory": "Equity Securities (Main Board)", "category_zh": "股本",
        "subcategory_zh": "股本證券(主板)", "currency": "HKD",
        "source": "HKEX Full List of Securities", "source_date": "2026-10-20",
    }]
    steps = []
    monkeypatch.setattr(provider, "search", lambda *_: steps.append("yahoo") or [])
    monkeypatch.setattr(symbol_search, "search", lambda *_: steps.append("tencent") or [])
    monkeypatch.setattr(symbol_directory, "search", lambda *_: pytest.fail("HK must not use US directory"))
    monkeypatch.setattr(hk_symbol_directory, "load_rows", lambda: steps.append("hkex") or fallback)

    result = service.catalog("hk", query)

    assert steps == ["yahoo", "tencent", "hkex"]
    assert result["items"] == fallback
    assert any("HKEX" in warning and "不提供行情" in warning for warning in result["warnings"])
    assert "quote" not in result["items"][0]


def test_concurrent_different_hk_queries_share_one_service_directory_download(monkeypatch):
    row = {
        "symbol": "00981.HK", "name": "ZZQALPHAX SAMPLE ISSUER", "name_en": "ZZQALPHAX SAMPLE ISSUER",
        "market": "hk", "kind": "stock", "category": "Equity",
        "subcategory": "Equity Securities (Main Board)", "category_zh": "股本",
        "subcategory_zh": "股本證券(主板)", "currency": "HKD",
        "source": "HKEX Full List of Securities", "source_date": "2026-10-20",
    }
    directory_key = ("hk-symbol-directory",)
    service._CACHE.pop(directory_key, None)
    service._FAILURES.pop(directory_key, None)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def load_rows():
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return [row]

    monkeypatch.setattr(provider, "search", lambda *_: [])
    monkeypatch.setattr(hk_symbol_directory, "load_rows", load_rows)
    monkeypatch.setattr(symbol_search, "search", lambda *_: pytest.fail("English query should skip Tencent"))
    monkeypatch.setattr(symbol_directory, "search", lambda *_: pytest.fail("HK must not use US directory"))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(service.catalog, "hk", "ZZQALPHAX")
            assert entered.wait(2)
            second = executor.submit(service.catalog, "hk", "SAMPLEISSUER")
            release.set()
            first_result = first.result()
            second_result = second.result()

        assert len(calls) == 1
        assert directory_key in service._CACHE
        assert first_result["items"][0]["symbol"] == "00981.HK"
        assert second_result["items"][0]["symbol"] == "00981.HK"
    finally:
        service._CACHE.pop(directory_key, None)
        service._FAILURES.pop(directory_key, None)


def test_service_skips_tencent_when_yahoo_has_results(monkeypatch):
    yahoo = [{"symbol": "AAPL", "name": "Apple", "market": "us", "kind": "stock"}]
    monkeypatch.setattr(provider, "search", lambda *_: yahoo)
    monkeypatch.setattr(symbol_search, "search", lambda *_: pytest.fail("Yahoo result should win"))

    result = service.catalog("us", "distinct yahoo result")

    assert result["items"] == yahoo


def test_tencent_failure_keeps_nasdaq_fallback_available(monkeypatch):
    fallback = [{"symbol": "OPEN", "name": "Open Systems", "market": "us", "kind": "stock"}]
    monkeypatch.setattr(provider, "search", lambda *_: (_ for _ in ()).throw(MarketDataError("Yahoo unavailable")))
    monkeypatch.setattr(symbol_search, "search", lambda *_: (_ for _ in ()).throw(MarketDataError("Tencent unavailable")))
    monkeypatch.setattr(symbol_directory, "search", lambda *_: fallback)

    result = service.catalog("us", "未收录证券名称")

    assert result["items"] == fallback
    assert any("Nasdaq Trader" in warning for warning in result["warnings"])
    assert any("腾讯" in warning for warning in result["warnings"])


def test_english_code_failure_skips_chinese_search_and_keeps_code_probe(monkeypatch):
    monkeypatch.setattr(provider, "search", lambda *_: (_ for _ in ()).throw(MarketDataError("Yahoo unavailable")))
    monkeypatch.setattr(symbol_search, "search", lambda *_: pytest.fail("English code should skip Tencent"))
    monkeypatch.setattr(symbol_directory, "search", lambda *_: [])

    result = service.catalog("us", "IBM")

    assert result["items"] == [{"symbol": "IBM", "name": "代码查询 · IBM", "market": "us", "kind": "stock"}]
