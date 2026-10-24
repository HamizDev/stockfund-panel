from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.data_providers import custom, source_routing
from app.data_providers.source_health import SourceHealth
from app.services import kline_sync, preferences, trading_calendar


@pytest.mark.parametrize(('field', 'primary', 'backup', 'dataset'), [
    ('realtime_data_provider', 'stocksdk', 'fuyao', 'realtime'),
])
def test_unavailable_primary_runtime_uses_installed_equivalent(monkeypatch, field, primary, backup, dataset):
    monkeypatch.setattr(preferences, 'load', lambda: {field: primary, 'data_source_failover_enabled': True})
    monkeypatch.setattr(preferences, '_allowed_data_providers', lambda: {'tickflow', backup})
    monkeypatch.setattr(custom, 'provider_has_dataset', lambda name, data: (name, data) == (backup, dataset))
    assert preferences._available_read_provider(field) == backup
    monkeypatch.setattr(custom, 'provider_has_dataset', lambda *_: False)
    assert preferences._available_read_provider(field) == 'tickflow'


def test_missing_opt_in_preserves_legacy_unavailable_provider_behavior(monkeypatch):
    monkeypatch.setattr(preferences, 'load', lambda: {'realtime_data_provider': 'stocksdk'})
    monkeypatch.setattr(preferences, '_allowed_data_providers', lambda: {'tickflow', 'fuyao'})
    assert preferences.get_realtime_data_provider() == 'tickflow'


def test_missing_minute_plugin_does_not_bypass_price_basis_and_frequency_checks(monkeypatch):
    monkeypatch.setattr(preferences, 'load', lambda: {
        'minute_data_provider': 'txquote', 'data_source_failover_enabled': True})
    monkeypatch.setattr(preferences, '_allowed_data_providers', lambda: {'tickflow', 'stocksdk'})
    assert preferences.get_minute_data_provider() == 'tickflow'


@pytest.mark.parametrize('symbol', ['600519.SH', '515880.SH', '000001.SH'])
def test_shared_daily_read_respects_selected_provider_for_stock_etf_index(monkeypatch, symbol):
    from app.data_providers import source_routing

    monkeypatch.setattr(source_routing, 'enabled', lambda: True)
    monkeypatch.setattr(preferences, 'get_daily_data_provider', lambda: 'stocksdk')
    monkeypatch.setattr(custom, 'provider_has_dataset', lambda *_: True)
    frame = pl.DataFrame({'symbol': [symbol], 'date': [date(2026, 10, 23)], 'close': [1.0]})
    calls = []
    monkeypatch.setattr(custom, 'get_provider', lambda name: SimpleNamespace(
        get_daily=lambda symbols, **kwargs: (calls.append((name, symbols)) or frame)))
    monkeypatch.setattr(kline_sync, '_sync_tickflow_daily_batch', lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError('must not bypass selected provider')))
    received = kline_sync.sync_daily_batch([symbol], start_time=datetime(2026, 10, 22),
                                         end_time=datetime(2026, 10, 23, 23, 59))
    assert received.equals(frame)
    assert calls == [('stocksdk', [symbol])]


def test_stale_daily_response_tries_backup_without_replacing_newer_prices(monkeypatch):
    from app.data_providers.tickflow_provider import TickFlowProvider

    def frame(day, close):
        return pl.DataFrame({'symbol': ['515880.SH'], 'date': [day], 'open': [close],
                             'high': [close], 'low': [close], 'close': [close], 'volume': [100]})

    monkeypatch.setattr(source_routing, 'enabled', lambda: True)
    monkeypatch.setattr(source_routing, 'HEALTH', SourceHealth())
    monkeypatch.setattr(trading_calendar, 'previous_trading_day', lambda *_args, **_kwargs: date(2026, 10, 23))
    primary = SimpleNamespace(name='stocksdk', get_daily=lambda *_args, **_kwargs: frame(date(2026, 10, 22), 0.5))
    calls = []
    monkeypatch.setattr(TickFlowProvider, 'get_daily', lambda *_args, **_kwargs:
                        (calls.append('backup') or frame(date(2026, 10, 23), 0.514)))
    result = source_routing.wrap(primary).get_daily(['515880.SH'],
        start_time=datetime(2026, 10, 20), end_time=datetime(2026, 10, 23, 23, 59))
    assert calls == ['backup']
    assert result['date'][-1] == date(2026, 10, 23)
    assert result['close'][-1] == 0.514
    assert result['date'].to_list() == [date(2026, 10, 22), date(2026, 10, 23)]


def test_two_stale_or_failed_sources_do_not_report_daily_sync_success(monkeypatch):
    from app.data_providers.tickflow_provider import TickFlowProvider

    monkeypatch.setattr(source_routing, 'enabled', lambda: True)
    monkeypatch.setattr(source_routing, 'HEALTH', SourceHealth())
    monkeypatch.setattr(trading_calendar, 'previous_trading_day', lambda *_args, **_kwargs: date(2026, 10, 23))
    old = pl.DataFrame({'symbol': ['515880.SH'], 'date': [date(2026, 10, 22)],
        'open': [0.5], 'high': [0.5], 'low': [0.5], 'close': [0.5], 'volume': [100]})
    primary = SimpleNamespace(name='stocksdk', get_daily=lambda *_args, **_kwargs: old)
    proxy = source_routing.wrap(primary)
    monkeypatch.setattr(TickFlowProvider, 'get_daily', lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(TimeoutError('private-url')))
    monkeypatch.setattr(preferences, 'get_daily_data_provider', lambda: 'stocksdk')
    monkeypatch.setattr(custom, 'provider_has_dataset', lambda *_: True)
    monkeypatch.setattr(custom, 'get_provider', lambda *_: proxy)
    failures = []
    result = kline_sync.sync_daily_batch(['515880.SH'], start_time=datetime(2026, 10, 20),
        end_time=datetime(2026, 10, 23, 23, 59), failed_out=failures)
    assert result.is_empty()
    assert failures == ['515880.SH']


def test_minute_partial_result_fills_only_missing_symbol_from_backup(monkeypatch):
    monkeypatch.setattr(source_routing, 'enabled', lambda: True)
    monkeypatch.setattr(source_routing, 'HEALTH', SourceHealth())
    now = datetime(2026, 10, 23, 10)
    def frame(symbol):
        return pl.DataFrame({'symbol': [symbol], 'datetime': [now], 'close': [0.5], 'amount': [None]})
    primary = SimpleNamespace(name='txquote', get_minute=lambda *_args, **_kwargs: frame('515880.SH'))
    calls = []
    monkeypatch.setattr(source_routing, '_raw', lambda name: SimpleNamespace(
        get_minute=lambda symbols, **kwargs: (calls.append((name, symbols)) or frame('512800.SH'))))
    result = source_routing.wrap(primary).get_minute(['515880.SH', '512800.SH'],
        start_time=datetime(2026, 10, 23, 9, 30), end_time=now, asset_type='etf', freq='1m')
    assert calls == [('stocksdk', ['512800.SH'])]
    assert set(result['symbol']) == {'515880.SH', '512800.SH'}
    assert result['amount'].null_count() == 2
