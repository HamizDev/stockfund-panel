



import asyncio
import time

import pytest

from app.custom.research_tools import cache


@pytest.mark.asyncio
async def test_get_is_read_only_refresh_coalesces_and_reload_keeps_snapshot(tmp_path):
    assert cache.state(tmp_path,'x')['status']=='idle'
    assert not (tmp_path/'research_tools').exists()
    calls=[]
    def load():
        calls.append(1)
        time.sleep(.02)
        return {'items':[{'value':1}]}
    await cache.refresh(tmp_path,'x',load)
    await cache.refresh(tmp_path,'x',load)
    assert cache.state(tmp_path,'x')['status']=='loading'
    await asyncio.gather(*list(cache._jobs.values()))
    assert calls==[1]
    result=cache.state(tmp_path,'x')
    assert result['snapshot']['items'][0]['value']==1 and not result['stale']
    assert cache.state(tmp_path,'other')['snapshot'] is None


@pytest.mark.asyncio
async def test_failed_refresh_keeps_old_snapshot_and_hides_upstream_body(tmp_path):
    await cache.refresh(tmp_path,'f',lambda:{'items':[{'value':0}]})
    await asyncio.gather(*list(cache._jobs.values()))
    def fail():
        raise ValueError('synthetic-private-token')
    await cache.refresh(tmp_path,'f',fail)
    await asyncio.gather(*list(cache._jobs.values()))
    result=cache.state(tmp_path,'f')
    assert result['snapshot']['items'][0]['value']==0
    assert result['error'] and 'synthetic-private-token' not in str(result)


@pytest.mark.asyncio
async def test_failed_adapter_envelope_keeps_old_values_but_legitimate_empty_replaces(tmp_path):
    key='source-envelope'
    await cache.refresh(tmp_path,key,lambda:{'status':'ok','items':[{'value':3}]})
    await asyncio.gather(*list(cache._jobs.values()))
    await cache.refresh(tmp_path,key,lambda:{'status':'partial','items':[],'gaps':['source_unavailable']})
    await asyncio.gather(*list(cache._jobs.values()))
    result=cache.state(tmp_path,key)
    assert result['snapshot']['items']==[{'value':3}] and result['error'] and result['stale']
    cache._errors.pop((str(tmp_path.resolve()),key))
    await cache.refresh(tmp_path,key,lambda:{'status':'empty','items':[]})
    await asyncio.gather(*list(cache._jobs.values()))
    assert cache.state(tmp_path,key)['snapshot']['status']=='empty'


@pytest.mark.asyncio
async def test_partial_industry_retains_failed_series_with_explicit_old_marker(tmp_path):
    await cache.refresh(tmp_path,'series',lambda:{'dataset':'gpu_rent','status':'ok','items':[
        {'name':'B200','date':'2026-10-23','series':[{'value':2}]},{'name':'H100','date':'2026-10-23','series':[{'value':1}]}]})
    await asyncio.gather(*list(cache._jobs.values()))
    await cache.refresh(tmp_path,'series',lambda:{'dataset':'gpu_rent','status':'partial','items':[
        {'name':'B200','date':'2026-10-24','series':[{'value':3}]}]})
    await asyncio.gather(*list(cache._jobs.values()))
    items=cache.state(tmp_path,'series')['snapshot']['items']
    assert items[1]['name']=='H100' and items[1]['date']=='2026-10-23' and items[1]['stale'] and items[1]['previous_snapshot']
