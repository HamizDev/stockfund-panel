"""Research rejects invalid inputs before a paid model call or a confident report."""
import json
from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.services import ai_provider, analysis_quality, analysis_snapshot, stock_analyzer


def frame(**overrides):
    return pl.DataFrame({'symbol': ['510300.SH'], 'date': [date(2026, 10, 22)],
                         'open': [4.], 'high': [4.2], 'low': [3.9], 'close': [4.1],
                         'volume': [100.]} | overrides)


@pytest.mark.parametrize('changes', [
    {'symbol': ['515880.SH']}, {'date': [date(2026, 10, 24)]},
    {'close': [float('nan')]}, {'volume': [-1.]}, {'high': [3.9]}, {'low': [4.3]}, {'volume': [True]},
])
def test_rejects_cross_symbol_future_and_invalid_market_inputs(changes):
    result = analysis_quality.inspect_frame(frame(**changes), '510300.SH', date(2026, 10, 23), strict=True)
    assert result['errors']


def test_duplicate_dates_and_missing_fields_are_not_an_accurate_sample():
    sample = frame()
    assert analysis_quality.inspect_frame(pl.concat([sample, sample]), '510300.SH', date(2026, 10, 23))['errors']
    assert analysis_quality.inspect_frame(sample.drop('volume'), '510300.SH', date(2026, 10, 23), strict=True)['errors']
    valid = analysis_quality.inspect_frame(sample, '510300.SH', date(2026, 10, 23), strict=True)
    assert not valid['errors'] and valid['as_of'] == '2026-10-22' and valid['warnings']


def test_financial_cutoff_uses_publication_not_period_end():
    sample = pl.DataFrame({'period_end': ['2026-06-30'] * 4,
                           'announce_date': ['2026-08-20', '2026-10-24', None, 'bad'],
                           'revenue': [1., 2., 3., 4.]})
    selected = analysis_quality.published_rows(sample, date(2026, 10, 22))
    assert selected['revenue'].to_list() == [1.]
    assert analysis_quality.published_rows(sample.drop('announce_date'), date(2026, 10, 22)).is_empty()


def test_financial_prompt_serializes_source_dates_and_skips_future_filings(monkeypatch, tmp_path):
    sample = pl.DataFrame({'symbol': ['600519.SH'] * 2,
                           'period_end': [date(2026, 6, 30)] * 2,
                           'announce_date': [date(2026, 8, 20), date(2026, 10, 24)],
                           'net_profit': [10., 999.]})
    monkeypatch.setattr(stock_analyzer, 'get_financial_df', lambda *_: sample)
    result = stock_analyzer._load_financials(tmp_path, '600519.SH', as_of=date(2026, 10, 22))
    assert result['income'][0]['announce_date'] == '2026-08-20'
    assert result['income'][0]['net_profit'] == 10.
    assert json.loads(json.dumps(result)) == result


async def test_invalid_fresh_snapshot_does_not_call_model(monkeypatch, tmp_path):
    repo = SimpleNamespace(resolve_asset_type=lambda _: 'etf')
    monkeypatch.setattr(analysis_snapshot, 'load_snapshot', lambda *a, **k: (frame(close=[float('inf')]), {}))

    async def never(*_a, **_k):
        pytest.fail('invalid input must stop before the model')
        yield ''

    monkeypatch.setattr(ai_provider, 'stream_ai_text', never)
    events = [json.loads(line) async for line in stock_analyzer.analyze_stock_stream(
        repo, tmp_path, '510300.SH', refresh=True)]
    assert len(events) == 1 and events[0]['type'] == 'error'


def test_evidence_does_not_call_input_checks_accuracy_or_invent_news():
    quality = analysis_quality.inspect_frame(frame(), '510300.SH', date(2026, 10, 23), strict=True)
    note = analysis_quality.evidence_instruction(quality, {'is_provisional': True},
                                                 financials_available=False, asset_type='etf')
    assert '2026-10-22' in note and '相反证据' in note and '未定版' in note
    assert '不代表预测准确' in note
    assert '盈利质量' not in note


async def test_cached_history_also_limits_financials_to_its_cutoff(monkeypatch, tmp_path):
    from app.strategy import paper

    repo = SimpleNamespace(resolve_asset_type=lambda _: 'stock')
    monkeypatch.setattr(stock_analyzer, '_load_kline', lambda *_: frame(symbol=['600519.SH']))
    monkeypatch.setattr(stock_analyzer, 'compute_levels', lambda *_: {})
    monkeypatch.setattr(paper, 'list_account_ids', lambda *_: [])
    captured = {}

    def load(*_a, **kwargs):
        captured.update(kwargs)
        return {}

    async def stream(*_a, **_k):
        yield 'fake report'

    monkeypatch.setattr(stock_analyzer, '_load_financials', load)
    monkeypatch.setattr(ai_provider, 'stream_ai_text', stream)
    events = [json.loads(line) async for line in stock_analyzer.analyze_stock_stream(
        repo, tmp_path, '600519.SH', research_plan=True)]
    assert events[-1]['type'] == 'done'
    assert captured['as_of'] == date(2026, 10, 22)
