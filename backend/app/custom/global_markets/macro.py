# ruff: noqa: RUF001 -- localized public-data labels.
"""Official daily rate references, separate from quotes and trading signals.

Source discovery: simonlin1212/global-stock-data cc6afb5 and
simonlin1212/a-stock-data 29b07d2. Parsers are adapted to panel contracts;
see docs/free-market-data.md for attribution and deliberately excluded sources.
"""
from __future__ import annotations

import asyncio
import csv
import io
import math
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from .catalog import MarketDataError
from .service import _cached

TREASURY_PAGE = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve'
CHINAMONEY_PAGE = 'https://www.chinamoney.com.cn/chinese/bkfrr/'
_FIXING_URL = 'https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/frr-chrt.csv'
_TENORS = [('1m', '1个月', '1 Mo'), ('3m', '3个月', '3 Mo'), ('6m', '6个月', '6 Mo'),
           ('1y', '1年', '1 Yr'), ('2y', '2年', '2 Yr'), ('5y', '5年', '5 Yr'),
           ('10y', '10年', '10 Yr'), ('30y', '30年', '30 Yr')]
_FIXINGS = [('FR001', '隔夜 FR001'), ('FR007', '7天 FR007'), ('FR014', '14天 FR014')]
TOTAL_TIMEOUT_S = 12


def _text(url: str, *, params: dict | None = None) -> str:
    # Sync FastAPI routes run this in a worker thread. The async timeout bounds
    # the whole stream, including a peer that keeps trickling bytes indefinitely.
    return asyncio.run(_read_text(url, params))


async def _read_text(url: str, params: dict | None) -> str:
    try:
        async with (
            asyncio.timeout(TOTAL_TIMEOUT_S),
            httpx.AsyncClient(
                timeout=httpx.Timeout(8, connect=3), follow_redirects=False
            ) as client,
            client.stream(
                'GET',
                url,
                params=params,
                headers={
                    'User-Agent': 'stockfund-panel public-data/1.0',
                    'Referer': CHINAMONEY_PAGE if 'chinamoney' in url else TREASURY_PAGE,
                },
            ) as response,
        ):
            if response.status_code != 200:
                raise MarketDataError('官方利率源暂不可用，请稍后重试')
            body = bytearray()
            async for block in response.aiter_bytes():
                body.extend(block)
                if len(body) > 512 * 1024:
                    raise MarketDataError('官方利率响应超过读取上限')
        return body.decode('utf-8-sig')
    except (httpx.HTTPError, UnicodeError, TimeoutError) as exc:
        raise MarketDataError('官方利率源读取失败，请检查网络后重试') from exc


def _number(value: str | None, *, required: bool = False) -> float | None:
    if value is None or value.strip() in ('', 'N/A', 'NA'):
        if required:
            raise MarketDataError('官方利率源缺少必要数值')
        return None
    try:
        number = float(value)
        if not math.isfinite(number) or not -100 < number < 100:
            raise ValueError
        return number
    except (TypeError, ValueError) as exc:
        raise MarketDataError('官方利率源数值格式无效') from exc


def _ordered(rows: list[dict], today: date) -> list[dict]:
    dates = [row['date'] for row in rows]
    if not dates or len(set(dates)) != len(dates) or any(day > today for day in dates):
        raise MarketDataError('官方利率源为空、日期重复或日期超前')
    return sorted(rows, key=lambda row: row['date'])


def parse_treasury(text: str, today: date) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    if not {'Date', '2 Yr', '10 Yr'}.issubset(reader.fieldnames or []):
        raise MarketDataError('美国财政部收益率字段格式改变')
    rows = []
    try:
        for row in reader:
            day = datetime.strptime(row['Date'], '%m/%d/%Y').date()
            values = {key: _number(row.get(field)) for key, _, field in _TENORS}
            if all(value is None for value in values.values()):
                raise MarketDataError('美国财政部收益率记录为空')
            rows.append({'date': day, 'values': values})
    except (TypeError, ValueError, KeyError) as exc:
        raise MarketDataError('美国财政部收益率记录格式改变') from exc
    return _ordered(rows, today)


def parse_fixing(text: str, today: date) -> list[dict]:
    rows = []
    try:
        for row in csv.reader(io.StringIO(text)):
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) != 9 or any(cell.strip() for cell in row[1:6]):
                raise MarketDataError('中国货币网定盘利率字段格式改变')
            rows.append({'date': date.fromisoformat(row[0]), 'values': {
                key: _number(row[index + 6], required=True) for index, (key, _) in enumerate(_FIXINGS)
            }})
    except (ValueError, TypeError) as exc:
        raise MarketDataError('中国货币网定盘利率日期格式改变') from exc
    return _ordered(rows, today)


def snapshot(market: str, rows: list[dict]) -> dict:
    latest = rows[-1]
    previous = rows[-2] if len(rows) > 1 else None
    labels = [(key, label) for key, label, _ in _TENORS] if market == 'us' else _FIXINGS
    items = []
    for key, label in labels:
        value = latest['values'][key]
        prior = previous['values'][key] if previous else None
        items.append({'id': key, 'label': label, 'value': value, 'previous_value': prior,
                      'change_bp': round((value - prior) * 100, 4) if value is not None and prior is not None else None})
    values = latest['values']
    spread = round((values['10y'] - values['2y']) * 100, 4) if market == 'us' and values['10y'] is not None and values['2y'] is not None else None
    return {'market': market, 'currency': 'USD' if market == 'us' else 'CNY',
            'date': latest['date'].isoformat(), 'previous_date': previous['date'].isoformat() if previous else None,
            'unit': 'percent', 'frequency': 'business_daily', 'items': items, 'spread_bp': spread,
            'source': '美国财政部 U.S. Treasury' if market == 'us' else '中国货币网 / 中国外汇交易中心',
            'source_url': TREASURY_PAGE if market == 'us' else CHINAMONEY_PAGE,
            'fetched_at_ms': int(time.time() * 1000), 'cached': False, 'stale': False, 'error': None}


def fetch(market: str) -> dict:
    today = datetime.now(ZoneInfo('America/New_York' if market == 'us' else 'Asia/Shanghai')).date()
    if market == 'us':
        year = today.year
        url = f'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all'
        rows = parse_treasury(_text(url, params={'type': 'daily_treasury_yield_curve',
            'field_tdr_date_value': str(year), '_format': 'csv'}), today)
    else:
        rows = parse_fixing(_text(_FIXING_URL), today)
    return snapshot(market, rows)


def get(market: str) -> dict:
    if market not in ('cn', 'us'):
        raise ValueError('利率参考仅支持人民币或美元市场')
    return _cached(('macro', market), 3600, lambda: fetch(market), stale=True)
