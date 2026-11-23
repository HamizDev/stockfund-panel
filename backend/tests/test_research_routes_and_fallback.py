from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.global_markets.routes import build_router
from app.data_providers import custom, source_routing
from app.data_providers.source_health import SourceHealth
from app.data_providers.tickflow_provider import TickFlowProvider
from app.services import etf_integrity_reader


def test_small_etf_reads_can_fall_back_without_market_wide_scraping(monkeypatch):
    monkeypatch.setattr(source_routing, "enabled", lambda: True)
    monkeypatch.setattr(source_routing, "_daily_session", lambda _: date(2026, 11, 6))
    monkeypatch.setattr(source_routing, "HEALTH", SourceHealth())
    monkeypatch.setattr(TickFlowProvider, "get_daily", lambda *_: pl.DataFrame())
    primary = SimpleNamespace(name="stocksdk", get_daily=lambda *_a, **_kw: pl.DataFrame())
    calls = []

    def fetch(symbols, *_):
        calls.append(list(symbols))
        return pl.DataFrame([dict(
            symbol=s, date=date(2026, 11, 6), open=1.0, high=1.1,
            low=0.9, close=1.0, volume=1.0, amount=None,
        ) for s in symbols])

    monkeypatch.setattr(source_routing, "_raw", lambda _: SimpleNamespace(get_daily=fetch))
    wrapped = source_routing._Provider(primary)
    frame = wrapped.get_daily(
        ["510300.SH"], datetime(2026, 11, 6), datetime(2026, 11, 6), "etf"
    )
    assert frame.height == 1 and calls == [["510300.SH"]]

    calls.clear()
    source_routing.HEALTH = SourceHealth()
    symbols = [f"51000{index}.SH" for index in range(5)]
    assert wrapped.get_daily(symbols, None, datetime(2026, 11, 6), "etf").is_empty()
    assert not calls
    assert wrapped.get_daily(["600519.SH"], None, datetime(2026, 11, 6), "stock").is_empty()
    assert not calls


def test_research_print_route_validates_scope_and_labels_partial_data(monkeypatch):
    app = FastAPI()
    app.state.repo = SimpleNamespace(
        resolve_asset_type=lambda symbol: "index" if symbol == "000001.SH" else "etf"
    )
    app.include_router(build_router())
    calls = []

    def fetch(symbol, *, max_pages):
        calls.append((symbol, max_pages))
        return {"is_level2": False, "complete": False, "rows": []}

    monkeypatch.setattr(
        custom, "get_provider",
        lambda *_args, **_kwargs: SimpleNamespace(get_trade_prints=fetch),
    )
    with TestClient(app) as client:
        response = client.get("/api/custom/global-markets/cn/trade-prints?symbol=510300.SH")
        assert response.status_code == 200
        assert response.json()["is_level2"] is False
        assert client.get(
            "/api/custom/global-markets/cn/trade-prints?symbol=000001.SH"
        ).status_code == 422
        assert client.get(
            "/api/custom/global-markets/cn/trade-prints?symbol=510300.SH&max_pages=4"
        ).status_code == 422
    assert calls == [("510300.SH", 1)]


def test_integrity_reader_uses_etf_partitions_and_does_not_write(tmp_path, monkeypatch):
    for table, days in (
        ("kline_etf_daily", [2, 4]),
        ("kline_etf_enriched", [2, 3, 4]),
    ):
        for day in days:
            folder = tmp_path / table / f"date=2026-11-{day:02}"
            folder.mkdir(parents=True)
            pl.DataFrame({"symbol": ["510300.SH"]}).write_parquet(folder / "part.parquet")
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*.parquet")}
    monkeypatch.setattr(
        etf_integrity_reader.trading_calendar,
        "month_open_days",
        lambda *_: {date(2026, 11, day) for day in [2, 3, 4]},
    )
    repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path),
        resolve_asset_type=lambda symbol: "etf" if symbol in {"510300.SH", "159919.SZ"} else None,
    )
    result = etf_integrity_reader.report(
        repo, ["510300.SH"], date(2026, 11, 2), date(2026, 11, 4)
    )
    row = result["symbols"][0]
    assert row["raw"]["gaps"][0]["missing_dates"] == (date(2026, 11, 3),)
    assert row["enriched"]["gaps"] == () and row["calendar_complete"]
    assert result["repaired"] == 0 and result["mode"] == "read_only"
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*.parquet")}


def test_etf_integrity_route_is_bounded_and_read_only(tmp_path, monkeypatch):
    app = FastAPI()
    app.state.repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path),
        resolve_asset_type=lambda symbol: "etf" if symbol in {"510300.SH", "159919.SZ"} else None,
    )
    app.include_router(build_router())
    monkeypatch.setattr(
        etf_integrity_reader.trading_calendar,
        "month_open_days",
        lambda *_: {date(2026, 11, day) for day in [2, 3, 4]},
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/custom/global-markets/cn/etf-integrity"
            "?symbols=510300.SH&start=2026-11-02&end=2026-11-04"
        )
        assert response.status_code == 200
        assert response.json()["mode"] == "read_only"
        assert response.json()["repaired"] == 0
        missing_symbols = client.get(
            "/api/custom/global-markets/cn/etf-integrity?start=2026-11-02&end=2026-11-04"
        )
        assert missing_symbols.status_code == 422
        too_many = client.get(
            "/api/custom/global-markets/cn/etf-integrity?symbols="
            "510300.SH,510300.SH&start=2026-11-02&end=2026-11-04"
        )
        assert too_many.status_code == 422
