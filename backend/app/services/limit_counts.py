"""Keep price-limit breadth separate from the independently timed order-book snapshot."""
from collections.abc import Iterable, Mapping


def sealed_counts(symbols: Iterable[str], snapshot: Mapping[str, dict], ready: bool) -> dict[str, int]:
    counts = {"real": 0, "fake": 0, "pending": 0}
    for symbol in set(symbols):
        value = snapshot.get(symbol, {}).get("sealed") if ready else None
        key = "real" if value is True else "fake" if value is False else "pending"
        counts[key] += 1
    return counts
