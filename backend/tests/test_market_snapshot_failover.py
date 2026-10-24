from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.custom.global_markets import hk_sina_snapshot, hk_tencent_etf, us_screeners
from app.custom.global_markets import market_snapshot as ms
from app.custom.global_markets.catalog import MarketDataError
from app.data_providers import source_routing
from app.data_providers.source_health import SourceHealth

QUOTE_AT_MS = int(datetime(2026, 10, 16, 8, tzinfo=UTC).timestamp() * 1000)


@pytest.fixture(autouse=True)
def isolate_failover(monkeypatch):
    monkeypatch.setattr(source_routing, "HEALTH", SourceHealth(base_cooldown=0, max_cooldown=0))
    monkeypatch.setattr(source_routing, "enabled", lambda: True)


def _quote(market: str, symbol: str, source: str) -> dict:
    return {
        "market": market,
        "symbol": symbol,
        "name": symbol,
        "price": 10.0,
        "change_pct": 0.02,
        "volume": 100.0,
        "amount": 1000.0,
        "turnover_rate": 0.01,
        "industry": "测试行业",
        "quote_at_ms": QUOTE_AT_MS,
        "fetched_at_ms": QUOTE_AT_MS,
        "source": source,
        "kind": "stock" if market == "hk" else "security",
        "status": "ok",
        "stale": False,
        "error": None,
    }


def _old_complete_state(market: str) -> dict:
    scope = "hk_mainboard" if market == "hk" else "us_listed_securities"
    return {
        "data": {"market": market, "generation": 1, "scope": scope, "source": "eastmoney_public"},
        "quotes": {f"OLD.{market.upper()}": {"symbol": f"OLD.{market.upper()}", "price": 9.0}},
        "error": None,
        "stale": False,
        "loading": True,
        "completed": 0.0,
    }


@pytest.mark.parametrize("market", ["hk", "us"])
def test_enabled_failover_replaces_full_snapshot_with_limited_scope_without_mixing_quotes(monkeypatch, market):
    state = _old_complete_state(market)
    old_quote = next(iter(state["quotes"]))
    primary_calls = []

    def fail_primary(requested_market):
        primary_calls.append(requested_market)
        raise MarketDataError("主快照断连")

    monkeypatch.setattr(ms, "fetch", fail_primary)
    if market == "hk":
        sina = _quote("hk", "00700.HK", "sina_hk_public")
        etf = _quote("hk", "03033.HK", "tencent_etf")
        monkeypatch.setattr(hk_sina_snapshot, "fetch_pool", lambda: ([sina], 1))
        monkeypatch.setattr(hk_tencent_etf, "fetch_etf_pool", lambda: ([etf], 1))
        expected_symbols = {"00700.HK", "03033.HK"}
        expected_scope = "hk_sina_tencent_hkd_pool"
    else:
        row = _quote("us", "AAPL.US", "yahoo_screener")
        monkeypatch.setattr(us_screeners, "fetch_us_screener_pool", lambda: {"quotes": [row]})
        expected_symbols = {"AAPL.US"}
        expected_scope = "us_screener_pool"

    ms._load(market, state)

    assert primary_calls == [market]
    assert state["data"]["scope"] == expected_scope
    assert set(state["quotes"]) == expected_symbols
    assert old_quote not in state["quotes"]
    assert state["data"]["warnings"]
    warning_text = " ".join(state["data"]["warnings"])
    assert "主快照不可用" in warning_text
    assert "有限覆盖池" in warning_text
    assert "快照不能直接比较" in warning_text
    assert not state["stale"] and state["error"] is None and not state["loading"]


@pytest.mark.parametrize("market", ["hk", "us"])
def test_enabled_primary_and_backup_failure_keeps_old_complete_snapshot_stale(monkeypatch, market):
    state = _old_complete_state(market)
    old_data = state["data"].copy()
    old_quotes = {key: value.copy() for key, value in state["quotes"].items()}
    backup_calls = []

    def fail_primary(_market):
        raise MarketDataError("主快照断连")

    def fail_backup():
        backup_calls.append(True)
        raise MarketDataError("备用池断连")

    monkeypatch.setattr(ms, "fetch", fail_primary)
    if market == "hk":
        monkeypatch.setattr(hk_sina_snapshot, "fetch_pool", fail_backup)
    else:
        monkeypatch.setattr(us_screeners, "fetch_us_screener_pool", fail_backup)

    ms._load(market, state)

    assert backup_calls == [True]
    assert state["data"] == old_data
    assert state["quotes"] == old_quotes
    assert state["stale"] and state["error"] == "备用池断连" and not state["loading"]


@pytest.mark.parametrize("market", ["hk", "us"])
def test_disabled_failover_preserves_old_full_snapshot_behavior(monkeypatch, market):
    state = _old_complete_state(market)
    old_data = state["data"].copy()
    old_quotes = {key: value.copy() for key, value in state["quotes"].items()}
    backup_calls = []

    monkeypatch.setattr(source_routing, "enabled", lambda: False)
    monkeypatch.setattr(ms, "fetch", lambda _market: (_ for _ in ()).throw(MarketDataError("主快照断连")))
    monkeypatch.setattr(hk_sina_snapshot, "fetch_pool", lambda: backup_calls.append(True))
    monkeypatch.setattr(us_screeners, "fetch_us_screener_pool", lambda: backup_calls.append(True))

    ms._load(market, state)

    assert backup_calls == []
    assert state["data"] == old_data
    assert state["quotes"] == old_quotes
    assert state["stale"] and state["error"] == "主快照断连" and not state["loading"]
