"""Research-only Binance spot and USD-M paper trading API."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.custom.crypto_paper import auto, client, ledger

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
    market: str
    symbol: str
    strategy_id: str
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
            if account.get("maintenance_error"):
                raise ValueError(account["maintenance_error"])
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
            order, account = auto.manual_order(
                _data_dir(request), market=body.market, symbol=body.symbol,
                action=body.action, quantity=body.quantity, leverage=body.leverage,
                quote=quote, request_id=body.request_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=503, detail="合约资金费暂不可用; 本次没有创建订单") from exc
        return {"order": order, "account": account}

    @router.get("/strategies")
    def strategies() -> dict:
        return {"strategies": auto.STRATEGIES, "model": auto.MODEL}

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
