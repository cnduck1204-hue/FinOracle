from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from starlette.concurrency import run_in_threadpool

_password_hasher = PasswordHasher()

# 固定的非账号摘要：不存在的用户名仍执行 Argon2 校验，减少耗时差异。
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "M8O9dGcpWzoFrz0ne4sQ3g$"
    "oIUXtDH68gm6s2S2NjRJYZEBCCGUUV16yNbMLQFuWno"
)


async def hash_password(password: str) -> str:
    return await run_in_threadpool(
        _password_hasher.hash,
        password,
    )


async def verify_password(
    password: str,
    password_hash: str,
) -> bool:
    try:
        return await run_in_threadpool(
            _password_hasher.verify,
            password_hash,
            password,
        )
    except (VerificationError, InvalidHashError):
        return False
