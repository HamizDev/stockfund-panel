"""Keep price-limit breadth separate from the independently timed order-book snapshot."""
from collections.abc import Iterable, Mapping


def sealed_counts(symbols: Iterable[str], snapshot: Mapping[str, dict], ready: bool) -> dict[str, int]:
    counts = {"real": 0, "fake": 0, "pending": 0}
    for symbol in set(symbols):
        value = snapshot.get(symbol, {}).get("sealed") if ready else None
        key = "real" if value is True else "fake" if value is False else "pending"
        counts[key] += 1
    return counts


def price_limit_scope(rows: Iterable[dict]) -> dict:
    """Explain the price-limit universe without substituting order-book counts."""
    unique = {row['symbol']: row for row in rows}
    non_st = [row for row in unique.values() if row.get('name') and not str(row['name']).upper().lstrip('*').startswith('ST')]
    return {'label': 'A股价格触限, 含ST及北交所, 不含ETF', 'covered': len(unique),
            'non_st_up': sum(bool(row.get('signal_limit_up')) for row in non_st),
            'non_st_down': sum(bool(row.get('signal_limit_down')) for row in non_st)}
