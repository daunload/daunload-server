import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import Queue
from app.db.base import sessionmaker, utcnow
from app.db.models import PlaybackBatch, PlaybackJob, User
from app.lms.todo import VideoTodo
from app.services import playback
from app.worker import playback as worker
from tests.test_api import _signup, env  # noqa: F401


def video(index=1, duration=60, deadline=None):
    return VideoTodo("video", 1, "course", 1, "module", 1, index, index, index,
                     f"video {index}", duration, "https://example.test/video", None,
                     deadline or utcnow() + timedelta(days=1), None)


async def add_user(student_id):
    async with sessionmaker()() as db:
        user = User(student_id=student_id, password_hash="test", consent_at=utcnow())
        db.add(user)
        await db.commit()
        return user.id


async def enqueue(user_id, count=2, **kwargs):
    async with sessionmaker()() as db:
        return await playback.create_batch(db, user_id, "all", [video(i + 1, **kwargs) for i in range(count)])


@pytest.fixture
def fake_lms(monkeypatch):
    @asynccontextmanager
    async def client(db, user_id):
        yield SimpleNamespace(session=user_id)

    async def refresh(self, v):
        return v if v.deadline > utcnow() else None

    monkeypatch.setattr(playback, "lms_client_for", client)
    monkeypatch.setattr(playback.BatchRunner, "_refresh", refresh)
    monkeypatch.setattr("app.lms.player.verify_completed", AsyncMock(return_value=True))
    monkeypatch.setattr(playback, "notify_user", AsyncMock())


async def test_sync_still_uses_redis():
    redis = AsyncMock()
    await Queue(redis).enqueue("sync_user_job", 1, job_id="sync:1")
    redis.enqueue_job.assert_awaited_once_with("sync_user_job", 1, _job_id="sync:1")


async def test_round_robin_and_estimates(env, fake_lms):
    a = await enqueue(await add_user("a"), count=3)
    b = await enqueue(await add_user("b"), count=2)
    c = await enqueue(await add_user("c"), count=1)
    async with sessionmaker()() as db:
        estimates = await playback.queue_estimates(db)
    assert [estimates[x.id]["queue_position"] for x in (a, b, c)] == [1, 2, 3]
    assert estimates[b.id]["estimated_wait_seconds"] == 60
    assert estimates[a.id]["job_estimates"][1]["expected_start"] == estimates[c.id]["expected_end"]
    played = []

    async def play(user, v, **kw):
        played.append((user, v.module_item_id))

    factory = AsyncMock(return_value=SimpleNamespace(play=play))
    turns = [await worker.process_next(factory) for _ in range(6)]
    assert turns == [a.id, b.id, c.id, a.id, b.id, a.id]
    assert played == [(a.user_id, 1), (b.user_id, 1), (c.user_id, 1), (a.user_id, 2), (b.user_id, 2), (a.user_id, 3)]
    assert await worker.process_next(factory) is None
    async with sessionmaker()() as db:
        batches = (await db.scalars(select(PlaybackBatch))).all()
        assert all(x.status == "done" and x.done_count == x.total for x in batches)


async def test_database_rejects_duplicate_active_batch(env):
    uid = await add_user("a")
    await enqueue(uid)
    async with sessionmaker()() as db:
        db.add(PlaybackBatch(user_id=uid, scope="all", status="queued", total=0))
        with pytest.raises(IntegrityError):
            await db.commit()
    with pytest.raises(playback.ActiveBatchExists):
        await enqueue(uid)


async def test_cancelled_queue_entry_never_starts_browser(env):
    uid = await add_user("a")
    batch = await enqueue(uid)
    async with sessionmaker()() as db:
        await playback.cancel_batch(db, batch.id, uid)
    factory = AsyncMock()
    assert await worker.process_next(factory) is None
    factory.assert_not_awaited()
    async with sessionmaker()() as db:
        assert all(j.status == "cancelled" for j in (await db.scalars(select(PlaybackJob))).all())


async def test_cancel_during_play_stops_child_and_finishes(env, fake_lms):
    uid = await add_user("a")
    batch = await enqueue(uid)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def play(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(playback.run_batch(batch.id, SimpleNamespace(play=play), one_item=True, cancel_poll_seconds=.01))
    await asyncio.wait_for(started.wait(), 2)
    async with sessionmaker()() as db:
        await playback.cancel_batch(db, batch.id, uid)
    assert await asyncio.wait_for(task, 2) == "cancelled"
    assert stopped.is_set()


async def test_interrupt_recovery_preserves_finished_items(env, fake_lms):
    batch = await enqueue(await add_user("a"), count=3)
    factory = AsyncMock(return_value=SimpleNamespace(play=AsyncMock()))
    await worker.process_next(factory)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def play(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(playback.run_batch(batch.id, SimpleNamespace(play=play), one_item=True))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert await worker.recover_interrupted() == 1
    async with sessionmaker()() as db:
        jobs = (await db.scalars(select(PlaybackJob).order_by(PlaybackJob.position))).all()
        assert [j.status for j in jobs] == ["done", "queued", "queued"]
    await worker.process_next(factory)
    await worker.process_next(factory)
    assert factory.return_value.play.await_count == 3


async def test_deadline_risk_and_expired_skip(env, fake_lms):
    await enqueue(await add_user("a"), count=1, duration=600)
    b = await enqueue(await add_user("b"), count=1, deadline=utcnow() + timedelta(seconds=100))
    async with sessionmaker()() as db:
        estimates = await playback.queue_estimates(db)
        assert estimates[b.id]["deadline_at_risk"]
        assert estimates[b.id]["estimated_wait_seconds"] == 600
    expired = await enqueue(await add_user("c"), count=1, deadline=utcnow() - timedelta(seconds=1))
    player = SimpleNamespace(play=AsyncMock())
    assert await playback.run_batch(expired.id, player, one_item=True) == "done"
    player.play.assert_not_awaited()


async def test_lock_loss_cancels_work():
    connection = AsyncMock()
    connection.scalar.side_effect = ConnectionError("lost")
    stopped = asyncio.Event()

    async def work():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    with pytest.raises(ConnectionError):
        await worker.run_guarded(connection, work)
    assert stopped.is_set()


async def test_queue_api_does_not_expose_other_users(env):
    api, queue = env
    headers = await _signup(api)
    own = await enqueue(1)
    other = await enqueue(await add_user("other"))
    response = await api.get(f"/playback/batches/{own.id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["queue_position"] == 1
    assert all(j["expected_end"] for j in response.json()["jobs"])
    assert (await api.get(f"/playback/batches/{other.id}", headers=headers)).status_code == 404
    assert (await api.delete(f"/playback/batches/{other.id}", headers=headers)).status_code == 404
