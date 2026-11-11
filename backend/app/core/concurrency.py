"""需要在同步副作用完成后才能释放数据库锁的线程调用。"""

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


async def run_sync_to_completion(
    function: Callable[..., T], /, *args: object, **kwargs: object
) -> T:
    """取消协程时仍等待线程退出，避免数据库锁先于同步写入释放。"""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            if not cancelled:
                raise
    if cancelled:
        # 消费线程异常，保留调用方的取消语义。
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()
