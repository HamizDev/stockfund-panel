"""Fresh date metadata emitted by the per-fund analysis stream."""

from __future__ import annotations

import json

import pytest

from app.custom.fund import analyzer


async def _first_meta(*, nav_rows=None, **kwargs):
    stream = analyzer.analyze_fund_stream(
        nav_rows or [
            {"nav_date": "2026-09-30", "unit_nav": 1.0},
            {"nav_date": "2026-10-01", "unit_nav": 1.01},
        ],
        {},
        "011370.OF",
        "测试基金",
        **kwargs,
    )
    try:
        return json.loads(await anext(stream))
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_analysis_meta_uses_current_nav_and_research_dates_not_candidate_snapshot():
    candidate_snapshot = {
        "nav_date": "2025-01-01",
        "research": {"holdings": {"report_date": "2024-12-31"}},
    }

    meta = await _first_meta(research={"holdings": {"report_date": "2026-06-30"}})

    assert meta["type"] == "meta"
    assert meta["nav_date"] == "2026-10-01"
    assert meta["holdings_report_date"] == "2026-06-30"
    assert meta["nav_date"] != candidate_snapshot["nav_date"]
    assert meta["holdings_report_date"] != candidate_snapshot["research"]["holdings"]["report_date"]


@pytest.mark.asyncio
async def test_analysis_meta_falls_back_to_current_holdings_when_research_has_no_report_date():
    meta = await _first_meta(
        research={"holdings": {"report_date": None}},
        holdings={"report_date": "2026-09-30"},
    )

    assert meta["type"] == "meta"
    assert meta["nav_date"] == "2026-10-01"
    assert meta["holdings_report_date"] == "2026-09-30"


@pytest.mark.asyncio
async def test_analysis_drawdown_uses_research_total_return_risk_and_explains_basis():
    # The unit-NAV drop models an ex-dividend adjustment. The total-return
    # series reports a smaller observed drawdown and is the authoritative risk.
    risk = {
        "status": "ok",
        "basis": "source_return_series",
        "max_drawdown_pct": 4.25,
        "start_date": "2025-10-02",
        "end_date": "2026-10-01",
        "observations": 182,
        "source_url": None,
        "note": "来源累计收益采样可能稀疏, 可能低估每日回撤。",
    }
    meta = await _first_meta(
        nav_rows=[
            {"nav_date": "2026-09-30", "unit_nav": 2.0},
            {"nav_date": "2026-10-01", "unit_nav": 1.0},
        ],
        research={"horizon": "1y", "risk": risk},
    )

    assert meta["stats"]["最大回撤%"] == 4.25
    assert meta["drawdown"] == {**risk, "horizon": "1y"}
    assert "4.25%" in meta["summary"]
    assert "source_return_series" in meta["summary"]
    assert "2025-10-02" in meta["summary"] and "2026-10-01" in meta["summary"]
    assert "182" in meta["summary"] and "采样可能稀疏" in meta["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("research", [None, {}, {"risk": {"status": "unavailable"}}])
async def test_analysis_does_not_fallback_to_unit_nav_drawdown_when_research_risk_missing(research):
    meta = await _first_meta(
        nav_rows=[
            {"nav_date": "2026-09-30", "unit_nav": 2.0},
            {"nav_date": "2026-10-01", "unit_nav": 1.0},
        ],
        research=research,
    )

    assert meta["stats"]["最大回撤%"] is None
    assert meta["drawdown"]["status"] == "unavailable"
    assert meta["drawdown"]["max_drawdown_pct"] is None
    assert "未复权单位净值回撤不作替代" in meta["summary"]


def test_analysis_prompt_forbids_recomputing_drawdown_from_unit_nav():
    assert "仅引用 research.risk" in analyzer._SYSTEM_PROMPT
    assert "不得从未复权单位净值重新计算或替代最大回撤" in analyzer._SYSTEM_PROMPT
