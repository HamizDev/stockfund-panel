"""AI 助手单只行情附带的分时小图, 必须与行情快照属于同一交易日。

_intraday_chart_payload 用服务器本地 date.today() 读分钟K: 周末/节假日/开盘前
行情快照停在最近交易日 (周五), 分钟分区却按「今天」去查 → 永远为空, 分时图不附。
图的昨收基准线取自快照行, 分时序列也应取快照所在交易日。
"""
from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from app.custom.assistant import tools as assistant_tools

_SNAPSHOT_DATE = date(2026, 9, 18)  # 周五: 周末问股时 enriched 快照停在这一天


class _SnapshotRepo:
    def __init__(self, snapshot_date: date | None) -> None:
        self.snapshot_date = snapshot_date
        self.requested: list[date] = []

    def get_name_map(self, symbols=None) -> dict[str, str]:
        return {"600519.SH": "贵州茅台"}

    def resolve_asset_type(self, symbol: str) -> str:
        return "stock"

    def get_enriched_latest_asset(self, asset_type: str, refresh: bool = True):
        return pl.DataFrame({"symbol": ["600519.SH"]}), self.snapshot_date

    def get_minute(self, symbol, trade_date, asset_type="stock") -> pl.DataFrame:
        self.requested.append(trade_date)
        return pl.DataFrame({
            "datetime": [datetime.combine(trade_date, datetime.min.time()).replace(hour=9, minute=m) for m in (31, 32)],
            "close": [1490.0, 1500.0],
            "volume": [120.0, 80.0],
        }) if trade_date in (_SNAPSHOT_DATE, date(2026, 9, 21)) else pl.DataFrame()


class _QuoteService:
    def get_quotes_compat(self) -> pl.DataFrame:
        return pl.DataFrame({
            "symbol": ["600519.SH"],
            "close": [1500.0],
            "prev_close": [1481.8],
            "change_pct": [0.0123],
        })


async def test_chart_uses_snapshot_trade_date() -> None:
    repo = _SnapshotRepo(_SNAPSHOT_DATE)
    ctx = assistant_tools.ToolContext.build(repo=repo, quote_service=_QuoteService())
    payload = await assistant_tools.execute_assistant_tool("get_stock_quote", {"symbols": ["600519.SH"]}, ctx)

    assert payload["ok"] is True
    assert repo.requested == [_SNAPSHOT_DATE]
    chart = payload["result"]["charts"][0]
    assert chart["kind"] == "intraday"
    assert chart["prev_close"] == 1481.8
    assert chart["points"] == [["09:31", 1490.0, 120.0], ["09:32", 1500.0, 80.0]]


async def test_chart_falls_back_to_beijing_today_without_snapshot_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assistant_tools, "cn_today", lambda: date(2026, 9, 21), raising=False)
    repo = _SnapshotRepo(None)
    ctx = assistant_tools.ToolContext.build(repo=repo, quote_service=_QuoteService())
    payload = await assistant_tools.execute_assistant_tool("get_stock_quote", {"symbols": ["600519.SH"]}, ctx)

    assert repo.requested == [date(2026, 9, 21)]
    assert payload["result"]["charts"][0]["points"][0] == ["09:31", 1490.0, 120.0]
