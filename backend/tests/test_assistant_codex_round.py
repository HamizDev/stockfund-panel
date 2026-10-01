"""Codex transport preserves tool evidence and rejects unsafe decisions."""
import json

import pytest

from app.custom.assistant import codex_round
from app.custom.assistant.tools import build_tool_schemas


def decision(calls=None, text=""):
    return json.dumps({"text": text, "tool_calls": calls or []})


@pytest.mark.parametrize("raw", [
    '{"text":"a","text":"b","tool_calls":[]}',
    '{"text":"a","tool_calls":[]}extra',
    '{"text":"a","tool_calls":[],"command":"rm"}',
    '{"text":null,"tool_calls":[]}',
    decision(),
    decision([{"name": "list_strategies", "arguments": {}}], text="未取数的判断"),
    decision([{"name": "shell", "arguments": {}}]),
    decision([{"name": "get_stock_quote", "arguments": {}}]),
    decision([{"name": "get_stock_quote", "arguments": {"symbols": "600519.SH"}}]),
    decision([{"name": "get_stock_quote", "arguments": {"symbols": [12]}}]),
    decision([{"name": "get_stock_quote", "arguments": {"symbols": ["600519.SH"], "command": "x"}}]),
    decision([{"name": "get_stock_daily", "arguments": {"symbol": "600519.SH", "days": True}}]),
    decision([{"name": "list_factors", "arguments": {"asset_type": "crypto"}}]),
    decision([{"name": "list_factors", "arguments": {"stable_only": "true"}}]),
    decision([{"name": "list_strategies", "arguments": {}}] * 5),
    decision([{"name": "get_stock_quote", "arguments": {"symbols": ["600519.SH"] * 51}}]),
    decision([{"name": "run_backtest", "arguments": {"strategy_id": "x", "params": {"n": float("nan")}}}]),
])
def test_invalid_decision_fails_before_dispatch(raw):
    with pytest.raises(ValueError, match="本轮未执行工具"):
        codex_round.parse_decision(raw, build_tool_schemas())


def test_registered_read_only_decision_is_accepted():
    calls = [{"name": "get_stock_quote", "arguments": {"symbols": ["600519.SH"]}}]
    assert codex_round.parse_decision(decision(calls), build_tool_schemas())["tool_calls"] == calls


async def test_round_preserves_call_ids_and_evidence_in_text_transport(monkeypatch):
    captured = {}

    async def generate(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return decision(text="数据日期是 2026-09-30。")

    monkeypatch.setattr(codex_round, "generate_ai_text", generate)
    history = [
        {"role": "user", "content": "数据日期?"},
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_stock_daily", "arguments": '{"symbol":"600519.SH"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok":true,"result":{"as_of":"2026-09-30"}}'},
    ]
    events = [event async for event in codex_round.stream_codex_round(history, build_tool_schemas())]
    payload = json.loads(captured["messages"][1]["content"])
    assert payload["conversation"] == history
    assert payload["tools"] == build_tool_schemas()
    assert events == [{"type": "text", "delta": "数据日期是 2026-09-30。"}, {"type": "round_end", "tool_calls": [], "finish_reason": "stop"}]


async def test_round_emits_only_validated_tool_calls(monkeypatch):
    async def generate(messages, **kwargs):
        return decision([{"name": "list_strategies", "arguments": {}}])

    monkeypatch.setattr(codex_round, "generate_ai_text", generate)
    events = [event async for event in codex_round.stream_codex_round([], build_tool_schemas())]
    assert events[0]["tool_calls"] == [{"name": "list_strategies", "arguments": "{}"}]
