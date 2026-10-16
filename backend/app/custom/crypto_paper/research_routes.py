"""Bounded local strategy research API, separated from account/order writes."""
# ruff: noqa: RUF001 -- localized errors.
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import research_service as service


def build_router() -> APIRouter:
    router = APIRouter(prefix='/research')

    async def call(fn, *args):
        try:
            return await run_in_threadpool(fn, *args)
        except service.ResearchBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, '研究记录或账户不存在') from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise HTTPException(503, '研究数据暂不可用，已有记录保留') from exc

    def data_dir(request):
        return request.app.state.repo.store.data_dir

    @router.post('/backtest')
    async def backtest(request: Request, body: service.BacktestRequest):
        return await call(service.backtest, data_dir(request), body)

    @router.get('/history')
    async def history(request: Request):
        return await call(service.history, data_dir(request))

    @router.get('/runs/{run_id}')
    async def detail(request: Request, run_id: str):
        return await call(service.run_detail, data_dir(request), run_id)

    @router.get('/accounts/{account_id}/decisions')
    async def decisions(request: Request, account_id: str):
        return await call(service.decisions, data_dir(request), account_id)

    @router.get('/reviews')
    async def reviews(request: Request):
        return await call(service.reviews, data_dir(request))

    @router.post('/review')
    async def review(request: Request):
        return await call(service.review, data_dir(request))

    @router.post('/review-ai')
    async def review_ai(request: Request):
        try:
            return await service.review_ai(data_dir(request))
        except service.ResearchBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, 'AI复盘暂不可用，已有统计保留') from exc

    return router
