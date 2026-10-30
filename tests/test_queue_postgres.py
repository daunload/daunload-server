"""Opt-in real PostgreSQL/Redis checks; each test owns an isolated schema.

QUEUE_TEST_DATABASE_URL=postgresql+asyncpg://... QUEUE_TEST_REDIS_URL=redis://... pytest tests/test_queue_postgres.py
LMS and playback are simulated; no school account or video is accessed.
"""
import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db.base import Base, init_engine, sessionmaker
from app.db.models import PlaybackBatch, PlaybackJob
from app.main import create_app
from app.security import create_access_token
from app.services import playback
from app.worker import playback as worker
from tests.test_playback_queue import add_user, enqueue, video, fake_lms  # noqa: F401

pytestmark = pytest.mark.skipif(not os.getenv("QUEUE_TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test service")


@pytest.fixture
async def database(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "queue-test-secret-queue-test-secret!")
    monkeypatch.setenv("PLAYBACK_EXTRA_SECONDS", "0")
    monkeypatch.setenv("PLAYBACK_GAP_SECONDS", "[0,0]")
    get_settings.cache_clear()
    schema = "queue_test_" + uuid.uuid4().hex
    engine = init_engine(os.environ["QUEUE_TEST_DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine.update_execution_options(schema_translate_map={None: schema})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine, schema
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
        get_settings.cache_clear()


async def test_60_concurrent_api_requests_and_redis_independence(database, monkeypatch):
    users = [await add_user(f"user-{i}") for i in range(30)]
    monkeypatch.setattr(playback, "pending_videos_from_db", AsyncMock(return_value=[video()]))

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = create_app(lifespan)
    app.state.queue = SimpleNamespace(enqueue=AsyncMock(side_effect=ConnectionError("Redis unavailable")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        async def submit(uid):
            return await api.post("/playback/batches", json={"scope": "all"},
                                  headers={"Authorization": "Bearer " + create_access_token(uid)})
        responses = await asyncio.gather(*(submit(uid) for uid in users for _ in range(2)))
    assert sum(r.status_code == 201 for r in responses) == 30, [r.text for r in responses if r.status_code not in (201,409)]
    assert sum(r.status_code == 409 for r in responses) == 30
    app.state.queue.enqueue.assert_not_awaited()
    async with sessionmaker()() as db:
        assert await db.scalar(select(func.count()).select_from(PlaybackBatch)) == 30


async def test_real_singleton_lock(database):
    engine, _ = database
    async with worker.exclusive_worker(engine):
        with pytest.raises(RuntimeError, match="이미 실행 중"):
            async with worker.exclusive_worker(engine):
                pytest.fail("duplicate worker accepted")
    async with worker.exclusive_worker(engine):
        pass  # the previous holder really released its DB session lock


async def test_cancel_start_race(database, fake_lms):
    uid = await add_user("cancel")
    for _ in range(10):
        batch = await enqueue(uid)
        async def cancel():
            async with sessionmaker()() as db:
                await playback.cancel_batch(db, batch.id, uid)
        await asyncio.gather(
            playback.run_batch(batch.id, SimpleNamespace(play=AsyncMock()), one_item=True, cancel_poll_seconds=.01),
            cancel(),
        )
        async with sessionmaker()() as db:
            saved = await db.get(PlaybackBatch, batch.id)
            assert saved.status == "cancelled"
            jobs = (await db.scalars(select(PlaybackJob).where(PlaybackJob.batch_id == batch.id))).all()
            assert all(j.status not in ("queued", "playing") for j in jobs)


async def test_sigkill_then_new_process_recovers(database):
    _, schema = database
    batch = await enqueue(await add_user("crash"), count=1)
    child_env = {**os.environ, "QUEUE_TEST_SCHEMA": schema}
    async def spawn(mode):
        return await asyncio.create_subprocess_exec(
            sys.executable, "-m", "tests.queue_process_helper", mode, env=child_env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    child = await spawn("block")
    try:
        line = await asyncio.wait_for(child.stdout.readline(), 10)
        assert line == b"playing\n", (line, await child.stderr.read())
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
    child = await spawn("finish")
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), 10)
        assert child.returncode == 0, stderr.decode()
        assert b"playing" in stdout
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    async with sessionmaker()() as db:
        saved = await db.get(PlaybackBatch, batch.id)
        assert saved.status == "done" and saved.done_count == 1


async def test_real_redis_sync_queue_remains_usable(database):
    url = os.getenv("QUEUE_TEST_REDIS_URL")
    if not url:
        pytest.skip("requires isolated Redis test service")
    from arq import create_pool
    from arq.connections import RedisSettings
    from arq.worker import Worker, func as arq_func
    from app.api.deps import Queue

    key = "queue-test:" + uuid.uuid4().hex
    redis = await create_pool(RedisSettings.from_dsn(url), default_queue_name=key)
    seen = []
    async def sync_test(ctx, uid):
        seen.append(uid)
    executor = Worker([arq_func(sync_test, name="sync_test")], redis_pool=redis, queue_name=key, burst=True, keep_result=0)
    try:
        await asyncio.gather(*(Queue(redis).enqueue("sync_test", i, job_id=f"{key}:{i}") for i in range(30)))
        await executor.async_run()
        assert sorted(seen) == list(range(30))
    finally:
        await executor.close()
        await redis.aclose()
