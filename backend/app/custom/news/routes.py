# ruff: noqa: RUF001 -- localized Chinese UI and prompt punctuation.
"""Thin authenticated endpoints for news and explicit AI research jobs."""
from weakref import WeakSet

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.services.news_service import NewsService, collect_context

_instances: WeakSet = WeakSet()


class AnalyzeIn(BaseModel):
    article_id: str = Field(min_length=1, max_length=32, pattern=r"^(daily|[a-f0-9]{24})$")


def service(request: Request) -> NewsService:
    existing = getattr(request.app.state, "news_service", None)
    if existing is None:
        existing = NewsService(request.app.state.repo.store.data_dir)
        request.app.state.news_service = existing
        _instances.add(existing)
    return existing


def stop_services(data_dir) -> None:
    for news in list(_instances):
        if news.data_dir.resolve() == data_dir.resolve():
            for task in [news.refresh_task, *news.tasks.values()]:
                if task is not None and not task.done():
                    task.get_loop().call_soon_threadsafe(task.cancel)


async def current_feed(request: Request, refresh: bool = False) -> dict:
    news = service(request)
    await news.refresh(refresh)
    instruments, related, status = await run_in_threadpool(collect_context, request.app.state.repo)
    return await run_in_threadpool(news.feed, instruments, related, status)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/custom/news", tags=["custom-news"])

    @router.get("/feed")
    async def feed(request: Request, refresh: bool = False):
        return await current_feed(request, refresh)

    @router.post("/analyze")
    async def analyze(request: Request, body: AnalyzeIn):
        snapshot = await current_feed(request)
        try:
            return await service(request).analyze(body.article_id, snapshot)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/analysis/{report_id}")
    async def analysis(request: Request, report_id: str):
        if len(report_id) != 32 or any(char not in "0123456789abcdef" for char in report_id):
            raise HTTPException(400, "无效的解读标识")
        report = service(request).analysis(report_id)
        if report is None:
            raise HTTPException(404, "该解读不存在或未完成，请重新点击解读")
        return report

    return router
