import asyncio
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import strategy as routes
from app.strategy import ai_reviewer as mod


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(mod.ai_provider, "is_codex_cli_provider", lambda: True)
    monkeypatch.setattr(mod.ai_provider, "ai_configured", lambda: True)


@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_review_routes_to_sol_and_reuses_exact_code_only(effort, monkeypatch):
    calls = []
    async def model(messages, **kwargs):
        calls.append((messages, kwargs))
        return "## 复核\n检查 shift(-1) 导致的未来数据。"
    monkeypatch.setattr(mod.ai_provider, "generate_ai_text", model)
    async def run():
        reviewer = mod.StrategyReviewer()
        code = 'def filter(df): return df["close"].shift(-1)'
        first = await reviewer.review(code, effort)
        second = await reviewer.review(code, effort)
        assert first["model"] == "gpt-6.1-sol" and first["reasoning_effort"] == effort
        assert first["code_hash"] == hashlib.sha256(code.encode()).hexdigest()
        assert second["cache_hit"] is True
        assert len(calls) == 1
        assert calls[0][1]["codex_model"] == "gpt-6.1-sol"
        assert calls[0][1]["codex_reasoning_effort"] == effort
        assert json.loads(calls[0][0][1]["content"])["code"] == code
        assert "未提供" in calls[0][0][0]["content"]
        await reviewer.review(code + "\n# revised", effort)
        assert len(calls) == 2
    asyncio.run(run())


def test_review_rejects_non_codex_and_does_not_execute_source(monkeypatch):
    monkeypatch.setattr(mod.ai_provider, "is_codex_cli_provider", lambda: False)
    async def run():
        with pytest.raises(ValueError, match="Codex"):
            await mod.StrategyReviewer().review("raise SystemExit('do not execute')", "xhigh")
    asyncio.run(run())


def test_review_failure_is_sanitized_and_retry_possible(monkeypatch):
    async def bad(*args, **kwargs):
        raise RuntimeError("private upstream error")
    monkeypatch.setattr(mod.ai_provider, "generate_ai_text", bad)
    async def run():
        reviewer = mod.StrategyReviewer()
        with pytest.raises(ValueError, match="复核未完成") as error:
            await reviewer.review("CODE", "xhigh")
        assert "private" not in str(error.value)
        assert reviewer.running is False and not reviewer.cache
        async def good(*args, **kwargs):
            return "review"
        monkeypatch.setattr(mod.ai_provider, "generate_ai_text", good)
        assert (await reviewer.review("CODE", "xhigh"))["content"] == "review"
    asyncio.run(run())


def test_review_rejects_overlapping_requests(monkeypatch):
    gate = asyncio.Event()
    async def model(*args, **kwargs):
        await gate.wait()
        return "review"
    monkeypatch.setattr(mod.ai_provider, "generate_ai_text", model)
    async def run():
        reviewer = mod.StrategyReviewer()
        task = asyncio.create_task(reviewer.review("ONE", "xhigh"))
        await asyncio.sleep(0)
        with pytest.raises(ValueError, match="正在"):
            await reviewer.review("TWO", "max")
        gate.set()
        await task
    asyncio.run(run())


def test_review_api_validates_profile_and_code(monkeypatch):
    class FakeReviewer:
        async def review(self, code, effort):
            return {"code": code, "reasoning_effort": effort}
    app = FastAPI()
    app.state.strategy_reviewer = FakeReviewer()
    app.include_router(routes.router)
    with TestClient(app) as client:
        assert client.post("/api/strategies/ai/review", json={"code": "C", "reasoning_effort": "ultra"}).status_code == 422
        assert client.post("/api/strategies/ai/review", json={"code": "C" * 80_001}).status_code == 422
        result = client.post("/api/strategies/ai/review", json={"code": "C", "reasoning_effort": "max"}).json()
        assert result == {"code": "C", "reasoning_effort": "max"}


def test_review_cancel_resets_gate_and_does_not_cache(monkeypatch):
    async def run():
        entered = asyncio.Event()
        async def model(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(mod.ai_provider, "generate_ai_text", model)
        reviewer = mod.StrategyReviewer()
        task = asyncio.create_task(reviewer.review("CODE"))
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not reviewer.running and not reviewer.cache
    asyncio.run(run())
