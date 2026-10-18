
"""Progressive fixed observation pool, not whole-market statistics.

One shared two-worker pool leaves capacity for interactive chart queries. A
market has at most one batch, so repeated page polls cannot enqueue more work.
No scheduler, disk writes, broker calls, or jobs survive a process restart.
"""
from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import market_snapshot, service
from .catalog import INDICES, MARKETS, SEEDS, MarketDataError

_LOCK = threading.RLock()
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='global-board')
_STATES: dict[str, dict] = {}
TTL = 60


def _load(market: str, item: tuple, state: dict):
    symbol, name, kind = item
    base = {'market': market, 'symbol': symbol, 'name': name, 'kind': kind}
    try:
        value = service.quote(market, symbol)
        row = {**base, **value, 'name': name, 'refreshing': False, 'status': 'ok' if not value.get('stale') else 'error'}
    except Exception as error:
        # Unexpected failures are isolated to one row and never echo raw bodies.
        message = str(error) if isinstance(error, MarketDataError) else '该标的行情暂不可用'
        row = {**base, 'price': None, 'change_pct': None, 'quote_at_ms': None,
               'fetched_at_ms': None, 'stale': False, 'error': message, 'status': 'error'}
    with _LOCK:
        state['items'][symbol] = row
        state['remaining'] -= 1
        if not state['remaining']:
            state['completed'] = time.monotonic()


def get(market: str) -> dict:
    if market not in MARKETS:
        raise ValueError('请选择港股或美股市场')
    broad = market_snapshot.get(market)
    items = [*INDICES[market], *SEEDS[market]]
    with _LOCK:
        state = _STATES.get(market)
        if state is None or (not state['remaining'] and time.monotonic() - state['completed'] >= TTL):
            state = {'started': time.monotonic(), 'completed': None, 'items': {}, 'previous': {}, 'remaining': len(items)}
            previous = _STATES.get(market)
            if previous:
                state['previous'] = {key: {**value, 'refreshing': True}
                                  for key, value in previous['items'].items()
                                  if value.get('fetched_at_ms') and time.time() * 1000 - value['fetched_at_ms'] < 86_400_000}
            _STATES[market] = state
            for item in items:
                _POOL.submit(_load, market, item, state)
        rows = []
        for symbol, name, kind in items:
            # Keep the last complete snapshot while revalidating. Initial loads
            # remain progressive; later refreshes switch atomically at completion.
            visible = state.get('previous') if state['remaining'] and state.get('previous') else state['items']
            rows.append(copy.deepcopy(visible.get(symbol, {
                'market': market, 'symbol': symbol, 'name': name, 'kind': kind,
                'price': None, 'change_pct': None, 'quote_at_ms': None, 'fetched_at_ms': None,
                'stale': False, 'error': None, 'status': 'loading',
            })))
        return {'market': market, **MARKETS[market], 'scope': 'fixed_watchlist', 'source': 'yahoo_public',
                'loading': bool(state['remaining']), 'refresh_after_seconds': TTL,
                'completed_count': len(items) - state['remaining'],
                'market_snapshot': broad,
                'checked_at_ms': round(time.time() * 1000), 'items': rows}
