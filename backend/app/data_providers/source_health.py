"""In-memory health and cooldown tracking for data source attempts."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from threading import Lock
from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")
_HARD_MAX_ENTRIES = 128


@dataclass(frozen=True)
class Attempt(Generic[T]):
    """The public result of a source attempt, without retained exception text."""

    value: T | None
    success: bool
    skipped: bool
    error_type: str | None = None


@dataclass
class _HealthEntry:
    failures: int = 0
    generation: int = 0
    failure_floor_generation: int = 0
    cooldown_until: float = 0.0
    probing: bool = False


class SourceHealth:
    """Bounded, thread-safe cooldown tracking keyed by source identity.

    Healthy keys allow concurrent attempts. After a failure, attempts are
    suppressed until the cooldown expires, when exactly one recovery probe is
    admitted. Fetching and validation always happen without the internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        base_cooldown: float = 30,
        max_cooldown: float = 300,
        max_entries: int = _HARD_MAX_ENTRIES,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isfinite(base_cooldown) or base_cooldown < 0:
            raise ValueError("base_cooldown must be a finite non-negative number")
        if not isfinite(max_cooldown) or max_cooldown < 0:
            raise ValueError("max_cooldown must be a finite non-negative number")
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")

        self._clock = clock
        self._base_cooldown = float(base_cooldown)
        self._max_cooldown = float(max_cooldown)
        self._max_entries = min(max_entries, _HARD_MAX_ENTRIES)
        self._lock = Lock()
        self._entries: OrderedDict[tuple, _HealthEntry] = OrderedDict()

    def attempt(
        self,
        key: tuple,
        fetch: Callable[[], T],
        accept: Callable[[T], bool],
    ) -> Attempt[T]:
        """Fetch and validate once, or report that cooldown/probing skipped it."""
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is None:
                if not self._make_room_locked():
                    return Attempt(value=None, success=False, skipped=True)
                entry = _HealthEntry()
                self._entries[key] = entry
            else:
                self._entries.move_to_end(key)

            if entry.probing or (entry.failures and entry.cooldown_until > now):
                return Attempt(value=None, success=False, skipped=True)

            is_probe = entry.failures > 0
            if is_probe:
                entry.probing = True
            generation = entry.generation

        try:
            value = fetch()
            if not accept(value):
                self._record_failure(key, entry, generation, is_probe)
                return Attempt(value=None, success=False, skipped=False, error_type="InvalidResponse")
        except Exception as error:
            error_type = type(error).__name__
            self._record_failure(key, entry, generation, is_probe)
            return Attempt(value=None, success=False, skipped=False, error_type=error_type)

        self._record_success(key, entry, generation, is_probe)
        return Attempt(value=value, success=True, skipped=False)

    def snapshot(self) -> list[dict[str, object]]:
        """Return health metadata only; never include fetched values or errors."""
        with self._lock:
            now = self._clock()
            return [
                {
                    "key": list(key),
                    "failures": entry.failures,
                    "retry_after_seconds": max(0.0, entry.cooldown_until - now),
                    "probing": entry.probing,
                }
                for key, entry in self._entries.items()
            ]

    def _make_room_locked(self) -> bool:
        if len(self._entries) < self._max_entries:
            return True
        victim = next((key for key, entry in self._entries.items() if not entry.probing), None)
        if victim is None:
            return False
        del self._entries[victim]
        return True

    def _record_failure(
        self,
        key: tuple,
        entry: _HealthEntry,
        generation: int,
        is_probe: bool,
    ) -> None:
        with self._lock:
            if self._entries.get(key) is not entry:
                return

            # A failure that began before recovery must not poison the new epoch.
            if generation < entry.failure_floor_generation:
                if is_probe:
                    entry.probing = False
                return

            entry.failures += 1
            entry.generation += 1
            entry.cooldown_until = self._clock() + self._cooldown_for(entry.failures)
            if is_probe:
                entry.probing = False

    def _record_success(
        self,
        key: tuple,
        entry: _HealthEntry,
        generation: int,
        is_probe: bool,
    ) -> None:
        with self._lock:
            if self._entries.get(key) is not entry:
                return

            if generation == entry.generation and entry.failures:
                entry.failures = 0
                entry.cooldown_until = 0.0
                entry.generation += 1
                entry.failure_floor_generation = entry.generation
                entry.probing = False
            elif is_probe and entry.probing:
                # A concurrent older failure may have advanced the generation.
                # Release this probe without clearing that newer failure.
                entry.probing = False

    def _cooldown_for(self, failures: int) -> float:
        delay = min(self._base_cooldown, self._max_cooldown)
        if delay == 0 or delay == self._max_cooldown:
            return delay
        remaining_doublings = failures - 1
        while remaining_doublings and delay < self._max_cooldown:
            delay = min(delay * 2, self._max_cooldown)
            remaining_doublings -= 1
        return delay
