"""Fresh date metadata emitted by the per-fund analysis stream."""

from __future__ import annotations

import json

import pytest

from app.custom.fund import analyzer


async def _first_meta(**kwargs):
    stream = analyzer.analyze_fund_stream(
        [
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
