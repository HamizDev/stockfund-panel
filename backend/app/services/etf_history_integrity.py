"""Bounded, read-only integrity checks for ETF daily history.

This module deliberately does not read storage, call a quote provider, repair data,
or infer exchange sessions. Callers supply the small symbol/date snapshots and
month-complete official trading calendars they already obtained.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

MAX_SYMBOLS = 8
MAX_WINDOW_DAYS = 550
MAX_SOURCE_DATES = MAX_WINDOW_DAYS
_SYMBOL_RE = re.compile(r"^\d{6}\.(?:SH|SZ)$")


@dataclass(frozen=True)
class GapRange:
    """A repair-sized range that still needs suspension/source verification."""

    source: str  # "raw" or "enriched"
    start_date: date
    end_date: date
    missing_dates: tuple[date, ...]
    status: str = "待核验缺口"


@dataclass(frozen=True)
class DatasetIntegrity:
    """Observed coverage and candidate gaps for one stored representation."""

    source: str
    first_observed_date: date | None
    last_observed_date: date | None
    observed_date_count: int
    gaps: tuple[GapRange, ...]


@dataclass(frozen=True)
class SymbolIntegrity:
    """Read-only internal coverage summary for one ETF."""

    symbol: str
    window_start: date
    window_end: date
    listing_date: date | None
    raw: DatasetIntegrity
    enriched: DatasetIntegrity
    calendar_complete: bool
    unknown_calendar_months: tuple[str, ...]


def _as_date(value: date | str, *, field: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{field} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid ISO date in {field}: {value!r}") from exc
        if parsed.isoformat() != value:
            raise ValueError(f"invalid ISO date in {field}: {value!r}")
        return parsed
    raise ValueError(f"{field} values must be date or ISO date strings")


def _month_keys(start: date, end: date) -> tuple[tuple[int, int], ...]:
    keys: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return tuple(keys)


def _normalize_calendar(
    calendar_by_month: Mapping[tuple[int, int], Iterable[date | str] | None],
    start: date,
    end: date,
) -> tuple[dict[tuple[int, int], tuple[date, ...]], tuple[str, ...]]:
    """Normalize complete official months; missing/None months remain unknown."""
    open_by_month: dict[tuple[int, int], tuple[date, ...]] = {}
    unknown: list[str] = []
    for key in _month_keys(start, end):
        label = f"{key[0]:04d}-{key[1]:02d}"
        days = calendar_by_month.get(key)
        if days is None:
            unknown.append(label)
            continue
        normalized: set[date] = set()
        for count, item in enumerate(days, start=1):
            if count > 31:
                raise ValueError(f"calendar[{label}] contains too many dates")
            day = _as_date(item, field=f"calendar[{label}]")
            if (day.year, day.month) != key:
                raise ValueError(f"calendar[{label}] contains a date from another month")
            normalized.add(day)
        if not normalized:
            raise ValueError(f"calendar[{label}] has no confirmed open sessions")
        open_by_month[key] = tuple(sorted(normalized))
    return open_by_month, tuple(unknown)


def _normalize_observed(
    values: Iterable[date | str], *, field: str, start: date, end: date
) -> tuple[date, ...]:
    normalized: set[date] = set()
    for received, item in enumerate(values, 1):
        if received > MAX_SOURCE_DATES:
            raise ValueError(f"{field} exceeds the bounded date input")
        day = _as_date(item, field=field)
        if start <= day <= end:
            normalized.add(day)
    if len(normalized) > MAX_SOURCE_DATES:
        raise ValueError(f"{field} exceeds the bounded date input")
    return tuple(sorted(normalized))


def _dataset_gaps(
    source: str,
    observed: tuple[date, ...],
    listing_date: date | None,
    open_by_month: Mapping[tuple[int, int], tuple[date, ...]],
) -> DatasetIntegrity:
    if not observed:
        return DatasetIntegrity(source, None, None, 0, ())

    first, last = observed[0], observed[-1]
    effective_start = max(first, listing_date) if listing_date is not None else first
    if effective_start > last:
        return DatasetIntegrity(source, first, last, len(observed), ())

    observed_set = set(observed)
    gaps: list[GapRange] = []
    # Keep each calendar month as its own repair range. This prevents an unknown
    # calendar month from being silently bridged by a range across it.
    for open_days in open_by_month.values():
        expected = [day for day in open_days if effective_start <= day <= last]
        missing = [day for day in expected if day not in observed_set]
        if not missing:
            continue
        run: list[date] = []
        expected_index = {day: index for index, day in enumerate(expected)}
        for day in missing:
            if run and expected_index[day] != expected_index[run[-1]] + 1:
                gaps.append(GapRange(source, run[0], run[-1], tuple(run)))
                run = []
            run.append(day)
        if run:
            gaps.append(GapRange(source, run[0], run[-1], tuple(run)))

    return DatasetIntegrity(source, first, last, len(observed), tuple(gaps))


def detect_etf_history_gaps(
    *,
    symbols: Sequence[str],
    raw_dates_by_symbol: Mapping[str, Iterable[date | str]],
    enriched_dates_by_symbol: Mapping[str, Iterable[date | str]],
    official_open_days_by_month: Mapping[
        tuple[int, int], Iterable[date | str] | None
    ],
    window_start: date | str,
    window_end: date | str,
    listing_dates: Mapping[str, date | str | None] | None = None,
) -> tuple[SymbolIntegrity, ...]:
    """Report bounded internal ETF daily gaps using caller-confirmed sessions.

    `official_open_days_by_month` must contain a complete, validated official
    session list for each month in the requested window. Use `None` (or omit the
    key) for an unavailable month; dates from that month are not guessed. Only
    missing sessions between each source's first and last observed dates are
    candidates. Every candidate remains `待核验缺口`, since this function cannot
    establish whether the ETF was suspended or its source omitted that session.

    The function is pure and makes no storage, network, or repair calls. One call
    is limited to eight SH/SZ ETFs and a 550-calendar-day window.
    """
    start = _as_date(window_start, field="window_start")
    end = _as_date(window_end, field="window_end")
    if end < start:
        raise ValueError("window_end must be on or after window_start")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError(f"window exceeds {MAX_WINDOW_DAYS} calendar days")
    if not symbols or len(symbols) > MAX_SYMBOLS:
        raise ValueError(f"symbols must contain 1..{MAX_SYMBOLS} ETFs")
    if len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be unique")
    for symbol in symbols:
        if not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError(f"invalid ETF symbol: {symbol!r}")

    selected = set(symbols)
    if (
        len(raw_dates_by_symbol) > MAX_SYMBOLS
        or len(enriched_dates_by_symbol) > MAX_SYMBOLS
        or (listing_dates is not None and len(listing_dates) > MAX_SYMBOLS)
    ):
        raise ValueError("date inputs exceed the selected-symbol scope")
    if set(raw_dates_by_symbol) - selected or set(enriched_dates_by_symbol) - selected:
        raise ValueError("date inputs contain symbols outside the requested scope")
    if listing_dates is not None and set(listing_dates) - selected:
        raise ValueError("listing-date inputs contain symbols outside the requested scope")

    open_by_month, unknown_months = _normalize_calendar(
        official_open_days_by_month, start, end
    )
    output: list[SymbolIntegrity] = []
    for symbol in symbols:
        listing_value = (listing_dates or {}).get(symbol)
        listing_date = (
            _as_date(listing_value, field=f"listing_dates[{symbol}]")
            if listing_value is not None
            else None
        )
        raw = _normalize_observed(
            raw_dates_by_symbol.get(symbol, ()),
            field=f"raw_dates_by_symbol[{symbol}]",
            start=start,
            end=end,
        )
        enriched = _normalize_observed(
            enriched_dates_by_symbol.get(symbol, ()),
            field=f"enriched_dates_by_symbol[{symbol}]",
            start=start,
            end=end,
        )
        output.append(
            SymbolIntegrity(
                symbol=symbol,
                window_start=start,
                window_end=end,
                listing_date=listing_date,
                raw=_dataset_gaps("raw", raw, listing_date, open_by_month),
                enriched=_dataset_gaps("enriched", enriched, listing_date, open_by_month),
                calendar_complete=not unknown_months,
                unknown_calendar_months=unknown_months,
            )
        )
    return tuple(output)
