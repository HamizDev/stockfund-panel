from datetime import UTC, date, datetime, timedelta, timezone

import polars as pl
import pytest

from app.services.analysis_quality import published_rows

BEIJING = timezone(timedelta(hours=8))
CUTOFF = date(2026, 4, 30)


def _frame(publications: list[object], *, periods: list[str] | None = None) -> pl.DataFrame:
    return pl.DataFrame(
        {
            'row_id': list(range(len(publications))),
            'period_end': periods or ['2025-12-31'] * len(publications),
            'published_at': publications,
        }
    )


def _ids(frame: pl.DataFrame) -> list[int]:
    return frame['row_id'].to_list()


def test_same_day_timestamp_uses_exact_beijing_cutoff_and_date_only_is_unknown() -> None:
    frame = _frame([
        '2026-04-30T14:59:59',
        '2026-04-30T15:00:00',
        '2026-04-30T15:00:00.000001',
        '2026-04-30',
    ])

    result = published_rows(
        frame,
        CUTOFF,
        cutoff_at=datetime(2026, 4, 30, 15, 0, tzinfo=BEIJING),
    )

    assert _ids(result) == [0, 1]


def test_date_only_publication_must_precede_cutoff_day() -> None:
    frame = _frame(['2026-04-29', '2026-04-30', '2026-05-01', 'not-a-date'])

    result = published_rows(
        frame,
        CUTOFF,
        cutoff_at=datetime(2026, 4, 30, 9, 30, tzinfo=BEIJING),
    )

    assert _ids(result) == [0]


def test_aware_announcement_timestamps_are_converted_to_beijing() -> None:
    frame = _frame([
        '2026-04-30T06:59:59Z',
        '2026-04-30T07:00:00+00:00',
        '2026-04-30T07:00:01Z',
        '2026-04-29T16:00:00Z',
    ])

    result = published_rows(
        frame,
        CUTOFF,
        cutoff_at=datetime(2026, 4, 30, 7, 0, tzinfo=UTC),
    )

    assert _ids(result) == [0, 1, 3]


def test_period_end_still_must_not_exceed_cutoff() -> None:
    frame = _frame(
        ['2026-04-29', '2026-04-29'],
        periods=['2026-04-30', '2026-05-01'],
    )

    result = published_rows(
        frame,
        CUTOFF,
        cutoff_at=datetime(2026, 4, 30, 12, 0, tzinfo=BEIJING),
    )

    assert _ids(result) == [0]


def test_missing_announcement_column_returns_empty_frame() -> None:
    frame = pl.DataFrame({'period_end': ['2025-12-31']})

    result = published_rows(
        frame,
        CUTOFF,
        cutoff_at=datetime(2026, 4, 30, 12, 0, tzinfo=BEIJING),
    )

    assert result.is_empty()
    assert result.columns == frame.columns


def test_call_without_cutoff_at_preserves_date_only_legacy_behavior() -> None:
    frame = _frame([
        '2026-04-30',
        '2026-04-30T23:59:59+08:00',
        '2026-05-01',
    ])

    assert _ids(published_rows(frame, CUTOFF)) == [0, 1]


def test_null_and_invalid_publication_or_period_dates_are_excluded() -> None:
    frame = _frame(
        ['2026-04-29', None, 'not-a-date', '2026-04-29Tnot-a-time'],
        periods=['2025-12-31', '2025-12-31', '2025-12-31', 'bad-period'],
    )

    result = published_rows(
        frame,
        CUTOFF,
        cutoff_at=datetime(2026, 4, 30, 12, 0, tzinfo=BEIJING),
    )

    assert _ids(result) == [0]


def test_cutoff_at_must_be_aware_and_match_beijing_cutoff_date() -> None:
    frame = _frame(['2026-04-29'])

    with pytest.raises(ValueError, match='timezone-aware'):
        published_rows(frame, CUTOFF, cutoff_at=datetime(2026, 4, 30, 12, 0))

    with pytest.raises(ValueError, match='Beijing date'):
        published_rows(
            frame,
            CUTOFF,
            cutoff_at=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
        )
