# ruff: noqa: RUF001 -- Chinese series labels.
"""Public industry observations, independently adapted from Vibe-Research's catalog.

Reference: simonlin1212/Vibe-Research (MIT), industry.py / commodity.py.
These are observed series, not company purchase prices or trading signals.
"""
import contextlib
import math
from datetime import UTC, date, datetime, timedelta

from .http import get_json

FARM = 'https://500.farm/vastai/grafana.v2/api/datasources/proxy/uid/EdgV2xcnz/api/v1/query_range'
MEMORY = 'https://raw.githubusercontent.com/nlee756525/dram-prices/main/history.json'
TW = {'2383':'台光电子','6274':'台燿','2368':'金像电','3081':'联亚光电'}


def number(value):
    try:
        value = None if isinstance(value,bool) else float(value)
    except (TypeError,ValueError):
        return None
    return value if value is not None and math.isfinite(value) and value > 0 else None


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def fetch(dataset: str) -> dict:
    now = datetime.now(UTC)
    today = (now+timedelta(hours=8)).date()
    items, gaps = [], []
    if dataset == 'gpu_rent':
        source = '500.farm / Vast.ai'
        guard = '可租挂单按机型切片统计后聚合的中位租金，非逐张挂单精确中位，非租用成交价。'
        for gpu in ('B200','H100 SXM','A100 SXM4'):
            try:
                body = get_json(FARM,{'query':f'quantile(0.5, vastai_v2_ondemand_price_median_dollars{{gpu_name="{gpu}", rented="no"}})',
                    'start':int(now.timestamp())-180*86400,'end':int(now.timestamp()),'step':86400})
                rows = (body.get('data') or {}).get('result') or []
                points = []
                for ts,val in (rows[0].get('values',[]) if rows else []):
                    n = number(val)
                    if n is not None and 0 < float(ts) <= now.timestamp():
                        points.append({'date':datetime.fromtimestamp(float(ts),UTC).date().isoformat(),'value':n})
                points = sorted({p['date']:p for p in points}.values(),key=lambda p:p['date'])[-181:]
                if points:
                    items.append({'name':gpu,'unit':'USD/卡时','series':points,'value':points[-1]['value'],'date':points[-1]['date']})
                else:
                    gaps.append(f'{gpu}：暂无有效采样')
            except (ValueError,TypeError,KeyError,IndexError):
                gaps.append(f'{gpu}：统计站暂不可用')
    elif dataset == 'tw_revenue':
        source = 'FinMind / 台湾月营收公开资料'
        guard = '月营收为公司披露的历史经营数据，非未来收入预测；新月份可能尚未披露。'
        for symbol,name in TW.items():
            try:
                body = get_json('https://api.finmindtrade.com/api/v4/data',{'dataset':'TaiwanStockMonthRevenue',
                    'data_id':symbol,'start_date':(today-timedelta(days=730)).isoformat()})
                points = []
                for row in body.get('data') or []:
                    if str(row.get('stock_id')) != symbol:
                        raise ValueError('wrong identity')
                    # Revenue month is not the publication date stored in date.
                    published = _day(row.get('date'))
                    month = date(int(row['revenue_year']),int(row['revenue_month']),1)
                    value = number(row.get('revenue'))
                    if published and published <= today and month <= today and value is not None:
                        points.append({'date':month.isoformat(),'published_at':published.isoformat(),'value':value})
                points = sorted({p['date']:p for p in points}.values(),key=lambda p:p['date'])[-24:]
                if points:
                    items.append({'name':name,'symbol':symbol,'unit':'TWD','series':points,'value':points[-1]['value'],'date':points[-1]['date']})
                else:
                    gaps.append(f'{name}：暂无有效营收或免费配额受限')
            except (ValueError,TypeError,KeyError):
                gaps.append(f'{name}：取数失败或配额受限')
    elif dataset == 'memory_prices':
        source = 'GitHub 社区 DRAMeXchange 转录'
        guard = '社区存档可能转录错误或停更；DRAM 是存储景气参考，不是 HBM 现货价格。'
        body = get_json(MEMORY)
        for key,label,field in [('ddr5','DDR5','session_avg'),('tlc','NAND TLC','session_avg')]:
            points = []
            for row in body.get(key) or []:
                day = _day(row.get('date'))
                if day is None:
                    with contextlib.suppress(ValueError):
                        day = datetime.strptime(str(row.get('date')),'%m/%d/%Y').date()
                raw_value = row.get(field)
                value = number(raw_value.replace('$','').replace(',','') if isinstance(raw_value,str) else raw_value)
                if day and day <= today and value is not None:
                    points.append({'date':day.isoformat(),'value':value})
            points = sorted({p['date']:p for p in points}.values(),key=lambda p:p['date'])[-365:]
            if points:
                items.append({'name':label,'unit':'USD/颗','series':points,'value':points[-1]['value'],'date':points[-1]['date'],
                              'stale':(today-date.fromisoformat(points[-1]['date'])).days>14})
            else:
                gaps.append(f'{label}：未找到有效序列')
    else:
        raise ValueError('未知产业资料类型')
    return {'dataset':dataset,'status':'partial' if gaps else ('ok' if items else 'empty'),
            'items':items,'gaps':gaps,'source':source,'guard':guard,'fetched_at':now.isoformat(),
            'as_of':max((r['date'] for r in items),default=None)}
