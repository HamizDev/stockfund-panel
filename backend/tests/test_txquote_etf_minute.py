"""腾讯原生 ETF 分钟 OHLC 适配契约。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import httpx
import polars as pl
import pytest

from app.market_time import CN_TZ
from app.plugins.txquote.client import TxQuoteClient, TxQuoteError
from app.plugins.txquote.provider import TxQuoteProvider

_ROWS = [
    ["202610220931", "0.530", "0.534", "0.536", "0.529", "128", "0", "17"],
    ["202610220932", "0.534", "0.532", "0.535", "0.531", "90", "0", "12"],
]


def _payload(rows: list) -> dict:
    return {"code": 0, "msg": "", "data": {"sh515880": {"m1": rows}}}


def _parsed_bars() -> list[dict]:
    return [
        {
            "datetime": datetime.strptime(row[0], "%Y%m%d%H%M"),
            "open": float(row[1]),
            "close": float(row[2]),
            "high": float(row[3]),
            "low": float(row[4]),
            "volume": float(row[5]),
            "amount": None,
        }
        for row in _ROWS
    ]


class _StreamResponse:
    def __init__(self, payload: bytes, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def iter_bytes(self):
        # Split across chunks to cover the streaming size guard.
        mid = len(self.payload) // 2
        yield self.payload[:mid]
        yield self.payload[mid:]


class _FakeStreamHttp:
    def __init__(self, payload: bytes, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.calls: list[tuple[str, dict, dict]] = []

    @contextmanager
    def stream(self, method, url, *, params=None, headers=None):
        self.calls.append((method, url, params or {}, headers or {}))
        yield _StreamResponse(self.payload, self.status_code)


class _FailingStreamHttp:
    def __init__(self, error):
        self.error = error

    @contextmanager
    def stream(self, *args, **kwargs):
        raise self.error
        yield  # pragma: no cover - makes this a contextmanager generator


def _client(payload: dict, status_code: int = 200) -> TxQuoteClient:
    client = TxQuoteClient.__new__(TxQuoteClient)
    client._http = _FakeStreamHttp(json.dumps(payload).encode(), status_code)
    return client


def test_client_parses_tencent_etf_ohlc_order_and_keeps_amount_unknown():
    client = _client(_payload(_ROWS))

    bars = client.minute_etf_kline("515880.SH", "m1", count=320)

    assert [bar["datetime"] for bar in bars] == [
        datetime(2026, 10, 22, 9, 31),
        datetime(2026, 10, 22, 9, 32),
    ]
    assert bars[0] == {
        "datetime": datetime(2026, 10, 22, 9, 31),
        "open": 0.530,
        "close": 0.534,
        "high": 0.536,
        "low": 0.529,
        "volume": 128.0,
        "amount": None,
    }
    method, url, params, headers = client._http.calls[0]
    assert method == "GET"
    assert url.endswith("/appstock/app/kline/mkline")
    assert params == {"param": "sh515880,m1,,320"}
    assert headers["Referer"] == "https://gu.qq.com/"


@pytest.mark.parametrize(
    "row",
    [
        ["202610220931", "nan", "0.534", "0.536", "0.529", "128"],
        ["202610220931", "0.530", "0.534", "inf", "0.529", "128"],
        ["202610220931", "0.530", "0.534", "0.533", "0.529", "128"],
        ["202610220931", "0.530", "0.534", "0.536", "0.535", "128"],
        ["202610220931", "0.530", "0.534", "0.536", "0.529", "-1"],
        ["202610220931", "0.530", "0.534", "0.536", "0.529", "nan"],
        ["202613990999", "0.530", "0.534", "0.536", "0.529", "128"],
    ],
)
def test_client_rejects_bad_ohlc_volume_and_timestamp(row):
    client = _client(_payload([row]))

    with pytest.raises(TxQuoteError):
        client.minute_etf_kline("515880.SH", "m1")


def test_client_rejects_duplicate_or_mismatched_symbol():
    duplicate = _client(_payload([_ROWS[0], _ROWS[0]]))
    with pytest.raises(TxQuoteError):
        duplicate.minute_etf_kline("515880.SH", "m1")

    mismatch = _client({"code": 0, "msg": "", "data": {"sh510300": {"m1": _ROWS}}})
    with pytest.raises(TxQuoteError, match="标的与请求不匹配"):
        mismatch.minute_etf_kline("515880.SH", "m1")


def test_client_caps_response_bytes_and_does_not_echo_response_body():
    client = _client(_payload(_ROWS))
    client._http = _FakeStreamHttp(b"{" + b" " * (2 * 1024 * 1024))

    with pytest.raises(TxQuoteError, match="响应超过大小限制") as exc:
        client.minute_etf_kline("515880.SH", "m1")
    assert "20261022" not in str(exc.value)


def test_client_http_errors_are_sanitized():
    client = _client(_payload(_ROWS), status_code=502)
    with pytest.raises(TxQuoteError, match="HTTP 502") as status_error:
        client.minute_etf_kline("515880.SH", "m1")
    assert "response body" not in str(status_error.value)

    client._http = _FailingStreamHttp(httpx.ReadTimeout("private proxy detail"))
    with pytest.raises(TxQuoteError, match="请求失败") as timeout_error:
        client.minute_etf_kline("515880.SH", "m1")
    assert "private proxy detail" not in str(timeout_error.value)


def test_client_rejects_invalid_period_count_and_non_mainland_symbols():
    client = _client(_payload(_ROWS))
    with pytest.raises(ValueError, match="周期"):
        client.minute_etf_kline("515880.SH", "day")
    with pytest.raises(ValueError, match="320"):
        client.minute_etf_kline("515880.SH", "m1", count=321)
    with pytest.raises(TxQuoteError, match="不支持的代码格式"):
        client.minute_etf_kline("920001.BJ", "m1")


def test_provider_filters_requested_datetime_window_and_keeps_beijing_wall_clock(monkeypatch):
    provider = TxQuoteProvider()
    client = MagicMock()
    client.minute_etf_kline.return_value = [
        {
            "datetime": datetime(2026, 10, 21, 15, 0),
            "open": 0.520,
            "high": 0.520,
            "low": 0.520,
            "close": 0.520,
            "volume": 0.0,
            "amount": None,
        },
        *_parsed_bars(),
        {
            "datetime": datetime(2026, 10, 22, 9, 33),
            "open": 0.532,
            "high": 0.532,
            "low": 0.532,
            "close": 0.532,
            "volume": 2.0,
            "amount": None,
        },
    ]
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    frame = provider.get_minute(
        ["515880.SH"],
        datetime(2026, 10, 22, 9, 32, tzinfo=CN_TZ),
        datetime(2026, 10, 22, 9, 32, tzinfo=CN_TZ),
        asset_type="etf",
        freq="1m",
    )

    assert provider.minute_asset_types == ("stock", "index", "etf")
    assert frame.height == 1
    assert frame["datetime"].to_list() == [datetime(2026, 10, 22, 9, 32)]
    assert frame["open"].to_list() == [0.534]
    assert frame["close"].to_list() == [0.532]
    assert frame["high"].to_list() == [0.535]
    assert frame["low"].to_list() == [0.531]
    assert frame["volume"].to_list() == [90.0]
    assert frame["amount"].null_count() == 1
    client.minute_etf_kline.assert_called_once_with("515880.SH", "m1", count=320)


@pytest.mark.parametrize("freq,period", [("1m", "m1"), ("5m", "m5"), ("15m", "m15"), ("30m", "m30"), ("60m", "m60")])
def test_provider_routes_etf_frequencies_to_tencent_only(monkeypatch, freq, period):
    provider = TxQuoteProvider()
    client = MagicMock()
    client.minute_etf_kline.return_value = [
        {"datetime": datetime(2026, 10, 22, 9, 31), "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "volume": 1.0, "amount": None},
    ]
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    frame = provider.get_minute(["515880.SH"], None, None, asset_type="etf", freq=freq)

    assert frame.height == 1
    client.minute_etf_kline.assert_called_once_with("515880.SH", period, count=320)
    client.minute_kline.assert_not_called()


def test_stock_and_index_still_use_existing_minute_paths(monkeypatch):
    provider = TxQuoteProvider()
    client = MagicMock()
    client.minute_kline.return_value = [
        {"day": "2026-10-22 09:35:00", "open": "1", "high": "1", "low": "1",
         "close": "1", "volume": "100", "amount": "100"},
    ]
    client.fetch_day_minute.return_value = [("2026-10-22", ["0931 3842.19 0 0"])]
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    provider.get_minute(["600519.SH"], None, None, asset_type="stock", freq="5m")
    provider.get_minute(["000001.SH"], None, None, asset_type="index", freq="1m")

    client.minute_kline.assert_called_once()
    client.fetch_day_minute.assert_called_once_with("000001.SH")
    client.minute_etf_kline.assert_not_called()


def test_provider_etf_wrong_date_stays_empty(monkeypatch):
    provider = TxQuoteProvider()
    client = MagicMock()
    client.minute_etf_kline.return_value = [
        {"datetime": datetime(2026, 10, 21, 9, 31), "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "volume": 1.0, "amount": None},
    ]
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    frame = provider.get_minute(
        ["515880.SH"], datetime(2026, 10, 22), datetime(2026, 10, 22, 15),
        asset_type="etf", freq="1m",
    )

    assert frame.is_empty()


def test_raw_chart_feed_is_projected_on_read_and_daily_aggregation_is_etf_only(monkeypatch, tmp_path):
    from app.services import analysis_current_session, kline_sync

    provider = TxQuoteProvider()
    monkeypatch.setattr(provider, 'get_minute', lambda *a, **k: pl.DataFrame(_parsed_bars()).with_columns(
        pl.lit('515880.SH').alias('symbol')))
    monkeypatch.setattr(kline_sync.preferences, 'get_minute_data_provider', lambda: 'txquote')
    monkeypatch.setattr(kline_sync, '_resolve_minute_provider', lambda *a, **k: (provider, False, None))
    calls = []
    monkeypatch.setattr(kline_sync.minute_adjust, 'apply_minute_adjustment',
                        lambda data, *_: calls.append(True) or data)
    frame, fallback = kline_sync._try_custom_minute(['515880.SH'], None, None, 'etf')
    assert not fallback and frame.height == 2 and calls == [True]
    assert analysis_current_session._source_context(tmp_path, None, 'etf')[0] is not None
    assert analysis_current_session._source_context(tmp_path, None, 'stock') == (None, None)
