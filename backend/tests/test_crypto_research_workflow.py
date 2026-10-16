from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.crypto_paper import auto, research_log
from app.custom.crypto_paper import research_service as service
from app.custom.crypto_paper.research_routes import build_router

NOW = 1_800_000_000_000


@pytest.fixture
def account(tmp_path):
    body = dict(name='Research', exchange='bitget', market='usdm', symbol='ETHUSDT',
                strategy_id='supertrend_pullback', interval='1h', leverage=1,
                initial_cash=10000, allocation_pct=10, stop_loss_pct=2,
                take_profit_pct=4, request_id='test-account')
    created = auto.create(tmp_path, body)[0]
    return auto._load(tmp_path)['accounts'][created['id']]


def test_decisions_deduplicate_quote_only_changes_and_bound_history(account):
    account.update(enabled=True, last_signal='hold', status='running')
    bars = [{'close_time_ms': NOW}]
    research_log.record(account, NOW, bars, 0)
    account['equity'] = '10001'
    research_log.record(account, NOW + 1000, bars, 0)
    assert len(account['decisions']) == 1
    assert account['decisions'][0]['status'] == 'waiting'
    assert account['decisions'][0]['equity'] == '10000'
    for i in range(305):
        research_log.record(account, NOW + i, [{'close_time_ms': NOW + i}], 0)
    assert len(account['decisions']) == 300


def test_funding_records_are_not_described_as_strategy_fills(account):
    account.update(enabled=True, last_signal='hold', status='running')
    account['ledger']['trades'] = [{'kind': 'funding', 'fee': '0'}]
    research_log.record(account, NOW, [], 0)
    assert account['decisions'][-1]['trades_added'] == 0
    assert account['decisions'][-1]['status'] == 'waiting'


def test_errors_and_risk_exits_have_no_stale_direction_or_fabricated_metrics(account):
    account.update(enabled=True, last_signal='long', status='error', last_error='行情过期')
    research_log.record(account, NOW, [], 0)
    event = account['decisions'][-1]
    assert event['target'] is None and event['metrics'] == {}
    assert event['status'] == 'blocked' and '过期' in event['reason']
    account.update(status='running', last_error=None)
    account['ledger']['trades'].append({'kind': 'trade', 'request_id': 'x_risk'})
    research_log.record(account, NOW + 1, [], 0)
    assert account['decisions'][-1]['status'] == 'risk_exit'
    assert account['decisions'][-1]['target'] is None


def test_daily_review_uses_iso_trade_timezone_and_real_events_only(tmp_path, account, monkeypatch):
    monkeypatch.setattr(service.time, 'time', lambda: NOW / 1000)
    day = service._date(NOW)
    account.update(decisions=[dict(at_ms=NOW, status='blocked', reason='数据过期',
                                   trades_added=0, equity='9995')])
    account['ledger']['trades'] = [dict(kind='trade', at=datetime.fromtimestamp(NOW / 1000, UTC).isoformat()),
                                   dict(kind='funding', at=datetime.fromtimestamp(NOW / 1000, UTC).isoformat())]
    state = auto._load(tmp_path)
    state['accounts'][account['id']] = account
    auto._save(tmp_path, state)
    original = auto._path(tmp_path).read_bytes()
    result = service.review(tmp_path, day)
    assert result['accounts'][0]['trade_count'] == 1
    assert result['accounts'][0]['blocked'] == 1
    assert result['accounts'][0]['checks'] == 1
    assert result['accounts'][0]['equity'] == '9995'
    assert auto._path(tmp_path).read_bytes() == original
    assert service.reviews(tmp_path)['reviews'][0] == result
    assert service._trade_date({'at': '2026-10-15T16:01:00+00:00'}) == '2026-10-16'


def test_missing_historical_decisions_stay_unknown_and_future_day_rejected(tmp_path, account):
    value = service.review(tmp_path, '2026-01-01')
    assert value['accounts'][0]['checks'] == 0
    assert value['accounts'][0]['equity'] is None
    assert '尚无' in value['accounts'][0]['last_reason']
    with pytest.raises(ValueError):
        service.review(tmp_path, '2999-01-01')
    with pytest.raises(ValueError):
        service.review(tmp_path, '../x')


def test_review_rebuild_invalidates_older_ai_commentary(tmp_path, account):
    value = service.review(tmp_path)
    path = service._root(tmp_path) / 'reviews' / f"{value['date']}.json"
    service._write(path, {**value, 'ai': {'content': 'old'}})
    assert 'ai' not in service.review(tmp_path)
    assert 'ai' not in service.reviews(tmp_path)['reviews'][0]


