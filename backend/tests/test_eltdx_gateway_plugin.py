"""Contract tests for the local eltdx gateway minute provider."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import polars as pl
import pytest
import yaml

from app.plugins.eltdx_gateway.client import EltdxGatewayClient, EltdxGatewayError
from app.plugins.eltdx_gateway.provider import EltdxGatewayProvider, availability

_BASE_URL = "http://127.0.0.1:18765"
_BJ = ZoneInfo("Asia/Shanghai")
_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]


def _bar(
    time: str,
    *,
    open_: float = 10.0,
    high: float = 10.5,
    low: float = 9.5,
    close: float = 10.2,
    volume_lots: float = 12.0,
    amount: float = 12345.0,
) -> dict:
    return {
        "time": time,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume_lots": volume_lots,
        "amount": amount,
    }


def _health_payload(**overrides) -> dict:
    return {"ok": True, "service": "eltdx", "version": "3.2.2", **overrides}


def _rpc_payload(request: httpx.Request, bars: list[dict], **result_overrides) -> dict:
    request_body = json.loads(request.content)
    result = {
        "exchange": "sz",
        "code": "000001",
        "adjust_mode": "none",
        "period_name": "1m",
        "bars": bars,
        **result_overrides,
    }
    return {"id": request_body["id"], "ok": True, "result": result}


def _client(handler) -> EltdxGatewayClient:
    """All client tests use MockTransport; none can contact a real gateway."""
    return EltdxGatewayClient(
        base_url=_BASE_URL,
        transport=httpx.MockTransport(handler),
    )


def _rpc_requests(requests: list[httpx.Request]) -> list[tuple[httpx.Request, dict]]:
    return [
        (request, json.loads(request.content))
        for request in requests
        if request.url.path == "/rpc"
    ]


def test_client_health_rpc_shape_symbol_mapping_and_pagination():
    requests: list[httpx.Request] = []
    pages = [
        [_bar("2026-09-30T09:34:00"), _bar("2026-09-30T09:33:00")],
        [
            _bar("2026-09-30T09:31:00"),
            _bar("2026-09-30T09:30:00"),
            _bar("2026-09-30T09:29:00"),
        ],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            assert request.method == "GET"
            return httpx.Response(200, json=_health_payload())
        assert request.url.path == "/rpc"
        assert request.method == "POST"
        return httpx.Response(200, json=_rpc_payload(request, pages.pop(0)))

    start = datetime(2026, 9, 30, 9, 30)
    end = datetime(2026, 9, 30, 9, 33)
    bars = _client(handler).fetch_minute("000001.SZ", start, end)

    rpc = _rpc_requests(requests)
    assert [request.url.path for request in requests].count("/health") == 1
    assert len(rpc) == 2  # A short page may still have a later page.
    assert [body["params"]["start"] for _, body in rpc] == [0, 2]
    for _, body in rpc:
        assert body["method"] == "bars.get"
        assert body["params"]["code"] == "sz000001"
        assert body["params"]["period"] == "1m"
        assert 0 < body["params"]["count"] <= 800
        assert body["params"]["adjust"] == "none"
    # The client preserves source bars, including rows outside the requested window;
    # the provider owns local time-window filtering.
    assert len(bars) == 5


def test_client_without_range_requests_only_the_latest_240_bars():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        return httpx.Response(200, json=_rpc_payload(request, [_bar("2026-09-30T09:30:00")]))

    bars = _client(handler).fetch_minute("000001.SZ", None, None)

    rpc = _rpc_requests(requests)
    assert len(rpc) == 1
    assert rpc[0][1]["params"]["start"] == 0
    assert rpc[0][1]["params"]["count"] == 240
    assert len(bars) == 1


def test_client_stops_on_empty_page_and_advances_offset_by_returned_rows():
    requests: list[httpx.Request] = []
    pages = [[_bar("2026-09-30T09:33:00")], []]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        return httpx.Response(200, json=_rpc_payload(request, pages.pop(0)))

    bars = _client(handler).fetch_minute(
        "000001.SZ", datetime(2026, 9, 30, 9, 0), datetime(2026, 9, 30, 10, 0)
    )

    rpc = _rpc_requests(requests)
    assert [body["params"]["start"] for _, body in rpc] == [0, 1]
    assert len(bars) == 1


def test_client_rejects_range_that_is_not_covered_within_six_pages():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        page_index = len(_rpc_requests(requests)) - 1
        # Every page is nonempty, but the oldest row remains newer than the requested start.
        bar_minute = 36 - page_index
        bars = [_bar(f"2026-09-30T09:{bar_minute:02d}:00")]
        return httpx.Response(200, json=_rpc_payload(request, bars))

    with pytest.raises(EltdxGatewayError):
        _client(handler).fetch_minute(
            "000001.SZ", datetime(2026, 9, 30, 8, 0), datetime(2026, 9, 30, 10, 0)
        )

    rpc = _rpc_requests(requests)
    assert len(rpc) == 6
    assert [body["params"]["start"] for _, body in rpc] == list(range(6))
    assert all(0 < body["params"]["count"] <= 800 for _, body in rpc)


@pytest.mark.parametrize(
    "health",
    [
        {"ok": False, "service": "eltdx", "version": "3.2.2"},
        {"ok": True, "service": "other", "version": "3.2.2"},
        {"ok": True, "service": "eltdx", "version": "3.2.1"},
    ],
)
def test_client_rejects_unhealthy_or_incompatible_gateway(health):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=health)

    with pytest.raises(EltdxGatewayError):
        _client(handler).fetch_minute("000001.SZ", None, None)
    assert [request.url.path for request in requests] == ["/health"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:18765",
        "http://user:private-marker@127.0.0.1:18765",
        "http://127.0.0.1:18765/api",
        "http://127.0.0.1:18765?token=private-marker",
    ],
)
def test_client_rejects_nonlocal_or_credential_bearing_gateway_url(url):
    def forbidden_request(_request: httpx.Request) -> httpx.Response:
        pytest.fail("gateway URL validation must happen without making an HTTP request")

    with pytest.raises(EltdxGatewayError) as caught:
        EltdxGatewayClient(base_url=url, transport=httpx.MockTransport(forbidden_request))
    assert "private-marker" not in str(caught.value)


@pytest.mark.parametrize(
    ("exchange", "code", "wire_code"),
    [
        ("sz", "000001", "sz000001"),
        ("sh", "600000", "sh600000"),
        ("bj", "830001", "bj830001"),
    ],
)
def test_client_maps_exchange_codes_and_rejects_response_for_another_symbol(
    exchange, code, wire_code
):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "id": body["id"],
                "ok": True,
                "result": {
                    "exchange": exchange,
                    "code": code,
                    "adjust_mode": "none",
                    "period_name": "1m",
                    "bars": [],
                },
            },
        )

    symbol = f"{code}.{exchange.upper()}"
    _client(handler).fetch_minute(symbol, None, None)
    assert seen[0]["params"]["code"] == wire_code

    def wrong_symbol(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        return httpx.Response(
            200,
            json=_rpc_payload(
                request,
                [],
                exchange=exchange,
                code="000002" if code != "000002" else "000003",
            ),
        )

    with pytest.raises(EltdxGatewayError):
        _client(wrong_symbol).fetch_minute(symbol, None, None)


def test_rpc_errors_are_sanitized():
    secret_marker = "private-marker-from-gateway"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": body["id"], "ok": False, "error": f"Bearer {secret_marker}"},
        )

    with pytest.raises(EltdxGatewayError) as caught:
        _client(handler).fetch_minute("000001.SZ", None, None)
    assert secret_marker not in str(caught.value)


def test_transport_errors_are_sanitized():
    secret_marker = "private-marker-from-transport"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        raise httpx.ConnectError(f"Bearer {secret_marker}", request=request)

    with pytest.raises(EltdxGatewayError) as caught:
        _client(handler).fetch_minute("000001.SZ", None, None)
    assert secret_marker not in str(caught.value)


def test_client_rejects_rpc_response_with_non_list_bars():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        return httpx.Response(
            200,
            json=_rpc_payload(request, bars={"not": "a list"}),
        )

    with pytest.raises(EltdxGatewayError):
        _client(handler).fetch_minute("000001.SZ", None, None)


@pytest.mark.parametrize("adjust_mode", ["qfq", None, "missing"])
def test_client_rejects_missing_or_adjusted_rpc_bars(adjust_mode):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        result = {
            "exchange": "sz",
            "code": "000001",
            "adjust_mode": "none",
            "period_name": "1m",
            "bars": [],
        }
        if adjust_mode == "missing":
            result.pop("adjust_mode")
        else:
            result["adjust_mode"] = adjust_mode
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": body["id"], "ok": True, "result": result},
        )

    with pytest.raises(EltdxGatewayError):
        _client(handler).fetch_minute("000001.SZ", None, None)


def test_availability_checks_locally_without_contacting_gateway(monkeypatch):
    def forbidden_send(*_args, **_kwargs):
        pytest.fail("availability must not probe the gateway")

    monkeypatch.setattr(httpx.Client, "send", forbidden_send)
    result = availability()
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], bool)
    assert isinstance(result[1], str)


def test_provider_declares_only_on_demand_minute_dataset():
    provider = EltdxGatewayProvider()
    plugin_yaml = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "plugins"
        / "eltdx_gateway"
        / "plugin.yaml"
    )
    manifest = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))

    assert set(manifest["datasets"]) == {"minute"}
    assert set(provider.config.datasets) == {"minute"}
    assert provider.name == "eltdx_gateway"
    assert provider.minute_asset_types == ("stock", "etf")
    assert provider.minute_max_symbols_per_request == 1
    assert provider.minute_history_days == 5
    assert provider.minute_price_basis == "raw"
    assert provider.minute_fail_closed is True


def test_provider_filters_window_normalizes_time_and_preserves_lots_without_amount():
    source_bars = [
        _bar("2026-09-30T09:30:00", volume_lots=1),
        _bar("2026-09-30T01:31:00Z", volume_lots=200, amount=999999),
        _bar("2026-09-30T09:32:00", volume_lots=183, amount=777777),
        _bar("2026-09-30T09:32:00", volume_lots=183, amount=777777),
        _bar("2026-09-30T09:33:00", volume_lots=2),
    ]

    class FakeClient:
        def __init__(self):
            self.calls = []

        def fetch_minute(self, symbol, start_time, end_time):
            self.calls.append((symbol, start_time, end_time))
            return source_bars

    provider = EltdxGatewayProvider()
    fake = FakeClient()
    provider._client = fake
    start = datetime(2026, 9, 30, 9, 31)
    end = datetime(2026, 9, 30, 9, 32)

    frame = provider.get_minute(
        ["000001.SZ"],
        start_time=start,
        end_time=end,
        asset_type="stock",
        freq="1m",
    )

    assert fake.calls == [("000001.SZ", start, end)]
    assert frame.columns == _COLUMNS
    assert frame.height == 2  # Inclusive window; identical timestamp duplicate is removed.
    assert frame["symbol"].to_list() == ["000001.SZ", "000001.SZ"]
    assert frame["datetime"].to_list() == [
        datetime(2026, 9, 30, 9, 31),
        datetime(2026, 9, 30, 9, 32),
    ]
    assert frame["datetime"].dtype.time_zone is None
    assert frame["volume"].to_list() == [200.0, 183.0]
    assert frame["amount"].null_count() == frame.height


def test_provider_accepts_etf_and_returns_typed_empty_frame():
    class FakeClient:
        def fetch_minute(self, symbol, start_time, end_time):
            assert symbol == "510300.SH"
            return []

    provider = EltdxGatewayProvider()
    provider._client = FakeClient()
    frame = provider.get_minute(
        ["510300.SH"], None, None, asset_type="etf", freq="1m"
    )

    assert frame.is_empty()
    assert frame.columns == _COLUMNS
    assert frame.schema["datetime"] == pl.Datetime("us")


@pytest.mark.parametrize(
    "bars",
    [
        [_bar("not-a-time")],
        [_bar("2026-09-30T09:31:00", open_=float("nan"))],
        [_bar("2026-09-30T09:31:00", close=float("inf"))],
        [_bar("2026-09-30T09:31:00", low=-0.1)],
        [_bar("2026-09-30T09:31:00", volume_lots=-1)],
        [_bar("2026-09-30T09:31:00", low=10.3, high=10.2)],
        [_bar("2026-09-30T09:31:00", close=10.8)],
        [
            _bar("2026-09-30T09:31:00", close=10.2),
            _bar("2026-09-30T09:31:00", close=10.3),
        ],
    ],
)
def test_provider_rejects_invalid_or_conflicting_bars(bars):
    class FakeClient:
        def fetch_minute(self, *_args):
            return bars

    provider = EltdxGatewayProvider()
    provider._client = FakeClient()
    with pytest.raises(EltdxGatewayError):
        provider.get_minute(["000001.SZ"], None, None, asset_type="stock", freq="1m")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbols": ["000001.SZ", "600000.SH"], "asset_type": "stock", "freq": "1m"},
        {"symbols": ["000001.SZ"], "asset_type": "index", "freq": "1m"},
        {"symbols": ["000001.SZ"], "asset_type": "stock", "freq": "5m"},
        {"symbols": ["000001"], "asset_type": "stock", "freq": "1m"},
        {"symbols": ["000001.XX"], "asset_type": "stock", "freq": "1m"},
    ],
)
def test_provider_rejects_out_of_contract_requests(kwargs):
    class FakeClient:
        def fetch_minute(self, *_args):
            pytest.fail("invalid provider request must be rejected before fetching")

    provider = EltdxGatewayProvider()
    provider._client = FakeClient()
    with pytest.raises((EltdxGatewayError, ValueError)):
        provider.get_minute(
            kwargs["symbols"],
            None,
            None,
            asset_type=kwargs["asset_type"],
            freq=kwargs["freq"],
        )


def test_provider_rejects_reversed_time_window_before_fetching():
    class FakeClient:
        def fetch_minute(self, *_args):
            pytest.fail("reversed time bounds must be rejected before fetching")

    provider = EltdxGatewayProvider()
    provider._client = FakeClient()
    with pytest.raises((EltdxGatewayError, ValueError)):
        provider.get_minute(
            ["000001.SZ"],
            datetime(2026, 9, 30, 9, 32, tzinfo=_BJ),
            datetime(2026, 9, 30, 9, 31, tzinfo=_BJ),
            asset_type="stock",
            freq="1m",
        )


@pytest.mark.parametrize("period", ["5m", None])
def test_client_rejects_wrong_or_missing_minute_period(period):
    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        return httpx.Response(200, json=_rpc_payload(request, [], period_name=period))

    with pytest.raises(EltdxGatewayError):
        _client(handler).fetch_minute("000001.SZ", None, None)


def test_provider_rejects_range_over_thirty_days_even_by_one_minute():
    provider = EltdxGatewayProvider()
    with pytest.raises(EltdxGatewayError, match="30"):
        provider.get_minute(["000001.SZ"], datetime(2026, 8, 31, 9, 30), datetime(2026, 9, 30, 9, 31))


def test_settings_trial_returns_observed_dates_and_never_writes(monkeypatch):
    calls = []

    class FakeClient:
        def fetch_minute(self, symbol, start, end):
            calls.append((symbol, start, end))
            return [_bar("2026-09-30T15:00:00+08:00")]

    provider = EltdxGatewayProvider()
    provider._client = FakeClient()
    result = provider.test_dataset("minute")
    assert calls == [("600519.SH", None, None)]
    assert result["rows"] == 1
    assert result["observed_end"] == "2026-09-30T15:00:00"
    assert result["preview"][0]["amount"] is None
    assert result["amount_available"] is False
    with pytest.raises(ValueError):
        provider.test_dataset("full_minute")
    assert len(calls) == 1


def test_client_does_not_publish_response_finished_after_deadline(monkeypatch):
    from app.plugins.eltdx_gateway import client as module

    elapsed = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: elapsed[0])

    def handler(request):
        elapsed[0] = 26.0
        return httpx.Response(200, json=_health_payload())

    with pytest.raises(EltdxGatewayError, match="超时"):
        _client(handler).fetch_minute("000001.SZ", None, None)


def test_loader_and_settings_trial_use_the_registered_minute_plugin(monkeypatch):
    from app.api.settings import CustomSourceTestIn, test_data_source
    from app.data_providers.custom import loader

    manifest_path = Path(__file__).resolve().parents[1] / "app/plugins/eltdx_gateway/plugin.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(loader, "_PROVIDERS", {})
    monkeypatch.setattr(loader, "_PLUGIN_STATUS", {})
    loader._register_one_plugin(manifest)
    assert loader.provider_has_dataset("eltdx_gateway", "minute") is True
    assert loader.provider_has_dataset("eltdx_gateway", "full_minute") is False
    assert loader.list_plugins()[0]["available"] is True
    provider = loader.get_provider("eltdx_gateway")

    class FakeClient:
        def fetch_minute(self, *_args):
            return [_bar("2026-09-30T15:00:00+08:00")]

    provider._client = FakeClient()
    result = test_data_source(CustomSourceTestIn(provider="eltdx_gateway", dataset="minute"))
    assert result["provider"] == "eltdx_gateway"
    assert result["observed_end"] == "2026-09-30T15:00:00"
    assert result["rows"] == 1
