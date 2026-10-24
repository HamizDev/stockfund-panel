"""Deterministic tests for the Sina + Tencent HKD snapshot fallback."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.custom.global_markets import hk_sina_snapshot, hk_tencent_etf, market_snapshot
from app.custom.global_markets.catalog import MarketDataError

_NOW_MS = round(datetime(2026, 10, 20, 16, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")).timestamp() * 1000)


def _quote(
    symbol: str,
    name: str,
    *,
    kind: str,
    source: str,
    price: float,
) -> dict:
    return {
        "market": "hk",
        "symbol": symbol,
        "name": name,
        "kind": kind,
        "currency": "HKD",
        "price": price,
        "change_pct": 0.01,
        "amount": None,
        "volume": None,
        "turnover_rate": None,
        "industry": None,
        "quote_at_ms": _NOW_MS,
        "fetched_at_ms": _NOW_MS,
        "source": source,
        "scope": "hk_sina_hkd_pool" if source == "sina_hk_public" else "hk_etf_tencent_batch",
        "status": "ok",
        "stale": False,
        "error": None,
    }


def _prepare_fallback(monkeypatch: pytest.MonkeyPatch, sina_rows: list[dict], sina_total: int) -> None:
    monkeypatch.setattr(
        market_snapshot,
        "fetch",
        lambda _market: (_ for _ in ()).throw(MarketDataError("primary unavailable")),
    )
    monkeypatch.setattr(hk_sina_snapshot, "fetch_pool", lambda: (sina_rows, sina_total))


def _load_hk() -> dict:
    state = {"data": None, "quotes": {}, "error": None, "stale": False, "loading": True}
    market_snapshot._load("hk", state)
    return state


def test_snapshot_merge_deduplicates_with_tencent_preferred_and_discloses_coverage(monkeypatch: pytest.MonkeyPatch):
    sina_rows = [
        _quote("02800.HK", "新浪旧名", kind="etf", source="sina_hk_public", price=24.0),
        _quote("00700.HK", "腾讯控股", kind="stock", source="sina_hk_public", price=500.0),
        _quote("09988.HK", "阿里巴巴", kind="stock", source="sina_hk_public", price=150.0),
    ]
    tencent_rows = [
        _quote("02800.HK", "盈富基金", kind="etf", source="tencent_hk_etf", price=24.54),
        _quote("03033.HK", "南方恒生科技", kind="etf", source="tencent_hk_etf", price=4.5),
    ]
    _prepare_fallback(monkeypatch, sina_rows, 3)
    monkeypatch.setattr(hk_tencent_etf, "fetch_etf_pool", lambda: (tencent_rows, 2))

    state = _load_hk()
    data = state["data"]

    assert state["error"] is None
    assert data["source"] == "sina_hk_public+tencent_etf"
    assert data["scope"] == "hk_sina_tencent_hkd_pool"
    assert data["total_reported"] == 4  # 3 Sina + 2 Tencent, minus one overlapping symbol.
    assert data["received"] == 4
    assert data["complete"] is True
    assert data["etf_coverage"] == {"requested": 2, "accepted": 2, "unavailable": 0}
    assert state["quotes"]["02800.HK"]["source"] == "tencent_hk_etf"
    assert state["quotes"]["02800.HK"]["price"] == 24.54
    assert len(state["quotes"]) == 4
    assert any("不代表港股全市场" in warning for warning in data["warnings"])


def test_snapshot_merge_marks_partial_etf_coverage_incomplete(monkeypatch: pytest.MonkeyPatch):
    sina_rows = [_quote("00700.HK", "腾讯控股", kind="stock", source="sina_hk_public", price=500.0)]
    tencent_rows = [_quote("02800.HK", "盈富基金", kind="etf", source="tencent_hk_etf", price=24.54)]
    _prepare_fallback(monkeypatch, sina_rows, 1)
    monkeypatch.setattr(hk_tencent_etf, "fetch_etf_pool", lambda: (tencent_rows, 2))

    data = _load_hk()["data"]

    assert data["source"] == "sina_hk_public+tencent_etf"
    assert data["scope"] == "hk_sina_tencent_hkd_pool"
    assert data["total_reported"] == 3
    assert data["received"] == 2
    assert data["complete"] is False
    assert data["etf_coverage"] == {"requested": 2, "accepted": 1, "unavailable": 1}


@pytest.mark.parametrize("failure", ["empty", "error"])
def test_snapshot_merge_falls_back_to_sina_without_claiming_tencent_source(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
):
    sina_rows = [_quote("00700.HK", "腾讯控股", kind="stock", source="sina_hk_public", price=500.0)]
    _prepare_fallback(monkeypatch, sina_rows, 1)
    if failure == "empty":
        monkeypatch.setattr(hk_tencent_etf, "fetch_etf_pool", lambda: ([], 2))
    else:
        monkeypatch.setattr(
            hk_tencent_etf,
            "fetch_etf_pool",
            lambda: (_ for _ in ()).throw(MarketDataError("Tencent unavailable")),
        )

    state = _load_hk()
    data = state["data"]

    assert state["error"] is None
    assert data["source"] == "sina_hk_public"
    assert data["scope"] == "hk_sina_hkd_pool"
    assert data["total_reported"] == 1
    assert data["received"] == 1
    assert "etf_coverage" not in data
    assert any("腾讯" in warning for warning in data["warnings"])
    assert set(state["quotes"]) == {"00700.HK"}
