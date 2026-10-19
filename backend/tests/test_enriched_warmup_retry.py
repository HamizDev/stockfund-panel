"""Bounded retry behavior for generation-discarded background enriched warmups."""
from __future__ import annotations

from app.enriched_generation import EnrichedGenerationUnavailableError
from app.tickflow.repository import (
    _ENRICHED_WARMUP_MAX_ATTEMPTS,
    _ENRICHED_WARMUP_PUBLISH_WAIT_SECONDS,
    DataStore,
    KlineRepository,
)


def _repo(tmp_path) -> KlineRepository:
    return KlineRepository(DataStore(tmp_path))


def test_warmup_retries_after_generation_discard(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    calls = 0

    def refresh_once() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return True
        repo._enriched_history_cache = object()
        return False

    monkeypatch.setattr(repo, "_refresh_enriched", refresh_once)
    monkeypatch.setattr(
        "app.tickflow.repository.enriched_publication_incomplete",
        lambda *_args: False,
    )

    repo._warmup_enriched()

    assert calls == 2
    assert repo._enriched_history_cache is not None


def test_warmup_does_not_retry_success_or_nonretryable_failure(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    calls = 0

    def refresh_once() -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(repo, "_refresh_enriched", refresh_once)

    repo._warmup_enriched()

    assert calls == 1


def test_warmup_waits_only_between_bounded_retries(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    calls = 0
    publication_checks = 0
    sleeps: list[float] = []

    def refresh_once() -> bool:
        nonlocal calls
        calls += 1
        return True

    def publication_incomplete(*_args) -> bool:
        nonlocal publication_checks
        publication_checks += 1
        return True

    monkeypatch.setattr(repo, "_refresh_enriched", refresh_once)
    monkeypatch.setattr(
        "app.tickflow.repository.enriched_publication_incomplete",
        publication_incomplete,
    )
    monkeypatch.setattr("app.tickflow.repository.time.sleep", sleeps.append)

    repo._warmup_enriched()

    assert calls == _ENRICHED_WARMUP_MAX_ATTEMPTS
    assert publication_checks == _ENRICHED_WARMUP_MAX_ATTEMPTS - 1
    assert sleeps == [
        _ENRICHED_WARMUP_PUBLISH_WAIT_SECONDS
    ] * (_ENRICHED_WARMUP_MAX_ATTEMPTS - 1)
    assert sum(sleeps) <= 10.0
    assert repo._enriched_history_cache is None


def test_refresh_enriched_reports_generation_race_without_retry(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    calls = 0

    def discarded(*_args, **_kwargs) -> None:
        nonlocal calls
        calls += 1
        raise EnrichedGenerationUnavailableError("generation changed")

    monkeypatch.setattr(repo, "_refresh_enriched_impl", discarded)

    assert repo._refresh_enriched() is True
    assert calls == 1


def test_refresh_enriched_reports_other_failure_as_nonretryable(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    calls = 0

    def failed(*_args, **_kwargs) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("read failed")

    monkeypatch.setattr(repo, "_refresh_enriched_impl", failed)

    assert repo._refresh_enriched() is False
    assert calls == 1
