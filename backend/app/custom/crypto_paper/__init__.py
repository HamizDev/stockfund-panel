"""Public Bitget data and research-only crypto paper accounts."""
from __future__ import annotations

from app.extensions import BACKEND_EXTENSION_API_VERSION, BackendExtensionRegistrar

EXTENSION_ID = "crypto.paper"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    from app.custom.crypto_paper.routes import build_router

    registrar.include_router(build_router())


def startup(context) -> None:
    from app.custom.crypto_paper import auto
    from app.custom.crypto_paper.public_stream import stream

    stream.start()
    try:
        auto.start(context.data_dir)
    except Exception:
        stream.stop()
        raise


def shutdown(context) -> None:
    from app.custom.crypto_paper import auto
    from app.custom.crypto_paper.public_stream import stream

    try:
        auto.shutdown(context.data_dir)
    finally:
        stream.stop()
