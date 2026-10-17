"""Optional local Jevos annotation extension. No trading decisions are changed."""
from app.extensions import BACKEND_EXTENSION_API_VERSION, BackendExtensionRegistrar

EXTENSION_ID = 'local.jevos'
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    from .routes import build_router

    registrar.include_router(build_router())


def startup(context) -> None:
    from .service import start

    start(context.data_dir)


def shutdown(context) -> None:
    from .service import stop

    stop(context.data_dir)
