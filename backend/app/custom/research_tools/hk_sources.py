# ruff: noqa: RUF001 -- Native Chinese research text.
"""Small public Hong Kong market research adapters.

Endpoint and field-schema references were reviewed in Vibe-Research's
``sources/eastmoney.py``, ``sources/tencent.py`` and ``sources/hkex.py``.
Copyright (c) 2026 Simon Lin; MIT License:
https://github.com/simonlin1212/Vibe-Research/blob/main/LICENSE

This module is intentionally stateless. Callers own cache policy and storage.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx

EASTMONEY_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
TENCENT_URL = 'https://qt.gtimg.cn/q='
PAGE_SIZE = 100
MAX_PAGES = 3
TIMEOUT_SECONDS = 8.0
MAX_TENCENT_RESPONSE_BYTES = 64 * 1024

_SYMBOL_RE = re.compile(r'^\d{5}\.HK$')
_AH_A_CODE_RE = re.compile(r'^(\d{6})\.(SH|SZ)$')
_QUOTE_KEY_RE = re.compile(r'^v_([A-Za-z0-9_]+)="([^"]*)"$')
_HK_ZONE = ZoneInfo('Asia/Hong_Kong')

_DATASET_CONFIG = {
    'southbound_flow': {
        'source': 'eastmoney',
        'source_url': EASTMONEY_URL,
        'unit': {
            'amounts': 'HKD; source amounts in million HKD multiplied by 1,000,000',
            'trade_count': 'trades',
            'hsi_change_ratio': 'decimal',
        },
    },
    'southbound_holding': {
        'source': 'eastmoney',
        'source_url': EASTMONEY_URL,
        'unit': {
            'held_shares': 'shares',
            'market_value_hkd': 'HKD',
            'holding_ratio': 'decimal',
            'shares_change': 'shares',
            'close_hkd': 'HKD',
        },
    },
    'dividends': {
        'source': 'eastmoney',
        'source_url': EASTMONEY_URL,
        'unit': 'Dividend plan is preserved as source text, including its currency.',
    },
    'ah_premium': {
        'source': 'eastmoney+tencent',
        'source_url': f'{EASTMONEY_URL} + {TENCENT_URL}',
        'unit': {
            'a_price_cny': 'CNY',
            'h_price_hkd': 'HKD',
            'hkd_cny_rate': 'CNY per HKD',
            'ah_ratio': 'A share CNY price / converted H share CNY price',
            'premium_ratio': 'decimal',
        },
    },
}


def _fetched_at() -> str:
    return datetime.now(UTC).isoformat()


def _envelope(
    dataset: str,
    symbol: str | None,
    *,
    status: str,
    items: list[dict],
    as_of: str | None,
    fetched_at: str,
    gaps: list[str],
) -> dict:
    config = _DATASET_CONFIG.get(dataset)
    return {
        'dataset': dataset,
        'symbol': symbol,
        'status': status,
        'items': items,
        'source': config['source'] if config else 'unknown',
        'source_url': config['source_url'] if config else '',
        'as_of': as_of,
        'fetched_at': fetched_at,
        'unit': config['unit'] if config else None,
        'gaps': gaps,
    }


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip().replace(',', ''))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _number(value: object) -> int | float | None:
    parsed = _decimal(value)
    if parsed is None:
        return None
    if parsed == parsed.to_integral_value():
        return int(parsed)
    return float(parsed)


def _ratio_from_percent(value: object) -> float | None:
    parsed = _decimal(value)
    return None if parsed is None else float(parsed / Decimal(100))


def _hkd_from_million(value: object) -> int | float | None:
    parsed = _decimal(value)
    if parsed is None:
        return None
    amount = parsed * Decimal(1_000_000)
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


def _date_text(value: object) -> str | None:
    if not isinstance(value, (str, date, datetime)):
        return None
    text = value.isoformat() if isinstance(value, (date, datetime)) else value.strip()
    text = text[:10].replace('/', '-')
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _date_as_of(items: list[dict], field: str) -> str | None:
    dates = [item[field] for item in items if isinstance(item.get(field), str)]
    return max(dates) if dates else None


def _integer(value: object) -> int | None:
    parsed = _decimal(value)
    if parsed is None or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _datacenter_page(report_name: str, filter_text: str, page: int,
                     sort_columns: str, sort_types: str) -> tuple[list[dict], int, int] | None:
    params = {
        'reportName': report_name,
        'columns': 'ALL',
        'filter': filter_text,
        'pageNumber': str(page),
        'pageSize': str(PAGE_SIZE),
        'sortColumns': sort_columns,
        'sortTypes': sort_types,
        'source': 'WEB',
        'client': 'WEB',
    }
    response = httpx.get(
        EASTMONEY_URL,
        params=params,
        headers={'User-Agent': 'Mozilla/5.0'},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('invalid response')
    if payload.get('success') is False and str(payload.get('code')) == '9201':
        return None
    if payload.get('success') is not True or payload.get('code') not in (0, '0'):
        raise ValueError('source rejected request')
    result = payload.get('result')
    if not isinstance(result, dict):
        raise ValueError('missing result')
    rows = result.get('data')
    count = _integer(result.get('count'))
    pages = _integer(result.get('pages'))
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
            or count is None or pages is None or count < 0 or pages < 0
            or len(rows) > PAGE_SIZE):
        raise ValueError('invalid result page')
    if count == 0 and pages == 0 and not rows:
        return None
    if count <= 0 or pages <= 0:
        raise ValueError('inconsistent pagination')
    return rows, count, pages


def _fetch_report(report_name: str, filter_text: str,
                  sort_columns: str, sort_types: str) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    gaps: list[str] = []
    expected_count: int | None = None
    expected_pages: int | None = None

    for page in range(1, MAX_PAGES + 1):
        try:
            result = _datacenter_page(
                report_name, filter_text, page, sort_columns, sort_types,
            )
        except Exception:
            gaps.append('source_unavailable' if not rows else 'incomplete_pagination')
            break
        if result is None:
            if page == 1:
                return [], []
            gaps.append('incomplete_pagination')
            break

        page_rows, count, pages = result
        if expected_count is None:
            expected_count, expected_pages = count, pages
        elif count != expected_count or pages != expected_pages:
            gaps.append('pagination_changed')
            break

        rows.extend(page_rows)
        expected_page_rows = min(PAGE_SIZE, max(0, count - (page - 1) * PAGE_SIZE))
        if len(page_rows) != expected_page_rows:
            gaps.append('incomplete_page')
            break
        if page >= pages:
            break
        if page == MAX_PAGES:
            gaps.append('page_limit_reached')

    if expected_count is not None and len(rows) < expected_count and not gaps:
        gaps.append('incomplete_pagination')
    return rows, gaps


def _map_flow(row: dict) -> tuple[dict | None, list[str]]:
    gaps: list[str] = []
    day = _date_text(row.get('TRADE_DATE'))
    if day is None:
        return None, ['missing_trade_date']

    def mapped(source_key: str, converter):
        if source_key not in row or row.get(source_key) is None:
            gaps.append(f'missing_{source_key.lower()}')
            return None
        value = converter(row[source_key])
        if value is None:
            gaps.append(f'invalid_{source_key.lower()}')
        return value

    item = {
        'date': day,
        'net_inflow_hkd': mapped('NET_DEAL_AMT', _hkd_from_million),
        'buy_hkd': mapped('BUY_AMT', _hkd_from_million),
        'sell_hkd': mapped('SELL_AMT', _hkd_from_million),
        'turnover_hkd': mapped('DEAL_AMT', _hkd_from_million),
        'trade_count': mapped('DEAL_NUM', _integer),
        'cumulative_net_inflow_hkd': mapped('ACCUM_DEAL_AMT', _hkd_from_million),
        'hsi_close': mapped('INDEX_CLOSE_PRICE', _number),
        'hsi_change_ratio': mapped('INDEX_CHANGE_RATE', _ratio_from_percent),
    }
    return item, gaps


def _map_holding(row: dict) -> tuple[dict | None, list[str]]:
    gaps: list[str] = []
    day = _date_text(row.get('TRADE_DATE') or row.get('HOLD_DATE'))
    if day is None:
        return None, ['missing_trade_date']

    def mapped(source_key: str, converter):
        if source_key not in row or row.get(source_key) is None:
            gaps.append(f'missing_{source_key.lower()}')
            return None
        value = converter(row[source_key])
        if value is None:
            gaps.append(f'invalid_{source_key.lower()}')
        return value

    item = {
        'date': day,
        'name': row.get('SECURITY_NAME'),
        'held_shares': mapped('HOLD_SHARES', _integer),
        'market_value_hkd': mapped('HOLD_MARKET_CAP', _number),
        'holding_ratio': mapped('HOLD_SHARES_RATIO', _ratio_from_percent),
        'shares_change': mapped('HOLD_SHARES_CHANGE', _integer),
        'close_hkd': mapped('CLOSE_PRICE', _number),
    }
    return item, gaps


def _map_dividend(row: dict) -> tuple[dict | None, list[str]]:
    gaps: list[str] = []
    notice_date = _date_text(row.get('NOTICE_DATE'))
    if notice_date is None:
        return None, ['missing_notice_date']
    plan = row.get('PLAN_EXPLAIN')
    if not isinstance(plan, str) or not plan.strip():
        gaps.append('missing_plan_text')
        plan = None
    item = {
        'notice_date': notice_date,
        'year': row.get('YEAR'),
        'kind': row.get('REPORT_TYPE'),
        'plan_text': plan,
        'ex_date': _date_text(row.get('EX_DIVIDEND_DATE')),
        'pay_date': _date_text(row.get('DIVIDEND_DATE')),
        'book_close_date': _date_text(row.get('TRANSFER_END_DATE')),
    }
    for key in ('YEAR', 'REPORT_TYPE', 'EX_DIVIDEND_DATE', 'DIVIDEND_DATE', 'TRANSFER_END_DATE'):
        value = row.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            gaps.append(f'missing_{key.lower()}')
    return item, gaps


def _map_rows(rows: list[dict], mapper) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    gaps: list[str] = []
    for row in rows:
        item, row_gaps = mapper(row)
        if item is not None:
            items.append(item)
        gaps.extend(row_gaps)
    return items, sorted(set(gaps))


def _quote_time(value: str | None) -> datetime | None:
    if not isinstance(value,str):
        return None
    for pattern in ('%Y%m%d%H%M%S', '%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(value.strip(), pattern).replace(tzinfo=_HK_ZONE)
        except ValueError:
            continue
    return None


def _quote_value(fields: list[str], index: int) -> float | None:
    if index >= len(fields):
        return None
    value = _decimal(fields[index])
    return float(value) if value is not None and value > 0 else None


def _tencent_quotes(a_key: str, h_key: str) -> tuple[dict[str, list[str]], list[str]]:
    try:
        response = httpx.get(
            f'{TENCENT_URL}{a_key},{h_key},whHKDCNY',
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        response.raise_for_status()
        body = response.content
        if len(body) > MAX_TENCENT_RESPONSE_BYTES:
            return {}, ['source_response_too_large']
        text = body.decode('gbk')
    except Exception:
        return {}, ['source_unavailable']

    expected = {a_key, h_key, 'whHKDCNY'}
    quotes: dict[str, list[str]] = {}
    for entry in text.split(';'):
        match = _QUOTE_KEY_RE.fullmatch(entry.strip())
        if not match:
            continue
        key, value = match.groups()
        if key in expected:
            quotes[key] = value.split('~')
    gaps = ['missing_quote_fields'] if expected - quotes.keys() else []
    return quotes, gaps


def _ah_item(symbol: str, pair_row: dict) -> tuple[dict | None, list[str], str | None]:
    secucode = pair_row.get('SECUCODE')
    h_code = pair_row.get('STR_CODEH')
    a_match = _AH_A_CODE_RE.fullmatch(str(secucode or ''))
    h_match = re.fullmatch(r'(\d{5})\.HK', str(h_code or ''))
    if a_match is None or h_match is None or h_match.group(1) != symbol[:5]:
        return None, ['invalid_ah_pair'], None

    a_key = f'{a_match.group(2).lower()}{a_match.group(1)}'
    h_key = f'r_hk{h_match.group(1)}'
    quotes, gaps = _tencent_quotes(a_key, h_key)
    a_fields = quotes.get(a_key, [])
    h_fields = quotes.get(h_key, [])
    fx_fields = quotes.get('whHKDCNY', [])
    a_price = _quote_value(a_fields, 3)
    h_price = _quote_value(h_fields, 3)
    rate = _quote_value(fx_fields, 3)
    a_time = a_fields[30] if len(a_fields) > 30 and a_fields[30] else None
    h_time = h_fields[30] if len(h_fields) > 30 and h_fields[30] else None
    fx_time = fx_fields[5] if len(fx_fields) > 5 and fx_fields[5] else None

    ah_ratio = None
    premium = None
    if a_price is not None and h_price is not None and rate is not None:
        ah_ratio = a_price / (h_price * rate)
        premium = ah_ratio - 1
    else:
        gaps.append('missing_ah_quote_or_fx')

    quote_dates = [stamp for stamp in (_quote_time(value) for value in (a_time, h_time, fx_time)) if stamp]
    as_of = max(quote_dates).isoformat() if quote_dates else None
    if len(quote_dates)!=3:
        gaps.append('missing_quote_time')
    elif max(quote_dates)-min(quote_dates)>timedelta(minutes=5):
        gaps.append('跨时点报价，仅作价差参考，不是同一时刻可成交溢价')

    item = {
        'pair': {
            'a_symbol': f'{a_match.group(1)}.{a_match.group(2)}',
            'h_symbol': symbol,
            'a_name': pair_row.get('STR_NAMEA') or pair_row.get('SECURITY_NAME_ABBR'),
            'h_name': pair_row.get('STR_NAMEH'),
        },
        'a_price_cny': a_price,
        'h_price_hkd': h_price,
        'hkd_cny_rate': rate,
        'ah_ratio': round(ah_ratio, 8) if ah_ratio is not None else None,
        'premium_ratio': round(premium, 8) if premium is not None else None,
        'a_quote_time': a_time,
        'h_quote_time': h_time,
        'fx_quote_time': fx_time,
    }
    return item, gaps, as_of


def fetch(dataset: str, symbol: str | None = None) -> dict:
    """Fetch one bounded public HK research dataset into the shared result envelope."""
    fetched_at = _fetched_at()
    if dataset not in _DATASET_CONFIG:
        return _envelope(dataset, symbol, status='partial', items=[], as_of=None,
                         fetched_at=fetched_at, gaps=['unsupported_dataset'])
    if dataset == 'southbound_flow' and symbol is not None:
        return _envelope(dataset, symbol, status='empty', items=[], as_of=None,
                         fetched_at=fetched_at, gaps=['symbol_not_applicable'])
    if dataset != 'southbound_flow' and (
        not isinstance(symbol, str) or _SYMBOL_RE.fullmatch(symbol) is None
    ):
        return _envelope(dataset, symbol, status='empty', items=[], as_of=None,
                         fetched_at=fetched_at, gaps=['invalid_symbol_format'])

    if dataset == 'southbound_flow':
        report_name = 'RPT_MUTUAL_DEAL_HISTORY'
        filter_text = '(MUTUAL_TYPE="006")'
        mapper = _map_flow
        sort_columns, sort_types = 'TRADE_DATE', '-1'
    elif dataset == 'southbound_holding':
        report_name = 'RPT_MUTUAL_STOCK_HOLDRANKS'
        filter_text = f'(SECUCODE="{symbol}")(MUTUAL_TYPE="002")'
        mapper = _map_holding
        sort_columns, sort_types = 'TRADE_DATE', '-1'
    elif dataset == 'dividends':
        report_name = 'RPT_HKF10_MAIN_DIVBASIC'
        filter_text = f'(SECURITY_CODE="{symbol[:5]}")(IS_BFP="0")'
        mapper = _map_dividend
        sort_columns, sort_types = 'NOTICE_DATE,EX_DIVIDEND_DATE', '-1,-1'
    else:
        rows, report_gaps = _fetch_report(
            'RPT_F10_BASIC_ORGINFO', f'(STR_CODEH="{symbol}")', '', '',
        )
        if report_gaps:
            return _envelope(dataset, symbol, status='partial', items=[], as_of=None,
                             fetched_at=fetched_at, gaps=report_gaps)
        if not rows:
            return _envelope(dataset, symbol, status='empty', items=[], as_of=None,
                             fetched_at=fetched_at, gaps=['no_ah_pair_found'])
        exact = [row for row in rows if row.get('STR_CODEH') == symbol]
        if len(exact) != 1:
            return _envelope(dataset, symbol, status='empty', items=[], as_of=None,
                             fetched_at=fetched_at, gaps=['no_exact_ah_pair'])
        item, quote_gaps, as_of = _ah_item(symbol, exact[0])
        status = 'partial' if quote_gaps else 'ok'
        return _envelope(dataset, symbol, status=status, items=[item] if item else [], as_of=as_of,
                         fetched_at=fetched_at, gaps=sorted(set(quote_gaps)))

    rows, report_gaps = _fetch_report(report_name, filter_text, sort_columns, sort_types)
    if dataset in {'southbound_holding','dividends'}:
        for row in rows:
            identifiers=[str(row[key]) for key in ('SECUCODE','SECURITY_CODE') if row.get(key) is not None]
            if not identifiers or any(value not in {symbol,symbol[:5]} for value in identifiers):
                return _envelope(dataset,symbol,status='partial',items=[],as_of=None,
                                 fetched_at=fetched_at,gaps=['source_symbol_mismatch_or_missing'])
    items, row_gaps = _map_rows(rows, mapper)
    if symbol:
        items=[{**item,'symbol':symbol} for item in items]
    gaps = sorted(set(report_gaps + row_gaps))
    status = 'partial' if gaps else ('ok' if items else 'empty')
    date_field = 'date' if dataset != 'dividends' else 'notice_date'
    return _envelope(dataset, symbol, status=status, items=items,
                     as_of=_date_as_of(items, date_field), fetched_at=fetched_at, gaps=gaps)
