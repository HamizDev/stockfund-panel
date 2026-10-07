"""Market-news page extension; removable without touching core routes."""
from app.extensions import (
    BACKEND_EXTENSION_API_VERSION,
    BackendExtensionRegistrar,
    ExtensionContext,
)

EXTENSION_ID = "market.news"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    from app.custom.news.routes import build_router

    registrar.include_router(build_router())


def shutdown(context: ExtensionContext) -> None:
    from app.custom.news.routes import stop_services

    stop_services(context.data_dir)
