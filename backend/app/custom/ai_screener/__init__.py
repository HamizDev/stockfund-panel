"""Read-only research candidates from the existing strategy and quote caches."""

from app.extensions import BACKEND_EXTENSION_API_VERSION, BackendExtensionRegistrar

EXTENSION_ID = "ai.screener"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    from app.custom.ai_screener.routes import build_router

    registrar.include_router(build_router())
