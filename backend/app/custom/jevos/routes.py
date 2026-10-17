"""Read-only local model status; never exposes credentials or execution APIs."""
from fastapi import APIRouter, Request

from .service import get_service


def build_router() -> APIRouter:
    router = APIRouter(prefix='/api/custom/jevos', tags=['local-jevos'])

    @router.get('/status')
    def status(request: Request):
        return get_service(request.app.state.repo.store.data_dir).snapshot()

    return router
