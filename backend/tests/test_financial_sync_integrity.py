from types import SimpleNamespace

import polars as pl
import pytest

from app.services import financial_sync
from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet


@pytest.mark.parametrize("bad", [
    {"symbol": ["600519.SH"], "bps": [2.0]},
    {"symbol": ["600519.SH"], "period_end": [None]},
    {"symbol": ["600519.SH"], "period_end": ["not a date"]},
])
def test_malformed_first_sync_never_replaces_existing_file(tmp_path, bad):
    target = tmp_path / "financials/metrics/part.parquet"
    target.parent.mkdir(parents=True)
    old = pl.DataFrame({"symbol": ["600519.SH"], "period_end": ["2025-12-31"], "bps": [3.0]})
    old.write_parquet(target)
    before = target.read_bytes()
    with pytest.raises(ValueError):
        financial_sync._write_table("metrics", pl.DataFrame(bad), tmp_path)
    assert target.read_bytes() == before


def test_empty_refresh_preserves_file_and_success_time(tmp_path, monkeypatch):
    target = tmp_path / "financials/metrics/part.parquet"
    target.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH"], "period_end": ["2025-12-31"], "bps": [3.0]}).write_parquet(target)
    before = target.read_bytes()
    monkeypatch.setattr(financial_sync, "_fetch_table", lambda *a, **kw: pl.DataFrame())
    assert financial_sync._sync_history_table_for_symbols("metrics", ["600519.SH"], tmp_path, CapabilitySet()) == 0
    assert target.read_bytes() == before
    scheduler = financial_sync.FinancialScheduler()
    scheduler._data_dir = tmp_path
    scheduler._capset = CapabilitySet()
    scheduler._last_sync["metrics"] = "previous success"
    monkeypatch.setattr(financial_sync, "sync_metrics", lambda *a: 0)
    assert scheduler._run_body("metrics") == {"metrics": 0}
    assert scheduler._last_sync["metrics"] == "previous success"


def test_provider_failure_does_not_become_empty_success(monkeypatch):
    from app.data_providers import custom
    from app.services import preferences

    class FailingProvider:
        def get_financials(self, *args, **kwargs):
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(financial_sync, "_financial_is_custom", lambda: True)
    monkeypatch.setattr(preferences, "get_financial_provider", lambda: "test")
    monkeypatch.setattr(custom, "get_provider", lambda _: FailingProvider())
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        financial_sync._fetch_table("metrics", ["600519.SH"], CapabilitySet())


@pytest.mark.parametrize("malformed", [[], None, {"000001.SZ": {}}, {"000001.SZ": [None]}, {"unexpected": []}])
def test_malformed_later_batch_does_not_publish_partial_history(tmp_path, monkeypatch, malformed):
    from app.tickflow import client

    calls = iter([{"600519.SH": [{"period_end": "2025-12-31", "bps": 3.0}]}, malformed])
    fake = SimpleNamespace(financials=SimpleNamespace(
        metrics=lambda *args, **kwargs: next(calls), income=None,
        balance_sheet=None, cash_flow=None,
    ))
    monkeypatch.setattr(financial_sync, "_financial_is_custom", lambda: False)
    monkeypatch.setattr(financial_sync, "_BATCH_SIZE", 1)
    monkeypatch.setattr(client, "get_client", lambda: fake)
    target = tmp_path / "financials/metrics/part.parquet"
    target.parent.mkdir(parents=True)
    old = pl.DataFrame({"symbol": ["600519.SH"], "period_end": ["2025-12-31"], "bps": [2.0]})
    old.write_parquet(target)
    before = target.read_bytes()
    with pytest.raises(ValueError, match="Financial batch"):
        capset = CapabilitySet({Cap.FINANCIAL: CapabilityLimits()})
        financial_sync._sync_history_table_for_symbols("metrics", ["600519.SH", "000001.SZ"], tmp_path, capset)
    assert target.read_bytes() == before
