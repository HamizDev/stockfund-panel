from __future__ import annotations

import types
from copy import deepcopy
from datetime import date, timedelta

import polars as pl
import pytest

from app.custom.ai_screener import routes, service
from app.services.quote_service import QuoteService

ETF_DATE = date(2026, 9, 30)


def _strategies() -> list[dict]:
    return [
        {"id": "etf_a", "name": "ETF A", "asset_types": ["stock", "etf"], "timeframes": ["1d"]},
        {"id": "etf_b", "name": "ETF B", "asset_types": ["etf"], "timeframes": ["1d"]},
        {"id": "stock_only", "name": "Stock only", "asset_types": ["stock"], "timeframes": ["1d"]},
        {"id": "etf_minute", "name": "ETF minute", "asset_types": ["etf"], "timeframes": ["1m"]},
        {"id": "research", "name": "Research", "asset_types": ["etf"], "timeframes": ["1d"],
         "research_only": True},
    ]


class _Repo:
    def __init__(self, *, latest: date | None = ETF_DATE, generation: str = "g1") -> None:
        self.latest = latest
        self.generation = generation
        self.current = pl.DataFrame({"symbol": ["510300", "159915"]})

    def get_matrix_data_generation(self, asset_type: str) -> str:
        assert asset_type == "etf"
        return self.generation


class _Engine:
    def __init__(self) -> None:
        self.strategies = _strategies()
        self.run_calls: list[tuple[object, tuple[str, ...]]] = []
        self.fail = False

    def list_strategies(self) -> list[dict]:
        return self.strategies

    def run_all(self, context, *, params_map, overrides_map, strategy_ids):
        selected = tuple(strategy_ids)
        self.run_calls.append((context, selected))
        if self.fail:
            raise RuntimeError("stub strategy failure")
        as_of = context.as_of
        return {
            "etf_a": types.SimpleNamespace(
                as_of=as_of,
                rows=[
                    {"symbol": "510300", "name": "沪深300ETF", "close": 4.1, "amount": 1000},
                    {"symbol": "000001.SZ", "name": "stray stock", "close": 12.3},
                ],
            ),
            "etf_b": types.SimpleNamespace(
                as_of=as_of,
                rows=[
                    {"symbol": "510300", "name": "沪深300ETF", "close": 4.1},
                    {"symbol": "159915", "name": "创业板ETF", "close": 2.3},
                ],
            ),
        }


class _ScreenerStub:
    def __init__(self, repo: _Repo, *, asset_type: str) -> None:
        assert asset_type == "etf"
        self.repo = repo

    def latest_date(self):
        return self.repo.latest

    def build_strategy_context(self, engine, as_of, strategy_ids, **kwargs):
        assert kwargs["timeframe"] == "1d"
        assert strategy_ids == ["etf_a", "etf_b"]
        return types.SimpleNamespace(as_of=as_of, current=self.repo.current)


@pytest.fixture
def isolated_etf_candidate_cache(monkeypatch):
    with service._ETF_CANDIDATE_CACHE_LOCK:
        service._ETF_CANDIDATE_CACHE.clear()
    monkeypatch.setattr(service, "ScreenerService", _ScreenerStub)
    yield
    with service._ETF_CANDIDATE_CACHE_LOCK:
        service._ETF_CANDIDATE_CACHE.clear()


def test_etf_candidates_use_etf_universe_and_only_supported_daily_strategies(
    isolated_etf_candidate_cache,
):
    repo = _Repo()
    engine = _Engine()

    response = service.build_etf_candidates(repo, engine, limit=20)

    assert response["asset_type"] == "etf"
    assert response["cache_available"] is True
    assert response["coverage"] == {"computed": 2, "total": 2}
    assert response["as_of"] == str(ETF_DATE)
    assert engine.run_calls[0][1] == ("etf_a", "etf_b")
    assert {item["symbol"] for item in response["items"]} == {"510300", "159915"}
    assert all(not item["symbol"].endswith((".SZ", ".SH", ".BJ")) for item in response["items"])
    assert next(item for item in response["items"] if item["symbol"] == "510300")["hit_count"] == 2


def test_etf_candidate_cache_is_short_lived_and_returns_isolated_copies(
    isolated_etf_candidate_cache,
):
    repo = _Repo()
    engine = _Engine()

    first = service.build_etf_candidates(repo, engine)
    first["items"].clear()
    second = service.build_etf_candidates(repo, engine)

    assert len(engine.run_calls) == 1
    assert second["total"] == 2
    assert len(second["items"]) == 2


@pytest.mark.parametrize("change", ["generation", "date", "metadata"])
def test_etf_candidate_cache_key_tracks_available_data_and_strategy_versions(
    isolated_etf_candidate_cache, change: str,
):
    repo = _Repo()
    engine = _Engine()
    service.build_etf_candidates(repo, engine)

    if change == "generation":
        repo.generation = "g2"
    elif change == "date":
        repo.latest = ETF_DATE + timedelta(days=1)
    else:
        engine.strategies[0]["description"] = "updated in-memory config"

    service.build_etf_candidates(repo, engine)

    assert len(engine.run_calls) == 2


