from types import SimpleNamespace

import polars as pl

from app.services import preferences
from app.services import quote_service as quotes


def test_backup_receipt_time_cannot_confirm_final_or_arm_etf_execution(monkeypatch):
    stamp = 1_800_000_000_000
    received = []
    repo = SimpleNamespace(
        get_index_symbol_set=lambda: set(),
        get_etf_instruments=lambda: pl.DataFrame({'symbol': ['512800.SH', '515880.SH']}),
        flush_live_daily=lambda *_: (_ for _ in ()).throw(AssertionError('must not finalize')),
    )
    service = quotes.QuoteService()
    service._repo = repo
    service._app_state = SimpleNamespace(etf_shortterm_service=SimpleNamespace(
        update_quotes=lambda rows, **kwargs: received.append((rows, kwargs))))
    monkeypatch.setattr(preferences, 'get_realtime_data_provider', lambda: 'stocksdk')
    monkeypatch.setattr(quotes, '_persist_last_fetch', lambda *_: None)
    monkeypatch.setattr(quotes.time, 'time', lambda: stamp / 1000 + 1)
    monkeypatch.setattr(service, '_broadcast_quote_updated', lambda: None)
    records = [
        {'symbol': '512800.SH', 'last_price': 1.0, 'timestamp': stamp, '_source_timestamp_verified': True},
        {'symbol': '515880.SH', 'last_price': 0.5, 'timestamp': stamp + 500,
         '_source_timestamp_verified': False},
    ]
    service._process_full_market_records(records, t0=quotes.time.perf_counter(), now_ts=1,
                                         final_boundary_ms=stamp)
    assert service._last_final_confirmed is False
    assert service._source_timestamp_ms == stamp
    assert len(received) == 1
    assert [row['symbol'] for row in received[0][0]] == ['512800.SH']
    assert received[0][1] == {'source_timestamps': True}


def test_undated_backup_cannot_create_today_candle_or_evaluate_monitors(monkeypatch):
    record = {'symbol': '600519.SH', 'last_price': 1.0, 'open': 1.0, 'high': 1.0,
              'low': 1.0, 'volume': 100, 'amount': 10000, 'timestamp': None,
              '_source_timestamp_verified': False}
    assert quotes.QuoteService._build_daily([record]).is_empty()
    service = quotes.QuoteService()
    service._repo = SimpleNamespace(get_index_symbol_set=lambda: set(),
        get_etf_instruments=lambda: pl.DataFrame(),
        flush_live_daily=lambda *_: (_ for _ in ()).throw(AssertionError('must not persist')))
    deltas = []
    monkeypatch.setattr(quotes, '_persist_last_fetch', lambda *_: None)
    monkeypatch.setattr(service, '_update_volume_delta', lambda rows, *_: deltas.append(rows))
    monkeypatch.setattr(service, '_broadcast_quote_updated', lambda: None)
    monkeypatch.setattr(service, '_evaluate_monitors', lambda *_:
                        (_ for _ in ()).throw(AssertionError('must not evaluate stale cache')))
    service._process_full_market_records([record], t0=quotes.time.perf_counter(), now_ts=1)
    assert deltas == [[]]


def test_unverified_index_display_cache_is_excluded_from_indicator_consumers():
    service = quotes.QuoteService()
    service._index_quotes_cache = service._build_index_quotes([
        {'symbol': '000001.SH', 'last_price': 100.0, 'change_pct': 0.1, 'timestamp': None,
         '_source_timestamp_verified': False},
        {'symbol': '399001.SZ', 'last_price': 110.0, 'change_pct': 0.02, 'timestamp': 1_800_000_000_000,
         '_source_timestamp_verified': True},
    ])
    assert service.get_index_quotes()['symbol'].to_list() == ['399001.SZ']
    assert set(service.get_index_quotes(allow_unverified=True)['symbol']) == {'000001.SH', '399001.SZ'}
    assert service.get_index_quotes(['000001.SH']).is_empty()
