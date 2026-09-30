"""Research-only Binance spot and USD-M paper trading API."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.custom.crypto_paper import client, ledger

logger = logging.getLogger(__name__)


class PaperOrder(BaseModel):
    market: str
    symbol: str
    action: str
    quantity: str
    leverage: int = Field(default=1, ge=1, le=5)
    request_id: str = Field(min_length=1, max_length=80)


def _data_dir(request: Request) -> Path:
    return request.app.state.repo.store.data_dir


def _quote(market: str, symbol: str) -> dict:
    if market not in client.MARKETS or symbol not in client.SYMBOLS:
        raise HTTPException(status_code=400, detail="不支持的市场或交易对")
    try:
        return client.quote(market, symbol)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Binance public quote unavailable: %s %s: %s", market, symbol, exc)
        raise HTTPException(status_code=503, detail="币安公开行情暂不可用; 没有创建模拟订单") from exc


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
        try:
            account = ledger.load(_data_dir(request))
            quotes = {
                (market, symbol): _quote(market, symbol)
                for market in ("spot", "usdm")
                for symbol in account[market]["positions"]
            }
            return ledger.value_account(account, quotes)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/orders")
    def place_order(request: Request, body: PaperOrder) -> dict:
        try:
            prior = ledger.replay(
                _data_dir(request), market=body.market, symbol=body.symbol,
                action=body.action, quantity=body.quantity, leverage=body.leverage,
                request_id=body.request_id,
            )
            if prior:
                order, account = prior
                return {"order": order, "account": account}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        quote = _quote(body.market, body.symbol)
        try:
            order, account = ledger.trade(
                _data_dir(request), market=body.market, symbol=body.symbol,
                action=body.action, quantity=body.quantity, leverage=body.leverage,
                quote=quote, request_id=body.request_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"order": order, "account": account}

    return router
