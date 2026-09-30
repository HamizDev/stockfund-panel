"""本机 AI 选基候选的标准化、筛选与排序契约。"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.fund import routes, service


@pytest.fixture(autouse=True)
def isolated_research(monkeypatch):
    monkeypatch.setattr(routes, "_cache", service.TTLCache())
    monkeypatch.setattr(routes, "_research_cache", service.TTLCache(max_entries=96))
    monkeypatch.setattr(service, "fund_research", lambda *_args, **_kwargs: {
        "fees": {"status": "unavailable"}, "risk": {"status": "unavailable"},
        "holdings": {"status": "unavailable"},
    })


def test_rank_preserves_missing_values_and_percent_units(monkeypatch):
    monkeypatch.setattr(service, "_AKSHARE_PROXY", "http://127.0.0.1:3019")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"items": [
                {"code": "011370", "name": "测试基金C", "growth_1m": "12.5%", "growth_1y": "—"},
                {"code": "bad", "name": "无效基金", "growth_1m": 99},
            ]}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    rows = service.akshare_rank("混合型", sort_by="1m")
    assert len(rows) == 1
    assert rows[0]["share_class"] == "C"
    assert rows[0]["growth_1m"] == 12.5
    assert rows[0]["growth_1y"] is None


def test_ai_pick_sends_only_matching_candidates_to_model(monkeypatch):
    from app.services import ai_provider

    monkeypatch.setattr(service, "akshare_rank", lambda *args, **kwargs: [
        {"code": "011370", "name": "测试基金C", "share_class": "C", "growth_1m": 5.0},
        {"code": "011371", "name": "测试基金A", "share_class": "A", "growth_1m": 8.0},
    ])
    seen = []

    async def fake_stream(messages, **_kwargs):
        seen.extend(messages)
        yield "研究候选 011370"

    monkeypatch.setattr(ai_provider, "stream_ai_text", fake_stream)
    app = FastAPI()
    app.include_router(routes.build_router())
    response = TestClient(app).post("/api/custom/fund/screener/ai", json={
        "fund_type": "混合型", "horizon": "1m", "share": "C",
    })
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert [row["code"] for row in events[0]["candidates"]] == ["011370"]
    assert "011371" not in seen[1]["content"]
    assert events[-1]["type"] == "done"


def test_ai_pick_stream_keeps_heartbeat_as_separate_json_line(monkeypatch):
    from app.services import ai_provider, ndjson_heartbeat

    monkeypatch.setattr(service, "akshare_rank", lambda *args, **kwargs: [
        {"code": "011370", "name": "测试基金C", "share_class": "C", "growth_1m": 5.0},
    ])
    original_heartbeat = ndjson_heartbeat.with_heartbeat
    monkeypatch.setattr(
        ndjson_heartbeat,
        "with_heartbeat",
        functools.partial(original_heartbeat, interval=0.02),
    )

    async def slow_stream(*_args, **_kwargs):
        await asyncio.sleep(0.08)
        yield "研究候选 011370"

    monkeypatch.setattr(ai_provider, "stream_ai_text", slow_stream)
    app = FastAPI()
    app.include_router(routes.build_router())
    response = TestClient(app).post("/api/custom/fund/screener/ai", json={
        "fund_type": "混合型", "horizon": "1m", "share": "C",
    })

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[0]["type"] == "meta"
    assert any(event["type"] == "ping" for event in events)
    assert events[-1]["type"] == "done"


def test_proxy_sorts_selected_horizon_before_limiting(monkeypatch):
    class Frame:
        def iterrows(self):
            rows = [
                {"基金代码": "011371", "基金简称": "测试基金A", "近1周": 1,
                 "近1月": 3, "近3月": 4, "近6月": 5, "近1年": 20,
                 "近2年": 21, "近3年": 22, "单位净值": 1.1},
                {"基金代码": "011370", "基金简称": "测试基金C", "近1周": 2,
                 "近1月": 9, "近3月": 8, "近6月": 7, "近1年": 5,
                 "近2年": 6, "近3年": 7, "单位净值": 1.2},
            ]
            yield from enumerate(rows)

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(fund_open_fund_rank_em=lambda **_: Frame()))
    path = Path(__file__).resolve().parents[2] / "bin" / "akshare-proxy.py"
    spec = importlib.util.spec_from_file_location("test_akshare_proxy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.fund_rank("混合型", limit=1, sort_by="1m")[0]["code"] == "011370"
    assert module.fund_rank("混合型", limit=1, sort_by="1y")[0]["code"] == "011371"
