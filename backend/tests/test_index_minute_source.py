"""Dedicated index point reads must fail closed and never persist stock minutes."""
from datetime import date, datetime
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.services import index_minute, kline_sync


@pytest.mark.parametrize("resolution", [(None, True, None), (None, True, "disabled")])
def test_missing_plugin_is_an_unavailable_source(monkeypatch, resolution):
    monkeypatch.setattr(kline_sync, "_resolve_minute_provider", lambda *a, **kw: resolution)
    with pytest.raises(index_minute.IndexMinuteUnavailableError):
        index_minute.fetch_index_minute("000001.SH", date(2026, 9, 30))


def test_source_exception_is_sanitized(monkeypatch):
    provider = MagicMock()
    provider.get_minute.side_effect = RuntimeError("private-token-must-not-leak")
    monkeypatch.setattr(kline_sync, "_resolve_minute_provider", lambda *a, **kw: (provider, False, None))
    with pytest.raises(index_minute.IndexMinuteUnavailableError) as error:
        index_minute.fetch_index_minute("000001.SH", date(2026, 9, 30))
    assert "private-token" not in str(error.value)


def test_points_are_read_without_disk_writes(monkeypatch, tmp_path):
    provider = MagicMock()
    provider.get_minute.return_value = pl.DataFrame({
        "symbol": ["000001.SH"], "datetime": [datetime(2026, 9, 30, 9, 31)],
        "close": [3842.19],
    })
    monkeypatch.setattr(kline_sync, "_resolve_minute_provider", lambda *a, **kw: (provider, False, None))
    monkeypatch.setattr(kline_sync.preferences, "get_minute_data_provider", lambda: "eltdx_gateway")
    monkeypatch.setattr(kline_sync, "_write_minute_partition", MagicMock(side_effect=AssertionError("must not persist")))
    result = index_minute.fetch_index_minute("000001.SH", date(2026, 9, 30))
    assert result["close"].to_list() == [3842.19]
    assert list(tmp_path.iterdir()) == []
    kline_sync._write_minute_partition.assert_not_called()


@pytest.mark.parametrize("symbol,day,close", [
    ("000001.SZ", datetime(2026, 9, 30, 9, 31), 3000.0),
    ("000001.SH", datetime(2026, 9, 29, 9, 31), 3000.0),
    ("000001.SH", datetime(2026, 9, 30, 9, 31), float("nan")),
])
def test_source_rejects_wrong_symbol_day_or_invalid_price(monkeypatch, symbol, day, close):
    provider = MagicMock()
    provider.get_minute.return_value = pl.DataFrame({"symbol": [symbol], "datetime": [day], "close": [close]})
    monkeypatch.setattr(kline_sync, "_resolve_minute_provider", lambda *a, **kw: (provider, False, None))
    with pytest.raises(index_minute.IndexMinuteUnavailableError):
        index_minute.fetch_index_minute("000001.SH", date(2026, 9, 30))
