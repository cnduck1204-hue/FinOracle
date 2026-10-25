from time import time
from typing import Literal
from uuid import UUID

from redis.asyncio import Redis

from app.schemas.session import SessionRecord

# v1 records have no user index. A new namespace prevents logout-all missing them.
_SESSION_PREFIX = "finoracle:v2:auth:session:"
_USER_INDEX_PREFIX = "finoracle:v2:auth:user-sessions:"

_CREATE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
local now = tonumber(redis.call('TIME')[1])
if tonumber(ARGV[2]) <= now then
    return -1
end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[3])
redis.call('SET', KEYS[1], ARGV[1], 'EXAT', ARGV[2])
local last = redis.call('ZRANGE', KEYS[2], -1, -1, 'WITHSCORES')
redis.call('EXPIREAT', KEYS[2], last[2])
return 1
"""

_REVOKE_ALL_SCRIPT = """
local now = tonumber(redis.call('TIME')[1])
local ids = redis.call('ZRANGEBYSCORE', KEYS[1], now, '+inf')
local count = 0
for _, id in ipairs(ids) do
    local key = ARGV[1] .. id
    local raw = redis.call('GET', key)
    if raw then
        local data = cjson.decode(raw)
        if data.user_id == ARGV[2] and data.id == id and data.revoked == false then
            data.revoked = true
            redis.call('SET', key, cjson.encode(data), 'KEEPTTL')
            count = count + 1
        end
    end
end
redis.call('DEL', KEYS[1])
return count
"""

_REVOKE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])

if not raw then
    return 0
end

local data = cjson.decode(raw)
data.revoked = true

redis.call('SET', KEYS[1], cjson.encode(data), 'KEEPTTL')

return 1
"""
_ROTATE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])

if not raw then
    return 0
end

local data = cjson.decode(raw)
local now = tonumber(redis.call('TIME')[1])
local new_expires_at = tonumber(ARGV[5])

if data.id ~= ARGV[1]
    or data.user_id ~= ARGV[2]
    or data.revoked ~= false
    or tonumber(data.expires_at) <= now then
    return 0
end

if new_expires_at <= now then
    return 0
end

if data.refresh_jti ~= ARGV[3] then
    data.revoked = true

    redis.call(
        'SET',
        KEYS[1],
        cjson.encode(data),
        'KEEPTTL'
    )

    return -1
end

data.refresh_jti = ARGV[4]
data.expires_at = new_expires_at

-- Update the index before the record: an index type error cannot consume a token.
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
redis.call('ZADD', KEYS[2], ARGV[5], ARGV[1])
local last = redis.call('ZRANGE', KEYS[2], -1, -1, 'WITHSCORES')
redis.call('EXPIREAT', KEYS[2], last[2])

redis.call(
    'SET',
    KEYS[1],
    cjson.encode(data),
    'EXAT',
    ARGV[5]
)

return 1
"""


class SessionStore:
    def __init__(self, cache: Redis) -> None:
        self.cache = cache

    @staticmethod
    def _key(session_id: UUID) -> str:
        return f"{_SESSION_PREFIX}{session_id}"

    @staticmethod
    def _user_key(user_id: UUID) -> str:
        return f"{_USER_INDEX_PREFIX}{user_id}"

    async def create(self, record: SessionRecord) -> None:
        if record.expires_at <= int(time()):
            raise ValueError("不能创建已过期的会话")

        # A single script makes the session and its user index visible together.
        created = await self.cache.eval(
            _CREATE_SCRIPT,
            2,
            self._key(record.id),
            self._user_key(record.user_id),
            record.model_dump_json(),
            record.expires_at,
            str(record.id),
        )

        if int(created) == -1:
            raise ValueError("不能创建已过期的会话")
        if int(created) != 1:
            raise ValueError("会话 ID 已存在")

    async def get(
        self,
        session_id: UUID,
    ) -> SessionRecord | None:
        raw = await self.cache.get(self._key(session_id))

        if raw is None:
            return None

        record = SessionRecord.model_validate_json(raw)

        if record.id != session_id:
            raise ValueError("会话数据与 ID 不匹配")

        if record.expires_at <= int(time()):
            return None

        return record

    async def revoke(self, session_id: UUID) -> bool:
        result = await self.cache.eval(
            _REVOKE_SCRIPT,
            1,
            self._key(session_id),
        )
        return bool(result)

    async def revoke_all(self, user_id: UUID) -> int:
        """Revoke indexed sessions atomically; later logins start fresh sessions.

        The Lua script derives session keys from our own index. This deployment
        uses standalone Redis; Redis Cluster would need a different key layout.
        """
        return int(
            await self.cache.eval(
                _REVOKE_ALL_SCRIPT,
                1,
                self._user_key(user_id),
                _SESSION_PREFIX,
                str(user_id),
            )
        )

    async def rotate(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        expected_refresh_jti: UUID,
        new_refresh_jti: UUID,
        expires_at: int,
    ) -> Literal["rotated", "invalid", "replayed"]:
        if new_refresh_jti == expected_refresh_jti:
            raise ValueError("刷新令牌必须使用新的 jti")

        if expires_at <= int(time()):
            raise ValueError("新的会话过期时间必须晚于当前时间")

        result = await self.cache.eval(
            _ROTATE_SCRIPT,
            2,
            self._key(session_id),
            self._user_key(user_id),
            str(session_id),
            str(user_id),
            str(expected_refresh_jti),
            str(new_refresh_jti),
            expires_at,
        )

        result = int(result)

        if result == 1:
            return "rotated"

        if result == 0:
            return "invalid"

        if result == -1:
            return "replayed"

        raise ValueError("未知的会话轮换结果")
