import asyncio

import pytest

from app.worker.browser import LAUNCH_RETRY_DELAYS, BrowserManager, BrowserUnavailable


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeBrowser:
    def __init__(self, close_error=None):
        self.connected, self.closed, self.close_error = True, False, close_error

    def is_connected(self):
        return self.connected

    async def close(self):
        if self.close_error:
            raise self.close_error
        self.closed, self.connected = True, False


class Launcher:
    """fail번 실패한 뒤 새 FakeBrowser를 돌려준다."""

    def __init__(self, fail=0, **browser_kw):
        self.fail, self.calls, self.browsers, self.browser_kw = fail, 0, [], browser_kw

    async def __call__(self):
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError(f"launch {self.calls}")
        self.browsers.append(FakeBrowser(**self.browser_kw))
        return self.browsers[-1]


def manager(launcher, clock, **kw):
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    m = BrowserManager(launcher, clock=clock, sleep=sleep, **kw)
    m.slept = slept
    return m


async def test_reuses_connected_browser():
    launcher, clock = Launcher(), Clock()
    m = manager(launcher, clock, idle_seconds=600)
    first = await m.acquire()
    clock.now += 599
    m.release()
    assert await m.acquire() is first
    assert launcher.calls == 1


async def test_relaunches_when_disconnected():
    launcher = Launcher()
    m = manager(launcher, Clock())
    first = await m.acquire()
    first.connected = False
    assert await m.acquire() is not first
    assert launcher.calls == 2


async def test_launch_retries_then_succeeds():
    launcher = Launcher(fail=2)
    m = manager(launcher, Clock())
    assert await m.acquire() is launcher.browsers[0]
    assert launcher.calls == 3
    assert m.slept == list(LAUNCH_RETRY_DELAYS[1:3])


async def test_launch_gives_up_after_all_retries():
    launcher = Launcher(fail=99)
    m = manager(launcher, Clock())
    with pytest.raises(BrowserUnavailable, match="launch 4"):
        await m.acquire()
    assert launcher.calls == len(LAUNCH_RETRY_DELAYS)
    assert m.browser is None


async def test_launch_timeout_counts_as_failure(monkeypatch):
    monkeypatch.setattr("app.worker.browser.LAUNCH_TIMEOUT", 0.01)

    async def hang():
        await asyncio.Event().wait()

    with pytest.raises(BrowserUnavailable, match="TimeoutError"):
        await manager(hang, Clock()).acquire()


async def test_closes_only_after_idle_period():
    launcher, clock = Launcher(), Clock()
    m = manager(launcher, clock, idle_seconds=600)
    browser = await m.acquire()
    m.release()
    clock.now += 599
    assert not await m.close_if_idle()
    clock.now += 1
    assert await m.close_if_idle()
    assert browser.closed and m.browser is None
    await m.acquire()
    assert launcher.calls == 2


async def test_idle_zero_keeps_browser_open():
    clock = Clock()
    m = manager(Launcher(), clock, idle_seconds=0)
    await m.acquire()
    clock.now += 10**6
    assert not await m.close_if_idle()
    assert m.browser is not None


async def test_recycles_after_max_age():
    launcher, clock = Launcher(), Clock()
    m = manager(launcher, clock, max_age_seconds=3600)
    old = await m.acquire()
    clock.now += 3599
    assert not await m.recycle_if_old()
    clock.now += 1
    assert await m.recycle_if_old()
    assert old.closed
    assert await m.acquire() is not old


async def test_failed_close_still_drops_reference():
    launcher = Launcher(close_error=RuntimeError("stuck"))
    m = manager(launcher, Clock())
    await m.acquire()
    await m.close("shutdown")
    assert m.browser is None
    await m.acquire()
    assert launcher.calls == 2
