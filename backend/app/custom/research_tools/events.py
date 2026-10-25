# ruff: noqa: RUF001 -- Event labels.
"""Publication dates and estimated schedules, never assumed trade dates."""
import re
from datetime import UTC, date, datetime, timedelta

from .http import get_json

EM = 'https://datacenter-web.eastmoney.com/api/data/v1/get'


def day(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def fetch(market: str, symbol: str, *, asset_type: str = 'stock') -> dict:
    items,gaps = [],[]
    now = datetime.now(UTC)
    today = (now+timedelta(hours=8)).date()
    if market == 'cn':
        if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',symbol):
            raise ValueError('A股代码无效')
        source = '东方财富公开披露资料'
        if asset_type != 'stock':
            gaps.append('本事件接口覆盖股票；ETF/指数的分红或份额变更未覆盖，不代表没有事件。')
        else:
            for report,kind,datefield,titlefield in [
                ('RPT_PUBLIC_BS_APPOIN','财报预约','APPOINT_PUBLISH_DATE','REPORT_DATE'),
                ('RPT_SHAREBONUS_DET','除权除息','EX_DIVIDEND_DATE','PLAN_EXPLAIN')]:
                try:
                    body = get_json(EM,{'reportName':report,'columns':'ALL','filter':f'(SECURITY_CODE="{symbol[:6]}")',
                        'pageSize':30,'pageNumber':1,'sortColumns':datefield,'sortTypes':'-1'})
                    rows = (body.get('result') or {}).get('data')
                    if rows is None:
                        raise ValueError('missing rows')
                    for row in rows:
                        if str(row.get('SECURITY_CODE')) != symbol[:6]:
                            raise ValueError('wrong identity')
                        eventday = day(row.get(datefield))
                        if eventday:
                            items.append({'date':eventday,'kind':kind,'title':str(row.get(titlefield) or kind)[:240],
                                          'estimated':kind=='财报预约','symbol':symbol})
                except (ValueError,TypeError,KeyError):
                    gaps.append(f'{kind}：本次未获得有效资料')
    elif market == 'us':
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',symbol):
            raise ValueError('美股代码无效')
        source = 'Nasdaq 业绩日期公告'
        body = get_json(f'https://api.nasdaq.com/api/analyst/{symbol}/earnings-date',headers={
            'User-Agent':'Mozilla/5.0','Accept':'application/json','Origin':'https://www.nasdaq.com'})
        text = str((body.get('data') or {}).get('reportText') or (body.get('data') or {}).get('announcement') or '')
        # Nasdaq prose includes expected dates; keep the estimated flag even
        # when the announced prose happens to use a stronger verb.
        match = re.search(r'(?:report|announce|post|release)[^.]{0,80}?\bon\s+(\d{1,2})/(\d{1,2})/(\d{4})',text,re.I)
        if match:
            try:
                month,dom,year = map(int,match.groups())
                eventday = date(year,month,dom)
                if abs((eventday-today).days)<=366:
                    items.append({'date':eventday.isoformat(),'kind':'财报预计','title':text[:400],'estimated':True,'symbol':symbol})
            except ValueError:
                pass
        if not items:
            gaps.append('Nasdaq 未给出可核验的财报预计日期；不代表近期没有财报。')
    elif market == 'hk':
        from . import hk_announcements, hk_sources
        data = hk_sources.fetch('dividends',symbol)
        source = data.get('source','港股公开派息资料')
        for row in data.get('items',[]):
            eventday = day(row.get('ex_date') or row.get('EX_DIVIDEND_DATE') or row.get('date') or row.get('除净日'))
            if eventday:
                items.append({'date':eventday,'kind':'派息除净','title':str(row.get('plan_text') or '派息方案详见原始记录')[:400],
                              'estimated':False,'symbol':symbol})
        gaps.extend(data.get('gaps',[]))
        try:
            announcements=hk_announcements.fetch(symbol)
            items.extend(announcements['items'])
            gaps.extend(announcements['gaps'])
            source+=' / 港交所披露易'
        except ValueError:
            gaps.append('港交所业绩公告本次未取得，已保留其他已获得资料。')
        gaps.append('港交所记录为历史已发布业绩；不能据此推断下一次财报的预约日期。')
    else:
        raise ValueError('未知市场')
    return {'dataset':'events','market':market,'symbol':symbol,'items':sorted(items,key=lambda r:r['date'],reverse=True),
            'status':'partial' if gaps else ('ok' if items else 'empty'),'gaps':gaps,
            'source':source,'as_of':today.isoformat(),'fetched_at':now.isoformat(),
            'guard':'预约/预计日期可能调整；日历日期不是确认交易日，不用于自动下单或历史信号确认。'}
