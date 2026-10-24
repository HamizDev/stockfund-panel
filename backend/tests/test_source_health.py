from __future__ import annotations

from threading import Event, Thread

from app.data_providers.source_health import SourceHealth


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _raise(message: str = "source failed"):
    raise RuntimeError(message)


def test_success_and_exponential_cooldown_are_reset_after_recovery():
    clock = FakeClock()
    health = SourceHealth(clock=clock)
    key = ("provider", "daily")

    success = health.attempt(key, lambda: {"rows": 3}, lambda value: value["rows"] == 3)
    assert success.success is True
    assert success.skipped is False
    assert success.value == {"rows": 3}
    assert success.error_type is None

    expected_delays = [30, 60, 120, 240, 300, 300]
    for delay in expected_delays:
        result = health.attempt(key, _raise, lambda _value: True)
        assert result.success is False
        assert result.skipped is False
        assert result.error_type == "RuntimeError"
        row = health.snapshot()[0]
        assert row["failures"] >= 1
        assert row["retry_after_seconds"] == delay
        clock.advance(delay)

    recovered = health.attempt(key, lambda: "fresh", lambda value: value == "fresh")
    assert recovered.success is True
    assert recovered.value == "fresh"
    row = health.snapshot()[0]
    assert row["failures"] == 0
    assert row["retry_after_seconds"] == 0
    assert row["probing"] is False

    health.attempt(key, _raise, lambda _value: True)
    assert health.snapshot()[0]["retry_after_seconds"] == 30


def test_invalid_response_counts_as_failure_without_returning_the_payload():
    clock = FakeClock()
    health = SourceHealth(clock=clock)

    result = health.attempt(
        ("provider", "realtime"),
        lambda: {"private": "payload"},
        lambda _value: False,
    )

    assert result.success is False
    assert result.skipped is False
    assert result.value is None
    assert result.error_type == "InvalidResponse"
    assert health.snapshot()[0]["retry_after_seconds"] == 30
    assert "payload" not in repr(health.snapshot())


def test_only_one_attempt_is_admitted_as_a_probe_after_cooldown():
    clock = FakeClock()
    health = SourceHealth(clock=clock, base_cooldown=1, max_cooldown=8)
    key = ("provider", "minute")
    health.attempt(key, _raise, lambda _value: True)
    clock.advance(1)

    started = Event()
    release = Event()
    results: list[object] = []

    def probe() -> None:
        def fetch() -> str:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("probe release timed out")
            return "healthy"

        results.append(health.attempt(key, fetch, lambda value: value == "healthy"))

    thread = Thread(target=probe)
    thread.start()
    try:
        assert started.wait(timeout=2)
        skipped = health.attempt(key, lambda: _raise("second fetch must be skipped"), lambda _value: True)
        assert skipped.skipped is True
        assert skipped.value is None
        row = health.snapshot()[0]
        assert row["probing"] is True
    finally:
        release.set()
        thread.join(timeout=3)

    assert not thread.is_alive()
    assert len(results) == 1
    assert results[0].success is True
    assert health.snapshot()[0]["failures"] == 0


def test_healthy_attempts_can_run_concurrently():
    health = SourceHealth(clock=FakeClock())
    release = Event()
    started = [Event(), Event()]
    results: list[object] = []

    def run(index: int) -> None:
        def fetch() -> str:
            started[index].set()
            if not release.wait(timeout=3):
                raise TimeoutError("fetch release timed out")
            return "ok"

        results.append(health.attempt(("healthy",), fetch, lambda value: value == "ok"))

    threads = [Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    try:
        assert all(event.wait(timeout=2) for event in started)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert all(result.success for result in results)
    assert health.snapshot()[0]["failures"] == 0


def test_old_concurrent_success_does_not_clear_a_new_failure_and_error_text_is_discarded():
    clock = FakeClock()
    health = SourceHealth(clock=clock)
    key = ("provider", "daily")
    started = Event()
    release = Event()
    results: list[object] = []

    def old_attempt() -> None:
        def fetch() -> str:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("sensitive://old-attempt?token=secret")
            return "old success"

        results.append(health.attempt(key, fetch, lambda _value: True))

    thread = Thread(target=old_attempt)
    thread.start()
    try:
        assert started.wait(timeout=2)
        failed = health.attempt(
            key,
            lambda: _raise("https://private.invalid/path?token=secret API_KEY=hidden"),
            lambda _value: True,
        )
        assert failed.error_type == "RuntimeError"
        assert "private.invalid" not in repr(failed)
        assert "secret" not in repr(failed)
    finally:
        release.set()
        thread.join(timeout=3)

    assert not thread.is_alive()
    assert len(results) == 1
    assert results[0].success is True
    row = health.snapshot()[0]
    assert row["failures"] == 1
    assert row["retry_after_seconds"] == 30
    assert "private.invalid" not in repr(health.snapshot())
    assert "secret" not in repr(health.snapshot())
    assert set(row) == {"key", "failures", "retry_after_seconds", "probing"}


def test_lru_is_bounded_and_never_evicts_probes_when_every_slot_is_probing():
    clock = FakeClock()
    health = SourceHealth(clock=clock, max_entries=2, base_cooldown=1, max_cooldown=4)
    key_a, key_b, key_c = ("a",), ("b",), ("c",)
    for key in (key_a, key_b):
        health.attempt(key, lambda: "ok", lambda value: value == "ok")

    health.attempt(key_a, lambda: "touch", lambda value: value == "touch")
    health.attempt(key_c, lambda: "new", lambda value: value == "new")
    assert {tuple(row["key"]) for row in health.snapshot()} == {key_a, key_c}

    probing = SourceHealth(clock=clock, max_entries=2, base_cooldown=1, max_cooldown=4)
    for key in (key_a, key_b):
        probing.attempt(key, _raise, lambda _value: True)
    clock.advance(1)

    release = Event()
    started = {key_a: Event(), key_b: Event()}
    results: list[object] = []

    def run_probe(key: tuple) -> None:
        def fetch() -> str:
            started[key].set()
            if not release.wait(timeout=3):
                raise TimeoutError("probe release timed out")
            return "ok"

        results.append(probing.attempt(key, fetch, lambda value: value == "ok"))

    threads = [Thread(target=run_probe, args=(key,)) for key in (key_a, key_b)]
    for thread in threads:
        thread.start()
    try:
        assert all(started[key].wait(timeout=2) for key in (key_a, key_b))
        skipped = probing.attempt(
            key_c,
            lambda: _raise("full probe table must skip new keys"),
            lambda _value: True,
        )
        assert skipped.skipped is True
        rows = probing.snapshot()
        assert len(rows) == 2
        assert all(row["probing"] is True for row in rows)
        assert {tuple(row["key"]) for row in rows} == {key_a, key_b}
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert all(result.success for result in results)


def test_entry_capacity_is_capped_at_128_even_if_configured_higher():
    health = SourceHealth(clock=FakeClock(), max_entries=1000)
    for index in range(129):
        health.attempt((str(index),), lambda: True, lambda value: value)
    assert len(health.snapshot()) == 128
