"""Contract tests for batched ELTDX full-market minute reads."""

from __future__ import annotations

import json

import httpx
import pytest

from app.plugins.eltdx_gateway.client import EltdxGatewayClient, EltdxGatewayError

_BASE_URL = "http://127.0.0.1:18766"
_SYMBOLS = ["600519.SH", "000001.SZ", "920580.BJ"]
_WIRE_CODES = ["sh600519", "sz000001", "bj920580"]


def _health_payload(**overrides) -> dict:
    return {"ok": True, "service": "eltdx", "version": "3.2.2", **overrides}


def _bar(index: int = 0) -> dict:
    return {
        "time": f"2026-10-09T09:{30 + index:02d}:00",
        "open": 10.0,
        "high": 10.5,
        "low": 9.5,
        "close": 10.2,
        "volume_lots": 12.0,
        "amount": 12345.0,
    }


def _wire_result(wire_code: str, bars: list[dict] | None = None, **overrides) -> dict:
    return {
        "exchange": wire_code[:2],
        "code": wire_code[2:],
        "adjust_mode": "none",
        "period_name": "1m",
        "bars": [] if bars is None else bars,
        **overrides,
    }


def _success_response(request: httpx.Request, result: dict) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={"id": body["id"], "ok": True, "result": result},
    )


def _client(handler) -> EltdxGatewayClient:
    """All tests use MockTransport and cannot contact a real gateway."""
    return EltdxGatewayClient(
        base_url=_BASE_URL,
        transport=httpx.MockTransport(handler),
    )


def _canonical(wire_code: str) -> str:
    return f"{wire_code[2:]}.{wire_code[:2].upper()}"


def _rpc_body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def test_fetch_intraday_chunk_sends_one_rpc_and_returns_canonical_stock_rows():
    requests: list[httpx.Request] = []
    result = {
        _WIRE_CODES[0]: _wire_result(_WIRE_CODES[0], [_bar(0)]),
        _WIRE_CODES[1]: _wire_result(_WIRE_CODES[1], []),
        _WIRE_CODES[2]: _wire_result(_WIRE_CODES[2], [_bar(1)]),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            assert request.method == "GET"
            return httpx.Response(200, json=_health_payload())
        assert request.url.path == "/rpc"
        assert request.method == "POST"
        return _success_response(request, result)

    client = _client(handler)
    try:
        rows = client.fetch_intraday_chunk(_SYMBOLS)
    finally:
        client.close()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/health"),
        ("POST", "/rpc"),
    ]
    rpc = _rpc_body(requests[1])
    assert rpc["method"] == "bars.get"
    assert rpc["params"] == {
        "code": _WIRE_CODES,
        "period": "1m",
        "adjust": "none",
        "start": 0,
        "count": 300,
        "batch_size": 2,
    }
    assert rows == {
        _canonical(wire_code): result[wire_code]["bars"]
        for wire_code in _WIRE_CODES
    }


def test_fetch_intraday_chunk_deduplicates_before_requesting():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        wire_codes = _rpc_body(request)["params"]["code"]
        return _success_response(
            request,
            {wire: _wire_result(wire, [_bar(index)]) for index, wire in enumerate(wire_codes)},
        )

    symbols = ["600519.SH", "000001.SZ", "600519.SH", "920580.BJ", "000001.SZ"]
    client = _client(handler)
    try:
        rows = client.fetch_intraday_chunk(symbols, count=2)
    finally:
        client.close()

    assert _rpc_body(requests[1])["params"]["code"] == _WIRE_CODES
    assert set(rows) == {_canonical(code) for code in _WIRE_CODES}
    assert len(rows) == 3


def test_fetch_intraday_chunk_accepts_32_unique_codes_even_with_duplicate_inputs():
    requests: list[httpx.Request] = []
    wire_codes = [f"sh{number:06d}" for number in range(1, 33)]
    symbols = [_canonical(code) for code in wire_codes] + [_canonical(wire_codes[0])]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        requested = _rpc_body(request)["params"]["code"]
        return _success_response(request, {wire: _wire_result(wire) for wire in requested})

    client = _client(handler)
    try:
        rows = client.fetch_intraday_chunk(symbols, count=1)
    finally:
        client.close()

    assert len(_rpc_body(requests[1])["params"]["code"]) == 32
    assert len(rows) == 32


def test_fetch_intraday_chunk_rejects_more_than_32_unique_codes_without_network_io():
    requests: list[httpx.Request] = []
    symbols = [f"{number:06d}.SH" for number in range(1, 34)]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_health_payload())

    client = _client(handler)
    try:
        with pytest.raises(EltdxGatewayError):
            client.fetch_intraday_chunk(symbols)
    finally:
        client.close()

    assert requests == []


def test_fetch_intraday_chunk_empty_symbols_returns_without_network_io():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_health_payload())

    client = _client(handler)
    try:
        assert client.fetch_intraday_chunk([]) == {}
    finally:
        client.close()

    assert requests == []


@pytest.mark.parametrize("count", [1, 800])
def test_fetch_intraday_chunk_accepts_count_boundaries(count):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        return _success_response(
            request,
            {_WIRE_CODES[0]: _wire_result(_WIRE_CODES[0], [_bar()])},
        )

    client = _client(handler)
    try:
        client.fetch_intraday_chunk([_SYMBOLS[0]], count=count)
    finally:
        client.close()

    assert _rpc_body(requests[1])["params"]["count"] == count


