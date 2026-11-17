import asyncio

import pytest

from app.lms.errors import LmsError
from app.services.concurrency import gather_all, map_limited


async def test_map_limited_keeps_order_and_limit():
    running = peak = 0

    async def work(n):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01 * (5 - n))
        running -= 1
        return n * 10

    assert await map_limited(2, range(5), work) == [0, 10, 20, 30, 40]
    assert peak == 2


async def test_gather_all_raises_first_error_unwrapped_and_cancels_rest():
    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fail():
        raise LmsError("boom")

    with pytest.raises(LmsError, match="boom"):  # ExceptionGroup이 아니어야 기존 except가 동작한다
        await gather_all(slow(), fail())
    assert cancelled.is_set()
