# ruff: noqa: RUF001 -- localized Chinese labels.
"""Bounded Tencent quotes for the HKEX directory's HKD ETF subset.

Optional snapshot supplement only. Unknown volume/amount units remain null;
quote timestamps always come from the source, never the local fetch clock.
"""
from __future__ import annotations

import math
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .catalog import MarketDataError, normalize_symbol

BATCH_URL = 'https://qt.gtimg.cn/q='
BATCH_SIZE = 60
MAX_ETFS = 500
MAX_RESPONSE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 30
REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
_ZONE = ZoneInfo('Asia/Hong_Kong')
_FIELD_RE = re.compile(r'^v_(hk\d{5})="([^"]*)"$')


def _number(value: str, *, positive: bool = False) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and (not positive or result > 0) else None


def parse_row(symbol: str, fields: list[str], now: float, directory: dict[str, dict]) -> dict | None:
    if (not re.fullmatch(r'hk\d{5}', symbol) or not isinstance(fields, list)
            or len(fields) < 40 or fields[2] != symbol[2:]):
        return None
    normalized = normalize_symbol('hk', symbol[2:])
    security = directory.get(normalized)
    if not security or security.get('kind') != 'etf' or security.get('currency') != 'HKD':
        return None
    price, percent = _number(fields[3], positive=True), _number(fields[32])
    if price is None or percent is None:
        return None
    stamp = None
    for pattern in ('%Y/%m/%d %H:%M:%S', '%Y%m%d%H%M%S'):
        try:
            stamp = datetime.strptime(fields[30], pattern).replace(tzinfo=_ZONE).timestamp()
            break
        except (ValueError, TypeError):
            continue
    if stamp is None or not 0 < stamp <= now + 300:
        return None
    return {
        'market': 'hk', 'symbol': normalized, 'name': security['name'], 'kind': 'etf',
        'currency': 'HKD', 'price': price, 'change_pct': percent / 100,
        'volume': None, 'amount': None, 'industry': None, 'turnover_rate': None,
        'quote_at_ms': round(stamp * 1000), 'fetched_at_ms': round(now * 1000),
        'source': 'tencent_hk_etf', 'scope': 'hk_etf_tencent_batch',
        'status': 'ok', 'stale': False, 'error': None,
    }


def _read_batch(client: httpx.Client, batch: list[str], remaining: float) -> str:
    started = time.perf_counter()
    timeout = httpx.Timeout(min(8, remaining), connect=min(4, remaining))
    with client.stream('GET', BATCH_URL + ','.join(batch), timeout=timeout,
                       headers={'User-Agent': 'Mozilla/5.0 stockfund-panel',
                                'Referer': 'https://gu.qq.com/'}) as response:
        if response.status_code != 200:
            raise MarketDataError(f'腾讯 ETF 批量接口返回 HTTP {response.status_code}')
        content = bytearray()
        for chunk in response.iter_bytes():
            if time.perf_counter() - started > remaining:
                raise MarketDataError('腾讯 ETF 批量读取超时；本轮补充未发布')
            content.extend(chunk)
            if len(content) > MAX_RESPONSE_BYTES:
                raise MarketDataError('腾讯 ETF 批量响应超过大小限制')
        try:
            return content.decode('gbk')
        except UnicodeDecodeError as error:
            raise MarketDataError('腾讯 ETF 批量响应编码无效') from error


def fetch_etf_pool() -> tuple[list[dict], int]:
    from .service import hk_directory

    directory = {row['symbol']: row for row in hk_directory()}
    symbols = sorted(f"hk{symbol.split('.')[0]}" for symbol, row in directory.items()
                     if row.get('kind') == 'etf' and row.get('currency') == 'HKD')
    if not 0 < len(symbols) <= MAX_ETFS:
        raise MarketDataError('HKEX 港币 ETF 目录为空或超过读取上限')
    accepted = []
    started = time.monotonic()
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
            for offset in range(0, len(symbols), BATCH_SIZE):
                remaining = TIMEOUT_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    raise MarketDataError('腾讯 ETF 批量读取超时；本轮补充未发布')
                batch = symbols[offset:offset + BATCH_SIZE]
                text = _read_batch(client, batch, remaining)
                if time.monotonic() - started > TIMEOUT_SECONDS:
                    raise MarketDataError('腾讯 ETF 批量读取超时；本轮补充未发布')
                requested, seen = set(batch), set()
                now = time.time()
                for entry in text.split(';'):
                    if not entry.strip():
                        continue
                    match = _FIELD_RE.fullmatch(entry.strip())
                    if not match:
                        raise MarketDataError('腾讯 ETF 批量响应格式无效')
                    symbol, data = match.groups()
                    if symbol not in requested or symbol in seen:
                        raise MarketDataError('腾讯 ETF 批量响应出现外来或重复代码')
                    seen.add(symbol)
                    row = parse_row(symbol, data.split('~'), now, directory)
                    if row:
                        accepted.append(row)
                if seen != requested:
                    raise MarketDataError('腾讯 ETF 批量响应缺少请求代码；本轮补充未发布')
                if offset + BATCH_SIZE < len(symbols):
                    time.sleep(.15)
    except (httpx.HTTPError, OSError) as error:
        raise MarketDataError('腾讯 ETF 批量接口连接失败') from error
    if not accepted:
        raise MarketDataError('腾讯 ETF 批量接口没有返回有效报价')
    return accepted, len(symbols)
