import pytest

from app.custom.assistant import tools
from app.custom.fund import service


async def test_fund_tool_reuses_bounded_dated_context(monkeypatch):
    called = []
    research = {"thscode": "011370.OF", "nav_date": "2026-09-30", "missing_fields": ["nav"], "holdings": {"items": [], "related_reports": []}}
    monkeypatch.setattr(service, "fund_research", lambda code, horizon: called.append((code, horizon)) or research)
    payload = await tools.execute_assistant_tool("get_fund_research", {"thscode": "011370", "horizon": "1y"}, tools.ToolContext.build())
    assert payload["ok"]
    assert payload["result"]["nav_date"] == "2026-09-30"
    assert called == [("011370.OF", "1y")]
    assert "2026-09-30" in tools.summarize_tool_result("get_fund_research", payload)


@pytest.mark.parametrize("args", [{"thscode": "../auth.json"}, {"thscode": "510300.SH"}, {"thscode": "011370.OF", "horizon": "10y"}])
async def test_fund_tool_rejects_invalid_inputs_before_fetch(monkeypatch, args):
    def unexpected(*args):
        raise AssertionError("invalid input must not fetch")

    monkeypatch.setattr(service, "fund_research", unexpected)
    result = await tools.execute_assistant_tool("get_fund_research", args, tools.ToolContext.build())
    assert result["ok"] is False
