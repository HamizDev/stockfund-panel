"""Regression tests for the official demo-only REST transport and credential store.

Every credential and response below is synthetic test data, never a real API key.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.custom.crypto_paper import demo_client, demo_store

_FAKE_SECRET = "FAKE_SECRET_FOR_TEST_ONLY"


def _credentials(exchange: str, *, account_mode: str = "classic", site: str = "global") -> dict[str, str]:
    """Synthetic credentials only; these values cannot authenticate to an exchange."""
    return {
        "exchange": exchange,
        "site": site,
        "account_mode": account_mode,
        "api_key": f"FAKE_{exchange.upper()}_API_KEY_DO_NOT_USE",
        "secret_key": f"FAKE_{exchange.upper()}_SECRET_KEY_DO_NOT_USE",
        "passphrase": f"FAKE_{exchange.upper()}_PASSPHRASE_DO_NOT_USE",
    }


def _response(*, status_code: int = 200, payload: object | None = None, content: bytes = b""):
    class FakeResponse:
        def __init__(self):
            self.status_code = status_code
            self.content = content

        def json(self):
            return payload

    return FakeResponse()


def _install_client(monkeypatch: pytest.MonkeyPatch, response) -> list[tuple]:
    calls: list[tuple] = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url: str, *, headers: dict):
            calls.append(("get", url, headers))
            return response

    monkeypatch.setattr(demo_client.httpx, "Client", FakeClient)
    return calls


def test_signature_is_hmac_sha256_base64_of_timestamp_get_and_exact_path():
    # Expected value was independently computed from the documented prehash string.
    assert demo_client.signature(
        _FAKE_SECRET,
        "1730000000000",
        "/api/v3/account/assets",
    ) == "vGIM8Q6aFruUkHoue8UQWjY2OpzITGIrMnY5BkgxZuQ="


def test_exchange_headers_use_demo_mode_and_exchange_timestamp_formats(monkeypatch: pytest.MonkeyPatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return cls(2025, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)

    monkeypatch.setattr(demo_client, "datetime", FixedDateTime)
    monkeypatch.setattr(demo_client.time, "time_ns", lambda: 1_730_000_000_123_000_000)
    path = "/api/v5/account/balance?ccy=BTC"

    okx = demo_client.headers(_credentials("okx", site="eea"), path)
    bitget = demo_client.headers(_credentials("bitget", account_mode="classic"), path)

    assert okx["OK-ACCESS-TIMESTAMP"] == "2025-01-02T03:04:05.678Z"
    assert okx["OK-ACCESS-SIGN"] == demo_client.signature(
        "FAKE_OKX_SECRET_KEY_DO_NOT_USE", okx["OK-ACCESS-TIMESTAMP"], path
    )
    assert okx["x-simulated-trading"] == "1"
    assert okx["OK-ACCESS-KEY"].startswith("FAKE_")

    assert bitget["ACCESS-TIMESTAMP"] == "1730000000123"
    assert bitget["ACCESS-SIGN"] == demo_client.signature(
        "FAKE_BITGET_SECRET_KEY_DO_NOT_USE", bitget["ACCESS-TIMESTAMP"], path
    )
    assert bitget["paptrading"] == "1"
    assert bitget["ACCESS-KEY"].startswith("FAKE_")


_OKX_PATHS = (
    "/api/v5/account/balance",
    "/api/v5/account/positions",
    "/api/v5/trade/orders-pending",
    "/api/v5/trade/fills",
)
_BITGET_CLASSIC_PATHS = (
    "/api/v2/mix/account/accounts",
    "/api/v2/mix/position/all-position",
    "/api/v2/mix/order/orders-pending",
    "/api/v2/mix/order/fills",
)
_BITGET_UTA_PATHS = (
    "/api/v3/account/assets",
    "/api/v3/position/current-position",
    "/api/v3/trade/unfilled-orders",
    "/api/v3/trade/fills",
)


@pytest.mark.parametrize(
    ("credentials", "path", "host", "success_code"),
    [
        *[
            pytest.param(_credentials("okx", site="global"), path, "https://openapi.okx.com", "0", id=f"okx-{path.rsplit('/', 1)[-1]}")
            for path in _OKX_PATHS
        ],
        *[
            pytest.param(_credentials("okx", site="us"), path, "https://us.okx.com", "0", id=f"okx-us-{path.rsplit('/', 1)[-1]}")
            for path in _OKX_PATHS
        ],
        *[
            pytest.param(_credentials("okx", site="eea"), path, "https://eea.okx.com", "0", id=f"okx-eea-{path.rsplit('/', 1)[-1]}")
            for path in _OKX_PATHS
        ],
        *[
            pytest.param(_credentials("bitget", account_mode="classic"), path, "https://api.bitget.com", "00000", id=f"bitget-classic-{path.rsplit('/', 1)[-1]}")
            for path in _BITGET_CLASSIC_PATHS
        ],
        *[
            pytest.param(_credentials("bitget", account_mode="uta"), path, "https://api.bitget.com", "00000", id=f"bitget-uta-{path.rsplit('/', 1)[-1]}")
            for path in _BITGET_UTA_PATHS
        ],
    ],
)
def test_get_uses_only_fixed_demo_hosts_and_documented_paths(
    monkeypatch: pytest.MonkeyPatch, credentials: dict, path: str, host: str, success_code: str
):
    calls = _install_client(monkeypatch, _response(payload={"code": success_code, "data": []}))

    assert demo_client.get(credentials, path) == []
    assert calls[0] == (
        "init",
        {"timeout": demo_client.httpx.Timeout(12, connect=8), "follow_redirects": False},
    )
    assert calls[1][1].startswith(host + path)


def test_endpoint_allowlists_are_exact_and_bitget_modes_are_separate():
    assert set(_OKX_PATHS) == demo_client.OKX_PATHS
    assert set(_BITGET_CLASSIC_PATHS) == demo_client.BITGET_CLASSIC_PATHS
    assert set(_BITGET_UTA_PATHS) == demo_client.BITGET_UTA_PATHS


@pytest.mark.parametrize(
    ("credentials", "path"),
    [
        pytest.param(_credentials("bitget", account_mode="classic"), "/api/v3/account/assets", id="classic-cannot-call-uta"),
        pytest.param(_credentials("bitget", account_mode="uta"), "/api/v2/mix/account/accounts", id="uta-cannot-call-classic"),
        pytest.param(_credentials("okx", site="eea"), "https://attacker.invalid/api/v5/account/balance", id="full-url-is-not-a-path"),
        pytest.param(_credentials("okx", site="eea"), "/api/v5/trade/order", id="non-read-only-endpoint"),
    ],
)
def test_get_rejects_unlisted_path_before_http(monkeypatch: pytest.MonkeyPatch, credentials: dict, path: str):
    monkeypatch.setattr(demo_client.httpx, "Client", lambda **_kwargs: pytest.fail("must reject before HTTP"))

    with pytest.raises(demo_client.DemoAPIError):
        demo_client.get(credentials, path)


def test_get_signs_the_encoded_query_and_does_not_change_the_selected_host(monkeypatch: pytest.MonkeyPatch):
    response = _response(payload={"code": "0", "data": [{"ccy": "BTC"}]})
    calls = _install_client(monkeypatch, response)
    credentials = _credentials("okx", site="eea")

    data = demo_client.get(
        credentials,
        "/api/v5/account/positions",
        {"instType": "SWAP", "instId": "BTC-USDT"},
    )

    expected_path = "/api/v5/account/positions?instType=SWAP&instId=BTC-USDT"
    assert data == [{"ccy": "BTC"}]
    assert calls[1][1] == "https://eea.okx.com" + expected_path
    assert calls[1][2]["OK-ACCESS-SIGN"] == demo_client.signature(
        credentials["secret_key"], calls[1][2]["OK-ACCESS-TIMESTAMP"], expected_path
    )
    assert calls[1][2]["x-simulated-trading"] == "1"


def test_http_redirect_is_not_followed_and_raw_error_body_is_not_echoed(monkeypatch: pytest.MonkeyPatch):
    sensitive_body = b"FAKE_RAW_RESPONSE_WITH_FAKE_SECRET_DO_NOT_ECHO"
    calls = _install_client(monkeypatch, _response(status_code=302, content=sensitive_body))

    with pytest.raises(demo_client.DemoAPIError) as exc_info:
        demo_client.get(_credentials("okx", site="global"), "/api/v5/account/balance")

    assert calls[0][1]["follow_redirects"] is False
    assert len([call for call in calls if call[0] == "get"]) == 1
    assert "代码 302" in str(exc_info.value)
    assert "[demo-error:http_status:302]" in str(exc_info.value)
    assert sensitive_body.decode() not in str(exc_info.value)
    assert "FAKE_SECRET" not in str(exc_info.value)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"code": "FAKE_SECRET_AUTH_ERROR", "msg": "FAKE_RAW_ERROR_BODY_SECRET", "data": []}, id="business-error"),
        pytest.param(["FAKE_RAW_ERROR_BODY_SECRET"], id="non-object-json"),
        pytest.param({"code": "0", "msg": "success"}, id="missing-data"),
    ],
)
def test_exchange_errors_and_malformed_payloads_never_echo_raw_content(
    monkeypatch: pytest.MonkeyPatch, payload: object
):
    _install_client(monkeypatch, _response(payload=payload, content=b"FAKE_RAW_ERROR_BODY_SECRET"))

    with pytest.raises(demo_client.DemoAPIError) as exc_info:
        demo_client.get(_credentials("bitget", account_mode="uta"), "/api/v3/account/assets")

    message = str(exc_info.value)
    assert "FAKE_SECRET_AUTH_ERROR" not in message
    assert "FAKE_RAW_ERROR_BODY_SECRET" not in message


def test_numeric_exchange_code_is_safe_diagnostic_and_ignores_exchange_message(monkeypatch: pytest.MonkeyPatch):
    _install_client(monkeypatch, _response(payload={
        "code": "40014", "msg": "FAKE_RAW_MESSAGE_WITH_FAKE_KEY", "data": [],
    }, content=b"FAKE_RAW_RESPONSE_WITH_FAKE_KEY"))

    with pytest.raises(demo_client.DemoAPIError) as exc_info:
        demo_client.get(_credentials("bitget", account_mode="uta"), "/api/v3/account/assets")

    message = str(exc_info.value)
    assert "代码 40014" in message
    assert "[demo-error:exchange_code:40014]" in message
    assert "FAKE_RAW_MESSAGE_WITH_FAKE_KEY" not in message
    assert "FAKE_RAW_RESPONSE_WITH_FAKE_KEY" not in message


def test_invalid_json_is_classified_without_echoing_body_or_parser_error(monkeypatch: pytest.MonkeyPatch):
    class InvalidJSONResponse:
        status_code = 200
        content = b"FAKE_RAW_RESPONSE_WITH_FAKE_KEY"

        def json(self):
            raise ValueError("FAKE_PARSER_ERROR_WITH_FAKE_KEY")

    _install_client(monkeypatch, InvalidJSONResponse())

    with pytest.raises(demo_client.DemoAPIError) as exc_info:
        demo_client.get(_credentials("bitget", account_mode="uta"), "/api/v3/account/assets")

    message = str(exc_info.value)
    assert "[demo-error:invalid_response]" in message
    assert "FAKE_RAW_RESPONSE_WITH_FAKE_KEY" not in message
    assert "FAKE_PARSER_ERROR_WITH_FAKE_KEY" not in message


def _fake_crypt(payload: bytes, *, decrypt: bool = False) -> bytes:
    """Test-only reversible stand-in; never used by the DPAPI integration test."""
    marker = b"FAKE_TEST_CIPHER:"
    if decrypt:
        assert payload.startswith(marker)
        return payload[len(marker) :]
    return marker + payload


def test_store_roundtrips_fake_profiles_and_preserves_other_exchange(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(demo_store, "_crypt", _fake_crypt)
    okx = _credentials("okx", site="eea")
    bitget = _credentials("bitget", account_mode="classic")

    demo_store.save(tmp_path, "okx", okx, "FAKE_OKX_API_KEY_****")
    demo_store.save(tmp_path, "bitget", bitget, "FAKE_BITGET_API_KEY_****")

    updated_okx = {**okx, "api_key": "FAKE_UPDATED_OKX_KEY_DO_NOT_USE"}
    demo_store.save(tmp_path, "okx", updated_okx, "FAKE_UPDATED_OKX_KEY_****")

    assert demo_store.read(tmp_path, "okx") == updated_okx
    assert demo_store.read(tmp_path, "bitget") == bitget
    assert demo_store.profiles(tmp_path) == {
        "okx": {"site": "eea", "account_mode": "classic", "masked_key": "FAKE_UPDATED_OKX_KEY_****"},
        "bitget": {"site": "global", "account_mode": "classic", "masked_key": "FAKE_BITGET_API_KEY_****"},
    }


@pytest.mark.parametrize("corrupt_bytes", [b"{", b'{"schema":99,"accounts":{}}'])
def test_corrupt_store_is_preserved_when_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt_bytes: bytes
):
    monkeypatch.setattr(demo_store, "_crypt", _fake_crypt)
    target = tmp_path / "user_data" / "crypto_demo_credentials.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(corrupt_bytes)

    with pytest.raises(demo_store.DemoStoreError):
        demo_store.save(tmp_path, "okx", _credentials("okx", site="eea"), "FAKE_OKX_API_KEY_****")

    assert target.read_bytes() == corrupt_bytes
    assert list(target.parent.glob(target.name + ".*.tmp")) == []


@pytest.mark.skipif(not demo_store.supported(), reason="Windows DPAPI is only available on Windows")
def test_windows_dpapi_roundtrips_fake_credentials_without_persisting_plaintext(tmp_path: Path):
    credentials = _credentials("okx", site="eea")
    demo_store.save(tmp_path, "okx", credentials, "FAKE_OKX_API_KEY_****")

    target = tmp_path / "user_data" / "crypto_demo_credentials.json"
    persisted = target.read_bytes()
    for field in ("api_key", "secret_key", "passphrase"):
        assert credentials[field].encode() not in persisted

    assert demo_store.read(tmp_path, "okx") == credentials
    assert demo_store.profiles(tmp_path) == {
        "okx": {"site": "eea", "account_mode": "classic", "masked_key": "FAKE_OKX_API_KEY_****"}
    }
