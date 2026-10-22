import sqlite3

from fastapi import APIRouter, HTTPException, Query

from . import service
from .catalog import RECIPES, TOPICS


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/custom/indicator-library", tags=["indicator-research"])

    @router.get("/catalog")
    def catalog():
        return {"topics": TOPICS, "recipes": RECIPES}

    @router.get("/search")
    def search(q: str = Query("", max_length=80), author: str = Query("", max_length=60),
               kind: str = "all", page: int = Query(1, ge=1, le=20), offline: bool = False):
        try:
            return service.search(q, author, kind, page, offline)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except (service.LibraryError, OSError, sqlite3.Error) as error:
            message = str(error) if isinstance(error, service.LibraryError) else "本机研究缓存暂不可用"
            raise HTTPException(400, message) from None

    @router.get("/source")
    def source(id: str = Query(..., max_length=110), version: str = Query("last", max_length=15),
               offline: bool = False):
        try:
            return service.source(id, version, offline)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except (service.LibraryError, OSError, sqlite3.Error) as error:
            message = str(error) if isinstance(error, service.LibraryError) else "本机研究缓存暂不可用"
            raise HTTPException(400, message) from None

    return router
