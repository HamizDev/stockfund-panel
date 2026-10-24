"""Bounded, expiring in-memory cache for on-demand single-symbol minute fetches."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import polars as pl


@dataclass(frozen=True, slots=True)
class MinuteFetchKey:
    """Every input that can change a single-symbol minute fetch result."""

    data_dir: str | Path
    provider: str
    asset_type: str
    symbol: str
    trade_date: date
    frequency: str
    raw_basis: bool
    adjustment_context: tuple[object, ...] = ()
    local_partition_context: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", str(Path(self.data_dir).resolve()))
        object.__setattr__(self, "provider", str(self.provider).strip().lower())
        object.__setattr__(self, "asset_type", str(self.asset_type).strip().lower())
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        object.__setattr__(self, "frequency", str(self.frequency).strip().lower())
        object.__setattr__(self, "adjustment_context", _freeze(self.adjustment_context))
        object.__setattr__(self, "local_partition_context", _freeze(self.local_partition_context))


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    return value


def adjustment_context(
    data_dir: str | Path,
    asset_type: str,
    *,
    raw_basis: bool,
) -> tuple[object, ...]:
    """Fingerprint local adjustment inputs when a fetch may already be adjusted.

    Raw fetches are projected through the current local factors after this cache
    returns, so those factors do not belong to the cached value's context.
    """
    if raw_basis:
        return ("raw-fetch",)

    directory = Path(data_dir) / ("adj_factor_etf" if asset_type == "etf" else "adj_factor")
    try:
        files = tuple(
            sorted(
                (path.name, path.stat().st_mtime_ns, path.stat().st_size)
                for path in directory.glob("*.parquet")
            )
        )
    except OSError:
        files = (("unavailable",),)
    return ("forward-adjustment", str(directory.resolve()), files)


def local_partition_context(frame: pl.DataFrame) -> tuple[object, ...]:
    """Fingerprint the local-first read so newly written partitions miss cache."""
    schema = tuple((name, str(dtype)) for name, dtype in frame.schema.items())
    if frame.is_empty():
        return ("empty", schema)
    hashes = tuple(
        frame.hash_rows(seed=0, seed_1=1, seed_2=2, seed_3=3).to_list()
    )
    return (frame.height, schema, hashes)


def is_closed_minute_date(trade_date: date, today: date, now: datetime) -> bool:
    """Only historical dates and today's post-close data may be cached."""
    if trade_date < today:
        return True
    if trade_date > today:
        return False
    return (now.hour, now.minute) >= (15, 30)


@dataclass(slots=True)
class _CacheEntry:
    frame: pl.DataFrame
    expires_at: float
    rows: int


@dataclass(slots=True)
class _Flight:
    event: threading.Event = field(default_factory=threading.Event)
    frame: pl.DataFrame | None = None
    error: BaseException | None = None


