"""Independent public Binance quotes and research-only crypto paper accounts."""
from __future__ import annotations

from app.extensions import BACKEND_EXTENSION_API_VERSION, BackendExtensionRegistrar

EXTENSION_ID = "crypto.paper"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    from app.custom.crypto_paper.routes import build_router

    registrar.include_router(build_router())
