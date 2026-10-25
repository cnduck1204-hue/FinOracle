from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import engine
from app.middleware.request_context import RequestContextMiddleware

settings = get_settings()

configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        try:
            await redis_client.aclose()
        finally:
            await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    description="基于 RAG 的金融制度类知识问答系统",
    version="0.1.0",
    debug=False,
    docs_url="/docs" if settings.openapi_enabled else None,
    redoc_url="/redoc" if settings.openapi_enabled else None,
    openapi_url="/openapi.json" if settings.openapi_enabled else None,
    lifespan=lifespan,
)

app.add_middleware(
    RequestContextMiddleware,
)

register_exception_handlers(app)

app.include_router(
    api_router,
    prefix=settings.api_v1_prefix,
)
