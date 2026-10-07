"""Free index intraday points must keep their asset, date and field semantics."""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.market_time import CN_TZ
from app.plugins.txquote.client import TxQuoteError
from app.plugins.txquote.provider import TxQuoteProvider


@pytest.fixture
def provider(monkeypatch):
    instance = TxQuoteProvider()
    client = MagicMock()
    client.fetch_day_minute.return_value = [
        ("2026-09-30", [
            "0930 3840.00 100 100000",
            "0931 3842.19 200 200000",
            "0931 3842.20 201 201000",
            "1500 3842.19 900 900000",
        ]),
        ("2026-09-29", ["0931 3830.00 80 80000"]),
    ]
    monkeypatch.setattr(instance, "_get_client", lambda: client)
    return instance, client


@pytest.mark.parametrize("symbol", [
    "000001.SH", "399001.SZ", "399006.SZ", "000680.SH", "000688.SH",
])
def test_index_points_supported_without_fabricated_ohlc_or_volume(provider, symbol):
    instance, client = provider
    frame = instance.get_minute(
        [symbol], datetime(2026, 9, 30, 9, 25, tzinfo=CN_TZ),
        datetime(2026, 9, 30, 15, 5, tzinfo=CN_TZ), asset_type="index",
    )
    assert "index" in instance.minute_asset_types
    assert frame["symbol"].to_list() == [symbol] * 3
    assert frame["close"].to_list() == [3840.0, 3842.20, 3842.19]
    assert all(value.tzinfo is None for value in frame["datetime"])
    assert {value.date().isoformat() for value in frame["datetime"]} == {"2026-09-30"}
    for field in ("open", "high", "low", "volume", "amount"):
        assert frame[field].null_count() == frame.height
    client.fetch_day_minute.assert_called_once_with(symbol)


def test_index_requested_missing_date_stays_empty(provider):
    instance, _ = provider
    frame = instance.get_minute(
        ["000001.SH"], datetime(2026, 9, 28, 9, 25), datetime(2026, 9, 28, 15, 5),
        asset_type="index",
    )
    assert frame.is_empty()


def test_index_network_failure_is_distinct_from_empty(provider):
    instance, client = provider
    client.fetch_day_minute.side_effect = TxQuoteError("network failure")
    with pytest.raises(TxQuoteError):
        instance.get_minute(["000001.SH"], None, None, asset_type="index")


def test_stock_failure_behavior_is_unchanged(provider):
    instance, client = provider
    client.fetch_day_minute.side_effect = TxQuoteError("network failure")
    assert instance.get_minute(["000001.SZ"], None, None).is_empty()


def test_index_rejects_other_frequencies(provider):
    instance, client = provider
    with pytest.raises(ValueError, match="1m"):
        instance.get_minute(["000001.SH"], None, None, asset_type="index", freq="5m")
    client.fetch_day_minute.assert_not_called()


@pytest.mark.parametrize("row", ["0931 NaN", "0931 inf", "0931 -1", "invalid 3000", "2460 3000"])
def test_invalid_index_points_fail_without_made_up_values(provider, row):
    instance, client = provider
    client.fetch_day_minute.return_value = [("2026-09-30", [row])]
    with pytest.raises(TxQuoteError):
        instance.get_minute(["000001.SH"], None, None, asset_type="index")
