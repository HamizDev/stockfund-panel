"""zzshare 匿名分钟数据源契约测试; 所有 HTTP 响应均由假客户端提供。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest
import yaml

from app.plugins.zzshare import client as zzclient
from app.plugins.zzshare.client import ZZShareClient, ZZShareError
from app.plugins.zzshare.provider import ZZShareProvider


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class _FakeHttp:
    def __init__(self, response: _Response | None = None, error=None):
        self.response = response or _Response({"code": 200, "data": {"count": 0, "list": []}})
        self.error = error
        self.calls: list[tuple[str, dict]] = []
        self.started_at: list[float] = []

    def get(self, url, *, params):
        self.calls.append((url, params.copy()))
        self.started_at.append(zzclient.time.monotonic())
        if self.error:
            raise self.error
        return self.response

    def close(self):
        pass


class _Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, duration: float):
        self.sleeps.append(duration)
        self.now += duration


@pytest.fixture(autouse=True)
def _fake_clock(monkeypatch):
    clock = _Clock()
    with zzclient._REQUEST_LOCK:
        zzclient._LAST_REQUEST_STARTED = None
    monkeypatch.setattr(zzclient.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(zzclient.time, "sleep", clock.sleep)
    yield clock
    with zzclient._REQUEST_LOCK:
        zzclient._LAST_REQUEST_STARTED = None


def _client(payload: dict | None = None, *, status_code: int = 200, error=None):
    client = ZZShareClient.__new__(ZZShareClient)
    client._http = _FakeHttp(
        _Response(payload or {"code": 200, "data": {"count": 0, "list": []}}, status_code),
        error=error,
    )
    return client


def _row(**overrides):
    row = {
        "code": "000001.SZ",
        "trade_time": "202609300931",
        "open": 11.36,
        "close": 11.39,
        "high": 11.41,
        "low": 11.33,
        "vol": 2_089_600.0,
        "amount": 23_775_150.0,
    }
    row.update(overrides)
    return row


def _provider(payload: dict | None = None, *, status_code: int = 200, error=None):
    provider = ZZShareProvider()
    fake_client = _client(payload, status_code=status_code, error=error)
    provider._get_client = lambda: fake_client
    return provider, fake_client


def test_manifest_and_provider_advertise_only_single_symbol_minute():
    manifest_path = Path(__file__).parents[1] / "app" / "plugins" / "zzshare" / "plugin.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    provider = ZZShareProvider()

    assert manifest["name"] == "zzshare"
    assert manifest["runtime"] == "none"
    assert manifest["entry"] == "app.plugins.zzshare.provider:ZZShareProvider"
    assert manifest["check"] == "app.plugins.zzshare.provider:availability"
    assert manifest["datasets"] == ["minute"]
    assert "api_key_env" not in manifest
    assert provider.name == "zzshare"
    assert set(provider.config.datasets) == {"minute"}
    assert provider.minute_asset_types == ("stock",)
    assert not hasattr(provider, "minute_history_days")


def test_provider_requests_range_and_normalizes_share_volume_to_hand():
    provider, client = _provider({"code": 200, "data": {"count": 1, "list": [_row()]}})
    start = datetime(2026, 9, 30, 1, 30, tzinfo=UTC)
    end = datetime(2026, 9, 30, 7, 5, tzinfo=UTC)

    frame = provider.get_minute(["000001.SZ"], start, end, freq="1m")

    assert client._http.calls == [
        (
            "https://api.zizizaizai.com/v3/market/kline/minute/000001.SZ",
            {
                "freq": "1min",
                "start_time": "20260930 09:30:00",
                "end_time": "20260930 15:05:00",
            },
        )
    ]
    assert frame.columns == ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]
    assert frame.schema["datetime"] == pl.Datetime("us")
    assert frame["symbol"].to_list() == ["000001.SZ"]
    assert frame["datetime"].to_list() == [datetime(2026, 9, 30, 9, 31)]
    assert frame["volume"].to_list() == [20_896.0]
    assert frame["amount"].to_list() == [23_775_150.0]


def test_provider_filters_window_rejects_wrong_code_and_invalid_rows():
    rows = [
        _row(trade_time="202609300930"),
        _row(trade_time="202609300931", open=11.37),
        _row(trade_time="202609300931", open=11.38),  # duplicate timestamp: last row wins
        _row(code="600000.SH", trade_time="202609300932"),
        _row(trade_time="202609300933", close=float("inf")),
        _row(trade_time="202609300934", low=11.40),  # impossible OHLC
        _row(trade_time="202609310935"),  # impossible date
        _row(trade_time="202609301506"),  # outside requested window
    ]
    provider, _client = _provider({"code": 200, "data": {"count": len(rows), "list": rows}})

    frame = provider.get_minute(
        ["000001.SZ"], datetime(2026, 9, 30, 9, 31), datetime(2026, 9, 30, 15, 0)
    )

    assert frame.height == 1
    assert frame["symbol"].to_list() == ["000001.SZ"]
    assert frame["datetime"].to_list() == [datetime(2026, 9, 30, 9, 31)]
    assert frame["open"].to_list() == [11.38]


def test_provider_empty_payload_stays_empty_and_reports_progress():
    provider, _client = _provider({"code": 200, "data": {"count": 0, "list": []}})
    progress: list[tuple[int, int]] = []

    frame = provider.get_minute(
        ["000001.SZ"], datetime(2026, 9, 30, 9, 30), datetime(2026, 9, 30, 15, 0),
        on_chunk_done=lambda current, total: progress.append((current, total)),
    )

    assert frame.is_empty()
    assert frame.columns == ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]
    assert progress == [(1, 1)]


@pytest.mark.parametrize(
    ("symbol", "asset_type"),
    [("510300.SH", "etf"), ("000300.SH", "index")],
)
def test_provider_rejects_etf_and_index_without_request(symbol, asset_type):
    provider, client = _provider()

    with pytest.raises(ValueError, match="只支持股票"):
        provider.get_minute([symbol], None, None, asset_type=asset_type)

    assert client._http.calls == []


def test_provider_rejects_non_one_minute_freq_and_bad_symbols_without_request():
    provider, client = _provider()

    with pytest.raises(ValueError, match="只支持 1m"):
        provider.get_minute(["000001.SZ"], None, None, freq="5m")
    with pytest.raises(ValueError, match="证券代码") as caught:
        provider.get_minute(["510300.SH?token=secret"], None, None)

    assert "secret" not in str(caught.value)
    assert client._http.calls == []


@pytest.mark.parametrize("symbol", ["600000.SH", "000001.SZ", "430001.BJ"])
def test_provider_accepts_standard_stock_exchange_suffixes(symbol):
    provider, client = _provider()

    frame = provider.get_minute([symbol], None, None)

    assert frame.is_empty()
    assert client._http.calls[0][0].endswith(f"/minute/{symbol}")


def test_trade_time_request_has_no_auth_header_or_token_field():
    client = _client()

    rows = client.fetch_minute("000001.SZ", None, None, trade_time="20260930")

    assert rows == []
    assert client._http.calls == [
        (
            "https://api.zizizaizai.com/v3/market/kline/minute/000001.SZ",
            {"freq": "1min", "trade_time": "20260930"},
        )
    ]


def test_http_429_fails_once_with_clear_message_without_url_or_token():
    client = _client(status_code=429)

    with pytest.raises(ZZShareError, match=r"HTTP 429.*no automatic retry") as caught:
        client.fetch_minute("000001.SZ", None, None, trade_time="20260930")

    assert "https://" not in str(caught.value)
    assert "token" not in str(caught.value).lower()
    assert len(client._http.calls) == 1


def test_network_failure_message_hides_url_and_rate_limit_is_global_across_instances(_fake_clock):
    first = _client(error=httpx.ConnectError("fixture network error"))
    second = _client()

    with pytest.raises(ZZShareError, match="网络请求失败") as caught:
        first.fetch_minute("000001.SZ", None, None, trade_time="20260930")
    second.fetch_minute("000001.SZ", None, None, trade_time="20260930")

    assert "https://" not in str(caught.value)
    assert _fake_clock.sleeps == pytest.approx([2.1])
    assert first._http.started_at == [100.0]
    assert second._http.started_at == [102.1]


def test_response_schema_error_is_explicit_not_an_empty_frame():
    provider, _client = _provider({"code": 200, "data": {"count": 1, "list": None}})

    with pytest.raises(ZZShareError, match="list 字段不是数组"):
        provider.get_minute(
            ["000001.SZ"], datetime(2026, 9, 30, 9, 30), datetime(2026, 9, 30, 15, 0)
        )


def test_market_sized_request_is_rejected_before_any_network_call():
    provider, client = _provider()
    with pytest.raises(ValueError, match="最多 30"):
        provider.get_minute([f"{code:06d}.SZ" for code in range(1, 32)], None, None)
    assert client._http.calls == []
