"""Daily research persistence, concurrency and read-only holding contracts."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import holdings_review as review


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(review.ai_provider, "ai_configured", lambda: True)
    monkeypatch.setattr(review, "_model", lambda: {"provider": "codex_cli", "model": "test-model", "reasoning_effort": "xhigh"})
    monkeypatch.setattr(review, "cn_today", lambda: date(2026, 10, 5))
    monkeypatch.setattr(review, "_jobs", {})


HOLDINGS = [{"symbol": "600000.SH", "qty": 100, "cost_price": 12}]


def evidence(rows):
    return [{"symbol": row["symbol"], "name": "测试股票", "asset_type": "stock", "data_date": "2026-09-30",
             "registered_lot": row, "daily": [{"date": "2026-09-30", "close": 10, "raw_close": 15}],
             "warning": "旧盘后资料，非实时价"} for row in rows]


async def wait_jobs():
    await asyncio.gather(*list(review._jobs.values()))


def test_status_is_read_only_and_empty_unconfigured_are_distinct(tmp_path, monkeypatch):
    assert review.status(tmp_path, "lots", HOLDINGS)["status"] == "not_generated"
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(review.ai_provider, "ai_configured", lambda: False)
    assert review.status(tmp_path, "lots", HOLDINGS)["status"] == "unconfigured"
    assert review.status(tmp_path, "lots", [])["status"] == "empty"
    assert not list(tmp_path.iterdir())


def test_concurrent_start_and_same_day_reopen_call_model_once(tmp_path, monkeypatch):
    calls = []

    async def model(messages, **kwargs):
        calls.append(messages)
        await asyncio.sleep(0)
        assert kwargs == {"temperature": 0.2, "max_tokens": None, "timeout": 600}
        return "维持观察，先核对实时价格。"

    monkeypatch.setattr(review.ai_provider, "generate_ai_text", model)

    async def run():
        states = await asyncio.gather(*(review.start(tmp_path, "lots", HOLDINGS, evidence) for _ in range(5)))
        assert all(state["status"] == "running" for state in states)
        await wait_jobs()
        state = await review.start(tmp_path, "lots", HOLDINGS, evidence)
        assert state["status"] == "complete"
        assert state["report"]["report_date"] == "2026-10-05"
        assert state["report"]["observations"][0]["data_date"] == "2026-09-30"
        assert state["report"]["model"] == "test-model"
        assert "holdings_fingerprint" not in state["report"]
        context = json.loads(calls[0][1]["content"])
        assert context["holdings"][0]["registered_lot"]["cost_price"] == 12
        assert context["holdings"][0]["daily"][0]["raw_close"] == 15
        assert "登记成本不复权" in calls[0][0]["content"]

    asyncio.run(run())
    assert len(calls) == 1
    assert HOLDINGS[0]["cost_price"] == 12


@pytest.mark.parametrize("content", ["", " ", "x" * (review.MAX_CONTENT + 1)],
                         ids=["empty", "whitespace", "oversized"])
def test_bad_output_persisted_failure_never_automatically_retries(tmp_path, monkeypatch, content):
    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        return content

    monkeypatch.setattr(review.ai_provider, "generate_ai_text", model)

    async def run():
        await review.start(tmp_path, "lots", HOLDINGS, evidence)
        await wait_jobs()
        assert review.status(tmp_path, "lots", HOLDINGS)["status"] == "failed"
        await review.start(tmp_path, "lots", HOLDINGS, evidence)

    asyncio.run(run())
    assert len(calls) == 1


def test_missing_prices_do_not_call_model(tmp_path, monkeypatch):
    async def model(*args, **kwargs):
        pytest.fail("Must not infer a holding action without priced evidence")

    monkeypatch.setattr(review.ai_provider, "generate_ai_text", model)

    async def run():
        await review.start(tmp_path, "lots", HOLDINGS, lambda _: [{"symbol": "600000.SH", "data_date": "2026-09-30",
            "nav": [{"unit_nav": float("nan")}]}])
        await wait_jobs()
        assert review.status(tmp_path, "lots", HOLDINGS)["status"] == "failed"

    asyncio.run(run())


def test_scope_date_holdings_and_model_changes(tmp_path, monkeypatch):
    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        return "观察"

    monkeypatch.setattr(review.ai_provider, "generate_ai_text", model)

    async def run():
        await review.start(tmp_path, "lots", HOLDINGS, evidence)
        await wait_jobs()
        changed = [{**HOLDINGS[0], "qty": 200}]
        state = await review.start(tmp_path, "lots", changed, evidence)
        assert state["status"] == "complete" and state["holdings_changed"]
        monkeypatch.setattr(review, "_model", lambda: {"model": "new-model"})
        state = review.status(tmp_path, "lots", changed)
        assert state["model_changed"]
        assert review.status(tmp_path, "fund", HOLDINGS)["status"] == "not_generated"
        await review.start(tmp_path, "fund", HOLDINGS, evidence)
        await wait_jobs()
        monkeypatch.setattr(review, "cn_today", lambda: date(2026, 10, 6))
        assert review.status(tmp_path, "lots", changed)["status"] == "not_generated"
        await review.start(tmp_path, "lots", changed, evidence)
        await wait_jobs()
        assert review.status(tmp_path, "lots", changed)["report"]["report_date"] == "2026-10-06"

    asyncio.run(run())
    assert len(calls) == 3
    assert len(list((tmp_path / "holdings_reviews").glob("*.json"))) == 2


def test_manual_regenerate_retries_failure_without_leaking_upstream_error(tmp_path, monkeypatch):
    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("api-key=do-not-expose")
        return "成功"

    monkeypatch.setattr(review.ai_provider, "generate_ai_text", model)

    async def run():
        await review.start(tmp_path, "lots", HOLDINGS, evidence)
        await wait_jobs()
        assert "do-not-expose" not in json.dumps(review.status(tmp_path, "lots", HOLDINGS))
        await review.start(tmp_path, "lots", HOLDINGS, evidence, force=True)
        await wait_jobs()
        assert review.status(tmp_path, "lots", HOLDINGS)["status"] == "complete"

    asyncio.run(run())
    assert len(calls) == 2


def test_restart_interrupted_job_requires_explicit_retry(tmp_path):
    review._save(tmp_path, "lots", {"scope": "lots", "status": "running", "report_date": "2026-10-05"})
    state = review.status(tmp_path, "lots", HOLDINGS)
    assert state["status"] == "failed"
    assert "中断" in state["report"]["error"]
    assert asyncio.run(review.start(tmp_path, "lots", HOLDINGS, evidence))["status"] == "failed"


def test_oversized_account_is_not_silently_truncated(tmp_path):
    holdings = [{**HOLDINGS[0], "id": str(i)} for i in range(review.MAX_ITEMS + 1)]
    result = asyncio.run(review.start(tmp_path, "lots", holdings, evidence))
    assert result["status"] == "failed"
    assert "未调用模型" in result["report"]["error"]


def test_stock_and_etf_read_separate_repository_paths_and_preserve_units(tmp_path, monkeypatch):
    from app.services import stock_analyzer

    calls = []

    class Repo:
        store = SimpleNamespace(data_dir=tmp_path)

        def get_name_map(self, symbols):
            return {"510300.SH": "沪深300ETF"}

        def resolve_asset_type(self, symbol):
            return "etf" if symbol == "510300.SH" else "stock"

        def get_daily_asset(self, asset, symbol, start, end):
            calls.append((asset, symbol))
            return pl.DataFrame({"date": [date(2026, 9, 30)], "close": [3.5], "raw_close": [4.5], "change_pct": [0.01]})

    financial_calls = []
    monkeypatch.setattr(stock_analyzer, "_load_financials", lambda _, symbol, **_kwargs: financial_calls.append(symbol) or {})
    rows = review.collect_lots(Repo(), [HOLDINGS[0], {"symbol": "510300.SH", "cost_price": 4.0}])
    assert calls == [("stock", "600000.SH"), ("etf", "510300.SH")]
    assert financial_calls == ["600000.SH"]
    assert rows[1]["name"] == "沪深300ETF"
    assert rows[1]["daily"][0]["change_pct"] == 0.01
    assert rows[1]["daily"][0]["raw_close"] == 4.5
    assert rows[1]["registered_lot"]["cost_price"] == 4.0


def test_multiple_registered_stocks_reuse_two_financial_table_snapshots(tmp_path, monkeypatch):
    from app.services import financial_sync, stock_analyzer

    calls = []
    frame = pl.DataFrame({"symbol": ["600000.SH", "600001.SH"],
                          "period_end": [date(2026, 6, 30)] * 2, "value": [10, 20]})
    monkeypatch.setattr(financial_sync, "get_financial_df", lambda _, table: calls.append(table) or frame)
    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda *_: pl.DataFrame({
        "date": [date(2026, 9, 30)], "close": [10], "raw_close": [15]}))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path),
                           get_name_map=lambda _: {}, resolve_asset_type=lambda _: "stock")
    result = review.collect_lots(repo, [HOLDINGS[0], {"symbol": "600001.SH"}])
    assert calls == ["metrics", "income"]
    assert result[0]["financials"]["metrics"][0]["value"] == 10
    assert result[1]["financials"]["metrics"][0]["value"] == 20


def test_today_daily_cache_is_not_labelled_as_confirmed_closing_price(tmp_path, monkeypatch):
    from app.services import stock_analyzer

    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda *_: pl.DataFrame({
        "date": [date(2026, 10, 5)], "close": [4.0], "raw_close": [4.0]}))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path),
                           get_name_map=lambda _: {}, resolve_asset_type=lambda _: "etf")
    row = review.collect_lots(repo, [{"symbol": "510300.SH", "qty": 100}])[0]
    assert row["data_date"] == "2026-10-05"
    assert row["daily_completeness"] == "not_confirmed"
    assert row["quote_time"] is None
    assert "尚未完成" in row["warning"] and "报价时点未确认" in row["warning"]
    assert "最终收盘价" in review._PROMPT


@pytest.mark.parametrize("scope", ["lots", "fund"])
def test_routes_status_and_start_do_not_edit_registered_holdings(tmp_path, monkeypatch, scope):
    from app.api import lots
    from app.custom.fund import routes, service
    from app.strategy import lots as lots_domain

    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(lots.router)
    app.include_router(routes.build_router())
    monkeypatch.setattr(lots_domain, "load_all", lambda _: HOLDINGS)
    monkeypatch.setattr(service, "load_portfolio", lambda: [{"thscode": "011370.OF", "amount": 1000, "profit": 2}])
    async def fake_start(data_dir, job_scope, rows, collect, *, force):
        assert job_scope == scope and force is False
        return {"status": "running", "holdings_count": len(rows)}
    monkeypatch.setattr(review, "start", fake_start)
    with TestClient(app) as client:
        path = "/api/lots/review" if scope == "lots" else "/api/custom/fund/portfolio/review"
        assert client.get(path).json()["status"] == "not_generated"
        assert client.post(path, json={}).json()["status"] == "running"
    assert not list(tmp_path.iterdir())


def test_fund_collector_preserves_nav_dates_and_partial_missing_data(tmp_path, monkeypatch):
    from app.custom.fund import routes, service
    from app import market_time

    registered = [{"thscode": "011370.OF", "name": "测试基金", "amount": 1000, "profit": 2},
                  {"thscode": "011371.OF", "amount": 500, "profit": -1}]
    monkeypatch.setattr(service, "load_portfolio", lambda: registered)
    monkeypatch.setattr(routes, "_cache", service.TTLCache())
    monkeypatch.setattr(routes, "_research_cache", service.TTLCache(max_entries=96))
    monkeypatch.setattr(routes, "_optional_client", lambda: None)
    monkeypatch.setattr(market_time, "cn_today", lambda: date(2026, 10, 5))
    monkeypatch.setattr(service, "akshare_profile", lambda _: None)
    monkeypatch.setattr(service, "eastmoney_nav", lambda *_: [])
    monkeypatch.setattr(service, "fund_research", lambda *_: {
        "fees": {"status": "unavailable"}, "risk": {"status": "unavailable"},
        "holdings": {"status": "unavailable", "items": []},
    })
    monkeypatch.setattr(service, "akshare_nav", lambda code, _: [] if code == "011371.OF" else [
        {"nav_date": "2026-09-30", "unit_nav": 1.25},
        {"nav_date": "2026-10-06", "unit_nav": 9.99},
        {"nav_date": "2026-10-02", "unit_nav": float("nan")},
        {"nav_date": "2026-10-03", "unit_nav": True},
    ])
    captured = []

    async def fake_start(data_dir, scope, rows, collect, *, force):
        assert scope == "fund" and not force
        captured.extend(collect(rows))
        return {"status": "running"}

    monkeypatch.setattr(review, "start", fake_start)
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(routes.build_router())
    with TestClient(app) as client:
        assert client.post("/api/custom/fund/portfolio/review", json={}).status_code == 200
    assert captured[0]["nav"] == [{"nav_date": "2026-09-30", "unit_nav": 1.25}]
    assert captured[0]["data_date"] == "2026-09-30"
    assert captured[0]["nav_source"] == "akshare"
    assert captured[0]["registered_holding"] == registered[0]
    assert captured[1]["data_date"] is None
    assert "净值暂不可用" in captured[1]["warning"]
    assert captured[1]["profile"] is None
    assert not list(tmp_path.iterdir())
