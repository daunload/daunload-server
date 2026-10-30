"""DB-backed single playback worker: python -m app.worker.playback.

One video per turn; completed turns move to the back of the durable queue.
No Redis in-progress keys or per-batch expiry affect playback recovery.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import asynccontextmanager

from sqlalchemy import select, text

from app.config import get_settings
from app.db.base import init_engine, sessionmaker, utcnow
from app.db.models import PlaybackBatch, PlaybackJob
from app.services import playback

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
    try:
        player = await player_factory()
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
                browser = None

                async def player_factory():
                    nonlocal browser
                    if browser is None or not browser.is_connected():
                        browser = await pw.chromium.launch(
                            channel=settings.playback_browser_channel or None,
                            headless=settings.playback_headless, args=BROWSER_ARGS,
                        )
                    return VideoPlayer(browser, settings)

                async def work():
                    while True:
                        batch_id = await process_next(player_factory)
                        delay = 1 if batch_id is None else sum(settings.playback_gap_seconds) / 2
                        await asyncio.sleep(delay)

                try:
                    await run_guarded(connection, work)
                finally:
                    if browser:
                        await browser.close()
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
