"""AI 助手工具轮次: 流式 tool_calls 缺 id 时, 回填消息的 tool_call_id 必须与助手消息一致。

部分 OpenAI 兼容网关的流式 tool_calls 分片不带 id (streaming 累积后 id 为空串)。
chat_service 给助手消息的 tool_calls[].id 补了随机 id, 但 role:tool 回填消息的
tool_call_id 仍取原始空串 —— 两者对不上, 下一轮请求会被上游按协议拒绝
(tool 消息找不到对应的 tool_call), 对话在第一次取数后直接失败。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.custom.assistant import chat_service
from app.custom.assistant import tools as assistant_tools


def _script_round(script: list[dict[str, Any]], capture: list[list[dict[str, Any]]]):
    async def fake_round(messages, tool_schemas, *, temperature=0.3, timeout=240.0):
        capture.append([dict(m) for m in messages])
        step = script.pop(0)
        for piece in step.get("text_pieces", []):
            yield {"type": "text", "delta": piece}
        yield {"type": "round_end", "tool_calls": step.get("tool_calls", []), "finish_reason": "stop"}

    return fake_round


async def _fake_execute(name: str, args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"ok": True, "result": {"rows": [], "count": 0}}


async def _run(monkeypatch: pytest.MonkeyPatch, tool_calls: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    captured: list[list[dict[str, Any]]] = []
    script = [{"tool_calls": tool_calls}, {"text_pieces": ["好的"]}]
    monkeypatch.setattr(chat_service, "ai_configured", lambda: True)
    monkeypatch.setattr(chat_service, "is_codex_cli_provider", lambda provider=None: False)
    monkeypatch.setattr(chat_service, "stream_openai_round", _script_round(script, captured))
    monkeypatch.setattr(assistant_tools, "execute_assistant_tool", _fake_execute)

    lines = [line async for line in chat_service.chat_stream(
        history=[{"role": "user", "content": "看看贵州茅台"}],
    )]
    events = [json.loads(line) for line in lines]
    assert [e["type"] for e in events if e["type"] == "error"] == []
    assert len(captured) == 2
    return captured


async def test_missing_tool_call_id_backfill_matches_assistant_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = await _run(monkeypatch, [
        {"id": "", "name": "get_stock_quote", "arguments": '{"symbols": ["600519.SH"]}'},
    ])

    second_round = captured[1]
    assistant_msg, tool_msg = second_round[-2], second_round[-1]
    call_id = assistant_msg["tool_calls"][0]["id"]
    assert call_id
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == call_id


async def test_parallel_calls_without_ids_get_distinct_matching_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = await _run(monkeypatch, [
        {"id": "", "name": "get_stock_quote", "arguments": '{"symbols": ["600519.SH"]}'},
        {"id": "", "name": "get_stock_daily", "arguments": '{"symbol": "600519.SH"}'},
    ])

    second_round = captured[1]
    assistant_msg = second_round[-3]
    tool_msgs = second_round[-2:]
    call_ids = [call["id"] for call in assistant_msg["tool_calls"]]
    assert all(call_ids)
    assert len(set(call_ids)) == 2
    assert [m["tool_call_id"] for m in tool_msgs] == call_ids


async def test_provider_supplied_tool_call_id_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = await _run(monkeypatch, [
        {"id": "call_abc", "name": "get_stock_quote", "arguments": '{"symbols": ["600519.SH"]}'},
    ])

    second_round = captured[1]
    assert second_round[-2]["tool_calls"][0]["id"] == "call_abc"
    assert second_round[-1]["tool_call_id"] == "call_abc"
