# ruff: noqa: RUF001 -- localized Chinese UI punctuation.
"""Read-only official demo API routes. Credential validation never echoes inputs."""
from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from . import demo_bots, demo_market, demo_service, demo_trading
from .demo_client import DemoAPIError
from .demo_store import DemoStoreError


def _same_origin(request: Request, response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        raise HTTPException(status_code=403, detail="请在看板页面内访问官方模拟账户")
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
            raise HTTPException(status_code=403, detail="官方模拟账户仅允许同源访问")


async def _call(fn: Callable, *args, **kwargs):
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except (DemoAPIError, DemoStoreError) as exc:
        raise HTTPException(status_code=400, detail=str(exc), headers={"Cache-Control": "no-store"}) from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(status_code=503, detail="官方模拟查询暂不可用，请检查网络和模拟账户配置",
                            headers={"Cache-Control": "no-store"}) from exc


def _data_dir(request: Request):
    return request.app.state.repo.store.data_dir


def build_router() -> APIRouter:
    router = APIRouter(prefix="/demo", dependencies=[Depends(_same_origin)])

    @router.get('/bots/okx')
    async def bots_status(request: Request):
        return await _call(demo_bots.status, _data_dir(request))

    @router.post('/bots/okx/presets')
    async def bots_presets(request: Request):
        return await _call(demo_bots.create_presets, _data_dir(request))

    @router.post('/bots/okx/enabled')
    async def bots_enabled(request: Request, body: demo_bots.EnabledRequest):
        return await _call(demo_bots.set_enabled, _data_dir(request), body)

    @router.post('/bots/okx/{identifier}/enabled')
    async def bot_enabled(request: Request, identifier: str, body: demo_bots.EnabledRequest):
        return await _call(demo_bots.set_enabled, _data_dir(request), body, identifier)

    @router.post('/bots/okx/{identifier}/reconcile')
    async def bot_reconcile(request: Request, identifier: str):
        return await _call(demo_bots.reconcile, _data_dir(request), identifier)

    @router.get('/trading/{exchange}')
    async def trading_status(request: Request, exchange: str):
        return await _call(demo_trading.status, _data_dir(request), exchange)

    @router.post('/trading/{exchange}/arm')
    async def trading_arm(request: Request, exchange: str, body: demo_trading.ArmRequest):
        return await _call(demo_trading.arm, _data_dir(request), exchange, body)

    @router.post('/trading/{exchange}/plan')
    async def trading_plan(request: Request, exchange: str, body: demo_trading.PlanRequest):
        return await _call(demo_trading.plan, _data_dir(request), exchange, body)

    @router.post('/trading/{exchange}/submit')
    async def trading_submit(request: Request, exchange: str, body: demo_trading.SubmitRequest):
        return await _call(demo_trading.submit, _data_dir(request), exchange, body)

    @router.post('/trading/{exchange}/reconcile/{client_id}')
    async def trading_reconcile(request: Request, exchange: str, client_id: str):
        return await _call(demo_trading.reconcile, _data_dir(request), exchange, client_id)

    @router.get("/accounts")
    async def accounts(request: Request):
        return await _call(demo_service.accounts, _data_dir(request))

    @router.post("/accounts/{exchange}/connect")
    async def connect(request: Request, exchange: str):
        # Avoid FastAPI's default validation error containing raw secret inputs.
        if request.headers.get("content-type", "").split(";")[0].lower() != "application/json":
            raise HTTPException(status_code=422, detail="连接配置需要JSON格式")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise HTTPException(status_code=413, detail="连接配置超过大小限制")
        try:
            body = demo_service.Connect.model_validate_json(raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="请完整填写模拟API Key、Secret、Passphrase及有效账户类型",
                                headers={"Cache-Control": "no-store"}) from exc
        return await _call(demo_service.connect, _data_dir(request), exchange, body)

    @router.get("/accounts/{exchange}")
    async def account(request: Request, exchange: str, refresh: bool = False):
        return await _call(demo_service.snapshot, _data_dir(request), exchange, refresh=refresh)

    @router.post("/accounts/{exchange}/disconnect")
    async def disconnect(request: Request, exchange: str):
        return await _call(demo_service.disconnect, _data_dir(request), exchange)

    @router.get("/market/{exchange}/{symbol}/candles")
    async def candles(exchange: str, symbol: str, interval: str = "1m", site: str = "global"):
        return await _call(demo_market.candles, exchange, symbol, interval, site)

    @router.get("/market/{exchange}/{symbol}/quote")
    async def quote(exchange: str, symbol: str, site: str = "global"):
        return await _call(demo_market.quote, exchange, symbol, site)

    return router
