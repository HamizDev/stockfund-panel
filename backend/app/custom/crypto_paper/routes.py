"""Research-only Bitget strategies; legacy Binance ledgers are read-only."""
# ruff: noqa: RUF001 -- localized Chinese UI messages use Chinese punctuation.
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.custom.crypto_paper import auto, bitget, ledger, market, strategy_draft
from app.custom.crypto_paper.public_stream import stream

logger = logging.getLogger(__name__)


class PaperOrder(BaseModel):
    market: str
    symbol: str
    action: str
    quantity: str
    leverage: int = Field(default=1, ge=1, le=20)
    request_id: str = Field(min_length=1, max_length=80)


class StrategyAccount(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    exchange: str = "bitget"
    market: str = "usdm"
    symbol: str
    strategy_id: str
    strategy_params: dict[str, int] | None = None
    interval: str = "1h"
    leverage: int = Field(default=1, ge=1, le=20)
    initial_cash: float = Field(default=10000, ge=100, le=10000000, allow_inf_nan=False)
    allocation_pct: float = Field(default=10, gt=0, le=100, allow_inf_nan=False)
    stop_loss_pct: float = Field(default=2, gt=0, le=100, allow_inf_nan=False)
    take_profit_pct: float = Field(default=4, gt=0, le=100, allow_inf_nan=False)
    request_id: str = Field(min_length=1, max_length=80)


class ComparisonAccount(StrategyAccount):
    leverage_list: list[int] = Field(default_factory=lambda: [1, 5, 10, 20], min_length=1, max_length=4)


class EnabledState(BaseModel):
    enabled: bool = Field(strict=True)


def _data_dir(request: Request) -> Path:
    return request.app.state.repo.store.data_dir


def _quote(market: str, symbol: str) -> dict:
    raise HTTPException(status_code=410, detail="币安手动模拟已停用，历史账本只读；请使用 Bitget 策略模拟")


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/custom/crypto-paper", tags=["custom-crypto-paper"])

    @router.get("/quote/{market}/{symbol}")
    def get_quote(market: str, symbol: str) -> dict:
        return _quote(market, symbol)

    @router.get("/account")
    def get_account(request: Request) -> dict:
        try:
            return ledger.load(_data_dir(request))
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/valuation")
    def get_valuation(request: Request) -> dict:
        raise HTTPException(status_code=410, detail="历史币安账户已停止实时估值，账本和持仓保留")

    @router.post("/orders")
    def place_order(request: Request, body: PaperOrder) -> dict:
        raise HTTPException(status_code=410, detail="币安手动模拟已停用，原订单和持仓保留")

    @router.get("/market/{symbol}/quote")
    def market_quote(symbol: str) -> dict:
        if symbol not in bitget.SYMBOLS:
            raise HTTPException(status_code=400, detail="不支持的交易对")
        try:
            quote = bitget.quote("usdm", symbol)
            if not -5000 <= int(time.time() * 1000) - quote["asof_ms"] <= 60_000:
                raise ValueError("Bitget 行情过期")
            return {"quote": {"source": "bitget_public_rest", **quote}, "stream": stream.status(symbol)}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=503, detail="Bitget 公开行情暂不可用，暂停生成模拟订单") from exc

    @router.get("/market/{symbol}/candles")
    def market_candles(symbol: str, interval: str = "1h") -> dict:
        if symbol not in bitget.SYMBOLS or interval not in bitget.KLINE_INTERVALS:
            raise HTTPException(status_code=400, detail="不支持的交易对或图表周期")
        try:
            return market.candles(symbol, interval)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=503, detail="Bitget 历史 K 线暂不可用") from exc

    @router.get("/market/{symbol}/stream")
    async def market_stream(request: Request, symbol: str):
        if symbol not in bitget.SYMBOLS:
            raise HTTPException(status_code=400, detail="不支持的交易对")

        async def events():
            while not await request.is_disconnected():
                payload = {"ticker": stream.ticker(symbol), "stream": stream.status(symbol)}
                yield "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @router.get("/strategies")
    def strategies() -> dict:
        return {"strategies": auto.STRATEGIES, "model": auto.MODEL}

    @router.post("/strategy-draft")
    async def generate_strategy_draft(body: strategy_draft.DraftRequest) -> dict:
        try:
            return await strategy_draft.generate(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="策略草案校验失败: " + str(exc)) from exc
        except strategy_draft.DraftBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except strategy_draft.DraftUnavailableError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=auto._connection_error(exc, body.exchange, "行情")) from exc
        except Exception as exc:
            logger.warning("AI crypto draft failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="AI策略草案暂不可用, 请检查AI设置; 没有创建或启用账户") from exc

    @router.get("/strategy-accounts")
    def strategy_accounts(request: Request) -> dict:
        try:
            data_dir = _data_dir(request)
            return {"accounts": auto.accounts(data_dir), "runtime": auto.runtime_status(data_dir), "model": auto.MODEL}
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/strategy-accounts")
    def create_strategy_account(request: Request, body: StrategyAccount) -> dict:
        try:
            return {"account": auto.create(_data_dir(request), body.model_dump())[0]}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/strategy-accounts/compare")
    def create_comparison(request: Request, body: ComparisonAccount) -> dict:
        try:
            values = body.model_dump()
            levels = values.pop("leverage_list")
            return {"accounts": auto.create(_data_dir(request), values, levels)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/strategy-accounts/{account_id}")
    def strategy_detail(request: Request, account_id: str) -> dict:
        try:
            return auto.detail(_data_dir(request), account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="策略模拟账户不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/strategy-accounts/{account_id}/enabled")
    def enable_strategy(request: Request, account_id: str, body: EnabledState) -> dict:
        try:
            return {"account": auto.set_enabled(_data_dir(request), account_id, body.enabled)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="策略模拟账户不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/strategy-accounts/{account_id}/run")
    def check_strategy(request: Request, account_id: str) -> dict:
        try:
            data_dir = _data_dir(request)
            result = auto.run_once(data_dir, account_id)
            return {"account": auto.detail(data_dir, account_id)["account"], **result}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="策略模拟账户不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router
