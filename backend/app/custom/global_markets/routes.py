"""Authenticated read-only extension API; upstream failures never expose raw bodies."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from . import etf_shares, financials, industries, keyed_sources, macro, overview, service
from .catalog import MarketDataError
from .resources import RESOURCES


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/custom/global-markets", tags=["global-markets"])

    @router.get('/financials')
    def company_financials(market: Literal['hk', 'us'],
                           symbol: str = Query(..., min_length=1, max_length=20),
                           table: Literal['metrics', 'income', 'balance', 'cashflow'] = 'metrics') -> dict:
        try:
            return financials.get(market, symbol, table)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except MarketDataError as error:
            raise HTTPException(503, str(error)) from None

    @router.get('/research-sources')
    def research_sources() -> dict:
        return keyed_sources.status()

    @router.post('/research-sources/{provider}')
    async def configure_research_source(provider: Literal['finnhub', 'adanos'], request: Request) -> dict:
        # Parse the secret manually so validation errors never echo its input.
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, 'API Key 请求格式无效') from None
        if not isinstance(body, dict) or set(body) != {'api_key'}:
            raise HTTPException(422, '请仅填写 API Key')
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(keyed_sources.configure, provider, body.get('api_key'))
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except MarketDataError as error:
            raise HTTPException(400, str(error)) from None

    @router.delete('/research-sources/{provider}')
    def remove_research_source(provider: Literal['finnhub', 'adanos']) -> dict:
        try:
            return keyed_sources.remove(provider)
        except MarketDataError as error:
            raise HTTPException(400, str(error)) from None

    @router.get('/research-supplement')
    def research_supplement(symbol: str = Query(..., min_length=1, max_length=20)) -> dict:
        try:
            return keyed_sources.supplement(symbol)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except MarketDataError as error:
            raise HTTPException(400, str(error)) from None

    @router.post('/research-sentiment')
    def research_sentiment(symbol: str = Query(..., min_length=1, max_length=20),
                           source: Literal['reddit', 'news', 'x', 'polymarket'] = 'news') -> dict:
        try:
            return keyed_sources.sentiment(symbol, source)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except MarketDataError as error:
            raise HTTPException(400, str(error)) from None

    @router.get('/etf-shares')
    def official_etf_shares(symbol: str = Query(..., max_length=12), date: str | None = None) -> dict:
        try:
            return etf_shares.get(symbol, date)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/macro')
    def macro_reference(market: Literal['cn', 'us']) -> dict:
        try:
            return macro.get(market)
        except MarketDataError as error:
            raise HTTPException(503, str(error)) from error

    @router.get('/industries')
    def global_industries(request: Request, mode: Literal['auto', 'cn', 'us'] = 'auto') -> dict:
        return industries.get(mode, request.app.state.repo)

    @router.get('/overview')
    def market_overview(market: Literal['hk', 'us']) -> dict:
        return overview.get(market)

    @router.get("/catalog")
    def catalog(market: Literal["hk", "us"], q: str = Query("", max_length=60)) -> dict:
        try:
            return service.catalog(market, q)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/chart")
    def chart(market: Literal["hk", "us"], symbol: str = Query(..., min_length=1, max_length=20),
              interval: Literal["1d", "5m", "1m"] = "1d") -> dict:
        try:
            return service.chart(market, symbol, interval)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except MarketDataError as error:
            raise HTTPException(503, str(error)) from error

    @router.get("/resources")
    def resources() -> dict:
        return {"items": RESOURCES}

    return router
