"""基金中心扩展契约测试 (不依赖真实网络与 API Key)。

覆盖:
1. 字段映射与单位转换: 百分数→小数制、volume 股/份→手、缺失字段置 None 不伪造
2. ETF 日K前复权如实标注 (adjusted=forward), 不冒充原始价
3. 空数据 / 缺字段 / 接口业务错误 (3004) 的软失败语义
4. get_realtime_etfs: 映射、上限 60、异常 → None (与空列表区分)
5. 自选持久化 roundtrip
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.fund import routes as fund_routes
from app.custom.fund import service as svc
from app.custom.fund.client import FundError, FuyaoFundClient
from app.plugins.fuyao import client as fc
from app.plugins.fuyao.provider import FuyaoProvider


@pytest.fixture(autouse=True)
def isolated_research(monkeypatch):
    monkeypatch.setattr(fund_routes, "_cache", svc.TTLCache())
    monkeypatch.setattr(fund_routes, "_research_cache", svc.TTLCache(max_entries=96))
    monkeypatch.setattr(svc, "eastmoney_nav", lambda *a: [])
    monkeypatch.setattr(svc, "fund_research", lambda *_args, **_kwargs: {
        "fees": {"status": "unavailable"}, "risk": {"status": "unavailable"},
        "holdings": {"status": "unavailable"},
    })

# ---------- map_quote ----------

_FUND_ROW = {
    "thscode": "510300.SH",
    "ticker": "510300",
    "last_price": 4.753,
    "open_price": 4.775,
    "high_price": 4.825,
    "low_price": 4.724,
    "prev_price": 4.838,
    "price_change_ratio_pct": -1.756924,  # 百分数原值
    "price_change": -0.085,
    "price_amplitude_ratio_pct": 2.08764,
    "volume": 1657822800,  # 股
    "turnover": 7909234100,
    "turnover_ratio_pct": 9.012068,
    "timestamp": 1784210584000,
}


def test_map_quote_units():
    q = svc.map_quote(_FUND_ROW, name="沪深300ETF")
    assert q["symbol"] == "510300.SH"
    assert q["name"] == "沪深300ETF"
    # 百分数 → 小数制
    assert q["change_pct"] == pytest.approx(-1.756924 / 100)
    assert q["amplitude"] == pytest.approx(2.08764 / 100)
    assert q["turnover_rate"] == pytest.approx(9.012068 / 100)
    # 股 → 手
    assert q["volume"] == 1657822800 // 100
    assert q["amount"] == 7909234100
    assert q["prev_close"] == 4.838


def test_map_quote_derives_change_pct():
    row = dict(_FUND_ROW)
    row.pop("price_change_ratio_pct")
    q = svc.map_quote(row)
    # change_pct = change_amount / prev_close (小数制, 不乘 100)
    assert q["change_pct"] == pytest.approx(-0.085 / 4.838)


def test_map_quote_missing_fields_are_none_not_fabricated():
    q = svc.map_quote({"thscode": "510300.SH", "last_price": 4.75})
    assert q["prev_close"] is None
    assert q["change_pct"] is None  # 无法推导时置 None, 不启发式补全
    assert q["volume"] is None
    assert q["amplitude"] is None


def test_map_quote_no_thscode_returns_none():
    assert svc.map_quote({"last_price": 1.0}) is None


# ---------- map_holdings ----------

def _holding_row(**overrides):
    row = {
        "thscode": "600519.SH",
        "stock_name": "贵州茅台",
        "hold_ratio": 5.72,
        "asset_type": "stock",
        "start_date_ms": 1774972800000,
        "end_date_ms": 1782748800000,
        "publish_date_ms": 1784563200000,
    }
    row.update(overrides)
    return row


def test_map_holdings_maps_report_and_publication_dates_to_items_and_skipped():
    data = {
        "timestamp": 1784563200000,
        "stock_ratio_pct": 43.21,
        "total_stock_ratio_pct": 89.21,
        "concentration_ratio": 0.52,
        "item": [
            _holding_row(),
            _holding_row(thscode="110022.SH", stock_name="转债", asset_type="bond"),
        ],
    }

    out = svc.map_holdings(data)

    assert out["stock_ratio_pct"] == 43.21
    assert out["total_stock_ratio_pct"] == 89.21
    assert out["concentration_ratio"] == 0.52
    assert out["report_start_date"] == "2026-04-01"
    assert out["report_date"] == "2026-06-30"
    assert out["publication_date"] == "2026-07-21"
    assert out["report_periods"] == [{
        "report_start_date": "2026-04-01",
        "report_date": "2026-06-30",
        "publication_dates": ["2026-07-21"],
        "item_count": 2,
        "missing_publication_date_count": 0,
    }]
    for row in [*out["items"], *out["skipped"]]:
        assert row["report_start_date"] == "2026-04-01"
        assert row["report_date"] == "2026-06-30"
        assert row["publication_date"] == "2026-07-21"


def test_map_holdings_missing_dates_do_not_fall_back_to_root_timestamps():
    out = svc.map_holdings({
        "timestamp": 1784563200000,
        "item": [_holding_row(
            start_date_ms=None,
            end_date_ms=None,
            publish_date_ms=None,
            modify_time_ms=1784563200000,
        )],
    })

    assert out["report_start_date"] is None
    assert out["report_date"] is None
    assert out["publication_date"] is None
    assert out["report_periods"] == [{
        "report_start_date": None,
        "report_date": None,
        "publication_dates": [],
        "item_count": 1,
        "missing_publication_date_count": 1,
    }]
    row = out["items"][0]
    assert row["report_start_date"] is None
    assert row["report_date"] is None
    assert row["publication_date"] is None


def test_map_holdings_mixed_report_periods_leave_summary_dates_empty():
    out = svc.map_holdings({"item": [
        _holding_row(),
        _holding_row(
            thscode="000001.SZ",
            stock_name="平安银行",
            start_date_ms=1767225600000,
            end_date_ms=1774915200000,
            publish_date_ms=1776297600000,
        ),
    ]})

    assert out["report_start_date"] is None
    assert out["report_date"] is None
    assert out["publication_date"] is None
    assert out["report_periods"] == [
        {
            "report_start_date": "2026-01-01",
            "report_date": "2026-03-31",
            "publication_dates": ["2026-04-16"],
            "item_count": 1,
            "missing_publication_date_count": 0,
        },
        {
            "report_start_date": "2026-04-01",
            "report_date": "2026-06-30",
            "publication_dates": ["2026-07-21"],
            "item_count": 1,
            "missing_publication_date_count": 0,
        },
    ]


def test_map_holdings_different_publication_dates_do_not_get_one_summary_date():
    out = svc.map_holdings({"item": [
        _holding_row(),
        _holding_row(
            thscode="000001.SZ",
            stock_name="平安银行",
            asset_type="bond",
            publish_date_ms=1784649600000,
        ),
    ]})

    assert out["report_date"] == "2026-06-30"
    assert out["publication_date"] is None
    assert out["report_periods"] == [{
        "report_start_date": "2026-04-01",
        "report_date": "2026-06-30",
        "publication_dates": ["2026-07-21", "2026-07-22"],
        "item_count": 2,
        "missing_publication_date_count": 0,
    }]


def test_map_holdings_dates_are_converted_to_beijing_calendar_date():
    out = svc.map_holdings({"item": [_holding_row(
        start_date_ms=1774972800000,
        end_date_ms=1782763200000,  # 2026-06-29 20:00 UTC, June 30 in Beijing
        publish_date_ms=1784563200000,
    )]})

    assert out["items"][0]["report_date"] == "2026-06-30"
    assert out["report_date"] == "2026-06-30"


@pytest.mark.parametrize("invalid_ms", [float("inf"), float("nan"), 10**100, -1])
def test_map_holdings_invalid_dates_remain_unavailable(invalid_ms):
    out = svc.map_holdings({"item": [_holding_row(
        start_date_ms=invalid_ms,
        end_date_ms=invalid_ms,
        publish_date_ms=invalid_ms,
    )]})

    assert out["items"][0]["report_date"] is None
    assert out["items"][0]["publication_date"] is None
    assert out["report_date"] is None


# ---------- map_kline ----------

def test_map_kline_marks_forward_adjusted():
    rows = [
        {"date_ms": 1784150400000, "open_price": 4.78, "high_price": 4.83,
         "low_price": 4.72, "close_price": 4.75, "volume": 1000000, "turnover": 4750000},
    ]
    out = svc.map_kline(rows)
    # 关键契约: 上游前复权, 必须如实标注, 不得冒充不复权原始价
    assert out["adjusted"] == "forward"
    bar = out["bars"][0]
    assert bar["close"] == 4.75
    assert bar["volume"] == 10000  # 股 → 手
    assert len(bar["date"]) == 10  # YYYY-MM-DD


def test_map_kline_empty_and_bad_dates():
    assert svc.map_kline([]) == {"adjusted": "forward", "bars": []}
    out = svc.map_kline([{"date_ms": "bad", "close_price": 1.0}])
    assert out["bars"] == []  # 无效日期整行跳过, 不抛异常


# ---------- map_nav ----------

def test_map_nav_sorted_and_empty():
    rows = [
        {"nav_date": 1784150400000, "unit_nav": 1.4753, "adj_nav": 1.4801},
        {"nav_date": 1784064000000, "unit_nav": 1.4700, "adj_nav": 1.4748},
    ]
    nav = svc.map_nav(rows)
    assert [n["nav_date"] for n in nav] == sorted(n["nav_date"] for n in nav)
    assert nav[0]["unit_nav"] == 1.47
    assert svc.map_nav([]) == []
    # 缺 nav_date 的行跳过
    assert svc.map_nav([{"unit_nav": 1.0}]) == []


def test_map_search_kind_labels():
    rows = [
        {"thscode": "005827.OF", "ticker": "005827", "name": "易方达蓝筹精选",
         "asset_type": "fund-otc"},
        {"thscode": "510300.SH", "ticker": "510300", "name": "沪深300ETF",
         "asset_type": "fund-etf"},
    ]
    items = svc.map_search(rows)
    assert items[0]["kind_label"] == "场外基金"
    assert items[1]["kind_label"] == "ETF"
    assert svc.map_search([{"ticker": "x"}]) == []  # 无 thscode 跳过


# ---------- TTLCache ----------

def test_ttl_cache_hit_expire_invalidate():
    c = svc.TTLCache()
    c.set("a", 1, ttl_s=60)
    assert c.get("a") == 1
    c.set("b", 2, ttl_s=-1)  # 已过期
    assert c.get("b") is None
    c.set("quote:X", 1, ttl_s=60)
    c.set("nav:X", 2, ttl_s=60)
    c.invalidate("quote:")
    assert c.get("quote:X") is None
    assert c.get("nav:X") == 2


# ---------- FuyaoFundClient: 3004 软失败 ----------

class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code != 200:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeHTTP:
    def __init__(self, payload):
        self._payload = payload

    def get(self, url, params=None, headers=None):
        return _FakeResp(self._payload)


def _fund_client(payload: dict) -> FuyaoFundClient:
    c = FuyaoFundClient(api_key="test-key")
    c._client = _FakeHTTP(payload)
    return c


def test_snapshot_3004_returns_none_not_error():
    """场外基金调快照端点: 上游 code=3004 → None, 不是异常。"""
    c = _fund_client({"code": 3004, "message": "This fund does not support market data"})
    assert c.snapshot("005827.OF") is None


def test_snapshot_3001_stock_returns_none():
    """股票代码调快照端点: 上游 code=3001 → None (单只跳过), 不是异常。"""
    c = _fund_client({"code": 3001, "message": "Fund not found: 600519.SH"})
    assert c.snapshot("600519.SH") is None


def test_snapshot_other_error_raises():
    c = _fund_client({"code": 1002, "message": "Unknown thscode"})
    with pytest.raises(FundError, match="code=1002"):
        c.snapshot("XXXX.XX")


def test_snapshot_ok_returns_first_item():
    c = _fund_client({"code": 0, "data": {"timestamp": 1, "item": [_FUND_ROW]}})
    assert c.snapshot("510300.SH")["thscode"] == "510300.SH"


# ---------- FuyaoProvider.get_realtime_etfs ----------

class _FakeFundClient:
    def __init__(self, rows: dict[str, dict | None], error: Exception | None = None):
        self.rows = rows
        self.error = error
        self.calls: list[str] = []

    def fund_snapshot(self, thscode: str):
        self.calls.append(thscode)
        if self.error:
            raise self.error
        # 真实 client 的 3004 语义: 内部消化返回 None (单只跳过), 不抛异常
        return self.rows.get(thscode)


def _etf_provider(monkeypatch, fake: _FakeFundClient) -> FuyaoProvider:
    p = FuyaoProvider()
    monkeypatch.setattr(p, "_get_client", lambda: fake)
    return p


def test_get_realtime_etfs_mapping(monkeypatch):
    fake = _FakeFundClient({"510300.SH": dict(_FUND_ROW)})
    p = _etf_provider(monkeypatch, fake)
    recs = p.get_realtime_etfs(["510300.SH"])
    assert len(recs) == 1
    r = recs[0]
    assert r["symbol"] == "510300.SH"
    assert r["change_pct"] == pytest.approx(-1.756924 / 100)
    assert r["volume"] == 1657822800 // 100  # 股 → 手
    assert r["amplitude"] == pytest.approx(2.08764 / 100)
    assert r["turnover_rate"] == pytest.approx(9.012068 / 100)


def test_get_realtime_etfs_skips_non_etf(monkeypatch):
    """股票/场外基金 (3004) 单只跳过, 不影响其他。"""
    fake = _FakeFundClient({"510300.SH": dict(_FUND_ROW)})
    p = _etf_provider(monkeypatch, fake)
    recs = p.get_realtime_etfs(["600519.SH", "510300.SH", "005827.OF"])
    assert [r["symbol"] for r in recs] == ["510300.SH"]
    assert fake.calls == ["600519.SH", "510300.SH", "005827.OF"]


def test_get_realtime_etfs_empty_and_error(monkeypatch):
    p = _etf_provider(monkeypatch, _FakeFundClient({}))
    assert p.get_realtime_etfs([]) == []  # 空输入 → 空列表
    p2 = _etf_provider(monkeypatch, _FakeFundClient({}, error=fc.FuyaoError("限流")))
    assert p2.get_realtime_etfs(["510300.SH"]) is None  # 异常 → None (保上轮缓存)


def test_get_realtime_etfs_caps_at_60(monkeypatch):
    fake = _FakeFundClient({})
    p = _etf_provider(monkeypatch, fake)
    p.get_realtime_etfs([f"{i:06d}.SH" for i in range(100)])
    assert len(fake.calls) == 60


# ---------- watchlist 持久化 ----------

def test_watchlist_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "fund_watchlist.json"
    monkeypatch.setattr(svc, "_watchlist_path", lambda: p)
    assert svc.load_watchlist() == []
    items = [{"thscode": "005827.OF", "name": "易方达蓝筹精选", "added_at": 1}]
    svc.save_watchlist(items)
    assert svc.load_watchlist() == items
    # 坏文件不抛异常, 返回 []
    p.write_text("not json", encoding="utf-8")
    assert svc.load_watchlist() == []


def _fund_api() -> TestClient:
    app = FastAPI()
    app.include_router(fund_routes.build_router())
    return TestClient(app)


def test_nav_uses_local_fund_proxy_without_fuyao_key(monkeypatch):
    monkeypatch.setattr(fund_routes, "_optional_client", lambda: None)
    monkeypatch.setattr(svc, "akshare_nav", lambda code, range_: [
        {"nav_date": "2026-09-28", "unit_nav": 1.25, "adj_nav": None}
    ])
    result = _fund_api().get("/api/custom/fund/nav/011370.OF?range=year")
    assert result.status_code == 200
    assert result.json() == {
        "thscode": "011370.OF",
        "nav": [{"nav_date": "2026-09-28", "unit_nav": 1.25, "adj_nav": None}],
        "source": "akshare",
    }


def test_nav_reports_real_source_failure_without_fuyao_key(monkeypatch):
    monkeypatch.setattr(fund_routes, "_optional_client", lambda: None)
    monkeypatch.setattr(svc, "akshare_nav", lambda code, range_: [])
    result = _fund_api().get("/api/custom/fund/nav/011371.OF?range=year")
    assert result.status_code == 503
    assert "本机基金数据服务" in result.json()["detail"]


def test_nav_reports_both_sources_unavailable_with_fuyao_key(monkeypatch):
    class EmptyClient:
        def nav(self, *args, **kwargs):
            return []

    monkeypatch.setattr(fund_routes, "_optional_client", EmptyClient)
    monkeypatch.setattr(svc, "akshare_nav", lambda code, range_: [])
    result = _fund_api().get("/api/custom/fund/nav/012345.OF?range=year")
    assert result.status_code == 503
    assert "扶摇与本机" in result.json()["detail"]


def test_profile_uses_local_fund_proxy_without_fuyao_key(monkeypatch):
    monkeypatch.setattr(fund_routes, "_optional_client", lambda: None)
    monkeypatch.setattr(svc, "akshare_profile", lambda code: {
        "thscode": code, "ticker": "011370", "fund_name": "测试基金",
        "estab_date": None, "mgmt_name": None, "manager_name": None,
        "fund_type": "混合型", "benchmark": None,
    })
    result = _fund_api().get("/api/custom/fund/profile/011370.OF")
    assert result.status_code == 200
    assert result.json()["profile"]["fund_name"] == "测试基金"
    assert result.json()["source"] == "akshare"


def test_profile_cache_preserves_source(monkeypatch):
    monkeypatch.setattr(fund_routes, "_optional_client", lambda: None)
    calls = []

    def local_profile(code):
        calls.append(code)
        return {"fund_name": "缓存测试基金"}

    monkeypatch.setattr(svc, "akshare_profile", local_profile)
    client = _fund_api()
    first = client.get("/api/custom/fund/profile/023456.OF")
    second = client.get("/api/custom/fund/profile/023456.OF")
    assert first.json() == second.json()
    assert second.json()["source"] == "akshare"
    assert calls == ["023456.OF"]


def test_search_uses_local_fund_proxy_without_fuyao_key(monkeypatch):
    monkeypatch.setattr(fund_routes, "_optional_client", lambda: None)
    monkeypatch.setattr(svc, "akshare_search", lambda q, limit: [
        {"thscode": "011370.OF", "name": "测试基金C", "asset_type": "fund-otc"},
    ])
    result = _fund_api().get("/api/custom/fund/search?q=011370")
    assert result.status_code == 200
    assert result.json()["items"][0]["name"] == "测试基金C"


def test_akshare_nav_preserves_missing_adjusted_nav(monkeypatch):
    monkeypatch.setattr(svc, "_akshare_proxy_json", lambda *args, **kwargs: {
        "nav": [
            {"date": "2026-09-27", "unit_nav": 1.1},
            {"date": "2026-09-28", "unit_nav": 1.2},
            {"date": "bad", "unit_nav": 1.3},
        ],
    })
    assert svc.akshare_nav("011370.OF", "week") == [
        {"nav_date": "2026-09-27", "unit_nav": 1.1, "adj_nav": None},
        {"nav_date": "2026-09-28", "unit_nav": 1.2, "adj_nav": None},
    ]


def test_akshare_rank_normalizes_percent_and_missing_values(monkeypatch):
    monkeypatch.setattr(svc, "_akshare_proxy_json", lambda *args, **kwargs: {
        "items": [
            {"code": "011370", "name": "测试基金C", "growth_1m": "12.5%", "growth_1y": "—"},
            {"code": "bad", "name": "无效基金", "growth_1m": 99},
        ],
    })
    rows = svc.akshare_rank("混合型", sort_by="1m")
    assert len(rows) == 1
    assert rows[0]["share_class"] == "C"
    assert rows[0]["growth_1m"] == 12.5
    assert rows[0]["growth_1y"] is None


def test_ai_pick_uses_only_filtered_rank_candidates(monkeypatch):
    from app.services import ai_provider

    rows = [
        {"code": "011370", "name": "测试基金C", "share_class": "C", "growth_1m": 5.0},
        {"code": "011371", "name": "测试基金A", "share_class": "A", "growth_1m": 8.0},
    ]
    monkeypatch.setattr(svc, "akshare_rank", lambda *args, **kwargs: rows)
    seen = []

    async def fake_stream(messages, **kwargs):
        seen.extend(messages)
        yield "### 研究候选\n011370 测试基金C"

    monkeypatch.setattr(ai_provider, "stream_ai_text", fake_stream)
    response = _fund_api().post("/api/custom/fund/screener/ai", json={
        "fund_type": "混合型", "horizon": "1m", "share": "C",
    })
    assert response.status_code == 200
    events = [__import__("json").loads(line) for line in response.text.splitlines() if line]
    meta = next(event for event in events if event["type"] == "meta")
    assert [item["code"] for item in meta["candidates"]] == ["011370"]
    assert "011371" not in seen[1]["content"]
    assert events[-1]["type"] == "done"


def test_local_proxy_sorts_before_limiting_short_horizon(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace

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

    monkeypatch.setitem(
        sys.modules, "akshare", SimpleNamespace(fund_open_fund_rank_em=lambda **_: Frame())
    )
    path = Path(__file__).resolve().parents[2] / "bin" / "akshare-proxy.py"
    spec = importlib.util.spec_from_file_location("test_akshare_proxy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.fund_rank("混合型", limit=1, sort_by="1m")[0]["code"] == "011370"
    assert module.fund_rank("混合型", limit=1, sort_by="1y")[0]["code"] == "011371"
