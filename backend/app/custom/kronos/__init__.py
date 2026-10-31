"""Optional local on-demand forecasts; never changes accounts or trading rules."""
from app.extensions import BACKEND_EXTENSION_API_VERSION, BackendExtensionRegistrar

EXTENSION_ID = 'local.kronos'
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    from .routes import build_router
    registrar.include_router(build_router())


def startup(context) -> None:
    from .routes import EVALUATOR
    EVALUATOR.start(context.repository)


def shutdown(context) -> None:
    from .routes import COMPARISONS, EVALUATOR
    from .service import MANAGER
    COMPARISONS.stop()
    EVALUATOR.stop()
    MANAGER.stop()
