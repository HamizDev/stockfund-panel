import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.news import routes
from app.data_providers.news import NewsFeed, NewsRecord
from app.services import news_service as mod

STAMP = "2026-10-07T10:00:00+08:00"
ITEM = NewsRecord("cls", "123", "工商银行公告回购", "工商银行公告回购详情", STAMP, "https://www.cls.cn/detail/123")


@pytest.fixture(autouse=True)
def fixed_ai(monkeypatch):
    monkeypatch.setattr(mod, "cn_today", lambda: date(2026, 10, 7))
    monkeypatch.setattr(mod.ai_provider, "current_ai_provider", lambda: "codex_cli")
    monkeypatch.setattr(mod.ai_provider, "current_ai_model", lambda: "gpt-6.1-sol")
    monkeypatch.setattr(mod.ai_provider, "is_codex_cli_provider", lambda: True)
    monkeypatch.setattr(mod.ai_provider, "current_codex_reasoning_effort", lambda: "xhigh")
    monkeypatch.setattr(mod.ai_provider, "ai_configured", lambda: True)


async def good_fetch(source):
    return NewsFeed(source, "ok" if source == "cls" else "empty", [ITEM] if source == "cls" else [], STAMP)


def feed(svc):
    return svc.feed([{"symbol": "601398.SH", "name": "工商银行", "asset_type": "stock"}], ["601398.SH"])


def test_refresh_coalesces_and_publishes_atomically(tmp_path):
    async def run():
        calls = []
        async def fetch(source):
            calls.append(source)
            await asyncio.sleep(0.01)
            return await good_fetch(source)
        svc = mod.NewsService(tmp_path, fetch)
        await asyncio.gather(svc.refresh(), svc.refresh(), svc.refresh(True))
        assert sorted(calls) == ["cls", "eastmoney"]
        snapshot = feed(svc)
        assert snapshot["summary"]["today_count"] == 1
        assert snapshot["related_symbols"] == ["601398.SH"]
        assert snapshot["items"][0]["associations"][0]["symbol"] == "601398.SH"
        assert json.loads((tmp_path / "news/snapshot.json").read_text(encoding="utf-8"))["cls"]["items"]
    asyncio.run(run())


def test_partial_failure_retains_old_cache_and_original_time(tmp_path):
    async def run():
        svc = mod.NewsService(tmp_path, good_fetch)
        await svc.refresh()
        async def failed(source):
            return NewsFeed(source, "unavailable", [], "2026-10-07T11:00:00+08:00", "connection failed")
        svc.fetcher = failed
        svc.last_attempt = float("-inf")
        await svc.refresh(True)
        result = feed(svc)
        assert result["items"][0]["published_at"] == STAMP
        assert result["sources"][0]["status"] == "stale"
        assert result["sources"][0]["fetched_at"] == STAMP
        assert result["sources"][1]["status"] == "unavailable"
    asyncio.run(run())


def test_reload_snapshot_is_stale_until_verified_and_bad_cache_is_safe(tmp_path):
    asyncio.run(mod.NewsService(tmp_path, good_fetch).refresh())
    svc = mod.NewsService(tmp_path, good_fetch)
    assert feed(svc)["sources"][0]["status"] == "stale"
    (tmp_path / "news/snapshot.json").write_text('{"cls":{"items":42,"fetched_at":"2026-10-07T10:00:00+08:00"}}')
    assert mod.NewsService(tmp_path).feeds == {}


def test_unknown_article_and_empty_today_fail_before_model(tmp_path):
    async def run():
        svc = mod.NewsService(tmp_path, good_fetch)
        with pytest.raises(ValueError, match="当日"):
            await svc.analyze("daily", feed(svc))
        with pytest.raises(ValueError, match="不在"):
            await svc.analyze("a" * 24, feed(svc))
    asyncio.run(run())


def test_unconfigured_does_not_start_job(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.ai_provider, "ai_configured", lambda: False)
    async def run():
        svc = mod.NewsService(tmp_path, good_fetch)
        await svc.refresh()
        result = await svc.analyze("daily", feed(svc))
        assert result["status"] == "unconfigured"
        assert svc.tasks == {}
    asyncio.run(run())


