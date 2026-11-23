# ruff: noqa: RUF001 -- Public-data descriptions.
"""Raw daily fallback and sampled trade prints, informed by a-stock-data.

Endpoint/field reference: simonlin1212/a-stock-data, SKILL.md sections 1.2/1.4,
Apache-2.0. No source rotation or requests to unavailable mootdx quote services.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timedelta

import polars as pl

from app.market_time import CN_TZ, cn_now
from app.services.public_market_http import PUBLIC_HTTP

DAILY_URL = 'https://ifzq.gtimg.cn/appstock/app/fqkline/get'
TICKS_URL = 'https://stock.gtimg.cn/data/index.php'
QUOTE_URL = 'https://qt.gtimg.cn/q='


def symbol_code(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(SH|SZ)', symbol):
        raise ValueError('仅支持显式沪深股票或ETF代码')
    code, suffix = symbol.split('.')
    if (suffix == 'SH' and code.startswith('000')) or (suffix == 'SZ' and code.startswith('399')):
        raise ValueError('本研究入口不支持指数')
    return suffix.lower() + code


def number(value, *, positive=False):
    if isinstance(value, bool):
        raise ValueError('行情数值无效')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('行情数值无效') from exc
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError('行情数值无效')
    return result


def parse_daily(payload, symbol, first, last):
    code = symbol_code(symbol)
    data = payload.get('data') if isinstance(payload, dict) else None
    node = data.get(code) if isinstance(data, dict) else None
    if not isinstance(node, dict) or not isinstance(node.get('day'), list):
        raise ValueError('原始日K字段缺失，不能使用复权数据代替')
    rows, seen = [], set()
    for item in node['day']:
        if not isinstance(item, list) or len(item) < 6:
            raise ValueError('原始日K字段不完整')
        if not isinstance(item[0], str):
            raise ValueError('原始日K日期无效')
        day = date.fromisoformat(item[0])
        if not first <= day <= last or day in seen:
            raise ValueError('原始日K日期越界或重复')
        seen.add(day)
        op, cl, hi, lo = [number(v, positive=True) for v in item[1:5]]
        if hi < max(op, cl) or lo > min(op, cl):
            raise ValueError('原始日K价格关系无效')
        # Tencent STAR-board K volume is shares; the panel canonical is hands.
        volume = number(item[5]) / (100 if symbol.startswith(('688', '689')) else 1)
        rows.append(dict(symbol=symbol, date=day, open=op, high=hi, low=lo, close=cl,
                         volume=volume, amount=None))
    return rows


def daily(symbols, start_time, end_time, asset_type='stock', on_chunk_done=None):
    if asset_type not in {'stock', 'etf'} or not 1 <= len(symbols) <= 20:
        raise ValueError('备用日K每次支持1至20只沪深股票/ETF')
    last = (end_time.astimezone(CN_TZ) if end_time.tzinfo else end_time).date() if end_time else cn_now().date()
    first = ((start_time.astimezone(CN_TZ) if start_time.tzinfo else start_time).date()
             if start_time else last - timedelta(days=365))
    if first > last or (last - first).days > 1460:
        raise ValueError('备用日K单次范围最多四年')
    rows = []
    for index, symbol in enumerate(symbols):
        code, cursor = symbol_code(symbol), first
        while cursor <= last:
            stop = min(last, cursor + timedelta(days=549))
            payload = json.loads(PUBLIC_HTTP.text(DAILY_URL,
                {'param': f'{code},day,{cursor},{stop},640,'}, ttl=300))
            rows.extend(parse_daily(payload, symbol, cursor, stop))
            cursor = stop + timedelta(days=1)
        if on_chunk_done:
            on_chunk_done(index + 1, len(symbols))
    return pl.DataFrame(rows).with_columns(pl.col('amount').cast(pl.Float64)) if rows else pl.DataFrame()


def snapshot(text, code):
    match = re.search(r'v_' + re.escape(code) + r'="([^"]*)"', text)
    fields = match.group(1).split('~') if match else []
    if len(fields) <= 35 or not re.fullmatch(r'\d{14}', fields[30]):
        raise ValueError('分笔日期快照不可核验')
    stamp = datetime.strptime(fields[30], '%Y%m%d%H%M%S')
    parts = fields[35].split('/')
    if len(parts) != 3:
        raise ValueError('分笔成交额快照不可核验')
    return stamp, number(parts[2])


def parse_print_page(text, code, page):
    if not text.strip():
        return []
    match = re.fullmatch(r'\s*v_detail_data_' + re.escape(code) + r'=\[(\d+),"([^"]*)"\];?\s*', text)
    if not match or int(match.group(1)) != page:
        raise ValueError('分笔页码或结构不匹配')
    result = []
    for item in filter(None, match.group(2).split('|')):
        seq, clock, price, _change, volume, amount, side = item.split('/')
        if not re.fullmatch(r'\d{2}:\d{2}:\d{2}', clock):
            raise ValueError('分笔时间格式无效')
        time.fromisoformat(clock)
        if not seq.isdigit() or side not in {'B', 'S', 'M'}:
            raise ValueError('分笔序号或方向无效')
        result.append(dict(seq=int(seq), time=clock, price=number(price, positive=True),
                           volume=number(volume), amount=number(amount), side=side))
        if len(result) > 70:
            raise ValueError('分笔单页记录超出70条限制')
    return result


def trade_prints(symbol, *, max_pages=3):
    if type(max_pages) is not int or not 1 <= max_pages <= 3:
        raise ValueError('分笔研究每次最多3页，避免全天高频翻页')
    code = symbol_code(symbol)
    stamp, total_amount = snapshot(PUBLIC_HTTP.text(QUOTE_URL + code, ttl=30, encoding='gbk'), code)
    rows, terminal = [], False
    for page in range(max_pages):
        batch = parse_print_page(PUBLIC_HTTP.text(TICKS_URL,
            {'appn': 'detail', 'action': 'data', 'c': code, 'p': page}, ttl=60, encoding='gbk'), code, page)
        if not batch:
            terminal = True
            break
        for row in batch:
            expected = rows[-1]['seq'] + 1 if rows else 0
            if row['seq'] != expected or (rows and row['time'] < rows[-1]['time']):
                raise ValueError('分笔序号或时间不连续，结果未采用')
            rows.append(row)
    stamp_after, amount_after = snapshot(PUBLIC_HTTP.text(QUOTE_URL + code, ttl=0, encoding='gbk'), code)
    if stamp_after.date() != stamp.date():
        raise ValueError('分笔抓取期间交易日期发生变化')
    session_amount = sum(row['amount'] for row in rows if row['time'] <= '15:00:59')
    session_verified = bool(terminal and total_amount == amount_after and rows
                            and abs(session_amount - amount_after) <= amount_after * .001 + 1000)
    return dict(symbol=symbol, source='Tencent public trade prints', date=stamp.date().isoformat(),
                quote_as_of=stamp_after.isoformat(), retrieved_at=cn_now().isoformat(), timezone='Asia/Shanghai',
                level='sampled_trade_prints', is_level2=False, volume_unit='hands', amount_unit='CNY',
                complete=False, session_amount_verified=session_verified, pages_budget=max_pages,
                selection='session_start_sample',
                truncated=not terminal, rows=rows,
                note='普通约3秒合并分笔，非交易所Level-2；按需最多3页开盘起始片段，不能据此推断全天净流入。')
