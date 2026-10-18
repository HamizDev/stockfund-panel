from contextlib import contextmanager

import httpx
import pytest

from app.custom.global_markets import provider
from app.custom.global_markets.catalog import MarketDataError


@pytest.fixture(autouse=True)
def health(monkeypatch):
    monkeypatch.setattr(provider, '_failures', 0)
    monkeypatch.setattr(provider, '_health_generation', 0)
    monkeypatch.setattr(provider, '_cooldown_until', 0)
    monkeypatch.setattr(provider, '_clock', lambda: 1000)


def test_consecutive_source_failures_cool_down_and_old_success_cannot_clear_it(monkeypatch):
    old = provider._request_gate()
    provider._source_failed()
    provider._source_failed()
    provider._source_succeeded(old)
    provider._source_failed()
    with pytest.raises(MarketDataError, match='300秒'):
        provider._request_gate()
    monkeypatch.setattr(provider, '_clock', lambda: 1301)
    current = provider._request_gate()
    provider._source_succeeded(current)
    assert provider._failures == 0


def test_429_gates_later_requests_without_network_and_symbol_404_does_not_poison_source(monkeypatch):
    calls = []
    status = [404]

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def stream(self, *args, **kwargs):
            calls.append(status[0])
            yield httpx.Response(status[0], json={})

    monkeypatch.setattr(provider.httpx, 'Client', Client)
    for _ in range(4):
        with pytest.raises(MarketDataError):
            provider._json('/test', {})
    assert provider._failures == 0
    status[0] = 429
    with pytest.raises(MarketDataError, match='限流'):
        provider._json('/test', {})
    with pytest.raises(MarketDataError, match='冷却'):
        provider._json('/another-symbol', {})
    assert calls == [404, 404, 404, 404, 429]
