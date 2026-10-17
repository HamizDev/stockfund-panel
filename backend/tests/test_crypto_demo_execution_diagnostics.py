from __future__ import annotations

import httpx
import pytest

from app.custom.crypto_paper import demo_execution
from app.custom.crypto_paper.demo_client import DemoAPIError

_HTTPX_CLIENT = httpx.Client

_CREDENTIALS = {
    'exchange': 'okx',
    'site': 'global',
    'account_mode': 'classic',
    'api_key': 'FAKE_OKX_API_KEY_DO_NOT_USE',
    'secret_key': 'FAKE_OKX_SECRET_DO_NOT_USE',
    'passphrase': 'FAKE_OKX_PASSPHRASE_DO_NOT_USE',
}
_CLIENT_ID = 'dm' + 'a' * 30


def _install_response(monkeypatch, responder):
    requests = []

    def handle(request):
        requests.append(request)
        return responder(request)

    def client_factory(**kwargs):
        return _HTTPX_CLIENT(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(demo_execution.httpx, 'Client', client_factory)
    return requests


def _response(status_code=200, payload=None, content=None):
    if content is not None:
        return httpx.Response(status_code, content=content)
    return httpx.Response(status_code, json=payload)


def _post(body=None):
    return demo_execution._transport(
        _CREDENTIALS, 'POST', '/api/v5/trade/order', body=body or {'clOrdId': _CLIENT_ID},
    )


def test_okx_get_keeps_numeric_exchange_code_as_safe_diagnostic(monkeypatch):
    _install_response(monkeypatch, lambda _request: _response(payload={
        'code': '51603', 'msg': 'FAKE_EXCHANGE_MESSAGE', 'data': [],
    }))

    with pytest.raises(DemoAPIError) as caught:
        demo_execution._transport(_CREDENTIALS, 'GET', '/api/v5/account/config')

    assert caught.value.diagnostic_category == 'exchange_code'
    assert caught.value.diagnostic_code == '51603'
    assert '[demo-error:exchange_code:51603]' in str(caught.value)
    assert 'FAKE_EXCHANGE_MESSAGE' not in str(caught.value)


def test_okx_post_uses_scode_only_from_matching_clordid_row(monkeypatch):
    _install_response(monkeypatch, lambda _request: _response(payload={
        'code': '1', 'msg': 'FAKE_TOP_LEVEL_MESSAGE',
        'data': [{'clOrdId': _CLIENT_ID, 'sCode': '51008', 'sMsg': 'FAKE_ROW_MESSAGE'}],
    }))

    with pytest.raises(DemoAPIError) as caught:
        _post()

    assert caught.value.diagnostic_category == 'exchange_code'
    assert caught.value.diagnostic_code == '51008'
    assert 'FAKE_TOP_LEVEL_MESSAGE' not in str(caught.value)
    assert 'FAKE_ROW_MESSAGE' not in str(caught.value)


def test_okx_post_mismatched_clordid_does_not_use_row_scode_or_smsg(monkeypatch):
    _install_response(monkeypatch, lambda _request: _response(payload={
        'code': '1', 'msg': 'FAKE_TOP_LEVEL_SECRET',
        'data': [{'clOrdId': 'dm' + 'b' * 30, 'sCode': '51008', 'sMsg': 'FAKE_ROW_SECRET'}],
    }))

    with pytest.raises(DemoAPIError) as caught:
        _post()

    assert caught.value.diagnostic_category == 'exchange_code'
    assert caught.value.diagnostic_code == '1'
    message = str(caught.value)
    assert '51008' not in message
    assert 'FAKE_TOP_LEVEL_SECRET' not in message
    assert 'FAKE_ROW_SECRET' not in message


def test_okx_post_malicious_code_and_message_are_not_echoed(monkeypatch):
    _install_response(monkeypatch, lambda _request: _response(payload={
        'code': 'FAKE_CODE_SECRET', 'msg': 'FAKE_MESSAGE_SECRET', 'data': [],
    }))

    with pytest.raises(DemoAPIError) as caught:
        _post()

    assert caught.value.diagnostic_category == 'exchange_error'
    assert caught.value.diagnostic_code is None
    assert 'FAKE_CODE_SECRET' not in str(caught.value)
    assert 'FAKE_MESSAGE_SECRET' not in str(caught.value)


def test_transport_classifies_network_and_http_status_failures_without_echoing_payload(monkeypatch):
    def network_failure(request):
        raise httpx.ConnectError('FAKE_NETWORK_SECRET', request=request)

    _install_response(monkeypatch, network_failure)
    with pytest.raises(DemoAPIError) as network_error:
        demo_execution._transport(_CREDENTIALS, 'GET', '/api/v5/account/config')
    assert network_error.value.diagnostic_category == 'network'
    assert network_error.value.diagnostic_code is None
    assert 'FAKE_NETWORK_SECRET' not in str(network_error.value)

    _install_response(monkeypatch, lambda _request: _response(
        status_code=503, content=b'FAKE_HTTP_BODY_SECRET',
    ))
    with pytest.raises(DemoAPIError) as status_error:
        demo_execution._transport(_CREDENTIALS, 'GET', '/api/v5/account/config')
    assert status_error.value.diagnostic_category == 'http_status'
    assert status_error.value.diagnostic_code == '503'
    assert 'FAKE_HTTP_BODY_SECRET' not in str(status_error.value)