@pytest.mark.parametrize("count", [0, -1, 801, True, 1.5, "300", None])
def test_fetch_intraday_chunk_rejects_invalid_count_without_network_io(count):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_health_payload())

    client = _client(handler)
    try:
        with pytest.raises(EltdxGatewayError):
            client.fetch_intraday_chunk([_SYMBOLS[0]], count=count)
    finally:
        client.close()

    assert requests == []


@pytest.mark.parametrize("code_set", ["missing", "extra"])
def test_fetch_intraday_chunk_rejects_missing_or_extra_response_codes(code_set):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        requested = _rpc_body(request)["params"]["code"]
        result = {wire: _wire_result(wire) for wire in requested}
        if code_set == "missing":
            result.pop(requested[-1])
        else:
            result["sh999999"] = _wire_result("sh999999")
        return _success_response(request, result)

    client = _client(handler)
    try:
        with pytest.raises(EltdxGatewayError):
            client.fetch_intraday_chunk(_SYMBOLS)
    finally:
        client.close()

    assert len(requests) == 2


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("exchange", "sz"),
        ("code", "600520"),
        ("adjust_mode", "qfq"),
        ("period_name", "day"),
    ],
)
def test_fetch_intraday_chunk_rejects_symbol_or_adjustment_mismatch(field, bad_value):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        result = _wire_result(_WIRE_CODES[0], [_bar()], **{field: bad_value})
        return _success_response(request, {_WIRE_CODES[0]: result})

    client = _client(handler)
    try:
        with pytest.raises(EltdxGatewayError):
            client.fetch_intraday_chunk([_SYMBOLS[0]])
    finally:
        client.close()

    assert len(requests) == 2


@pytest.mark.parametrize(
    "result_factory",
    [
        pytest.param(lambda _wire: [], id="result-not-object"),
        pytest.param(lambda _wire: {"sh600519": "not-an-item"}, id="item-not-object"),
        pytest.param(lambda _wire: {"sh600519": {"bars": []}}, id="missing-metadata"),
        pytest.param(
            lambda wire: {wire: _wire_result(wire, bars="not-a-list")},
            id="bars-not-list",
        ),
        pytest.param(
            lambda wire: {wire: _wire_result(wire, bars=[_bar(0), _bar(1)])},
            id="bars-over-count",
        ),
    ],
)
def test_fetch_intraday_chunk_rejects_invalid_response_shapes(result_factory):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        return _success_response(request, result_factory(_WIRE_CODES[0]))

    client = _client(handler)
    try:
        with pytest.raises(EltdxGatewayError):
            client.fetch_intraday_chunk([_SYMBOLS[0]], count=1)
    finally:
        client.close()

    assert len(requests) == 2


@pytest.mark.parametrize("failure", ["id-mismatch", "upstream-error"])
def test_fetch_intraday_chunk_rejects_rpc_failures_without_leaking_payload(failure):
    requests: list[httpx.Request] = []
    response_marker = "private-upstream-response-marker"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        body = _rpc_body(request)
        if failure == "id-mismatch":
            payload = {
                "id": response_marker,
                "ok": True,
                "result": {_WIRE_CODES[0]: _wire_result(_WIRE_CODES[0])},
            }
        else:
            payload = {
                "id": body["id"],
                "ok": False,
                "error": {"detail": response_marker, "url": "https://private.invalid/rpc"},
            }
        return httpx.Response(200, json=payload)

    client = _client(handler)
    try:
        with pytest.raises(EltdxGatewayError) as caught:
            client.fetch_intraday_chunk([_SYMBOLS[0]])
    finally:
        client.close()

    assert response_marker not in str(caught.value)
    assert "private.invalid" not in str(caught.value)
    assert len(requests) == 2


def test_fetch_intraday_chunk_sanitizes_transport_error_url():
    requests: list[httpx.Request] = []
    secret_marker = "transport-private-marker"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        raise httpx.ConnectError(
            f"request failed at https://user:{secret_marker}@private.invalid/rpc",
            request=request,
        )

    client = _client(handler)
    try:
        with pytest.raises(EltdxGatewayError) as caught:
            client.fetch_intraday_chunk([_SYMBOLS[0]])
    finally:
        client.close()

    assert secret_marker not in str(caught.value)
    assert "private.invalid" not in str(caught.value)
    assert _BASE_URL not in str(caught.value)
    assert len(requests) == 2


def test_progressing_response_is_stopped_when_deadline_is_reached(monkeypatch):
    from app.plugins.eltdx_gateway import client as module
    elapsed = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: elapsed[0])
    consumed_after_deadline = []

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            elapsed[0] = 26.0
            yield b'{"ok":'
            consumed_after_deadline.append(True)
            yield b'true}'

    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json=_health_payload())
        return httpx.Response(200, stream=SlowStream())

    client = _client(handler)
    try:
        with pytest.raises(EltdxGatewayError, match="超时"):
            client.fetch_intraday_chunk([_SYMBOLS[0]])
    finally:
        client.close()
    assert not consumed_after_deadline
