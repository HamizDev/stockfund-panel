"""Optional free auction research; isolated from signals and paper trading."""
from app.extensions import BACKEND_EXTENSION_API_VERSION

EXTENSION_ID = "local.auction.research"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar):
    from .routes import build_router
    registrar.include_router(build_router())