def test_missing_etf_data_is_a_normal_empty_response(isolated_etf_candidate_cache):
    response = service.build_etf_candidates(_Repo(latest=None), _Engine())

    assert response["asset_type"] == "etf"
    assert response["as_of"] is None
    assert response["cache_available"] is False
    assert response["coverage"] == {"computed": 0, "total": 2}
    assert response["items"] == []
    assert "error" not in response


def test_failed_etf_strategy_run_reports_failure_and_zero_coverage(
    isolated_etf_candidate_cache,
):
    engine = _Engine()
    engine.fail = True

    response = service.build_etf_candidates(_Repo(), engine)

    assert response["error"] == "ETF 候选策略计算失败"
    assert response["cache_available"] is False
    assert response["coverage"] == {"computed": 0, "total": 2}
    assert response["items"] == []


def test_stock_candidates_filter_by_stock_directory_and_keep_cache_coverage():
    as_of = "2026-09-30"
    cached = {
        "as_of": as_of,
        "updated_at": 123,
        "results": {
            "stock_strategy": {"as_of": as_of, "rows": [{"symbol": "000001.SZ", "name": "平安银行"}]},
            "etf_strategy": {"as_of": as_of, "rows": [{"symbol": "510300", "name": "沪深300ETF"}]},
        },
    }
    strategies = [
        {"id": "stock_strategy", "name": "Stock", "asset_types": ["stock"], "timeframes": ["1d"]},
        {"id": "etf_strategy", "name": "ETF", "asset_types": ["etf"], "timeframes": ["1d"]},
        {"id": "missing", "name": "Missing", "asset_types": ["stock"], "timeframes": ["1d"]},
    ]

    response = service.build_candidates(
        cached, strategies, [], {}, None, asset_type="stock", allowed_symbols={"000001.SZ"}
    )

    assert response["asset_type"] == "stock"
    assert response["coverage"] == {"computed": 1, "total": 2}
    assert response["cache_available"] is True
    assert [item["symbol"] for item in response["items"]] == ["000001.SZ"]


def test_stock_candidate_passes_through_existing_daily_technical_fields():
    as_of = "2026-09-30"
    row = {
        "symbol": "000001.SZ",
        "name": "平安银行",
        "close": 10.5,
        "raw_close": 10.3,
        "change_pct": 0.0123,
        "amount": 1_000_000,
        "volume": 10_000,
        "turnover_rate": 0.8,
        "ma5": 10.4,
        "ma20": 10.2,
        "ma60": 9.9,
        "vol_ratio_5d": 1.7,
        "macd_dif": 0.31,
        "macd_dea": 0.28,
        "macd_hist": 0.06,
        "kdj_k": 65.0,
        "kdj_d": 60.0,
        "kdj_j": 75.0,
    }
    response = service.build_candidates(
        {"as_of": as_of, "results": {"stock": {"as_of": as_of, "rows": [row]}}},
        [{"id": "stock", "asset_types": ["stock"], "timeframes": ["1d"]}],
        [], {}, None,
    )

    item = response["items"][0]
    assert {key: item["metrics"][key] for key in row if key not in {"symbol", "name"}} == {
        key: value for key, value in row.items() if key not in {"symbol", "name"}
    }
    assert item["price_source"] == "daily"
    assert item["price_basis"] == "qfq"
    assert item["technical_as_of"] == as_of


def test_stock_candidate_nulls_non_finite_and_missing_technical_fields():
    as_of = "2026-09-30"
    row = {
        "symbol": "000001.SZ",
        "close": float("nan"),
        "raw_close": None,
        "vol_ratio_5d": float("inf"),
        "macd_dif": float("-inf"),
        "macd_dea": None,
        "macd_hist": float("nan"),
        "kdj_k": float("nan"),
        "kdj_d": None,
        "kdj_j": float("inf"),
    }
    response = service.build_candidates(
        {"as_of": as_of, "results": {"stock": {"as_of": as_of, "rows": [row]}}},
        [{"id": "stock", "asset_types": ["stock"], "timeframes": ["1d"]}],
        [], {}, None,
    )

    item = response["items"][0]
    assert all(value is None for value in item["metrics"].values())
    assert item["price_basis"] == "qfq"
    assert item["technical_as_of"] == as_of


def test_stock_candidate_reads_legacy_cache_without_mutating_it():
    as_of = "2026-09-30"
    cached = {
        "as_of": as_of,
        "updated_at": 123,
        "results": {"stock": {"as_of": as_of, "rows": [{
            "symbol": "000001.SZ", "name": "平安银行", "close": 10.5,
            "change_pct": 0.01, "amount": 1000, "volume": 100,
            "turnover_rate": 0.2, "ma5": 10.4, "ma20": 10.2, "ma60": 9.9,
        }]}},
    }
    before = deepcopy(cached)

    response = service.build_candidates(
        cached,
        [{"id": "stock", "asset_types": ["stock"], "timeframes": ["1d"]}],
        [], {}, None,
    )

    item = response["items"][0]
    assert cached == before
    assert item["metrics"]["close"] == 10.5
    assert all(item["metrics"][field] is None for field in (
        "raw_close", "vol_ratio_5d", "macd_dif", "macd_dea", "macd_hist",
        "kdj_k", "kdj_d", "kdj_j",
    ))
    assert item["price_basis"] == "qfq"
    assert item["technical_as_of"] == as_of


