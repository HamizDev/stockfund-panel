from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from . import service


class RefreshRequest(BaseModel):
    symbol: str = Field(min_length=9, max_length=9)
    trade_date: str = Field(min_length=10, max_length=10)


def build_router():
    router = APIRouter(prefix="/api/custom/auction", tags=["auction-research"])

    @router.get("/status")
    def get_status():
        return service.status()

    @router.get("/evidence")
    def get_evidence(request: Request, symbol: str = Query(..., min_length=9, max_length=9),
                     trade_date: str = Query(..., min_length=10, max_length=10)):
        try:
            return service.evidence(request.app.state.repo.store.data_dir, symbol, trade_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/refresh")
    def refresh(request: Request, body: RefreshRequest):
        try:
            return service.evidence(request.app.state.repo.store.data_dir, body.symbol, body.trade_date, refresh=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
