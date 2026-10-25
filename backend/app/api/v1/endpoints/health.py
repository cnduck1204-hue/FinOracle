import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import get_redis
from app.core.exceptions import AppException
from app.db.session import get_db

router = APIRouter()

DEPENDENCY_TIMEOUT_SECONDS = 3.0


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "FinOracle",
    }


@router.get(
    "/ready",
    responses={503: {"description": "依赖服务不可用"}},
)
async def readiness_check(
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Redis, Depends(get_redis)],
):
    try:
        mysql_result = await asyncio.wait_for(
            db.scalar(text("SELECT 1")),
            timeout=DEPENDENCY_TIMEOUT_SECONDS,
        )

        redis_result = await asyncio.wait_for(
            cache.ping(),
            timeout=DEPENDENCY_TIMEOUT_SECONDS,
        )

        ready = mysql_result == 1 and redis_result is True
    except (SQLAlchemyError, RedisError, TimeoutError):
        ready = False

    if not ready:
        raise AppException(
            code="DEPENDENCY_UNAVAILABLE",
            message="服务暂未就绪，请稍后重试",
            status_code=503,
        )

    return {
        "status": "ok",
        "service": "FinOracle",
        "dependencies": {
            "mysql": "ok",
            "redis": "ok",
        },
    }
