# ruff: noqa: RUF001 -- localized Chinese labels and punctuation.
"""Bounded public cross-market snapshots; no disk or trading-system writes.

HK is main-board equities only. US is the provider's three-market listed
securities universe, including funds/warrants, NOT common-stock breadth.
"""
from __future__ import annotations

import copy
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from .catalog import MARKETS, MarketDataError, normalize_symbol

TTL = 300
MAX_ROWS = 20000
PAGE_SIZE = 100
_LOCK = threading.RLock()
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='global-snapshot')
_STATES: dict[str, dict] = {}
SPECS = {
    'hk': {'filter': 'm:128 t:3', 'scope': 'hk_mainboard', 'label': '港股主板股票', 'exchanges': {128}},
    'us': {'filter': 'm:105,m:106,m:107', 'scope': 'us_listed_securities',
           'label': '美股上市证券（含基金、权证）', 'exchanges': {105, 106, 107}},
}


def _number(value, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if (positive and value <= 0) or (nonnegative and value < 0):
        return None
    return float(value)


def parse_row(market: str, row: dict, now: float) -> dict | None:
    if not isinstance(row, dict) or row.get('f13') not in SPECS[market]['exchanges']:
        return None
    try:
        symbol = normalize_symbol(market, str(row['f12']))
    except (KeyError, ValueError):
        return None
    name = row.get('f14')
    if not isinstance(name, str) or not name.strip() or len(name) > 160:
        return None
    stamp = row.get('f124')
    quote_at = stamp * 1000 if isinstance(stamp, int) and not isinstance(stamp, bool) and 0 < stamp <= now + 120 else None
    pct = _number(row.get('f3'))
    turnover = _number(row.get('f8'), nonnegative=True)
    industry = row.get('f100')
    return {'market': market, 'symbol': symbol, 'name': name.strip(),
            'price': _number(row.get('f2'), positive=True), 'change_pct': pct / 100 if pct is not None else None,
            'volume': _number(row.get('f5'), nonnegative=True),
            'amount': _number(row.get('f6'), nonnegative=True),
            'turnover_rate': turnover / 100 if turnover is not None else None,
            'industry': industry[:80] if isinstance(industry, str) and industry not in ('', '-') else None,
            'quote_at_ms': quote_at, 'fetched_at_ms': round(now * 1000),
            'source': 'eastmoney_public', 'kind': 'stock' if market == 'hk' else 'security',
            'status': 'ok', 'stale': False, 'error': None}


def summarize(market: str, rows: list[dict], total: int, *, skipped: int = 0) -> dict:
    zone = ZoneInfo(MARKETS[market]['timezone'])
    def day(row):
        return datetime.fromtimestamp(row['quote_at_ms'] / 1000, UTC).astimezone(zone).date().isoformat() if row['quote_at_ms'] else None
    valid = [row for row in rows if row['price'] is not None and row['change_pct'] is not None and day(row)]
    date = max((day(row) for row in valid), default=None)
    valid = [row for row in valid if day(row) == date]
    values = [row['change_pct'] for row in valid]
    boundaries = [-.05, -.03, -.01, 0, .01, .03, .05]
    bins = [0] * 8
    for value in values:
        bins[next((i for i, boundary in enumerate(boundaries) if value < boundary), 7)] += 1
    groups: dict[str, list] = {}
    for row in valid:
        if row['industry']:
            groups.setdefault(row['industry'], []).append(row)
    industries = [{'name': name, 'count': len(members),
                   'change_pct': statistics.fmean(row['change_pct'] for row in members),
                   'leader': max(members, key=lambda row: row['change_pct']),
                   'laggard': min(members, key=lambda row: row['change_pct'])}
                  for name, members in groups.items()]
    industries.sort(key=lambda row: row['change_pct'], reverse=True)
    def ranking(key, reverse):
        return sorted((row for row in valid if row.get(key) is not None),
                      key=lambda row: row[key], reverse=reverse)[:8]
    return {'market': market, **MARKETS[market], 'source': 'eastmoney_public',
            'scope': SPECS[market]['scope'], 'universe_label': SPECS[market]['label'],
            'total_reported': total, 'received': len(rows), 'complete': len(rows) + skipped == total,
            'skipped': skipped, 'date': date, 'fetched_at_ms': round(time.time() * 1000),
            'quote_at_ms': max((row['quote_at_ms'] for row in valid), default=None),
            'stats': {'covered': len(valid), 'up': sum(value > 0 for value in values),
                      'flat': sum(value == 0 for value in values), 'down': sum(value < 0 for value in values),
                      'strong': sum(value >= .03 for value in values), 'weak': sum(value <= -.03 for value in values),
                      'mean': statistics.fmean(values) if values else None,
                      'median': statistics.median(values) if values else None, 'bins': bins},
            'ranks': {'gainers': ranking('change_pct', True), 'losers': ranking('change_pct', False),
                      'amount': ranking('amount', True), 'turnover': ranking('turnover_rate', True),
                      'volume': ranking('volume', True), 'market_cap': ranking('market_cap', True)},
            'industries': industries, 'industry_covered': sum(len(members) for members in groups.values())}


def _read_page(client, params):
    # Public GET only. Recover a bounded number of transport disconnects on
    # this page; never retry HTTP refusal/429, bad fields or an incomplete page.
    for attempt in range(3):
        try:
            with client.stream('GET', 'https://push2.eastmoney.com/api/qt/clist/get', params=params,
                               headers={'User-Agent': 'Mozilla/5.0 stockfund-panel'}) as response:
                if response.status_code != 200:
                    raise MarketDataError('公开市场快照暂不可用或限流；保留上一份完整快照')
                body = bytearray()
                for block in response.iter_bytes():
                    body.extend(block)
                    if len(body) > 1024 * 1024:
                        raise MarketDataError('市场快照单页超过读取上限')
            return httpx.Response(200, content=bytes(body)).json()['data']
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(.25 * (attempt + 1))


def fetch(market: str) -> tuple[dict, dict[str, dict]]:
    if market not in SPECS:
        raise ValueError('市场无效')
    rows, seen, skipped, total = [], set(), 0, None
    started = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(10, connect=4), follow_redirects=False) as client:
        page = 1
        while total is None or (page - 1) * PAGE_SIZE < total:
            if page > math.ceil(MAX_ROWS / PAGE_SIZE) or time.monotonic() - started > 150:
                raise MarketDataError('公开市场快照超过本轮读取上限；保留上一份完整快照')
            params = {'pn': page, 'pz': PAGE_SIZE, 'po': 1, 'np': 1, 'fid': 'f12',
                      'fs': SPECS[market]['filter'], 'fltt': 2, 'invt': 2,
                      'fields': 'f2,f3,f5,f6,f8,f12,f13,f14,f100,f124'}
            try:
                payload = _read_page(client, params)
                reported = payload['total']
                raw = payload['diff']
                if isinstance(raw, dict):
                    raw = list(raw.values())
                if (isinstance(reported, bool) or not isinstance(reported, int) or not 0 < reported <= MAX_ROWS
                        or not isinstance(raw, list) or not 0 < len(raw) <= PAGE_SIZE):
                    raise MarketDataError('公开市场快照分页结构无效')
                if total is not None and total != reported:
                    raise MarketDataError('市场证券总数在分页期间变化；本轮快照未发布')
                total = reported
                expected = min(PAGE_SIZE, total - (page - 1) * PAGE_SIZE)
                if len(raw) != expected:
                    raise MarketDataError('市场快照缺少分页记录；本轮快照未发布')
                now = time.time()
                for item in raw:
                    identity = (str(item.get('f13')), str(item.get('f12'))) if isinstance(item, dict) else None
                    if identity in seen:
                        raise MarketDataError('市场快照分页出现重复记录；本轮快照未发布')
                    seen.add(identity)
                    row = parse_row(market, item, now)
                    if row:
                        rows.append(row)
                    else:
                        skipped += 1
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
                raise MarketDataError(f'公开市场快照第{page}页读取失败；保留上一份完整快照') from error
            page += 1
            time.sleep(.35)
    # Source aliases can normalize to one ticker; preserve one quote per canonical
    # identifier, disclose dropped aliases rather than count them twice.
    unique = {row['symbol']: row for row in rows}
    skipped += len(rows) - len(unique)
    return summarize(market, list(unique.values()), total, skipped=skipped), unique


