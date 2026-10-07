import asyncio
import json
from dataclasses import asdict
from datetime import date

import pytest

from app.data_providers.news import NewsFeed, NewsRecord
from app.services import news_service as mod

TODAY = "2026-10-07"
STAMP = TODAY + "T10:00:00+08:00"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(mod, "cn_today", lambda: date(2026, 10, 7))
    monkeypatch.setattr(mod.ai_provider, "is_codex_cli_provider", lambda: True)
    monkeypatch.setattr(mod.ai_provider, "ai_configured", lambda: True)


def record(number, day=TODAY, title=None):
    return NewsRecord("cls", str(number), title or f"公司{number}发布公告", "公告披露业务情况。",
                      day + "T10:00:00+08:00", None)


def snapshot(svc):
    return svc.feed([], [])


def test_daily_archive_keeps_old_window_and_replaces_corrections(tmp_path):
    async def run():
        batches = [[record(1), record(2, "2026-10-06")], [record(3)], [record(1, title="更正公告")], []]
        async def fetch(source):
            items = batches.pop(0) if source == "cls" else []
            return NewsFeed(source, "ok" if items else "empty", items, STAMP)
        svc = mod.NewsService(tmp_path, fetch)
        for _ in range(4):
            svc.last_attempt = float("-inf")
            await svc.refresh(True)
        assert {row.title for row in svc.feeds["cls"].items} == {"更正公告", "公司3发布公告"}
        assert svc.feeds["cls"].status == "ok"
        assert len(mod.NewsService(tmp_path).feeds["cls"].items) == 2
        mod.cn_today = lambda: date(2026, 10, 8)
        svc.last_attempt = float("-inf")
        async def empty(source):
            return NewsFeed(source, "empty", [], STAMP)
        svc.fetcher = empty
        await svc.refresh(True)
        assert snapshot(svc)["items"] == []
    asyncio.run(run())


def test_background_refresh_returns_cache_and_coalesces(tmp_path):
    async def run():
        gate = asyncio.Event()
        calls = []
        async def fetch(source):
            calls.append(source)
            await gate.wait()
            return NewsFeed(source, "empty", [], STAMP)
        svc = mod.NewsService(tmp_path, fetch)
        svc.feeds["cls"] = NewsFeed("cls", "ok", [record(1)], STAMP)
        svc.refresh_in_background()
        svc.refresh_in_background()
        cached = snapshot(svc)
        assert cached["refreshing"] and len(cached["items"]) == 1
        await asyncio.sleep(0)
        gate.set()
        await svc.refresh_task
        assert sorted(calls) == ["cls", "eastmoney"]
        assert not snapshot(svc)["refreshing"]
        assert len(snapshot(svc)["items"]) == 1
    asyncio.run(run())


def install_model(monkeypatch, calls, fail_after=None):
    async def model(messages, **kwargs):
        calls.append(kwargs)
        if fail_after is not None and len(calls) > fail_after:
            raise RuntimeError("private upstream detail")
        rows = json.loads(messages[1]["content"])["items"]
        return json.dumps({"items": [{"article_id": row["id"], "direction": "uncertain", "scope": "unclear",
                                     "reason": "缺少影响背景", "evidence": []} for row in rows]})
    monkeypatch.setattr(mod.ai_provider, "generate_ai_text", model)


@pytest.mark.parametrize("effort", ["high", "max"])
def test_direction_batches_persist_and_only_new_content_uses_model(tmp_path, monkeypatch, effort):
    calls = []
    install_model(monkeypatch, calls)
    async def run():
        svc = mod.NewsService(tmp_path)
        svc.feeds["cls"] = NewsFeed("cls", "ok", [record(n) for n in range(25)], STAMP)
        assert calls == []  # Reading the feed never starts a model job.
        first = await svc.classify_directions(snapshot(svc), effort)
        await svc.direction_task
        assert svc.direction_status(first["id"])["completed"] == 25
        assert len(calls) == 2
        assert all(call["codex_model"] == "gpt-6-luna" and call["codex_reasoning_effort"] == effort for call in calls)
        svc._write("snapshot.json", {source: asdict(value) for source, value in svc.feeds.items()})
        reloaded = mod.NewsService(tmp_path)
        assert len(snapshot(reloaded)["items"]) == 25
        assert all(row["ai_direction"]["model"] == "gpt-6-luna" for row in snapshot(reloaded)["items"])
        assert (await reloaded.classify_directions(snapshot(reloaded), effort))["cache_hit"]
        reloaded.feeds["cls"] = NewsFeed("cls", "ok", [record(n, title="更正公告" if n == 0 else None)
                                                      for n in range(25)], STAMP)
        changed = await reloaded.classify_directions(snapshot(reloaded), effort)
        assert changed["completed"] == 24
        await reloaded.direction_task
        assert len(calls) == 3
    asyncio.run(run())


def test_direction_failure_keeps_completed_batches_and_retry_only_missing(tmp_path, monkeypatch):
    calls = []
    install_model(monkeypatch, calls, fail_after=1)
    async def run():
        svc = mod.NewsService(tmp_path)
        svc.feeds["cls"] = NewsFeed("cls", "ok", [record(n) for n in range(25)], STAMP)
        first = await svc.classify_directions(snapshot(svc))
        await svc.direction_task
        state = svc.direction_status(first["id"])
        assert state["status"] == "partial" and state["completed"] == 20
        assert "private" not in state["error"]
        install_model(monkeypatch, calls)
        retry = await svc.classify_directions(snapshot(svc))
        assert retry["completed"] == 20
        await svc.direction_task
        assert len(calls) == 3 and svc.direction_job["status"] == "complete"
    asyncio.run(run())