class MinuteResponseCache:
    """Bounded LRU row cache with bounded single-flight waits."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 20.0,
        max_entries: int = 128,
        max_rows: int = 32_768,
        max_rows_per_entry: int = 1_000,
        max_inflight: int = 64,
        wait_timeout_seconds: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
        auto_expire: bool = False,
    ) -> None:
        self._ttl_seconds = max(0.01, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._max_rows = max(1, int(max_rows))
        self._max_rows_per_entry = max(1, int(max_rows_per_entry))
        self._max_inflight = max(1, int(max_inflight))
        self._wait_timeout_seconds = max(0.0, float(wait_timeout_seconds))
        self._clock = clock
        self._auto_expire = auto_expire
        self._expiry_timer: threading.Timer | None = None
        self._timer_generation = 0
        self._entries: OrderedDict[MinuteFetchKey, _CacheEntry] = OrderedDict()
        self._pending: dict[MinuteFetchKey, _Flight] = {}
        self._cached_rows = 0
        self._lock = threading.Lock()

    def get_or_fetch(
        self,
        key: MinuteFetchKey,
        loader: Callable[[], pl.DataFrame],
        *,
        cache_if: Callable[[pl.DataFrame], bool] | None = None,
    ) -> pl.DataFrame:
        """Return a cached clone or run one upstream fetch for concurrent readers.

        Waiters stop waiting after the configured timeout and perform their own
        uncached fetch. A slow upstream call therefore cannot hold an API request
        indefinitely, and timed-out work never creates another cache writer.
        """
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry.frame.clone()

            flight = self._pending.get(key)
            if flight is None:
                if len(self._pending) >= self._max_inflight:
                    bypass = True
                else:
                    flight = _Flight()
                    self._pending[key] = flight
                    bypass = False
                owner = not bypass
            else:
                owner = False
                bypass = False

        if bypass:
            return loader()

        if not owner:
            if not flight.event.wait(self._wait_timeout_seconds):
                return loader()
            if flight.error is not None:
                raise flight.error
            return flight.frame.clone() if flight.frame is not None else pl.DataFrame()

        try:
            frame = loader()
        except BaseException as error:
            with self._lock:
                flight.error = error
                if self._pending.get(key) is flight:
                    self._pending.pop(key, None)
                flight.event.set()
            raise

        stored = False
        if self._is_valid_frame(key, frame):
            try:
                stored = cache_if is None or bool(cache_if(frame))
            except Exception:
                stored = False

        with self._lock:
            flight.frame = frame.clone() if isinstance(frame, pl.DataFrame) else None
            if stored:
                cached = frame.clone()
                self._insert(key, cached, self._clock() + self._ttl_seconds)
            if self._pending.get(key) is flight:
                self._pending.pop(key, None)
            flight.event.set()
        return frame.clone() if isinstance(frame, pl.DataFrame) else frame

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._cached_rows = 0
            self._timer_generation += 1
            if self._expiry_timer is not None:
                self._expiry_timer.cancel()
                self._expiry_timer = None

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def cached_rows(self) -> int:
        with self._lock:
            return self._cached_rows

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _is_valid_frame(self, key: MinuteFetchKey, frame: object) -> bool:
        if not isinstance(frame, pl.DataFrame) or frame.is_empty():
            return False
        if frame.height > min(self._max_rows_per_entry, self._max_rows):
            return False
        if not {"symbol", "datetime", "close"}.issubset(frame.columns):
            return False
        if not isinstance(frame.schema.get("datetime"), pl.Datetime):
            return False
        if frame["symbol"].null_count() or frame["symbol"].unique().to_list() != [key.symbol]:
            return False
        if frame["datetime"].null_count():
            return False
        try:
            if frame.select(pl.col("datetime").dt.date().n_unique()).item() != 1:
                return False
            if frame.select(pl.col("datetime").dt.date().first()).item() != key.trade_date:
                return False
            close = frame.select(pl.col("close").cast(pl.Float64, strict=False))
            if close["close"].null_count():
                return False
            if not close.select((pl.col("close").is_finite() & (pl.col("close") > 0)).all()).item():
                return False
        except Exception:
            return False
        return True

    def _remove_expired(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._cached_rows -= self._entries.pop(key).rows

    def _insert(self, key: MinuteFetchKey, frame: pl.DataFrame, expires_at: float) -> None:
        old = self._entries.pop(key, None)
        if old is not None:
            self._cached_rows -= old.rows
        if frame.height > self._max_rows:
            return
        self._entries[key] = _CacheEntry(frame, expires_at, frame.height)
        self._cached_rows += frame.height
        while self._entries and (
            len(self._entries) > self._max_entries or self._cached_rows > self._max_rows
        ):
            _old_key, evicted = self._entries.popitem(last=False)
            self._cached_rows -= evicted.rows
        self._schedule_expiry()

    def _schedule_expiry(self) -> None:
        # One daemon timer per cache releases idle frames without keeping a
        # market poller alive. It never starts a network request or writes data.
        if not self._auto_expire or self._expiry_timer is not None or not self._entries:
            return
        delay = max(0.01, min(entry.expires_at for entry in self._entries.values()) - self._clock())
        timer = threading.Timer(delay, self._expire_idle, args=(self._timer_generation,))
        timer.daemon = True
        self._expiry_timer = timer
        timer.start()

    def _expire_idle(self, generation: int) -> None:
        with self._lock:
            if generation != self._timer_generation:
                return
            self._expiry_timer = None
            self._remove_expired(self._clock())
            self._schedule_expiry()


minute_response_cache = MinuteResponseCache(auto_expire=True)
# View queries may reuse a just-fetched live response, but must not freeze it
# until the next minute. Strategies retain their separate observation pipeline.
live_minute_response_cache = MinuteResponseCache(
    ttl_seconds=5, max_entries=16, max_rows=4096, max_inflight=8, auto_expire=True,
)
