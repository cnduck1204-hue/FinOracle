import asyncio
import threading

import pytest

from app.core.concurrency import run_sync_to_completion


def test_sync_call_returns_its_result():
    assert asyncio.run(run_sync_to_completion(int, "2a", base=16)) == 42


def test_sync_call_propagates_its_error():
    def fail():
        raise ValueError("write failed")

    with pytest.raises(ValueError, match="write failed"):
        asyncio.run(run_sync_to_completion(fail))


@pytest.mark.parametrize("fail", [False, True])
def test_cancellation_waits_for_the_thread_even_after_repeated_cancels(fail):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def write():
        entered.set()
        assert release.wait(5)
        finished.set()
        if fail:
            raise ValueError("write failed after cancellation")

    async def scenario():
        task = asyncio.create_task(run_sync_to_completion(write))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert finished.is_set()

    asyncio.run(scenario())
