"""The screener's research plan is opt-in and uses the same dated data."""
import json
from datetime import date

import polars as pl
import pytest

from app.services import ai_provider, stock_analyzer


@pytest.mark.parametrize("enabled", [False, True])
async def test_analysis_selects_plan_prompt_and_preserves_data(monkeypatch, tmp_path, enabled):
    from app.strategy import paper

    class Repo:
        def resolve_asset_type(self, symbol):
            return "etf"

    frame = pl.DataFrame({"date": [date(2026, 9, 30)], "close": [4.12], "raw_close": [4.15]})
    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda repo, symbol: frame)
    monkeypatch.setattr(stock_analyzer, "compute_levels", lambda df: {})
    monkeypatch.setattr(paper, "list_account_ids", lambda data_dir: [])
    captured = {}

    async def stream(messages, **kwargs):
        captured["messages"] = messages
        yield "报告"

    monkeypatch.setattr(ai_provider, "stream_ai_text", stream)
    events = [json.loads(line) async for line in stock_analyzer.analyze_stock_stream(
        Repo(), tmp_path, "510300.SH", research_plan=enabled,
    )]
    assert events[0]["as_of"] == "2026-09-30"
    assert events[0]["close"] == 4.12
    assert captured["messages"][0]["content"] == (
        stock_analyzer._RESEARCH_PLAN_PROMPT if enabled else stock_analyzer._SYSTEM_PROMPT
    )
    assert "2026-09-30" in captured["messages"][1]["content"]
    assert "ETF" in captured["messages"][1]["content"]
    assert events[-1]["type"] == "done"


def test_plan_prompt_preserves_unknowns_and_execution_boundary():
    prompt = stock_analyzer._RESEARCH_PLAN_PROMPT
    for boundary in ("暂无法定价", "不编造金额", "不复权报价", "不能全部", "不会下单", "不保证盈利"):
        assert boundary in prompt


async def test_screener_plan_does_not_read_or_send_account_positions(monkeypatch, tmp_path):
    from app.strategy import paper

    class Repo:
        def resolve_asset_type(self, symbol):
            return "stock"

    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda *a: pl.DataFrame({"close": [10.0]}))
    monkeypatch.setattr(stock_analyzer, "compute_levels", lambda df: {})
    monkeypatch.setattr(stock_analyzer, "_load_financials", lambda *a: {})
    monkeypatch.setattr(paper, "list_account_ids", lambda *a: pytest.fail("Research plan must not read accounts"))

    async def stream(messages, **kwargs):
        assert "account_id" not in messages[1]["content"]
        yield "仅依据行情的计划"

    monkeypatch.setattr(ai_provider, "stream_ai_text", stream)
    events = [json.loads(line) async for line in stock_analyzer.analyze_stock_stream(
        Repo(), tmp_path, "600519.SH", research_plan=True,
    )]
    assert events[-1]["type"] == "done"
