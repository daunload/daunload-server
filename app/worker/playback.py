"""DB-backed single playback worker: python -m app.worker.playback.

One video per turn; completed turns move to the back of the durable queue.
No Redis in-progress keys or per-batch expiry affect playback recovery.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from contextlib import asynccontextmanager

from sqlalchemy import select, text

from app.config import get_settings
from app.db.base import init_engine, sessionmaker, utcnow
from app.db.models import PlaybackBatch, PlaybackJob
from app.services import playback
from app.services.notify import notify_admins
from app.worker.browser import BrowserManager, BrowserUnavailable

log = logging.getLogger(__name__)
LOCK_ID = 736012459


@asynccontextmanager
async def exclusive_worker(engine):
    connection = await engine.connect()
    try:
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID})
        await connection.commit()
        if not acquired:
            raise RuntimeError("재생 워커가 이미 실행 중입니다. 한 인스턴스만 실행하세요.")
        yield connection
    finally:
        # Closing the physical session releases its advisory lock, even on cancellation.
        await connection.invalidate()
        await connection.close()


async def recover_interrupted() -> int:
    """Only call while holding the exclusive worker lock, before starting playback."""
    async with sessionmaker()() as db:
        batches = list((await db.scalars(select(PlaybackBatch).where(
            PlaybackBatch.status == "running",
        ).with_for_update())).all())
        for batch in batches:
            jobs = (await db.scalars(select(PlaybackJob).where(
                PlaybackJob.batch_id == batch.id, PlaybackJob.status.in_(("queued", "playing")),
            ))).all()
            for job in jobs:
                job.status = "cancelled" if batch.cancel_requested else "queued"
                job.progress_updated_at = None
                job.progress_seconds = 0  # Actual LMS state is refreshed before replay.
                if batch.cancel_requested:
                    job.finished_at = utcnow()
            batch.status = "cancelled" if batch.cancel_requested else "queued"
            batch.current_position = None
            if batch.cancel_requested:
                batch.finished_at = utcnow()
        await db.commit()
        return len(batches)


async def process_next(player_factory) -> int | None:
    async with sessionmaker()() as db:
        batch_id = await db.scalar(select(PlaybackBatch.id).where(
            PlaybackBatch.status == "queued",
        ).order_by(PlaybackBatch.queue_at, PlaybackBatch.id).limit(1))
    if batch_id is None:
        return None
    # 묶음을 시작하기 전에 브라우저를 준비한다. BrowserUnavailable은 그대로 올려 보내 묶음을 queued로 남긴다.
    player = await player_factory()
    try:
        # Timeout cancellation closes the playback task before another turn starts.
        await asyncio.wait_for(playback.run_batch(batch_id, player, one_item=True), timeout=24 * 3600)
    except Exception as exc:
        log.exception("재생 작업 오류 batch=%s", batch_id)
        async with sessionmaker()() as db:
            batch = await db.scalar(select(PlaybackBatch).where(
                PlaybackBatch.id == batch_id,
            ).with_for_update())
            if batch and batch.status in playback.ACTIVE_STATUSES:
                batch.status = "cancelled" if batch.cancel_requested else "failed"
                batch.error, batch.finished_at, batch.current_position = str(exc) or type(exc).__name__, utcnow(), None
                jobs = (await db.scalars(select(PlaybackJob).where(
                    PlaybackJob.batch_id == batch_id, PlaybackJob.status.in_(("queued", "playing")),
                ))).all()
                for job in jobs:
                    job.status, job.error, job.finished_at = batch.status, batch.error, utcnow()
                batch.failed_count = sum(j.status == "failed" for j in jobs) + batch.failed_count
                await db.commit()
    return batch_id


LAUNCH_BACKOFF = (30, 300)  # Chrome을 못 띄우면 30초부터 두 배씩, 최대 5분 기다린다
ADMIN_ALERT_INTERVAL = 3600
# 큐가 비었을 때 다음 묶음을 찾는 간격. 새 재생이 이만큼 늦게 시작될 수 있다(재생 중에는 영상 간격만 쉰다).
IDLE_POLL_SECONDS = 3


class Loop:
    """재생 루프 한 바퀴(step)와 그 사이의 상태. step은 다음 바퀴까지 기다릴 초를 돌려준다."""

    def __init__(self, manager: BrowserManager, make_player, settings, *, alert=None, clock=time.monotonic) -> None:
        self.manager, self.make_player, self.settings = manager, make_player, settings
        self.alert, self.clock = alert or _alert_admins, clock
        self.backoff = 0.0
        self.last_alert: float | None = None

    async def player(self):
        return self.make_player(await self.manager.acquire())

    async def step(self) -> float:
        try:
            batch_id = await process_next(self.player)
        except BrowserUnavailable as e:
            self.backoff = min(self.backoff * 2, LAUNCH_BACKOFF[1]) if self.backoff else LAUNCH_BACKOFF[0]
            log.error("Chrome을 띄우지 못해 재생을 미룹니다(%.0f초 뒤 재시도): %s", self.backoff, e)
            now = self.clock()
            if self.last_alert is None or now - self.last_alert >= ADMIN_ALERT_INTERVAL:
                self.last_alert = now
                try:
                    await self.alert(f"[재생 워커] Chrome을 띄우지 못해 재생 대기열이 멈춰 있습니다. 자동으로 다시 시도합니다. ({e})"[:1900])
                except Exception:
                    log.exception("관리자 알림 실패")
            return self.backoff
        self.backoff = 0.0
        if batch_id is None:
            await self.manager.close_if_idle()  # 큐가 비었을 때만 닫는다
            return IDLE_POLL_SECONDS
        self.manager.release()
        await self.manager.recycle_if_old()  # 영상과 영상 사이에서만 새로 띄운다
        return sum(self.settings.playback_gap_seconds) / 2


async def _alert_admins(text: str) -> None:
    async with sessionmaker()() as db:
        await notify_admins(db, text)


async def _heartbeat(connection):
    while True:
        await asyncio.sleep(1)
        # A lost DB session means the singleton lock is lost: stop playback immediately.
        await asyncio.wait_for(connection.scalar(text("SELECT 1")), timeout=5)
        await connection.commit()


async def run_guarded(connection, work):
    tasks = [asyncio.create_task(_heartbeat(connection)), asyncio.create_task(work())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def serve():
    from playwright.async_api import async_playwright
    from app.lms.player import BROWSER_ARGS, VideoPlayer

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    engine = init_engine()
    try:
        async with exclusive_worker(engine) as connection:
            await recover_interrupted()
            async with async_playwright() as pw:
                manager = BrowserManager(
                    lambda: pw.chromium.launch(
                        channel=settings.playback_browser_channel or None,
                        headless=settings.playback_headless, args=BROWSER_ARGS,
                    ),
                    idle_seconds=settings.playback_browser_idle_seconds,
                    max_age_seconds=settings.playback_browser_max_age_hours * 3600,
                )
                log.info("재생 워커 시작: Chrome 쉬는 동안 종료 %s, 최대 수명 %s",
                         f"{settings.playback_browser_idle_seconds}초" if settings.playback_browser_idle_seconds > 0 else "끔",
                         f"{settings.playback_browser_max_age_hours}시간" if settings.playback_browser_max_age_hours > 0 else "끔")
                loop = Loop(manager, lambda browser: VideoPlayer(browser, settings), settings)

                async def work():
                    while True:
                        await asyncio.sleep(await loop.step())

                try:
                    await run_guarded(connection, work)
                finally:
                    await manager.close("shutdown")
    finally:
        await engine.dispose()


async def main():
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await serve()
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
