from hashlib import sha256

from redis.asyncio import Redis

_RECORD_FAILURE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local limit = tonumber(ARGV[1])

if count == 1 or count == limit then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end

return count
"""

_CHECK_LOCK_SCRIPT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')

if count < tonumber(ARGV[1]) then
    return 0
end

return math.max(redis.call('TTL', KEYS[1]), 1)
"""


class LoginFailureStore:
    def __init__(
        self,
        cache: Redis,
        *,
        max_failures: int,
        lock_seconds: int,
    ) -> None:
        if max_failures < 1 or lock_seconds < 1:
            raise ValueError("失败次数和限制时长必须为正数")

        self.cache = cache
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds

    @staticmethod
    def _key(identifier: str) -> str:
        digest = sha256(identifier.encode("utf-8")).hexdigest()
        return f"finoracle:v1:auth:login-failures:{digest}"

    async def retry_after(self, identifier: str) -> int:
        result = await self.cache.eval(
            _CHECK_LOCK_SCRIPT,
            1,
            self._key(identifier),
            self.max_failures,
        )
        return int(result)

    async def record_failure(self, identifier: str) -> int:
        result = await self.cache.eval(
            _RECORD_FAILURE_SCRIPT,
            1,
            self._key(identifier),
            self.max_failures,
            self.lock_seconds,
        )
        return int(result)

    async def clear(self, identifier: str) -> None:
        await self.cache.delete(self._key(identifier))
