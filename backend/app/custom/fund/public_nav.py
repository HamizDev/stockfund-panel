"""Credential-free Eastmoney rank and unit-NAV fallbacks.

The upstream JavaScript is treated as data: only the assigned JSON array is
decoded, and no code from the response is evaluated. Returned NAV is unit NAV;
the public cumulative series is deliberately not presented as adjusted NAV.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import UTC, date, datetime, timedelta, timezone

from app.custom.fund.public_data import _get, fund_code, iso_date, number

_RANK_URL = "https://fund.eastmoney.com/data/rankhandler.aspx"
_NAV_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
_FUND_TYPES = {"股票型": "gp", "混合型": "hh", "债券型": "zq", "指数型": "zs"}
_GROWTH_COLUMNS = {
    "growth_1w": 7,
    "growth_1m": 8,
    "growth_3m": 9,
    "growth_6m": 10,
    "growth_1y": 11,
    "growth_2y": 12,
    "growth_3y": 13,
}
_MISSING_TEXT = {"", "nan", "nat", "none", "null", "--", "---", "—", "-"}
_SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
_DATAS_RE = re.compile(r"(?:\bdatas\b|[\"']datas[\"'])\s*:")
_NAV_RE = re.compile(r"\bvar\s+Data_netWorthTrend\s*=")
_RANK_CACHE_TTL_SECONDS = 6 * 60 * 60
_RANK_CACHE_LOCK = threading.Lock()
_RANK_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _one_year_ago(today: date) -> date:
    """Use the same calendar day last year, clamping leap day to Feb 28."""
    try:
        return today.replace(year=today.year - 1)
    except ValueError:
        return today.replace(year=today.year - 1, day=28)


def _rank_params(fund_type: str, today: date | None = None) -> dict[str, str]:
    if not isinstance(fund_type, str):
        raise ValueError("不支持的基金类型")
    try:
        fund_type_code = _FUND_TYPES[fund_type]
    except KeyError as exc:
        raise ValueError("不支持的基金类型") from exc
    end = today or datetime.now(_SHANGHAI).date()
    start = _one_year_ago(end)
    return {
        "op": "ph",
        "dt": "kf",
        "ft": fund_type_code,
        "rs": "",
        "gs": "0",
        "sc": "1nzf",
        "st": "desc",
        "sd": start.isoformat(),
        "ed": end.isoformat(),
        "qdii": "",
        "tabSubtype": ",,,,,",
        "pi": "1",
        "pn": "30000",
        "dx": "1",
    }


def _rank_cache_now() -> float:
    return time.monotonic()


def _copy_rank_rows(rows: list[dict]) -> list[dict]:
    # Rows contain scalar fields only; copy each dict so callers cannot mutate
    # the shared cached objects.
    return [dict(row) for row in rows]


def _decode_array_after(pattern: re.Pattern, text: str) -> list | None:
    if not isinstance(text, str):
        return None
    match = pattern.search(text)
    if not match:
        return None
    start = match.end()
    while start < len(text) and text[start].isspace():
        start += 1
    # Eastmoney's arrays use JSON-compatible literals. Accept JS non-finite
    # tokens only as null data so the shared number() helper can reject them.
    decoder = json.JSONDecoder(parse_constant=lambda _value: None)
    try:
        value, _end = decoder.raw_decode(text, start)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


def _rank_strings(text: str) -> list[str]:
    values = _decode_array_after(_DATAS_RE, text)
    if values is None or any(not isinstance(row, str) for row in values):
        return []
    return values


def _field(row: list[str], index: int) -> str | None:
    if index >= len(row):
        return None
    value = row[index].strip()
    return None if value.lower() in _MISSING_TEXT else value


def _percentage(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _MISSING_TEXT:
        return None
    return number(text.removesuffix("%").replace(",", ""))


def _parse_rank(text: str) -> list[dict]:
    out = []
    for raw in _rank_strings(text):
        columns = raw.split(",")
        raw_code = _field(columns, 0)
        name = _field(columns, 1)
        if raw_code is None or name is None:
            continue
        try:
            code = fund_code(raw_code)
        except ValueError:
            continue
        item = {
            "code": code,
            "name": name,
            "nav_date": iso_date(_field(columns, 3)),
            "nav": number(_field(columns, 4)),
            "purchase_fee_text": _field(columns, 20),
        }
        for key, index in _GROWTH_COLUMNS.items():
            item[key] = _percentage(_field(columns, index))
        out.append(item)
    return out


def fetch_rank(fund_type: str) -> list[dict]:
    """Fetch/copy one-year rank rows, caching each supported type for six hours."""
    params = _rank_params(fund_type)
    # Coalesce concurrent cache misses. The set of keys is bounded by the four
    # fund types accepted by _rank_params.
    with _RANK_CACHE_LOCK:
        now = _rank_cache_now()
        cached = _RANK_CACHE.get(fund_type)
        if cached is not None:
            stored_at, rows = cached
            if now - stored_at < _RANK_CACHE_TTL_SECONDS:
                return _copy_rank_rows(rows)
            del _RANK_CACHE[fund_type]

        response = _get(_RANK_URL, params=params)
        rows = _parse_rank(response.text)
        if rows:
            _RANK_CACHE[fund_type] = (_rank_cache_now(), _copy_rank_rows(rows))
        return _copy_rank_rows(rows)


def _epoch_date(epoch_ms) -> str | None:
    value = number(epoch_ms)
    if value is None:
        return None
    try:
        local_time = datetime.fromtimestamp(value / 1000, tz=UTC).astimezone(_SHANGHAI)
        return local_time.date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _parse_nav(text: str) -> list[dict]:
    values = _decode_array_after(_NAV_RE, text)
    if values is None:
        return []
    by_date = {}
    for row in values:
        if not isinstance(row, dict):
            continue
        day = _epoch_date(row.get("x"))
        unit_nav = number(row.get("y"))
        if day is None or unit_nav is None or unit_nav <= 0:
            continue
        by_date[day] = {
            "date": day,
            "unit_nav": unit_nav,
            # Eastmoney supplies this as a percentage value (for example 0.16).
            "growth": _percentage(row.get("equityReturn")),
        }
    return [by_date[day] for day in sorted(by_date)]


def fetch_nav(code: str) -> list[dict]:
    """Fetch unit NAV and source daily return; no cumulative/adjusted NAV alias."""
    valid_code = fund_code(code)
    response = _get(_NAV_URL.format(code=valid_code))
    return _parse_nav(response.text)
