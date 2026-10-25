




import pytest

from app.custom.research_tools import events, hk_announcements, industry, routes


def test_memory_schema_and_stale_observation(monkeypatch):
    monkeypatch.setattr(industry,'get_json',lambda *_:{'ddr5':[{'date':'2020-01-02','session_avg':1.2}],
        'tlc':[{'date':'2020-01-02','session_avg':'NaN'}]})
    result=industry.fetch('memory_prices')
    assert result['status']=='partial' and len(result['items'])==1
    assert result['items'][0]['value']==1.2 and result['items'][0]['stale']
    assert '不是 HBM' in result['guard']


def test_tw_revenue_month_is_not_publication_date(monkeypatch):
    def request(_url,params):
        return {'data':[{'stock_id':params['data_id'],'date':'2026-09-10','revenue_year':2026,'revenue_month':8,'revenue':123}]}
    monkeypatch.setattr(industry,'get_json',request)
    result=industry.fetch('tw_revenue')
    assert len(result['items'])==4
    row=result['items'][0]['series'][0]
    assert row['date']=='2026-08-01' and row['published_at']=='2026-09-10'
    assert result['items'][0]['unit']=='TWD'


def test_memory_community_dollar_and_us_date_format(monkeypatch):
    monkeypatch.setattr(industry,'get_json',lambda *_:{'ddr5':[{'date':'5/11/2026','session_avg':'$39.000'}],
                                                    'tlc':[{'date':'5/11/2026','session_avg':'$2.500'}]})
    result=industry.fetch('memory_prices')
    assert result['items'][0]['date']=='2026-05-11' and result['items'][0]['value']==39.0


def test_nasdaq_estimate_uses_report_text_not_heading(monkeypatch):
    monkeypatch.setattr(events,'get_json',lambda *_a,**_k:{'data':{'reportText':'Company is expected* to report earnings on 11/02/2026 after market close.',
                                                              'announcement':'Earnings announcement* for AAPL: Nov 02, 2026'}})
    assert events.fetch('us','AAPL')['items'][0]['date']=='2026-11-02'
    assert events.fetch('us','AAPL')['items'][0]['estimated'] is True


def test_hkex_exact_identity_publication_and_dedup(monkeypatch):
    def request(url,params,**_kwargs):
        if url.endswith('prefix.do'):
            return {'stockInfo':[{'code':'00700','stockId':123},{'code':'00701','stockId':124}]}
        assert params['stockId']=='123'
        return {'result':'[{"NEWS_ID":"x1","STOCK_CODE":"00700","DATE_TIME":"10/09/2026 16:30","TITLE":"中期业绩"}]','hasNextRow':False}
    monkeypatch.setattr(hk_announcements,'get_json',request)
    result=hk_announcements.fetch('00700.HK')
    assert len(result['items'])==1
    assert result['items'][0]['published_at']=='2026-09-10T16:30:00+08:00'
    assert result['items'][0]['estimated'] is False


def test_hkex_refuses_another_security(monkeypatch):
    monkeypatch.setattr(hk_announcements,'get_json',lambda *_a,**_k:{'stockInfo':[{'code':'00941','stockId':100}]})
    with pytest.raises(ValueError):
        hk_announcements.fetch('00700.HK')


@pytest.mark.parametrize('lookup',[None,[],{'stockInfo':None},{'stockInfo':{}},{'stockInfo':[None]}])
def test_hkex_malformed_lookup_is_safe_value_error(monkeypatch,lookup):
    monkeypatch.setattr(hk_announcements,'get_json',lambda *_a,**_k:lookup)
    with pytest.raises(ValueError):
        hk_announcements.fetch('00700.HK')


def test_hkex_rejects_unbound_result_rows(monkeypatch):
    def request(url,*_a,**_k):
        if url.endswith('prefix.do'):
            return {'stockInfo':[{'code':'00700','stockId':123}]}
        return {'result':[{'NEWS_ID':'x1','STOCK_CODE':'00941','DATE_TIME':'10/09/2026 16:30'},
                          {'NEWS_ID':'x2','DATE_TIME':'10/09/2026 16:30'}]}
    monkeypatch.setattr(hk_announcements,'get_json',request)
    result=hk_announcements.fetch('00700.HK')
    assert not result['items'] and result['gaps']


def test_fund_events_do_not_query_company_financial_schedules(monkeypatch):
    monkeypatch.setattr(events,'get_json',lambda *_:pytest.fail('ETF must not use company calendar'))
    result=events.fetch('cn','515880.SH',asset_type='etf')
    assert not result['items'] and result['gaps']


def test_cn_schedule_preserves_estimate_and_rejects_wrong_security(monkeypatch):
    def request(_url,params):
        if params['reportName']=='RPT_PUBLIC_BS_APPOIN':
            return {'result':{'data':[{'SECURITY_CODE':'600519','APPOINT_PUBLISH_DATE':'2026-10-30','REPORT_DATE':'2026-09-30'}]}}
        return {'result':{'data':[{'SECURITY_CODE':'600000','EX_DIVIDEND_DATE':'2026-10-30'}]}}
    monkeypatch.setattr(events,'get_json',request)
    result=events.fetch('cn','600519.SH')
    assert len(result['items'])==1 and result['items'][0]['estimated']
    assert result['status']=='partial'


def test_market_and_symbol_boundaries():
    with pytest.raises(ValueError):
        routes._query('southbound_flow','us','',None)
    with pytest.raises(ValueError):
        routes._query('dividends','hk','600519.SH',None)
    assert routes._query('events','us','AAPL',None)[0]=='events:us:AAPL'
