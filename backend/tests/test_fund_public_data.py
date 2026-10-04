"""Free fund data contracts: no network, personal configuration or AI account."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from app.custom.fund import analyzer, routes, service
from app.custom.fund import public_data as public
from fastapi import FastAPI
from fastapi.testclient import TestClient


def millis(day):
    return datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000


def sample_research(code="011370.OF", horizon="1y"):
    return {"thscode": code, "horizon": horizon, "source": "eastmoney", "retrieved_at_ms": 1,
            "nav_date": None, "fees": public.parse_fees("", code[:6]),
            "risk": public.parse_risk({}, code[:6], horizon),
            "holdings": public.parse_holdings("", code[:6]), "missing_fields": [], "warnings": []}


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setattr(routes, "_cache", service.TTLCache())
    monkeypatch.setattr(routes, "_research_cache", service.TTLCache(max_entries=96))
    app = FastAPI()
    app.include_router(routes.build_router())
    return TestClient(app)


def test_fee_tiers_keep_fixed_amount_and_discount_distinct():
    html = """<h4>运作费用</h4><table><tr><td>管理费率</td><td>1.20%\uFF08每年\uFF09</td>
      <td>托管费率</td><td>0.20%\uFF08每年\uFF09</td><td>销售服务费率</td><td>---</td></tr></table>
      <h4>申购费率\uFF08前端\uFF09</h4><table><tr><th>适用金额</th><th>原费率|优惠</th></tr>
      <tr><td>小于100万</td><td>1.50% | 0.15%</td></tr>
      <tr><td>大于1000万</td><td>每笔1000元</td></tr></table>
      <h4>赎回费率</h4><table><tr><td>小于7天</td><td>1.50%</td></tr></table>"""
    out = public.parse_fees(html, "000001")
    assert out["management_pct"] == 1.2
    assert out["custody_pct"] == 0.2
    assert out["sales_service_pct"] is None
    assert out["subscription_rules"][0]["rate_text"] == "1.50%"
    assert out["subscription_rules"][0]["discount_text"] == "0.15%"
    assert out["subscription_rules"][1]["rate_text"] == "每笔1000元"
    assert out["redemption_rules"][0]["condition"].endswith("小于7天")
    assert out["as_of"] is None


def test_empty_fees_are_unknown_not_zero():
    out = public.parse_fees("<html>没有公开字段</html>", "011370")
    assert out["status"] == "unavailable"
    assert out["sales_service_pct"] is None


def test_holdings_select_latest_period_not_largest_table_and_preserve_market():
    def table(title, symbol, weight):
        return f"""<h4>{title}</h4><table><tr><th>股票代码</th><th>股票名称</th><th>占净值<br>比例</th></tr>
        <tr><td>{symbol}</td><td>测试</td><td>{weight}%</td></tr></table>"""
    out = public.parse_holdings(table("2026年1季度", "600519", 8) + table("2026年2季度", "00700", 5.2), "011370")
    assert out["report_date"] == "2026-06-30"
    assert out["publication_date"] is None
    assert out["items"][0]["thscode"] == "00700.HK"
    assert out["coverage_weight_pct"] == 5.2


def test_invalid_or_duplicated_weights_do_not_inflate_holdings():
    html = """<h4>2026年2季度</h4><table><tr><th>股票代码</th><th>占净值比例</th></tr>
    <tr><td>600519</td><td>5%</td></tr><tr><td>600519</td><td>5%</td></tr>
    <tr><td>000001</td><td>-4%</td></tr><tr><td>000002</td><td>NaN%</td></tr></table>"""
    out = public.parse_holdings(html, "011370")
    assert len(out["items"]) == 1
    assert out["coverage_weight_pct"] == 5


def test_archive_string_decodes_without_executing_remote_js():
    assert public._archive_content('var x={content:"<table>\\n</table>"}; alert(1)') == "<table>\n</table>"
    with pytest.raises(ValueError):
        public._archive_content('var x={content:alert(1)}')


def test_drawdown_uses_wealth_index_not_percentage_peak():
    payload = {"Data": [{"data": [[millis("2026-07-01"), 10], [millis("2026-07-02"), 20],
                                  [millis("2026-07-03"), -4]]}, {"data": [[0, 99]]}]}
    out = public.parse_risk(payload, "011370", "1m")
    assert out["max_drawdown_pct"] == 20  # 1.20 -> 0.96, not (20 - -4) / 20
    assert out["basis"] == "source_return_series"
    assert out["observations"] == 3
    assert out["end_date"] == "2026-07-03"


def test_drawdown_uses_selected_window_and_not_benchmark():
    payload = {"Data": [{"data": [[millis("2025-01-01"), 200], [millis("2026-09-01"), 10],
                                  [millis("2026-09-02"), 0]]}]}
    assert public.parse_risk(payload, "011370", "1m")["max_drawdown_pct"] == pytest.approx(9.0909)
    assert public.parse_risk({"Data": []}, "011370", "1y")["max_drawdown_pct"] is None
    assert public.parse_risk({"Data": [{"data": [[0, -100], [0, None]]}]}, "011370", "1y")["status"] == "unavailable"


def test_related_report_dates_do_not_become_holdings_publication():
    reports = [{"TITLE": "测试2026年第2季度报告", "PUBLISHDATE": "2026-07-21T00:00:00", "ID": "AN123", "FUNDCODE": "011370"},
               {"TITLE": "测试2026年中期报告", "PUBLISHDATE": "2026-08-31T00:00:00", "ID": "AN124", "FUNDCODE": "011370"}]
    rows = public._related_reports(reports, "2026-06-30")
    assert [row["publication_date"] for row in rows] == ["2026-07-21", "2026-08-31"]


def test_source_failure_keeps_partial_data_and_no_fake_dates(monkeypatch):
    def fake_get(url, params=None):
        if "jjfl_" in url:
            return httpx.Response(200, text="<h4>运作费用</h4><table><tr><td>管理费率</td><td>0.60%</td></tr></table>")
        raise httpx.ReadTimeout("test")
    monkeypatch.setattr(public, "_get", fake_get)
    out = public.fetch_research("011370.OF")
    assert out["fees"]["management_pct"] == 0.6
    assert out["risk"]["max_drawdown_pct"] is None
    assert out["nav_date"] is None
    assert out["holdings"]["publication_date"] is None
    assert out["warnings"]


def test_research_route_validates_code_and_horizon_before_network(api_client, monkeypatch):
    monkeypatch.setattr(service, "fund_research", lambda *_args: pytest.fail("network called"))
    assert api_client.get("/api/custom/fund/research/011370.SH").status_code == 400
    assert api_client.get("/api/custom/fund/research/011370.OF?horizon=bad").status_code == 400


def test_research_cache_and_free_holdings_without_key(api_client, monkeypatch):
    calls = []
    def research(code, horizon):
        calls.append((code, horizon))
        out = sample_research(code, horizon)
        out["holdings"].update(status="ok", report_date="2026-06-30", coverage_weight_pct=5,
                               items=[{"thscode": "600519.SH", "name": "测试", "hold_ratio": 5, "asset_type": "stock"}])
        return out
    monkeypatch.setattr(service, "fund_research", research)
    monkeypatch.setattr(routes, "_optional_client", lambda: None)
    first = api_client.get("/api/custom/fund/research/011370.OF").json()
    assert api_client.get("/api/custom/fund/research/011370.OF").json() == first
    response = api_client.get("/api/custom/fund/holdings/011370.OF")
    assert response.status_code == 200
    assert response.json()["source"] == "eastmoney"
    assert response.json()["coverage_weight_pct"] == 5
    assert calls == [("011370.OF", "1y")]


def test_rank_keeps_per_fund_date_without_fabricated_cutoff(monkeypatch):
    monkeypatch.setattr(service, "_akshare_proxy_json", lambda *a, **k: {"items": [
        {"code": "011370", "name": "测试C", "nav": 1.2, "nav_date": "2026-09-29", "purchase_fee_text": "0.00%"},
        {"code": "011371", "name": "测试A", "nav_date": "NaT"}]})
    rows = service.akshare_rank("混合型")
    assert rows[0]["nav_date"] == "2026-09-29"
    assert rows[0]["nav"] == 1.2
    assert rows[1]["nav_date"] is None


def test_direct_rank_fallback_sorts_short_horizon_before_limit(monkeypatch):
    from app.custom.fund import public_nav

    def unavailable(*args, **kwargs):
        raise OSError("local proxy unavailable")
    monkeypatch.setattr(service, "_akshare_proxy_json", unavailable)
    monkeypatch.setattr(public_nav, "fetch_rank", lambda *a: [
        {"code": "011370", "name": "测试C", "growth_1m": 8, "growth_1y": 10},
        {"code": "011371", "name": "测试A", "growth_1m": 20, "growth_1y": 5}])
    rows = service.akshare_rank("混合型", sort_by="1m", limit=1)
    assert [row["code"] for row in rows] == ["011371"]
    assert rows[0]["source"] == "eastmoney"


@pytest.mark.parametrize("items", [[], None, "invalid"])
def test_rank_empty_or_invalid_proxy_payload_uses_public_fallback(monkeypatch, items):
    from app.custom.fund import public_nav

    monkeypatch.setattr(service, "_akshare_proxy_json", lambda *a, **k: {"items": items})
    monkeypatch.setattr(public_nav, "fetch_rank", lambda *a: [
        {"code": "011370", "name": "测试C", "growth_1y": 10}])
    rows = service.akshare_rank("混合型")
    assert len(rows) == 1
    assert rows[0]["source"] == "eastmoney"


def test_nav_route_reports_direct_source_and_no_fake_adjustment(api_client, monkeypatch):
    monkeypatch.setattr(routes, "_optional_client", lambda: None)
    monkeypatch.setattr(service, "akshare_nav", lambda *a: [])
    monkeypatch.setattr(service, "eastmoney_nav", lambda *a: [{"nav_date": "2026-09-29", "unit_nav": 1.2, "adj_nav": None}])
    response = api_client.get("/api/custom/fund/nav/011370.OF?range=year")
    assert response.status_code == 200
    assert response.json()["source"] == "eastmoney"
    assert response.json()["nav"][0]["adj_nav"] is None


def test_ai_screen_enriches_all_filtered_candidates_before_model(api_client, monkeypatch):
    from app.services import ai_provider

    monkeypatch.setattr(service, "akshare_rank", lambda *a, **k: [
        {"code": "011370", "name": "测试C", "share_class": "C", "growth_1m": 10},
        {"code": "011371", "name": "测试A", "share_class": "A", "growth_1m": 11}])
    monkeypatch.setattr(service, "fund_research", sample_research)
    seen = []
    async def stream(messages, **kwargs):
        seen.extend(messages)
        yield "测试"
    monkeypatch.setattr(ai_provider, "stream_ai_text", stream)
    response = api_client.post("/api/custom/fund/screener/ai", json={"horizon": "1m", "share": "C"})
    events = [json.loads(line) for line in response.text.splitlines()]
    enriched = [event for event in events if event["type"] == "research"]
    assert len(enriched) == 1
    assert enriched[0]["research"]["horizon"] == "1m"
    payload = json.loads(seen[1]["content"])
    assert payload["candidates"][0]["research"]["thscode"] == "011370.OF"
    assert "011371" not in seen[1]["content"]


def test_analyzer_gets_fees_report_period_and_correct_estimate_fields():
    prompt = analyzer._build_user_prompt([], None, "011370.OF", "测试", holdings={
        "items": [{"name": "持仓"}], "coverage_weight_pct": 35.5, "report_date": "2026-06-30"},
        estimate={"estimate": {"change_pct": 0.012, "est_nav": 1.5}}, research=sample_research())
    assert "35.5%" in prompt
    assert "2026-06-30" in prompt
    assert "0.012" in prompt and "1.5" in prompt
    assert "management_pct" in prompt


def test_analyzer_reports_window_and_unadjusted_basis():
    stats = analyzer._calc_stats([{"nav_date": "2026-09-27", "unit_nav": None},
                                 {"nav_date": "2026-09-28", "unit_nav": 1},
                                 {"nav_date": "2026-09-29", "unit_nav": 0.8},
                                 {"nav_date": "2026-09-30", "unit_nav": float("nan")}])
    assert stats["统计起始日"] == "2026-09-28"
    assert stats["统计截止日"] == "2026-09-29"
    assert "未经复权" in stats["统计口径"]
    assert "最大回撤%" not in stats


def test_research_cache_bounds_payloads_and_sweeps_expired(monkeypatch):
    clock = [0]
    monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])
    cache = service.TTLCache(max_entries=2)
    cache.set("a", 1, ttl_s=10)
    cache.set("b", 2, ttl_s=10)
    cache.set("c", 3, ttl_s=10)
    assert cache.get("a") is None
    clock[0] = 11
    cache.set("d", 4, ttl_s=10)
    assert len(cache._store) == 1


def test_model_projection_limits_holdings_without_overstating_coverage():
    out = sample_research()
    out["holdings"]["items"] = [{"thscode": f"{i:06d}.SZ", "hold_ratio": 1} for i in range(58)]
    out["holdings"]["coverage_weight_pct"] = 58
    model = service.research_model_context(out)
    assert len(model["holdings"]["items"]) == 10
    assert model["holdings"]["available_holdings_count"] == 58
    assert model["holdings"]["model_items_weight_pct"] == 10
    assert len(out["holdings"]["items"]) == 58


@pytest.mark.parametrize("payload", [{"Data": {"data": []}}, {"Data": [None]}, {"Data": [{"data": None}]}, []])
def test_shape_changes_in_risk_are_unavailable_not_whole_request_failure(payload):
    assert public.parse_risk(payload, "011370", "1y")["status"] == "unavailable"


def test_estimate_bounds_free_holdings_fanout(api_client, monkeypatch):
    out = sample_research()
    out["holdings"].update(status="ok", items=[
        {"thscode": f"{i:06d}.SZ", "name": "测试", "hold_ratio": 1} for i in range(58)])
    monkeypatch.setattr(routes, "_optional_client", lambda: None)
    monkeypatch.setattr(service, "fund_research", lambda *a: out)
    monkeypatch.setattr(service, "akshare_nav", lambda *a: [{"nav_date": "2026-09-29", "unit_nav": 1.2}])
    captured = []
    monkeypatch.setattr(service, "estimate_nav", lambda h, n: captured.append(h) or {})
    monkeypatch.setattr(service, "estimate_curve", lambda h, n: captured.append(h) or {})
    response = api_client.get("/api/custom/fund/estimate/011370.OF")
    assert response.status_code == 200
    assert all(len(h["items"]) == 10 for h in captured)
    assert response.json()["holdings"]["coverage_weight_pct"] == 10


@pytest.mark.asyncio
async def test_analysis_moves_collection_off_event_loop_and_passes_data(api_client, monkeypatch):
    monkeypatch.setattr(routes, "_optional_client", lambda: None)
    monkeypatch.setattr(routes, "_watchlist_names", lambda: {"011370.OF": "测试"})
    monkeypatch.setattr(service, "akshare_nav", lambda *a: [{"nav_date": "2026-09-29", "unit_nav": 1.2}])
    monkeypatch.setattr(service, "akshare_profile", lambda *a: {"fund_name": "测试"})
    monkeypatch.setattr(service, "fund_research", sample_research)
    seen = []
    async def analyze(nav, profile, thscode, name, focus, **kwargs):
        seen.append(kwargs)
        yield '{"type":"done"}'
    monkeypatch.setattr(analyzer, "analyze_fund_stream", analyze)
    response = await asyncio.to_thread(api_client.post, "/api/custom/fund/analyze", json={"thscode": "011370.OF"})
    assert response.status_code == 200
    assert seen[0]["research"]["thscode"] == "011370.OF"
    assert seen[0]["holdings"] is None
