"""Codex text-JSON adapter for the bounded AI tool loop."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.custom.assistant import codex_round
from app.services import ai_provider


TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_backtest",
        "description": "Run one bounded strategy backtest.",
        "parameters": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["strategy_id"],
        },
    },
}]

INITIAL_MESSAGES = [
    {"role": "system", "content": "Use the registered strategy tools."},
    {"role": "user", "content": "Backtest and improve ai_test."},
]


def _decision(*, calls: list[dict[str, Any]] | None = None, text: str = "") -> str:
    return json.dumps({"text": text, "tool_calls": calls or []}, ensure_ascii=False)


def _patch_codex(monkeypatch, generate):
    monkeypatch.setattr(ai_provider, "is_codex_cli_provider", lambda: True)
    monkeypatch.setattr(ai_provider, "_resolve_max_tokens", lambda value: value)
    monkeypatch.setattr(ai_provider, "_check_input_budget", lambda *args, **kwargs: None)
    monkeypatch.setattr(codex_round, "generate_ai_text", generate)


@pytest.mark.asyncio
async def test_codex_tool_loop_replays_validated_backtest_evidence_and_unlimited_tokens(monkeypatch):
    backtest_args = {
        "strategy_id": "ai_test",
        "start": "2026-01-01",
        "end": "2026-06-30",
    }
    final_text = "```python\nMETA = {'id': 'ai_test'}\ndef filter(df):\n    return df\n```"
    responses = [
        _decision(calls=[{"name": "run_backtest", "arguments": backtest_args}]),
        _decision(text=final_text),
    ]
    codex_requests: list[dict[str, Any]] = []

    async def fake_generate(messages, **kwargs):
        request = json.loads(messages[1]["content"])
        codex_requests.append({
            "conversation": request["conversation"],
            "tools": request["tools"],
            "max_tokens": kwargs["max_tokens"],
        })
        return responses.pop(0)

    _patch_codex(monkeypatch, fake_generate)
    executions: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        executions.append((name, args))
        return {
            "ok": True,
            "result": {
                "strategy_id": "ai_test",
                "stats": {"sharpe": 1.42, "max_drawdown": -0.08},
            },
        }

    transcript = await ai_provider.generate_ai_text_with_tools(
        INITIAL_MESSAGES,
        TOOLS,
        execute_tool=execute_tool,
        max_tokens=None,
        max_rounds=2,
    )

    assert executions == [("run_backtest", backtest_args)]
    assert [request["max_tokens"] for request in codex_requests] == [None, None]
    assert all(request["tools"] == TOOLS for request in codex_requests)

    second_conversation = codex_requests[1]["conversation"]
    assistant_message, tool_message = second_conversation[-2:]
    tool_call = assistant_message["tool_calls"][0]
    assert tool_call["id"].startswith("codex_")
    assert tool_call["type"] == "function"
    assert tool_call["function"]["name"] == "run_backtest"
    assert json.loads(tool_call["function"]["arguments"]) == backtest_args
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == tool_call["id"]
    tool_payload = json.loads(tool_message["content"])
    assert tool_payload["result"]["stats"] == {"sharpe": 1.42, "max_drawdown": -0.08}

    assert transcript[:2] == INITIAL_MESSAGES
    assert transcript[-3] == assistant_message
    assert transcript[-2] == tool_message
    assert transcript[-1] == {"role": "assistant", "content": final_text}
    assert len(transcript) == 5


@pytest.mark.parametrize(
    "invalid_call",
    [
        {"name": "shell", "arguments": {}},
        {"name": "run_backtest", "arguments": {"strategy_id": True}},
    ],
    ids=["unknown-tool", "invalid-argument-type"],
)
@pytest.mark.asyncio
async def test_codex_rejects_whole_decision_before_executing_any_tool(monkeypatch, invalid_call):
    responses = [_decision(calls=[
        {"name": "run_backtest", "arguments": {"strategy_id": "ai_test"}},
        invalid_call,
    ])]
    calls_to_model = 0

    async def fake_generate(_messages, **_kwargs):
        nonlocal calls_to_model
        calls_to_model += 1
        return responses.pop(0)

    _patch_codex(monkeypatch, fake_generate)
    executions: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        executions.append((name, args))
        return {"ok": True, "result": {}}

    with pytest.raises(ValueError, match="本轮未执行工具"):
        await ai_provider.generate_ai_text_with_tools(
            INITIAL_MESSAGES, TOOLS, execute_tool=execute_tool, max_rounds=2,
        )

    assert calls_to_model == 1
    assert executions == []


@pytest.mark.asyncio
async def test_codex_tool_budget_exhaustion_raises_explicit_error(monkeypatch):
    model_calls = 0

    async def fake_generate(_messages, **_kwargs):
        nonlocal model_calls
        model_calls += 1
        return _decision(calls=[{"name": "run_backtest", "arguments": {"strategy_id": "ai_test"}}])

    _patch_codex(monkeypatch, fake_generate)
    executions: list[str] = []

    async def execute_tool(name: str, _args: dict[str, Any]) -> dict[str, Any]:
        executions.append(name)
        return {"ok": True, "result": {"stats": {"sharpe": 1.0}}}

    with pytest.raises(ValueError, match="已达到本轮工具调用上限"):
        await ai_provider.generate_ai_text_with_tools(
            INITIAL_MESSAGES,
            TOOLS,
            execute_tool=execute_tool,
            max_rounds=1,
        )

    assert model_calls == 1
    assert executions == ["run_backtest"]


@pytest.mark.asyncio
async def test_native_openai_tool_path_still_uses_message_runner_and_returns_transcript(monkeypatch):
    monkeypatch.setattr(ai_provider, "is_codex_cli_provider", lambda: False)
    monkeypatch.setattr(ai_provider, "_resolve_max_tokens", lambda value: value)
    monkeypatch.setattr(ai_provider, "_check_input_budget", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        codex_round,
        "generate_ai_text",
        lambda *_args, **_kwargs: pytest.fail("native OpenAI path must not enter Codex bridge"),
    )

    native_calls = [
        SimpleNamespace(
            id="native_call_1",
            type="function",
            function=SimpleNamespace(
                name="run_backtest",
                arguments=json.dumps({"strategy_id": "ai_test"}),
            ),
        ),
    ]
    runner_requests: list[dict[str, Any]] = []

    async def fake_openai_runner(messages, **kwargs):
        runner_requests.append({"messages": list(messages), **kwargs})
        if len(runner_requests) == 1:
            return SimpleNamespace(content=None, tool_calls=native_calls)
        return SimpleNamespace(content="native final strategy", tool_calls=[])

    monkeypatch.setattr(ai_provider, "_run_openai_message_once", fake_openai_runner)
    executions: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        executions.append((name, args))
        return {"ok": True, "result": {"stats": {"sharpe": 1.25}}}

    transcript = await ai_provider.generate_ai_text_with_tools(
        INITIAL_MESSAGES,
        TOOLS,
        execute_tool=execute_tool,
        max_tokens=None,
        max_rounds=2,
    )

    assert len(runner_requests) == 2
    assert all(request["tools"] == TOOLS for request in runner_requests)
    assert all(request["max_tokens"] is None for request in runner_requests)
    assert runner_requests[0]["messages"] == INITIAL_MESSAGES
    second_messages = runner_requests[1]["messages"]
    assert second_messages[:2] == INITIAL_MESSAGES
    assert second_messages[-1]["role"] == "tool"
    assert second_messages[-1]["tool_call_id"] == "native_call_1"
    assert executions == [("run_backtest", {"strategy_id": "ai_test"})]
    assert transcript[0:2] == INITIAL_MESSAGES
    assert transcript[-3]["tool_calls"][0]["id"] == "native_call_1"
    assert transcript[-2]["tool_call_id"] == "native_call_1"
    assert json.loads(transcript[-2]["content"])["result"]["stats"]["sharpe"] == 1.25
    assert transcript[-1] == {"role": "assistant", "content": "native final strategy"}
    assert transcript == second_messages + [{"role": "assistant", "content": "native final strategy"}]
