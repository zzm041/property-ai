"""FastAPI application factory for the modular monolith."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from app.api.v1.router import build_api_router
from app.core.config import Settings, get_settings
from app.core.database import dispose_database_engines, verify_test_database_schema
from app.core.exception_handlers import install_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.request_context import RequestContextMiddleware
from app.core.startup import (
    verify_rag_providers_are_real,
    verify_task_delivery_configuration,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application without importing any business domain."""

    resolved_settings = settings or get_settings()
    configure_logging(debug=resolved_settings.app_debug, service="api")
    logger = get_logger(__name__)
    # PA-104-A-1: assembly-time self-check; a production misconfiguration must
    # fail here instead of degrading into silent enqueue loss at request time.
    verify_task_delivery_configuration(resolved_settings)
    # t165: same contract for the RAG chain — a production app may not answer
    # knowledge questions from a development provider that never calls a model.
    verify_rag_providers_are_real(resolved_settings)
    # PA-118: an APP_ENV=test process must not come up on a disposable database that
    # was never migrated. Without this the whole integration suite fails one raw
    # MySQL 1146 at a time, which reads like hundreds of code defects.
    verify_test_database_schema(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application_started",
            app_env=resolved_settings.app_env,
        )
        try:
            yield
        finally:
            dispose_database_engines()
            logger.info("application_stopped")

    application = FastAPI(
        title=resolved_settings.app_name,
        debug=resolved_settings.app_debug,
        openapi_url=(
            None
            if resolved_settings.app_env == "production"
            else f"{resolved_settings.api_v1_prefix}/openapi.json"
        ),
        docs_url=(
            None
            if resolved_settings.app_env == "production"
            else f"{resolved_settings.api_v1_prefix}/docs"
        ),
        redoc_url=None,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    install_exception_handlers(application)
    application.include_router(
        build_api_router(resolved_settings),
        prefix=resolved_settings.api_v1_prefix,
    )

    if settings is not None:
        application.dependency_overrides[get_settings] = lambda: settings

    return application


app = create_app()
