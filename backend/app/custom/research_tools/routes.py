# ruff: noqa: RUF001 -- Localized API errors.
"""Authenticated extension routes; importing or visiting performs no external fetch."""
import base64
import binascii
import json
import re

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from . import cache, events, industry

DATASETS = {'gpu_rent','tw_revenue','memory_prices','southbound_flow','southbound_holding','dividends','ah_premium','events'}


def _query(dataset,market,symbol,repo):
    if dataset not in DATASETS:
        raise ValueError('未知研究资料类型')
    if market not in {'cn','hk','us'}:
        raise ValueError('市场无效')
    if dataset in {'southbound_flow','southbound_holding','dividends','ah_premium'} and market!='hk':
        raise ValueError('该数据集仅覆盖港股')
    if dataset in {'gpu_rent','tw_revenue','memory_prices','southbound_flow'}:
        symbol = ''
    elif dataset != 'events' or market=='hk':
        if not re.fullmatch(r'\d{5}\.HK',symbol):
            raise ValueError('请选取港股五位代码，例如00700.HK')
    elif market=='cn' and not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',symbol):
        raise ValueError('A股代码无效')
    elif market=='us' and not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',symbol):
        raise ValueError('美股代码无效')
    def load():
        if dataset in {'gpu_rent','tw_revenue','memory_prices'}:
            return industry.fetch(dataset)
        if dataset=='events':
            asset = repo.resolve_asset_type(symbol) if market=='cn' else 'stock'
            return events.fetch(market,symbol,asset_type=asset)
        from . import hk_sources
        return hk_sources.fetch(dataset,symbol or None)
    return f'{dataset}:{market}:{symbol}',load


def build_router():
    router = APIRouter(prefix='/api/custom/research-tools',tags=['research-tools'])

    @router.get('/snapshot')
    def snapshot(request: Request,dataset: str,market: str='cn',symbol: str=Query('',max_length=20)):
        try:
            key,_ = _query(dataset,market,symbol,request.app.state.repo)
            return cache.state(request.app.state.repo.store.data_dir,key)
        except ValueError as error:
            raise HTTPException(422,str(error)) from None

    @router.post('/refresh')
    async def refresh(request: Request,dataset: str,market: str='cn',symbol: str=Query('',max_length=20)):
        try:
            key,loader = _query(dataset,market,symbol,request.app.state.repo)
            return await cache.refresh(request.app.state.repo.store.data_dir,key,loader)
        except ValueError as error:
            raise HTTPException(422,str(error)) from None

    @router.get('/jobs')
    def jobs():
        return {'running':cache.active_jobs()}

    @router.get('/portfolio')
    def portfolio(request: Request,account: str=Query('default',max_length=64)):
        from .service import paper_xray
        try:
            return paper_xray(request.app.state.repo,request.app.state.repo.store.data_dir,account)
        except ValueError as error:
            raise HTTPException(422,str(error)) from None

    @router.get('/library')
    def library_list(request: Request):
        from .library import list_reports
        try:
            return list_reports(request.app.state.repo.store.data_dir)
        except ValueError as error:
            raise HTTPException(422,str(error)) from None

    @router.get('/library/search')
    def library_search(request: Request,q: str=Query(...,min_length=1,max_length=120)):
        from .library import search_reports
        try:
            return search_reports(request.app.state.repo.store.data_dir,q)
        except ValueError as error:
            raise HTTPException(422,str(error)) from None

    @router.post('/library')
    async def library_upload(request: Request):
        from .library import add_report
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>14_100_000:
                raise HTTPException(413,'资料文件不得超过10MiB')
        try:
            body = json.loads(raw)
            if not isinstance(body,dict) or set(body)!={'name','content'} or not isinstance(body['name'],str) or not isinstance(body['content'],str):
                raise ValueError('invalid upload')
            content = base64.b64decode(body['content'],validate=True)
        except (ValueError,TypeError,binascii.Error):
            raise HTTPException(422,'资料上传格式无效') from None
        try:
            result = await run_in_threadpool(add_report,request.app.state.repo.store.data_dir,body['name'],content)
            return {**result['item'],'duplicate':result['duplicate']}
        except ValueError as error:
            raise HTTPException(422,str(error)) from None
    return router
