from datetime import datetime
from types import SimpleNamespace

import pytest

from app.data_providers import tickflow_provider
from app.market_time import CN_TZ


@pytest.mark.parametrize(('day', 'hour', 'clock_hour', 'cleared'), [
    (28, 14, 0, True),
    (29, 14, 0, False),
    (29, 16, 0, True),
    (29, 16, 14, False),
    (28, 16, 14, False),
    (30, 16, 0, False),
])
def test_only_closed_history_midnight_markers_are_removed(monkeypatch, day, hour, clock_hour, cleared):
    stamp = int(datetime(2026, 10, day, clock_hour, tzinfo=CN_TZ).timestamp() * 1000)
    raw = {'515880.SH': {'timestamp': [stamp], 'open': [0.52], 'high': [0.53],
        'low': [0.51], 'close': [0.525], 'volume': [10000], 'amount': [525000]}}
    monkeypatch.setattr(tickflow_provider, 'cn_now', lambda: datetime(2026, 10, 29, hour, tzinfo=CN_TZ))
    monkeypatch.setattr(tickflow_provider, 'get_client', lambda: SimpleNamespace(
        klines=SimpleNamespace(batch=lambda *args, **kwargs: raw)))
    result = tickflow_provider.TickFlowProvider().get_daily(
        ['515880.SH'], datetime(2026, 10, day), datetime(2026, 10, day), 'etf')
    assert result['quote_ts'][0] == (None if cleared else stamp)
    assert result['date'][0].isoformat() == f'2026-10-{day}'
    assert result['close'][0] == 0.525 and result['volume'][0] == 10000
