import pytest

from app.services import quote_service as quotes


@pytest.mark.parametrize(('processing', 'next_start'), [(2.0, 6.0), (8.0, 8.5)])
def test_poll_interval_counts_processing_time_without_overlapping_or_catchup(monkeypatch, processing, next_start):
    service = quotes.QuoteService()
    service._running = service._enabled = True
    service._interval = 6.0
    clock = [0.0]
    starts = []
    monkeypatch.setattr(quotes.time, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(quotes.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(service, '_market_phase', lambda: 'morning')
    monkeypatch.setattr(service, '_should_fetch_for_phase', lambda _: True)
    monkeypatch.setattr(service, '_final_boundary_ms', lambda _: None)
    def fetch(**kwargs):
        starts.append(clock[0])
        clock[0] += processing
        if len(starts) == 2:
            service._running = False
        return True
    monkeypatch.setattr(service, '_fetch_quotes', fetch)
    service._poll_loop()
    assert starts == [0, next_start]
    assert service._cycle_ms == processing * 1000


def test_status_distinguishes_received_snapshot_age_from_source_time(monkeypatch):
    service = quotes.QuoteService()
    monkeypatch.setattr(service, 'realtime_mode', lambda: 'full_market')
    monkeypatch.setattr(service, '_market_phase', lambda: 'morning')
    monkeypatch.setattr(service, '_is_continuous_trading', lambda: True)
    monkeypatch.setattr(quotes.time, 'time', lambda: 1_800_000_100)
    monkeypatch.setattr(quotes.time, 'perf_counter', lambda: 500)
    service._fetch_time = 499
    service._source_timestamp_ms = 1_800_000_000_000
    service._fetch_ms = 3000
    service._cycle_ms = 6000
    result = service.status()
    assert result['quote_age_ms'] == 1000
    assert result['source_age_ms'] == 100_000
    assert result['fetch_duration_ms'] == 3000
    assert result['cycle_duration_ms'] == 6000
