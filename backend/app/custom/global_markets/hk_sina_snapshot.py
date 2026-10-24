# ruff: noqa: RUF001 -- localized source labels.
"""Anonymous Sina HK quote pool, joined to the existing HKEX HKD directory.

The endpoint and fields are documented by AKShare's stock_hk_sina adapter.
This is independently implemented with httpx, without a JS runtime or new deps.
It is a source-covered pool, never a claim of complete HK market coverage.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .catalog import MarketDataError, normalize_symbol

URL = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
       'Market_Center.getHKStockData')
PAGE_SIZE = 60
MAX_PAGES = 99
MAX_PAGE_BYTES = 256 * 1024
MAX_SECONDS = 150
_ZONE = ZoneInfo('Asia/Hong_Kong')
_STAMP = re.compile(r'^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$')


def _number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) and (not positive or result > 0) else None


def parse_row(raw: dict, directory: dict[str, dict], now: float) -> dict | None:
    if not isinstance(raw, dict) or not re.fullmatch(r'\d{5}', str(raw.get('symbol', ''))):
        return None
    symbol = normalize_symbol('hk', raw['symbol'])
    security = directory.get(symbol)
    if (not security or security.get('currency') != 'HKD'
            or security.get('kind') not in ('stock', 'etf')):
        return None
    stamp_text = raw.get('ticktime')
    if not isinstance(stamp_text, str) or not _STAMP.fullmatch(stamp_text):
        return None
    try:
        stamp = datetime.strptime(stamp_text, '%Y/%m/%d %H:%M:%S').replace(tzinfo=_ZONE).timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    if not 0 < stamp <= now + 120:
        return None
    price = _number(raw.get('lasttrade'), positive=True)
    pct = _number(raw.get('changepercent'))
    if price is None or pct is None:
        return None
    # Source names/zero-valued metadata are not used to infer currency or type.
    name = security['name']
    amount = _number(raw.get('amount'))
    return {'market': 'hk', 'symbol': symbol, 'name': name, 'kind': security['kind'],
            'currency': 'HKD', 'price': price, 'change_pct': pct / 100,
            'amount': amount if amount is not None and amount >= 0 else None,
            # Sina does not document the volume unit or provide a usable
            # industry/turnover field here. Never substitute invented zeroes.
            'volume': None, 'industry': None, 'turnover_rate': None,
            'quote_at_ms': round(stamp * 1000), 'fetched_at_ms': round(now * 1000),
            'source': 'sina_hk_public', 'scope': 'hk_sina_hkd_pool',
            'status': 'ok', 'stale': False, 'error': None}


def _read_page(client: httpx.Client, page: int) -> list[dict]:
    params = {'page': page, 'num': PAGE_SIZE, 'sort': 'symbol', 'asc': 1,
              'node': 'qbgg_hk', '_s_r_a': 'init'}
    with client.stream('GET', URL, params=params) as response:
        if response.status_code != 200:
            raise MarketDataError('新浪港股公开快照读取失败或限流')
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_PAGE_BYTES:
                raise MarketDataError('新浪港股快照单页超过读取上限')
        encoding = response.encoding or 'gbk'
    rows = json.loads(body.decode(encoding))
    if (not isinstance(rows, list) or len(rows) > PAGE_SIZE
            or any(not isinstance(row, dict) for row in rows)):
        raise MarketDataError('新浪港股快照分页结构无效')
    return rows


def fetch_pool() -> tuple[list[dict], int]:
    # Search and snapshots reuse the very same bounded 24h HKEX cache.
    from .service import hk_directory
    directory = {row['symbol']: row for row in hk_directory()}
    started = time.monotonic()
    accepted = []
    total = 0
    last_code = ''
    short_page = False
    with httpx.Client(timeout=httpx.Timeout(8, connect=4), follow_redirects=False,
                      headers={'User-Agent': 'Mozilla/5.0 stockfund-panel'}) as client:
        for page in range(1, MAX_PAGES + 1):
            if time.monotonic() - started > MAX_SECONDS:
                raise MarketDataError('新浪港股快照超过本轮读取时限')
            try:
                rows = _read_page(client, page)
            except (httpx.HTTPError, ValueError, UnicodeError, RecursionError) as error:
                raise MarketDataError('新浪港股快照读取失败；未发布部分分页') from error
            if not rows:
                if not total or not accepted:
                    raise MarketDataError('新浪港股快照没有可核验的港币报价')
                return accepted, total
            if short_page:
                raise MarketDataError('新浪港股分页缺记录；本轮快照未发布')
            short_page = len(rows) < PAGE_SIZE
            now = time.time()
            for raw in rows:
                code = raw.get('symbol')
                if not isinstance(code, str) or not re.fullmatch(r'\d{5}', code) or code <= last_code:
                    raise MarketDataError('新浪港股分页代码重复或顺序变化；未发布部分分页')
                last_code = code
                total += 1
                parsed = parse_row(raw, directory, now)
                if parsed:
                    accepted.append(parsed)
            time.sleep(.2)
    raise MarketDataError('新浪港股快照超过分页上限；未发布部分分页')
