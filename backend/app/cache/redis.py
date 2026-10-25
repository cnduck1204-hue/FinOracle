from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.core.config import get_settings

settings = get_settings()

redis_client = Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    db=settings.redis_db,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    health_check_interval=30,
    retry=Retry(NoBackoff(), 0),
)


async def get_redis() -> Redis:
    return redis_client
