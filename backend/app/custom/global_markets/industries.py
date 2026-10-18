# ruff: noqa: RUF001 -- localized Chinese labels and punctuation.
"""Transparent, equally weighted representative-company theme baskets."""
from __future__ import annotations

import math
import statistics
import time
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import polars as pl

from . import industry_quotes
from .industry_catalog import DISCLAIMER, THEMES, industry_market


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def cn_quotes(repo) -> dict[str, dict]:
    """Use the existing warm snapshot, never the cache-cold refresh getter."""
    frame, _ = repo.peek_enriched_latest()
    if frame.is_empty() or 'symbol' not in frame.columns:
        return {}
    symbols = {symbol for theme in THEMES for symbol in theme['cn']}
    columns = [name for name in ('symbol', 'name', 'raw_close', 'change_pct', 'quote_ts', 'date') if name in frame.columns]
    rows = frame.filter(pl.col('symbol').is_in(sorted(symbols))).select(columns).to_dicts()
    now = time.time()
    found = {}
    for row in rows:
        stamp = _number(row.get('quote_ts'))
        stamp = stamp if stamp and 0 < stamp <= now * 1000 + 120000 else None
        trading_day = row.get('date')
        day = datetime.fromtimestamp(stamp / 1000, UTC).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() if stamp else (
            trading_day.isoformat() if isinstance(trading_day, date) else None)
        found[row['symbol']] = {'symbol': row['symbol'], 'name': row.get('name') or row['symbol'],
                                'price': _number(row.get('raw_close')), 'change_pct': _number(row.get('change_pct')),
                                'quote_at_ms': stamp, 'quote_date': day,
                                'basis': 'source_quote' if stamp else 'daily_reference'}
    return found


def aggregate(market: str, quotes: dict[str, dict]) -> tuple[list[dict], str | None]:
    timezone = ZoneInfo('Asia/Shanghai' if market == 'cn' else 'America/New_York')
    def day(row):
        stamp = row.get('quote_at_ms')
        value = datetime.fromtimestamp(stamp / 1000, UTC).astimezone(timezone).date().isoformat() if stamp else row.get('quote_date')
        try:
            parsed = date.fromisoformat(value)
            return value if parsed <= datetime.now(timezone).date() else None
        except (ValueError, TypeError):
            return None
    required = {symbol for theme in THEMES for symbol in theme[market]}
    valid = {symbol: row for symbol, row in quotes.items() if symbol in required
             and _number(row.get('change_pct')) is not None and (_number(row.get('price')) or 0) > 0 and day(row)}
    as_of = max((day(row) for row in valid.values()), default=None)
    themes = []
    for theme in THEMES:
        members = []
        for symbol in theme[market]:
            row = quotes.get(symbol, {})
            eligible = symbol in valid and day(row) == as_of
            members.append({'symbol': symbol, 'name': row.get('name') or symbol,
                            'price': row.get('price'), 'change_pct': row.get('change_pct') if eligible else None,
                            'quote_at_ms': row.get('quote_at_ms'), 'quote_date': day(row) if row else None,
                            'basis': row.get('basis', 'source_quote'), 'included': eligible})
        values = [row['change_pct'] for row in members if row['included']]
        themes.append({'id': theme['id'], 'name': theme['name'], 'note': theme['note'],
                       'change_pct': statistics.fmean(values) if values else None,
                       'covered': len(values), 'total': len(members), 'members': members})
    return themes, as_of


def get(mode: str, repo) -> dict:
    market = industry_market(mode)
    if market == 'cn':
        values = cn_quotes(repo)
        state = {'loading': not bool(values), 'stale': False, 'error': None, 'data': None}
        source = 'panel_stock_snapshot'
    else:
        state, values = industry_quotes.get({symbol for theme in THEMES for symbol in theme['us']})
        source = 'tencent_public'
    themes, as_of = aggregate(market, values)
    return {'market': market, 'mode': mode, 'basis': 'equal_weight_representative_basket',
            'source': source, 'currency': 'CNY' if market == 'cn' else 'USD',
            'date': as_of, 'loading': state['loading'], 'stale': state['stale'], 'error': state['error'],
            'fetched_at_ms': state['data']['fetched_at_ms'] if state['data'] else None,
            'themes': themes, 'disclaimer': DISCLAIMER,
            'source_note': 'A股复用看板股票快照；美股为腾讯公开成分报价，仅保留源站日期，时区与延迟未声明，不代表实时行情或暗盘',
            'auto_rule': '香港时间08:00至20:00默认A股，其余时段默认美股；不代表交易所开市或实时暗盘'}
