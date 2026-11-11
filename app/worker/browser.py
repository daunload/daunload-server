"""재생 워커의 Chrome 수명 관리.

- acquire: 살아 있는 브라우저를 돌려주고, 없으면 몇 번 다시 시도해 띄운다. 끝내 못 띄우면 BrowserUnavailable.
  재생 묶음을 시작하기 전에 부르므로, 실행 실패는 묶음·항목 상태를 바꾸지 않는다.
- close_if_idle: 큐가 비었을 때만 부른다. 마지막 사용 뒤 idle_seconds가 지났으면 닫는다.
- recycle_if_old: 영상과 영상 사이에만 부른다. 띄운 지 max_age가 지났으면 닫는다(다음 acquire가 새로 띄움).
재생 루프 한 곳에서 차례로 부르므로 사용 중인 브라우저를 닫는 경쟁 상태가 없다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

LAUNCH_RETRY_DELAYS = (0, 2, 5, 10)  # 시도 4번, 약 17초 안에 끝난다
LAUNCH_TIMEOUT = 60
CLOSE_TIMEOUT = 15


class BrowserUnavailable(Exception):
    """다시 시도해도 Chrome을 띄우지 못했다. 묶음은 그대로 두고 나중에 다시 시도한다."""


class BrowserManager:
    def __init__(
        self,
        launch: Callable[[], Awaitable[Any]],
        *,
        idle_seconds: float = 0,
        max_age_seconds: float = 0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._launch, self._clock, self._sleep = launch, clock, sleep
        self.idle_seconds, self.max_age_seconds = idle_seconds, max_age_seconds
        self.browser: Any = None
        self.launched_at = self.last_used = clock()
        self.launches = 0

    async def acquire(self):
        if self.browser is not None and self.browser.is_connected():
            return self.browser
        self.browser = None
        last_error: BaseException | None = None
        for attempt, delay in enumerate(LAUNCH_RETRY_DELAYS, 1):
            if delay:
                await self._sleep(delay)
            started = self._clock()
            try:
                browser = await asyncio.wait_for(self._launch(), timeout=LAUNCH_TIMEOUT)
            except Exception as e:  # noqa: BLE001 - 어떤 실행 오류든 다시 시도한다
                last_error = e
                log.warning("Chrome 실행 실패(%d/%d): %s: %s", attempt, len(LAUNCH_RETRY_DELAYS), type(e).__name__, e)
                continue
            self.browser, self.launches = browser, self.launches + 1
            self.launched_at = self.last_used = self._clock()
            log.info("Chrome 실행 (%.1fs, 누적 %d회)", self.launched_at - started, self.launches)
            return browser
        raise BrowserUnavailable(f"{type(last_error).__name__}: {last_error}") from last_error

    def release(self) -> None:
        self.last_used = self._clock()

    async def close_if_idle(self) -> bool:
        if self.browser is None or self.idle_seconds <= 0 or self._clock() - self.last_used < self.idle_seconds:
            return False
        await self.close("idle")
        return True

    async def recycle_if_old(self) -> bool:
        if self.browser is None or self.max_age_seconds <= 0 or self._clock() - self.launched_at < self.max_age_seconds:
            return False
        await self.close("max-age")
        return True

    async def close(self, reason: str) -> None:
        browser, self.browser = self.browser, None  # 닫기가 실패해도 다음 acquire는 새로 띄운다
        if browser is None:
            return
        try:
            await asyncio.wait_for(browser.close(), timeout=CLOSE_TIMEOUT)
            log.info("Chrome 종료(%s)", reason)
        except Exception as e:  # noqa: BLE001 - 남은 프로세스는 Playwright 드라이버와 컨테이너 init이 정리한다
            log.warning("Chrome 종료 실패(%s), 참조만 버림: %s: %s", reason, type(e).__name__, e)
