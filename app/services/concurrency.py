"""동시에 여러 학교 서버 요청을 보내는 도우미."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def gather_all(*aws: Awaitable[T]) -> list[T]:
    """모두 기다려 순서대로 돌려준다. 하나가 실패하면 나머지를 취소하고 그 예외를 그대로 올린다
    (ExceptionGroup으로 감싸지 않아 호출하는 쪽의 `except LmsError`가 그대로 동작한다)."""
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_wrap(a)) for a in aws]
    except ExceptionGroup as eg:
        raise eg.exceptions[0] from None
    return [t.result() for t in tasks]


async def map_limited(limit: int, items: Iterable[T], fn: Callable[[T], Awaitable[R]]) -> list[R]:
    """items마다 fn을 최대 limit개까지 동시에 돌리고 결과를 입력 순서대로 돌려준다."""
    sem = asyncio.Semaphore(limit)

    async def one(item: T) -> R:
        async with sem:
            return await fn(item)

    return await gather_all(*(one(item) for item in items))


async def _wrap(aw: Awaitable[T]) -> T:
    return await aw
