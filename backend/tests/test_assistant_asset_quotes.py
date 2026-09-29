"""AI 助手行情类工具按资产类型取快照: ETF / 指数不能永远「未匹配到行情」。

quote_service.get_quotes_compat 只读股票 enriched 缓存; ETF 与指数各有独立缓存
(repo.get_enriched_latest_asset, 自选页 /api/watchlist/enriched 即按此分流)。
get_stock_quote / get_watchlist / get_lots 只查股票缓存 → ETF、指数查不到价格:
单查 510300.SH 返回「未匹配到行情」, 自选/持仓里的 ETF 行 close 与 change_pct 全是 null。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import pytest

from app.custom.assistant import tools as assistant_tools

_ASSET_TYPES = {"510300.SH": "etf", "000300.SH": "index"}


class _AssetRepo:
    """repo 桩: 股票之外的 ETF / 指数各有独立 enriched 缓存 (与 KlineRepository 同接口)。"""

    def __init__(self) -> None:
        self.loaded: list[str] = []

    def get_name_map(self, symbols=None) -> dict[str, str]:
        return {"600519.SH": "贵州茅台", "510300.SH": "沪深300ETF", "000300.SH": "沪深300"}

    def resolve_asset_type(self, symbol: str) -> str:
        return _ASSET_TYPES.get(symbol, "stock")

    def get_enriched_latest_asset(self, asset_type: str, refresh: bool = True) -> tuple[pl.DataFrame, date | None]:
        self.loaded.append(asset_type)
        if asset_type == "etf":
            return pl.DataFrame({
                "symbol": ["159915.SZ", "510300.SH"],
                "close": [2.301, 4.120],
                "prev_close": [2.280, 4.080],
                "change_pct": [0.0092, 0.0098],
                "amount": [1.2e9, 3.4e9],
                "ma5": [2.29, 4.10],
            }), date(2026, 9, 18)
        if asset_type == "index":
            return pl.DataFrame({
                "symbol": ["000300.SH"],
                "close": [4012.5],
                "prev_close": [3990.1],
                "change_pct": [0.0056],
                "amount": [3.1e11],
            }), date(2026, 9, 18)
        return pl.DataFrame(), None

    def get_minute(self, symbol, trade_date, asset_type="stock") -> pl.DataFrame:
        return pl.DataFrame()


class _StockOnlyQuoteService:
    """get_quotes_compat 与真实实现一致: 只含股票 enriched 缓存。"""

    def get_quotes_compat(self) -> pl.DataFrame:
        return pl.DataFrame({
            "symbol": ["600519.SH", "300750.SZ"],
            "close": [1500.0, 200.5],
            "prev_close": [1481.8, 210.0],
            "change_pct": [0.0123, -0.0452],
            "amount": [3.0e9, 5.0e9],
        })


def _ctx(repo: _AssetRepo, tmp_path=None) -> assistant_tools.ToolContext:
    return assistant_tools.ToolContext.build(
        repo=repo, quote_service=_StockOnlyQuoteService(), data_dir=tmp_path,
    )


async def test_get_stock_quote_finds_etf_snapshot() -> None:
    payload = await assistant_tools.execute_assistant_tool(
        "get_stock_quote", {"symbols": ["510300.SH"]}, _ctx(_AssetRepo()),
    )

    assert payload["ok"] is True
    result = payload["result"]
    assert result["count"] == 1
    row = result["rows"][0]
    assert row["symbol"] == "510300.SH"
    assert row["name"] == "沪深300ETF"
    assert row["close"] == 4.12
    assert row["change_pct"] == 0.0098


async def test_get_stock_quote_mixes_stock_etf_and_index() -> None:
    payload = await assistant_tools.execute_assistant_tool(
        "get_stock_quote", {"symbols": ["600519.SH", "510300.SH", "000300.SH"]}, _ctx(_AssetRepo()),
    )

    assert payload["ok"] is True
    rows = {r["symbol"]: r for r in payload["result"]["rows"]}
    assert set(rows) == {"600519.SH", "510300.SH", "000300.SH"}
    assert rows["600519.SH"]["close"] == 1500.0
    assert rows["510300.SH"]["change_pct"] == 0.0098
    assert rows["000300.SH"]["close"] == 4012.5


async def test_stock_only_query_does_not_load_etf_or_index_cache() -> None:
    repo = _AssetRepo()
    payload = await assistant_tools.execute_assistant_tool(
        "get_stock_quote", {"symbols": ["600519.SH", "300750.SZ"]}, _ctx(repo),
    )

    assert payload["result"]["count"] == 2
    assert repo.loaded == []


class _ColdRepo(_AssetRepo):
    """ETF / 指数缓存未焐热 (空表): 应安静降级为「未匹配到行情」, 不报错。"""

    def get_enriched_latest_asset(self, asset_type: str, refresh: bool = True) -> tuple[pl.DataFrame, date | None]:
        self.loaded.append(asset_type)
        return pl.DataFrame(), None


class _EmptyQuoteService:
    def get_quotes_compat(self) -> pl.DataFrame:
        return pl.DataFrame()


async def test_cold_caches_degrade_to_no_match() -> None:
    repo = _ColdRepo()
    ctx = assistant_tools.ToolContext.build(repo=repo, quote_service=_EmptyQuoteService())
    payload = await assistant_tools.execute_assistant_tool(
        "get_stock_quote", {"symbols": ["510300.SH", "000300.SH"]}, ctx,
    )

    assert payload["ok"] is True
    assert payload["result"]["count"] == 0
    assert sorted(repo.loaded) == ["etf", "index"]


async def test_get_watchlist_merges_etf_and_index_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import watchlist as watchlist_service

    monkeypatch.setattr(watchlist_service, "list_symbols", lambda: [
        {"symbol": "600519.SH", "note": ""},
        {"symbol": "510300.SH", "note": "宽基"},
        {"symbol": "000300.SH", "note": ""},
    ])
    payload = await assistant_tools.execute_assistant_tool("get_watchlist", {}, _ctx(_AssetRepo()))

    assert payload["ok"] is True
    rows = {r["symbol"]: r for r in payload["result"]["rows"]}
    assert rows["600519.SH"]["change_pct"] == 0.0123
    assert rows["510300.SH"]["close"] == 4.12
    assert rows["510300.SH"]["change_pct"] == 0.0098
    assert rows["000300.SH"]["change_pct"] == 0.0056


async def test_get_lots_merges_etf_price(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.strategy import lots as lots_module

    def fake_load_all(data_dir) -> list[dict[str, Any]]:
        return [{"symbol": "510300.SH", "qty": 1000, "cost_price": 3.95, "buy_date": "2026-09-01"}]

    monkeypatch.setattr(lots_module, "load_all", fake_load_all)
    payload = await assistant_tools.execute_assistant_tool("get_lots", {}, _ctx(_AssetRepo(), tmp_path))

    assert payload["ok"] is True
    row = payload["result"]["rows"][0]
    assert row["symbol"] == "510300.SH"
    assert row["close"] == 4.12
    assert row["change_pct"] == 0.0098
