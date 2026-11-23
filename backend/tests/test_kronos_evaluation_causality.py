from datetime import datetime

import pytest

from app.custom.kronos.evaluation import payload, score
from app.market_time import CN_TZ


def make_job(period, input_as_of, requested_at, forecasts):
    return {
        'id': 'a' * 32,
        'state': 'complete',
        'requested_at': requested_at,
        'request': {'symbol': '510300.SH', 'period': period},
        'result': {
            'symbol': '510300.SH',
            'asset_type': 'etf',
            'period': period,
            'price_basis': '原始价格（未复权）',  # noqa: RUF001 -- Exact persisted contract.
            'history': [{'time': input_as_of, 'close': 100.0}],
            'input_as_of': input_as_of,
            'requested_at': requested_at,
            'completed_at': requested_at,
            'forecast': [{'time': stamp, 'close': close} for stamp, close in forecasts],
        },
    }


def test_payload_rejects_minute_input_after_request_minute():
    job = make_job(
        '1m', '2026-11-06T14:53', '2026-11-06T14:52:59+08:00',
        [('2026-11-06T14:54', 101.0)],
    )

    with pytest.raises(ValueError, match='输入时点晚于请求时点'):
        payload(job)


def test_payload_rejects_daily_input_after_request_date():
    job = make_job('daily', '2026-11-07', '2026-11-06T16:00:00+08:00', [('2026-11-09', 101.0)])

    with pytest.raises(ValueError, match='输入日期晚于请求日期'):
        payload(job)


def test_payload_rejects_same_day_daily_input_before_close():
    job = make_job('daily', '2026-11-06', '2026-11-06T15:29:59+08:00', [('2026-11-09', 101.0)])

    with pytest.raises(ValueError, match='尚未收盘'):
        payload(job)


@pytest.mark.parametrize('requested_at', ['2026-11-06T15:30:00+08:00', '2026-11-07T10:00:00+08:00'])
def test_payload_keeps_closed_daily_and_older_historical_inputs(requested_at):
    job = make_job('daily', '2026-11-06', requested_at, [('2026-11-09', 101.0)])

    result, reference = payload(job)

    assert result['input_as_of'] == '2026-11-06'
    assert reference == 100.0


def test_payload_keeps_older_minute_context_for_a_later_request():
    job = make_job(
        '1m', '2026-11-05T14:59', '2026-11-06T14:00:00+08:00',
        [('2026-11-06T14:01', 101.0)],
    )

    result, reference = payload(job)

    assert result['input_as_of'] == '2026-11-05T14:59'
    assert reference == 100.0


def test_score_reports_direction_baseline_ties_and_coverage():
    stamps = [f'2026-11-06T14:{minute:02d}' for minute in range(54, 60)]
    job = make_job(
        '1m', '2026-11-06T14:53', '2026-11-06T14:53:30+08:00',
        list(zip(stamps, [101.0, 99.0, 101.0, 99.0, 100.0, 100.0], strict=True)),
    )
    actuals = dict(zip(stamps, [102.0, 98.0, 99.0, 101.0, 100.0, 101.0], strict=True))

    result = score(job, actuals, datetime(2026, 11, 7, 9, 0, tzinfo=CN_TZ))

    metrics = result['metrics']
    assert result['state'] == 'complete'
    assert (result['matched'], result['expected'], result['pending'], result['missing']) == (6, 6, 0, 0)
    assert metrics['direction_matches'] == 3  # correct up, correct down, and flat/flat
    assert metrics['direction_total'] == 6
    assert metrics['direction_accuracy'] == 0.5
    assert metrics['flat_actual_count'] == 1
    assert metrics['baseline_direction_accuracy'] == pytest.approx(1 / 6)
    assert metrics['coverage'] == 1.0
    assert [point['direction_match'] for point in result['points']] == [True, True, False, False, True, False]


def test_score_excludes_pending_and_unavailable_targets_from_direction_denominator():
    stamps = ['2026-11-06T14:53', '2026-11-06T14:54', '2026-11-06T14:55']
    job = make_job(
        '1m', '2026-11-06T14:52', '2026-11-06T14:52:30+08:00',
        [(stamps[0], 101.0), (stamps[1], 99.0), (stamps[2], 98.0)],
    )
    # First point is matched, second is mature but has no quote, and the third is not mature.
    now = datetime(2026, 11, 6, 14, 55, 30, tzinfo=CN_TZ)
    result = score(job, {stamps[0]: 102.0, stamps[2]: 99.0}, now)

    metrics = result['metrics']
    assert result['state'] == 'partial'
    assert (result['matched'], result['expected'], result['pending'], result['missing']) == (1, 3, 1, 1)
    assert [point['status'] for point in result['points']] == ['matched', 'unavailable', 'pending']
    assert metrics['direction_total'] == 1
    assert metrics['direction_matches'] == 1
    assert metrics['direction_accuracy'] == 1.0
    assert metrics['baseline_direction_accuracy'] == 0.0
    assert metrics['coverage'] == pytest.approx(1 / 3)
