from __future__ import annotations

from datetime import date

import polars as pl

from app.tickflow.repository import KlineRepository


def _repo_with_cache(frame: pl.DataFrame) -> KlineRepository:
    repo = object.__new__(KlineRepository)
    repo._enriched_cache = frame
    repo._enriched_cache_date = date(1999, 1, 1)

    def unexpected_refresh():
        raise AssertionError("peek_enriched_latest must never refresh the cache")

    repo._refresh_enriched = unexpected_refresh
    return repo


def test_cold_snapshot_peek_returns_empty_without_refreshing():
    repo = object.__new__(KlineRepository)
    repo._enriched_cache = None

    def unexpected_refresh():
        raise AssertionError("cold peek must not trigger _refresh_enriched")

    repo._refresh_enriched = unexpected_refresh

    frame, as_of = repo.peek_enriched_latest()

    assert frame.is_empty()
    assert as_of is None


def test_snapshot_peek_derives_date_from_the_returned_frame():
    expected_latest = date(2026, 10, 17)
    frame = pl.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "date": [date(2026, 10, 16), expected_latest],
            "close": [10.0, 20.0],
        }
    )
    repo = _repo_with_cache(frame)

    snapshot, as_of = repo.peek_enriched_latest()

    assert as_of == expected_latest
    assert as_of != repo._enriched_cache_date
    assert snapshot.equals(frame)


def test_snapshot_peek_returns_a_frame_that_can_be_modified_without_changing_cache():
    frame = pl.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "date": [date(2026, 10, 17), date(2026, 10, 17)],
            "close": [10.0, 20.0],
        }
    )
    repo = _repo_with_cache(frame)

    snapshot, _ = repo.peek_enriched_latest()
    snapshot.replace_column(snapshot.get_column_index("close"), pl.Series("close", [999.0, 888.0]))

    assert snapshot["close"].to_list() == [999.0, 888.0]
    assert repo._enriched_cache["close"].to_list() == [10.0, 20.0]