def test_daily_stats_run_once_per_day_and_do_not_call_ai(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(service, '_DAILY', {})
    monkeypatch.setattr(service, 'review', lambda data_dir, day: calls.append(day))
    monkeypatch.setattr(service.ai_provider, 'generate_ai_text', lambda *a, **k: pytest.fail('Daily stats never call AI'))
    service.maybe_daily(tmp_path)
    service.maybe_daily(tmp_path)
    assert len(calls) == 1
    assert calls[0] < datetime.now(service.HK).date().isoformat()


def test_backtest_service_saves_version_and_preserves_accounts(tmp_path, account, monkeypatch):
    data = [dict(open_time_ms=1, close_time_ms=2)] * 500
    result = dict(metrics={'return_pct': 1}, nav=[], trades=[], warnings=['fee only'], method='research')
    monkeypatch.setattr(service.bitget, '_server_time_ms', lambda: NOW)
    def fetch(*args, **kwargs):
        assert args == ('usdm', 'ETHUSDT', '1h', 500)
        assert kwargs['end_ms'] == NOW // 3_600_000 * 3_600_000 - 1
        return copy.deepcopy(data)
    monkeypatch.setattr(service.bitget, 'klines', fetch)
    monkeypatch.setattr(service.bitget, '_contract_rules', lambda *a: {'taker_fee_rate': '.0006'})
    monkeypatch.setattr(service.research_backtest, 'run', lambda *a: copy.deepcopy(result))
    original = auto._path(tmp_path).read_bytes()
    saved = service.backtest(tmp_path, service.BacktestRequest())
    assert service.run_detail(tmp_path, saved['id']) == saved
    summary = service.history(tmp_path)['runs'][0]
    assert 'nav' not in summary and 'trades' not in summary
    assert summary['config_sha256'] and summary['evidence']['bars_sha256']
    assert auto._path(tmp_path).read_bytes() == original
    report = service.review(tmp_path)
    assert report['candidates'][0]['status'] == 'research_only'
    with pytest.raises(ValueError):
        service.run_detail(tmp_path, '../escape')


def test_backtest_lock_releases_after_failed_source(tmp_path, monkeypatch):
    monkeypatch.setattr(service.bitget, '_server_time_ms', lambda: NOW)
    def fail(*a, **k):
        raise ValueError('source failed')
    monkeypatch.setattr(service.bitget, 'klines', fail)
    with pytest.raises(ValueError, match='source failed'):
        service.backtest(tmp_path, service.BacktestRequest())
    assert service._RUN_LOCK.acquire(blocking=False)
    service._RUN_LOCK.release()


def test_research_api_validates_before_fetch_and_keeps_gets_readonly(tmp_path, monkeypatch):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(build_router(), prefix='/api/custom/crypto-paper')
    monkeypatch.setattr(service, 'backtest', lambda *a: pytest.fail('Invalid request must not fetch'))
    with TestClient(app) as client:
        for body in ({'bars': 249}, {'bars': True}, {'symbol': 'X'}, {'allocation_pct': 81}):
            assert client.post('/api/custom/crypto-paper/research/backtest', json=body).status_code == 422
        assert client.get('/api/custom/crypto-paper/research/history').json() == {'runs': []}
        assert client.get('/api/custom/crypto-paper/research/reviews').json() == {'reviews': []}
        assert client.get('/api/custom/crypto-paper/research/accounts/missing/decisions').status_code == 404
    assert not list(tmp_path.iterdir())


def test_on_demand_ai_is_bounded_and_does_not_mutate_accounts(tmp_path, account, monkeypatch):
    monkeypatch.setattr(service.ai_provider, 'ai_configured', lambda: True)
    monkeypatch.setattr(service.ai_provider, 'current_ai_model', lambda: 'configured-model')
    async def generate(messages, **kwargs):
        value = json.loads(messages[-1]['content'])
        assert value['accounts'][0]['trade_count'] == 0
        assert 'id' not in value['accounts'][0] and 'name' not in value['accounts'][0]
        assert '缺失显示未知' in value['method']
        assert kwargs['max_tokens'] == 1400
        return '没有成交样本,需要继续积累数据。'
    monkeypatch.setattr(service.ai_provider, 'generate_ai_text', generate)
    before = auto._path(tmp_path).read_bytes()
    result = asyncio.run(service.review_ai(tmp_path))
    assert result['ai']['model'] == 'configured-model'
    assert service.reviews(tmp_path)['reviews'][0]['ai'] == result['ai']
    assert auto._path(tmp_path).read_bytes() == before


def test_research_quota_refuses_new_records_without_deleting_old_data(tmp_path):
    folder = service._root(tmp_path) / 'runs'
    folder.mkdir(parents=True)
    for index in range(100):
        (folder / f'{index}.json').write_text('{}')
    with pytest.raises(ValueError, match='上限'):
        service._write(folder / 'new.json', {'record': 1})
    assert len(list(folder.glob('*.json'))) == 100