def _load(market: str, state: dict):
    try:
        from app.data_providers import source_routing

        failover_enabled = source_routing.enabled()
        try:
            if failover_enabled:
                primary = source_routing.HEALTH.attempt(
                    ('global_snapshot', market, 'eastmoney_public'),
                    lambda: fetch(market),
                    lambda result: (
                        isinstance(result, tuple) and len(result) == 2
                        and isinstance(result[0], dict)
                        and isinstance(result[1], dict) and bool(result[1])
                    ),
                )
                if not primary.success:
                    raise MarketDataError('东财主快照不可用；尝试有限覆盖备用池')
                data, quotes = primary.value
            else:
                data, quotes = fetch(market)
        except MarketDataError:
            if (not failover_enabled and state['data']
                    and state['data']['scope'] in ('hk_mainboard', 'us_listed_securities')):
                raise
            if market == 'hk':
                from .hk_sina_snapshot import fetch_pool
                from .hk_tencent_etf import fetch_etf_pool
                sina_rows, sina_total = fetch_pool()

                # Publish only a verified optional batch; deduplicate before
                # breadth/rank calculations as well as the quote lookup.
                etf_rows, etf_total = [], 0
                with suppress(MarketDataError):
                    etf_rows, etf_total = fetch_etf_pool()
                unique = {row['symbol']: row for row in sina_rows}
                overlap = len(set(unique) & {row['symbol'] for row in etf_rows})
                unique.update((row['symbol'], row) for row in etf_rows)
                rows = list(unique.values())
                total = sina_total + etf_total - overlap if etf_rows else sina_total
                data = summarize(market, rows, total, skipped=total - len(rows))
                data.update(source='sina_hk_public', scope='hk_sina_hkd_pool',
                            universe_label='港股港币证券覆盖池（新浪×HKEX）',
                            source_note='价格币种经HKEX交易柜台核验；新浪成交额保留源站原值，未另声明单位，不能与其他来源金额直接比较；未核实单位的成交量、成交额不补零',
                            warnings=['东财主快照不可用；当前为有限覆盖池，与旧全市场快照不能直接比较；覆盖池经HKEX港币证券目录核验，可能含GEM，不代表港股全市场',
                                      '免费源时延未知，缺少行业分类与换手率'])
                if etf_rows:
                    data.update(source='sina_hk_public+tencent_etf', scope='hk_sina_tencent_hkd_pool',
                                universe_label=f'港股港币覆盖池（新浪×腾讯ETF×HKEX · ETF {len(etf_rows)}/{etf_total}）',
                                complete=len(etf_rows) == etf_total,
                                etf_coverage={'requested': etf_total, 'accepted': len(etf_rows),
                                              'unavailable': etf_total - len(etf_rows)})
                    if len(etf_rows) < etf_total:
                        data['warnings'].append('部分港币ETF没有有效报价，统计仅包含已取得的记录')
                else:
                    data['warnings'].append('腾讯ETF补充暂不可用，当前仅显示新浪覆盖池')
            else:
                from .us_screeners import fetch_us_screener_pool
                rows = fetch_us_screener_pool()['quotes']
                data = summarize(market, rows, len(rows))
                data.update(source='yahoo_screener', scope='us_screener_pool',
                            universe_label='美股活跃与涨跌筛选池（非全市场）',
                            warnings=['东财全市场主快照不可用；当前为有限覆盖池，与旧全市场快照不能直接比较；仅合并公开活跃、涨幅、跌幅预设池，存在选择偏差，不能推断全市场广度'])
            quotes = {row['symbol']: row for row in rows}
        if (market == 'us' and data.get('source') in ('eastmoney_public', 'yahoo_screener')
                and any(not row.get('industry') for row in quotes.values())):
            from .us_classification import enrich
            metadata = enrich(list(quotes.values()))
            grouped = summarize(market, list(quotes.values()), data['total_reported'])
            data.update(industries=grouped['industries'], industry_covered=grouped['industry_covered'],
                        classification=metadata)
        with _LOCK:
            state.update(data=data, quotes=quotes, error=None, stale=False)
    except Exception as error:
        with _LOCK:
            state['error'] = str(error) if isinstance(error, MarketDataError) else '公开市场快照暂不可用'
            state['stale'] = state['data'] is not None
    finally:
        with _LOCK:
            state.update(loading=False, completed=time.monotonic())


def get(market: str) -> dict:
    if market not in SPECS:
        raise ValueError('市场无效')
    with _LOCK:
        state = _STATES.get(market)
        if state is None:
            state = {'data': None, 'quotes': {}, 'error': None, 'stale': False, 'loading': False, 'completed': -math.inf}
            _STATES[market] = state
        if not state['loading'] and time.monotonic() - state['completed'] >= TTL:
            state['loading'] = True
            _POOL.submit(_load, market, state)
        return copy.deepcopy({key: state[key] for key in ('data', 'error', 'stale', 'loading')})


def quotes(market: str, symbols: set[str]) -> tuple[dict, dict[str, dict]]:
    get(market)
    with _LOCK:
        state = _STATES[market]
        status = {key: state[key] for key in ('data', 'error', 'stale', 'loading')}
        subset = {symbol: state['quotes'][symbol] for symbol in symbols if symbol in state['quotes']}
        return copy.deepcopy(status), copy.deepcopy(subset)
