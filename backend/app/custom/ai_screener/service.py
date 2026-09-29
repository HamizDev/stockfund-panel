"""Aggregate existing daily strategy results without running new selection jobs."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

_SYMBOL = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_FIELDS = ("close", "change_pct", "amount", "volume", "turnover_rate", "pe_ttm", "pb", "ma5", "ma20", "ma60")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_candidates(
    cached: dict,
    strategies: list[dict],
    quotes: list[dict],
    quote_status: dict,
    provider: str,
    limit: int = 60,
) -> dict:
    """Only same-date stock/daily hits count; live overlay requires a fresh quote poll."""
    as_of = cached.get("as_of")
    if not as_of:
        return {"as_of": None, "updated_at": cached.get("updated_at"),
                "quote": {"live": False, "provider": None, "last_fetch_ms": None, "age_ms": None},
                "coverage": {"computed": 0, "total": 0}, "total": 0, "items": []}
    metadata = {
        str(meta["id"]): meta
        for meta in strategies
        if meta.get("id")
        and not meta.get("research_only")
        and "stock" in meta.get("asset_types", ["stock"])
        and "1d" in meta.get("timeframes", ["1d"])
    }
    computed = sum(
        1 for sid, result in (cached.get("results") or {}).items()
        if sid in metadata and isinstance(result, dict) and result.get("as_of") == as_of
    )
    grouped: dict[str, dict] = defaultdict(lambda: {"hits": [], "row": {}})
    for sid, result in (cached.get("results") or {}).items():
        if sid not in metadata or not isinstance(result, dict) or result.get("as_of") != as_of:
            continue
        for row in result.get("rows") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            if not _SYMBOL.fullmatch(symbol):
                continue
            candidate = grouped[symbol]
            if sid not in candidate["hits"]:
                candidate["hits"].append(sid)
            if len(row) > len(candidate["row"]):
                candidate["row"] = row

    age = _finite(quote_status.get("quote_age_ms"))
    live = bool(
        quote_status.get("enabled") and quote_status.get("running")
        and quote_status.get("is_trading_hours") and age is not None
        and age <= 120_000 and quote_status.get("last_fetch_ms") and bool(quotes)
    )
    quote_map = {
        str(row.get("symbol") or "").upper(): row
        for row in quotes if isinstance(row, dict)
    } if live else {}
    candidates = []
    for symbol, item in grouped.items():
        base = item["row"]
        quote = quote_map.get(symbol)
        metrics = {key: _finite(base.get(key)) for key in _FIELDS}
        if quote:
            for key in ("close", "change_pct", "amount", "volume", "turnover_rate"):
                value = _finite(quote.get(key))
                if value is not None:
                    metrics[key] = value
        hits = sorted(item["hits"])
        candidates.append({
            "symbol": symbol,
            "name": str(base.get("name") or (quote or {}).get("name") or symbol),
            "strategies": [{"id": sid, "name": str(metadata[sid].get("name") or sid),
                            "description": str(metadata[sid].get("description") or "")} for sid in hits],
            "hit_count": len(hits),
            "metrics": metrics,
            "price_source": "live" if quote else "daily",
        })
    candidates.sort(key=lambda item: (-item["hit_count"], -abs(item["metrics"]["amount"] or 0), item["symbol"]))
    return {
        "as_of": as_of,
        "updated_at": cached.get("updated_at"),
        "quote": {"live": live, "provider": provider if live else None,
                  "last_fetch_ms": quote_status.get("last_fetch_ms") if live else None,
                  "age_ms": age if live else None},
        "coverage": {"computed": computed, "total": len(metadata)},
        "total": len(candidates),
        "items": candidates[:limit],
    }
