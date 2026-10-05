"""本机 AI 选基候选的标准化、筛选与排序契约。"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import sys
from pathlib import Path
from threading import Barrier
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


def _ai_client():
    app = FastAPI()
    app.include_router(routes.build_router())
    return TestClient(app)


async def _fake_ai_stream(*_args, **_kwargs):
    yield "研究候选"


def _rank_row(code, *, share="A", growth_1m=2.0, growth_1y=8.0):
    return {
        "code": code,
        "name": f"测试基金{code}",
        "share_class": share,
        "nav": 1.23,
        "nav_date": "2026-09-30",
        "growth_1m": growth_1m,
        "growth_3m": None,
        "growth_6m": 4.0,
        "growth_1y": growth_1y,
        "growth_2y": 9.0,
        "growth_3y": 10.0,
    }


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


def test_ai_pick_defaults_to_all_supported_types(monkeypatch):
    from app.services import ai_provider

    calls = []
    ranking_barrier = Barrier(len(routes._AI_PICK_TYPES))

    def rank(fund_type, **_kwargs):
        calls.append(fund_type)
        ranking_barrier.wait(timeout=3)
        return [_rank_row(str(110000 + routes._AI_PICK_TYPES.index(fund_type)))]

    monkeypatch.setattr(service, "akshare_rank", rank)
    monkeypatch.setattr(ai_provider, "stream_ai_text", _fake_ai_stream)
    response = _ai_client().post("/api/custom/fund/screener/ai", json={})

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert sorted(calls) == sorted(routes._AI_PICK_TYPES)
    assert events[0]["fund_type"] == "all"
    assert {row["fund_type"] for row in events[0]["candidates"]} == set(routes._AI_PICK_TYPES)


def test_ai_pick_all_merges_unique_funds_with_balanced_source_types(monkeypatch):
    from app.services import ai_provider

    rankings = {}
    for offset, fund_type in enumerate(routes._AI_PICK_TYPES):
        rows = [_rank_row("100000")]
        rows.extend(_rank_row(str(110000 + offset * 100 + number)) for number in range(6))
        rankings[fund_type] = rows

    monkeypatch.setattr(
        service, "akshare_rank", lambda fund_type, **_kwargs: rankings[fund_type]
    )
    monkeypatch.setattr(ai_provider, "stream_ai_text", _fake_ai_stream)
    response = _ai_client().post("/api/custom/fund/screener/ai", json={"fund_type": "all"})

    assert response.status_code == 200
    candidates = json.loads(response.text.splitlines()[0])["candidates"]
    assert len(candidates) == 10
    assert len({row["code"] for row in candidates}) == len(candidates)
    assert {kind: sum(row["fund_type"] == kind for row in candidates) for kind in routes._AI_PICK_TYPES} == {
        "股票型": 3, "混合型": 3, "指数型": 2, "债券型": 2,
    }
    shared = next(row for row in candidates if row["code"] == "100000")
    assert shared["fund_type"] == "股票型"
    assert shared["nav_date"] == "2026-09-30"
    assert shared["growth_3m"] is None


def test_ai_pick_all_keeps_available_rankings_when_one_type_fails(monkeypatch):
    from app.services import ai_provider

    def rank(fund_type, **_kwargs):
        if fund_type == "股票型":
            return [_rank_row("123456")]
        raise RuntimeError("ranking source unavailable")

    monkeypatch.setattr(service, "akshare_rank", rank)
    monkeypatch.setattr(ai_provider, "stream_ai_text", _fake_ai_stream)
    response = _ai_client().post("/api/custom/fund/screener/ai", json={"fund_type": "all"})

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert [row["code"] for row in events[0]["candidates"]] == ["123456"]
    assert events[0]["unavailable_types"] == ["混合型", "指数型", "债券型"]


def test_ai_pick_all_reports_empty_rankings_as_unavailable(monkeypatch):
    from app.services import ai_provider

    monkeypatch.setattr(service, "akshare_rank", lambda kind, **_: [_rank_row("123456")] if kind == "股票型" else [])
    monkeypatch.setattr(ai_provider, "stream_ai_text", _fake_ai_stream)
    response = _ai_client().post("/api/custom/fund/screener/ai", json={"fund_type": "all"})
    assert response.status_code == 200
    meta = json.loads(response.text.splitlines()[0])
    assert meta["unavailable_types"] == ["混合型", "指数型", "债券型"]
    assert len(meta["candidates"]) == 1


def test_ai_pick_all_returns_unavailable_when_every_ranking_fails(monkeypatch):
    def rank(*_args, **_kwargs):
        raise RuntimeError("ranking source unavailable")

    monkeypatch.setattr(service, "akshare_rank", rank)
    response = _ai_client().post("/api/custom/fund/screener/ai", json={"fund_type": "all"})

    assert response.status_code == 502
    assert response.json()["detail"] == "基金历史收益榜单暂不可用"


def test_ai_pick_all_filters_by_share_and_selected_horizon(monkeypatch):
    from app.services import ai_provider

    rankings = {}
    for offset, fund_type in enumerate(routes._AI_PICK_TYPES):
        base = 120000 + offset * 10
        rankings[fund_type] = [
            _rank_row(str(base), share="A", growth_1m=99.0),
            _rank_row(str(base + 1), share="C", growth_1m=None, growth_1y=99.0),
            _rank_row(str(base + 2), share="C", growth_1m=2.0),
        ]

    monkeypatch.setattr(
        service, "akshare_rank", lambda fund_type, **_kwargs: rankings[fund_type]
    )
    monkeypatch.setattr(ai_provider, "stream_ai_text", _fake_ai_stream)
    response = _ai_client().post("/api/custom/fund/screener/ai", json={
        "fund_type": "all", "horizon": "1m", "share": "C",
    })

    assert response.status_code == 200
    candidates = json.loads(response.text.splitlines()[0])["candidates"]
    assert len(candidates) == len(routes._AI_PICK_TYPES)
    assert all(row["share_class"] == "C" and row["growth_1m"] is not None for row in candidates)


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


@pytest.mark.parametrize("limit", [10, 16])
def test_single_category_preserves_source_order_and_only_enriches_requested_limit(monkeypatch, limit):
    from app.services import ai_provider

    rows = [_rank_row(str(120000 + i)) for i in range(30)]
    enriched = []
    monkeypatch.setattr(service, "akshare_rank", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(service, "fund_research", lambda code, *_args, **_kwargs:
        enriched.append(code) or {"fees": {"status": "unavailable"},
                                "risk": {"status": "unavailable"},
                                "holdings": {"status": "unavailable"}})
    monkeypatch.setattr(ai_provider, "stream_ai_text", _fake_ai_stream)
    response = _ai_client().post("/api/custom/fund/screener/ai", json={
        "fund_type": "混合型", "limit": limit,
    })
    assert response.status_code == 200
    candidates = json.loads(response.text.splitlines()[0])["candidates"]
    assert [row["code"] for row in candidates] == [row["code"] for row in rows[:limit]]
    assert len(enriched) == limit


@pytest.mark.parametrize("limit", [0, 17])
def test_candidate_limit_rejects_out_of_range_before_fetching(monkeypatch, limit):
    monkeypatch.setattr(service, "akshare_rank", lambda *_args, **_kwargs: pytest.fail("Invalid limit must not fetch"))
    assert _ai_client().post("/api/custom/fund/screener/ai", json={"limit": limit}).status_code == 422


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