def test_direction_jobs_coalesce_and_block_interpretation(tmp_path, monkeypatch):
    async def run():
        gate = asyncio.Event()
        async def model(messages, **kwargs):
            await gate.wait()
            row = json.loads(messages[1]["content"])["items"][0]
            return json.dumps({"items": [{"article_id": row["id"], "direction": "uncertain", "scope": "unclear",
                                         "reason": "不足", "evidence": []}]})
        monkeypatch.setattr(mod.ai_provider, "generate_ai_text", model)
        svc = mod.NewsService(tmp_path)
        svc.feeds["cls"] = NewsFeed("cls", "ok", [record(1)], STAMP)
        first, second = await asyncio.gather(svc.classify_directions(snapshot(svc)),
                                             svc.classify_directions(snapshot(svc)))
        assert first["id"] == second["id"] and second["status"] == "running"
        assert (await svc.classify_directions(snapshot(svc), "max"))["status"] == "busy"
        assert (await svc.analyze("daily", snapshot(svc)))["status"] == "busy"
        svc.direction_task.cancel()
        await asyncio.gather(svc.direction_task, return_exceptions=True)
        assert svc.direction_job["status"] == "failed"
        assert not svc.directions
    asyncio.run(run())


def test_direction_requires_codex_and_limits_latest_today(tmp_path, monkeypatch):
    calls = []
    install_model(monkeypatch, calls)
    async def run():
        svc = mod.NewsService(tmp_path)
        assert (await svc.classify_directions(snapshot(svc)))["status"] == "empty"
        svc.feeds["cls"] = NewsFeed("cls", "ok", [record(n) for n in range(260)] + [record(999, "2026-10-06")], STAMP)
        monkeypatch.setattr(mod.ai_provider, "is_codex_cli_provider", lambda: False)
        assert (await svc.classify_directions(snapshot(svc)))["status"] == "unsupported"
        monkeypatch.setattr(mod.ai_provider, "is_codex_cli_provider", lambda: True)
        monkeypatch.setattr(mod.ai_provider, "ai_configured", lambda: False)
        assert (await svc.classify_directions(snapshot(svc)))["status"] == "unconfigured"
        assert not calls
        monkeypatch.setattr(mod.ai_provider, "ai_configured", lambda: True)
        assert (await svc.classify_directions(snapshot(svc)))["total"] == 240
        await svc.direction_task
        assert len(calls) == 12
    asyncio.run(run())


def test_corrupt_direction_evidence_is_not_used(tmp_path, monkeypatch):
    calls = []
    install_model(monkeypatch, calls)
    async def run():
        svc = mod.NewsService(tmp_path)
        svc.feeds["cls"] = NewsFeed("cls", "ok", [record(1)], STAMP)
        await svc.classify_directions(snapshot(svc))
        await svc.direction_task
        row = next(iter(svc.directions.values()))
        row.update(direction="positive", scope="company", evidence=["不存在于原文"])
        assert "ai_direction" not in snapshot(svc)["items"][0]
        assert (await svc.classify_directions(snapshot(svc)))["completed"] == 0
        await svc.direction_task
        assert len(calls) == 2
    asyncio.run(run())


def test_disk_failure_keeps_paid_results_in_memory_for_retry(tmp_path, monkeypatch):
    calls = []
    install_model(monkeypatch, calls)
    async def run():
        svc = mod.NewsService(tmp_path)
        svc.feeds["cls"] = NewsFeed("cls", "ok", [record(n) for n in range(25)], STAMP)
        original_write = svc._write
        def fail_write(*args):
            raise OSError("private filesystem path")
        monkeypatch.setattr(svc, "_write", fail_write)
        await svc.classify_directions(snapshot(svc))
        await svc.direction_task
        assert svc.direction_job["status"] == "partial" and svc.direction_job["completed"] == 20
        assert "暂存内存" in svc.direction_job["error"] and "private" not in svc.direction_job["error"]
        assert len(svc.directions) == 20 and len(calls) == 1
        monkeypatch.setattr(svc, "_write", original_write)
        retry = await svc.classify_directions(snapshot(svc))
        assert retry["completed"] == 20
        await svc.direction_task
        assert svc.direction_job["status"] == "complete" and len(calls) == 2
    asyncio.run(run())


def test_retry_final_cache_write_without_new_model_call(tmp_path, monkeypatch):
    calls = []
    install_model(monkeypatch, calls)
    async def run():
        svc = mod.NewsService(tmp_path)
        svc.feeds["cls"] = NewsFeed("cls", "ok", [record(1)], STAMP)
        original_write = svc._write
        def fail_write(*args):
            raise OSError("full disk")
        monkeypatch.setattr(svc, "_write", fail_write)
        await svc.classify_directions(snapshot(svc))
        await svc.direction_task
        assert svc.direction_cache_dirty
        assert (await svc.classify_directions(snapshot(svc)))["status"] == "partial"
        monkeypatch.setattr(svc, "_write", original_write)
        retry = await svc.classify_directions(snapshot(svc))
        assert retry["status"] == "complete" and retry["cache_hit"]
        assert not svc.direction_cache_dirty and len(calls) == 1
        assert len(mod.NewsService(tmp_path).directions) == 1
    asyncio.run(run())
