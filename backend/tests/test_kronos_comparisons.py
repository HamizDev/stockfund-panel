# ruff: noqa: RUF001 -- Chinese research messages and exact price-basis contract.
"""Frozen-input comparisons never fan out GPU jobs or fabricate paired scores."""
import copy
import threading
from datetime import datetime

import pytest

from app.custom.kronos import service
from app.market_time import CN_TZ


def test_reserved_comparison_blocks_manual_job(tmp_path, monkeypatch):
    jobs = service.JobManager(tmp_path)
    monkeypatch.setattr(jobs, 'status', lambda: {'ready': True, 'busy': False})
    jobs.reserve('a' * 32)
    with pytest.raises(service.BusyError):
        jobs.start({'symbol': '515880.SH'}, None, None)
    jobs.release('b' * 32)
    with pytest.raises(service.BusyError):
        jobs.reserve('c' * 32)
    jobs.release('a' * 32)
    jobs.reserve('c' * 32)


def test_reservation_is_atomic(tmp_path):
    jobs = service.JobManager(tmp_path)
    barrier = threading.Barrier(4)
    winners = []
    def reserve(index):
        barrier.wait()
        try:
            jobs.reserve(str(index) * 32)
            winners.append(index)
        except service.BusyError:
            pass
    threads = [threading.Thread(target=reserve, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(winners) == 1


def snapshot():
    history = [{'time': f'2026-10-26T09:{minute:02}', 'open': 1., 'high': 1.1,
                'low': .9, 'close': 1., 'volume': 10., 'amount': 10.}
               for minute in range(31, 35)]
    return {'symbol': '515880.SH', 'name': '通信ETF', 'asset_type': 'etf', 'period': '1m',
            'model': 'small', 'history': history, 'future_times': ['2026-10-26T09:35'],
            'samples': 1, 'input_as_of': history[-1]['time'],
            'requested_at': '2026-10-26T09:34:01+08:00', 'price_basis': '原始价格（未复权）', 'warnings': []}


def test_frozen_preparation_never_refetches(tmp_path, monkeypatch):
    jobs = service.JobManager(tmp_path)
    (tmp_path / 'results').mkdir()
    original = snapshot()
    monkeypatch.setattr(service, 'load_input', lambda *_: pytest.fail('frozen snapshot must not refetch'))
    monkeypatch.setattr(service.subprocess, 'Popen', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('stop before CUDA')))
    job = {'id': 'a' * 32, 'request': {}, 'state': 'preparing'}
    done = threading.Event()
    jobs._run(job, None, None, datetime.now(CN_TZ), threading.Event(), copy.deepcopy(original), done)
    assert job['state'] == 'failed'
    assert done.is_set()
    assert original == snapshot()