def test_ai_coalesces_persists_and_cache_key_ignores_fetch_time(tmp_path, monkeypatch):
    async def run():
        calls = []
        gate = asyncio.Event()
        async def model(messages, **kwargs):
            calls.append(messages)
            await gate.wait()
            return "### 事实\n工商银行公告回购。\n### 核对\n查原文。"
        monkeypatch.setattr(mod.ai_provider, "generate_ai_text", model)
        svc = mod.NewsService(tmp_path, good_fetch)
        await svc.refresh()
        snapshot = feed(svc)
        first, second = await asyncio.gather(svc.analyze("daily", snapshot), svc.analyze("daily", snapshot))
        assert first["id"] == second["id"]
        await asyncio.sleep(0.01)
        assert len(calls) == 1
        busy = await svc.analyze(snapshot["items"][0]["id"], snapshot)
        assert busy["status"] == "busy"
        gate.set()
        await asyncio.gather(*svc.tasks.values())
        assert svc.analysis(first["id"])["status"] == "complete"
        snapshot["sources"][0]["fetched_at"] = "2026-10-07T11:00:00+08:00"
        assert (await svc.analyze("daily", snapshot))["cache_hit"] is True
        reloaded = mod.NewsService(tmp_path)
        assert reloaded.analysis(first["id"])["model"] == "gpt-6.1-sol"
        assert reloaded.analysis(first["id"])["reasoning_effort"] == "xhigh"
        assert "不可信" in calls[0][0]["content"]
        assert "published_at" in calls[0][1]["content"]
    asyncio.run(run())


def test_failed_model_does_not_leak_error_or_cache_success(tmp_path, monkeypatch):
    async def model(*args, **kwargs):
        raise RuntimeError("sk-private-secret https://user:password@api")
    monkeypatch.setattr(mod.ai_provider, "generate_ai_text", model)
    async def run():
        svc = mod.NewsService(tmp_path, good_fetch)
        await svc.refresh()
        result = await svc.analyze("daily", feed(svc))
        await asyncio.gather(*svc.tasks.values())
        state = svc.analysis(result["id"])
        assert state["status"] == "failed"
        assert "password" not in state["error"] and "secret" not in state["error"]
        assert svc.reports == {}
    asyncio.run(run())


def test_model_change_invalidates_report(tmp_path, monkeypatch):
    async def model(*args, **kwargs):
        monkeypatch.setattr(mod.ai_provider, "current_ai_model", lambda: "changed")
        return "Result"
    monkeypatch.setattr(mod.ai_provider, "generate_ai_text", model)
    async def run():
        svc = mod.NewsService(tmp_path, good_fetch)
        await svc.refresh()
        result = await svc.analyze("daily", feed(svc))
        await asyncio.gather(*svc.tasks.values())
        assert svc.analysis(result["id"])["status"] == "failed"
        assert not svc.reports
    asyncio.run(run())


def test_api_contracts_invalid_ids_and_unconfigured_state(tmp_path, monkeypatch):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.state.news_service = mod.NewsService(tmp_path, good_fetch)
    monkeypatch.setattr(routes, "collect_context", lambda repo: ([], [], "维表未同步"))
    monkeypatch.setattr(mod.ai_provider, "ai_configured", lambda: False)
    app.include_router(routes.build_router())
    with TestClient(app) as client:
        result = client.get("/api/custom/news/feed").json()
        assert result["items"][0]["title"] == ITEM.title
        assert result["association_status"] == "维表未同步"
        assert client.post("/api/custom/news/analyze", json={"article_id": "../secrets"}).status_code == 422
        assert client.get("/api/custom/news/analysis/..%2Fsecrets").status_code != 200
        assert client.get("/api/custom/news/analysis/" + "a" * 32).status_code == 404
        state = client.post("/api/custom/news/analyze", json={"article_id": "daily"}).json()
        assert state["status"] == "unconfigured"


def test_invalid_report_schema_and_report_limit(tmp_path):
    directory = tmp_path / "news"
    directory.mkdir()
    reports = {f"{number:032x}": {"id": f"{number:032x}", "article_id": "daily", "status": "complete",
                              "content": "Report", "model": "test", "reasoning_effort": None,
                              "generated_at": f"2026-10-07T10:{number % 60:02}:00+08:00"}
               for number in range(70)}
    reports["invalid"] = {"id": "invalid", "status": "complete", "content": "text", "generated_at": 42}
    (directory / "analysis.json").write_text(json.dumps(reports), encoding="utf-8")
    svc = mod.NewsService(tmp_path)
    assert len(svc.reports) == mod.MAX_REPORTS
    assert "invalid" not in svc.reports


def test_shutdown_cancels_only_matching_data_directory(tmp_path):
    async def run():
        matching = mod.NewsService(tmp_path)
        unrelated = mod.NewsService(tmp_path / "other")
        gate = asyncio.Event()
        matching.refresh_task = asyncio.create_task(gate.wait())
        unrelated.refresh_task = asyncio.create_task(gate.wait())
        routes._instances.add(matching)
        routes._instances.add(unrelated)
        routes.stop_services(tmp_path)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert matching.refresh_task.cancelled()
        assert not unrelated.refresh_task.done()
        unrelated.refresh_task.cancel()
        await asyncio.gather(unrelated.refresh_task, return_exceptions=True)
    asyncio.run(run())
