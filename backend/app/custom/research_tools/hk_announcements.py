# ruff: noqa: RUF001 -- HKEX disclosure labels.
"""HKEX title-search schema adapted from Vibe-Research (MIT).

Only actual publication timestamps are returned. Future results schedules
are not inferred from a previous year's publication date.
"""
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .http import get_json

BASE='https://www1.hkexnews.hk'


def fetch(symbol: str) -> dict:
    if not re.fullmatch(r'\d{5}\.HK',symbol):
        raise ValueError('港股代码无效')
    lookup=get_json(BASE+'/search/prefix.do',{'callback':'callback','lang':'ZH','type':'A','name':symbol[:5],'market':'SEHK'},jsonp=True)
    stock_info=lookup.get('stockInfo') if isinstance(lookup,dict) else None
    if not isinstance(stock_info,list):
        raise ValueError('披露易未确认该证券身份')
    matches=[row for row in stock_info if isinstance(row,dict) and str(row.get('code'))==symbol[:5]]
    if len(matches)!=1 or not isinstance(matches[0].get('stockId'),int) or isinstance(matches[0].get('stockId'),bool):
        raise ValueError('披露易未确认该证券身份')
    now=datetime.now(ZoneInfo('Asia/Hong_Kong'))
    items,gaps,seen=[],[],set()
    for category,label in [('13300','末期业绩'),('13400','中期业绩'),('13600','季度业绩')]:
        try:
            body=get_json(BASE+'/search/titleSearchServlet.do',{'sortDir':'0','sortByOptions':'DateTime','category':'0','market':'SEHK',
                'stockId':str(matches[0]['stockId']),'documentType':'-1','fromDate':(now-timedelta(days=730)).strftime('%Y%m%d'),
                'toDate':now.strftime('%Y%m%d'),'title':'','searchType':'1','t1code':'10000','t2Gcode':'-2',
                't2code':category,'rowRange':'50','lang':'zh'})
            raw=body.get('result')
            rows=json.loads(raw) if isinstance(raw,str) and raw.strip() else raw
            if rows is None and body.get('recordCnt')==0:
                rows=[]
            if not isinstance(rows,list):
                raise ValueError('Invalid results envelope')
            if body.get('hasNextRow'):
                gaps.append(f'{label}：仅显示最近50条')
            for row in rows[:50]:
                if not isinstance(row,dict) or symbol[:5] not in re.findall(r'(?<!\d)\d{5}(?!\d)',str(row.get('STOCK_CODE') or '')):
                    gaps.append(f'{label}：公告证券代码缺失或不匹配，已排除')
                    continue
                when=datetime.strptime(str(row.get('DATE_TIME') or ''),'%d/%m/%Y %H:%M').replace(tzinfo=now.tzinfo)
                if when>now:
                    gaps.append(f'{label}：排除了未来发布时间')
                    continue
                identifier=str(row.get('NEWS_ID') or '')
                if not identifier or identifier in seen:
                    continue
                seen.add(identifier)
                items.append({'date':when.date().isoformat(),'published_at':when.isoformat(),'kind':label,
                              'title':str(row.get('TITLE') or label)[:500],'estimated':False,'symbol':symbol})
        except (ValueError,TypeError,KeyError,AttributeError):
            gaps.append(f'{label}：未获得完整公告资料')
    return {'items':sorted(items,key=lambda r:r['published_at'],reverse=True),'gaps':gaps,'source':'港交所披露易'}