def test_live_candidate_uses_raw_price_and_enriched_percent_turnover():
    as_of = "2026-09-30"
    row = {
        "symbol": "000001.SZ", "name": "平安银行", "close": 10.0,
        "raw_close": 9.8, "change_pct": 0.01, "amount": 500,
        "volume": 50, "turnover_rate": 0.4, "ma20": 9.9,
        "vol_ratio_5d": 1.5, "macd_dif": 0.2,
    }
    # Realtime provider input is decimal; quote_service turns it into the
    # enriched percent value before get_enriched_today feeds this endpoint.
    normalized = QuoteService._build_quote_extra([{
        "symbol": "000001.SZ", "turnover_rate": 0.008,
    }]).to_dicts()[0]
    quote = {
        **normalized, "close": 10.25, "change_pct": 0.025,
        "amount": 1100, "volume": 100,
    }
    response = service.build_candidates(
        {"as_of": as_of, "results": {"stock": {"as_of": as_of, "rows": [row]}}},
        [{"id": "stock", "asset_types": ["stock"], "timeframes": ["1d"]}],
        [quote],
        {"enabled": True, "running": True, "is_trading_hours": True,
         "quote_age_ms": 100, "last_fetch_ms": 123},
        "test-provider",
    )

    item = response["items"][0]
    assert response["quote"]["live"] is True
    assert item["metrics"]["close"] == 10.25
    assert item["metrics"]["raw_close"] == 9.8
    assert item["metrics"]["turnover_rate"] == pytest.approx(0.8)
    assert item["metrics"]["ma20"] == 9.9
    assert item["metrics"]["macd_dif"] == 0.2
    assert item["price_source"] == "live"
    assert item["price_basis"] == "raw"
    assert item["technical_as_of"] == as_of


@pytest.mark.parametrize("quote_close", [None, float("nan"), 0])
def test_invalid_live_price_keeps_daily_values_and_does_not_claim_live(quote_close):
    as_of = "2026-09-30"
    row = {
        "symbol": "000001.SZ", "name": "平安银行", "close": 10.0,
        "raw_close": 9.8, "change_pct": 0.01, "amount": 500,
        "volume": 50, "turnover_rate": 0.4,
    }
    response = service.build_candidates(
        {"as_of": as_of, "results": {"stock": {"as_of": as_of, "rows": [row]}}},
        [{"id": "stock", "asset_types": ["stock"], "timeframes": ["1d"]}],
        [{"symbol": "000001.SZ", "close": quote_close, "change_pct": 0.5,
          "amount": 9999, "volume": 999, "turnover_rate": 8.0}],
        {"enabled": True, "running": True, "is_trading_hours": True,
         "quote_age_ms": 100, "last_fetch_ms": 123},
        "test-provider",
    )

    item = response["items"][0]
    assert response["quote"]["live"] is False
    assert item["price_source"] == "daily"
    assert item["price_basis"] == "qfq"
    assert item["metrics"]["close"] == 10.0
    assert item["metrics"]["change_pct"] == 0.01
    assert item["metrics"]["amount"] == 500
    assert item["metrics"]["volume"] == 50
    assert item["metrics"]["turnover_rate"] == 0.4
    assert item["technical_as_of"] == as_of


def test_empty_stock_cache_exposes_missing_computation():
    response = service.build_candidates(
        None,
        [{"id": "stock_strategy", "asset_types": ["stock"], "timeframes": ["1d"]}],
        [],
        {},
        None,
    )

    assert response["coverage"] == {"computed": 0, "total": 1}
    assert response["cache_available"] is False
    assert response["items"] == []


def test_etf_route_does_not_read_stock_strategy_cache_or_override(monkeypatch):
    expected = {"asset_type": "etf", "items": []}
    calls = []
    monkeypatch.setattr(routes, "build_etf_candidates", lambda repo, engine, limit: calls.append(
        (repo, engine, limit)
    ) or expected)
    monkeypatch.setattr(routes.strategy_cache, "read_cache", lambda *_: pytest.fail(
        "ETF route must not read the stock strategy cache"
    ))
    monkeypatch.setattr(routes.strategy_config, "load_override", lambda *_: pytest.fail(
        "ETF route must not read stock strategy overrides"
    ))
    repo = object()
    engine = object()
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(repo=repo, strategy_engine=engine))
    )
    endpoint = next(
        route.endpoint for route in routes.build_router().routes
        if getattr(route, "path", None) == "/api/custom/ai-screener/candidates"
    )

    result = endpoint(request, asset_type="etf", limit=77)

    assert result == expected
    assert calls == [(repo, engine, 77)]
