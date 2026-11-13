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
from app.db.models import PlaybackBatch, PlaybackJob, TodoRow, User
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
        user = User(student_id=student_id, consent_at=utcnow())
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


async def test_cancel_settles_batch_the_worker_abandoned(env):
    """워커가 놓친 '재생 중' 묶음은 중지를 요청하면 바로 끝난다. 이미 완료된 강의는 '이미 완료'로 닫는다."""
    uid = await add_user("a")
    batch = await enqueue(uid)
    old = utcnow() - timedelta(days=14)
    async with sessionmaker()() as db:
        b = await db.get(PlaybackBatch, batch.id)
        b.status, b.started_at, b.queue_at, b.current_position = "running", old, old, 0
        jobs = (await db.scalars(select(PlaybackJob).order_by(PlaybackJob.position))).all()
        jobs[0].status, jobs[0].started_at, jobs[0].progress_seconds = "playing", old, 302
        # 첫 강의만 아직 미수강이다. 둘째 강의는 동기화에서 완료로 빠졌다.
        db.add(TodoRow(user_id=uid, lms_course_id=jobs[0].lms_course_id, course_name="course", kind="video",
                       lms_item_id=jobs[0].module_item_id, title=jobs[0].title))
        await db.commit()
    async with sessionmaker()() as db:
        await playback.cancel_batch(db, batch.id, uid)
    async with sessionmaker()() as db:
        b = await db.get(PlaybackBatch, batch.id)
        assert (b.status, b.skipped_count, b.current_position) == ("cancelled", 1, None)
        jobs = (await db.scalars(select(PlaybackJob).order_by(PlaybackJob.position))).all()
        assert [j.status for j in jobs] == ["cancelled", "skipped"]
    await enqueue(uid)  # 막혀 있던 새 재생도 다시 요청할 수 있다


async def test_batch_read_settles_stale_cancel_request(env):
    """이미 '중지 요청됨'에 갇힌 묶음은 화면이 조회할 때 끝난다."""
    api, _ = env
    headers = await _signup(api)
    async with sessionmaker()() as db:
        uid = await db.scalar(select(User.id))
    batch = await enqueue(uid)
    old = utcnow() - timedelta(days=14)
    async with sessionmaker()() as db:
        b = await db.get(PlaybackBatch, batch.id)
        b.status, b.cancel_requested, b.started_at, b.queue_at = "running", True, old, old
        await db.commit()
    res = await api.get(f"/playback/batches/{batch.id}", headers=headers)
    assert res.json()["status"] == "cancelled"
    res = await api.get("/playback/batches", headers=headers)
    assert [b["status"] for b in res.json()] == ["cancelled"]


async def test_recent_cancel_request_waits_for_worker(env):
    uid = await add_user("a")
    batch = await enqueue(uid)
    async with sessionmaker()() as db:
        b = await db.get(PlaybackBatch, batch.id)
        b.status, b.started_at = "running", utcnow()
        await db.commit()
    async with sessionmaker()() as db:
        b = await playback.cancel_batch(db, batch.id, uid)
        assert (b.status, b.cancel_requested) == ("running", True)


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


# ---------- Chrome 수명 관리: 서버 문제는 사용자 실패로 세지 않는다 ----------

async def _statuses():
    async with sessionmaker()() as db:
        batch = (await db.scalars(select(PlaybackBatch))).one()
        jobs = (await db.scalars(select(PlaybackJob).order_by(PlaybackJob.position))).all()
        return batch, jobs


async def test_launch_failure_leaves_batch_untouched(env, fake_lms):
    from app.worker.browser import BrowserUnavailable

    await enqueue(await add_user("a"))
    factory = AsyncMock(side_effect=BrowserUnavailable("no chrome"))
    with pytest.raises(BrowserUnavailable):
        await worker.process_next(factory)
    batch, jobs = await _statuses()
    assert batch.status == "queued" and batch.started_at is None and batch.failed_count == 0
    assert [j.status for j in jobs] == ["queued", "queued"]


async def test_browser_lost_requeues_item_without_failure(env, fake_lms):
    from app.lms.errors import BrowserLost

    await enqueue(await add_user("a"))
    play = AsyncMock(side_effect=[BrowserLost("crashed"), None, None])
    factory = AsyncMock(return_value=SimpleNamespace(play=play))
    await worker.process_next(factory)
    batch, jobs = await _statuses()
    assert batch.status == "queued" and batch.failed_count == 0 and batch.current_position is None
    assert [j.status for j in jobs] == ["queued", "queued"]
    assert jobs[0].attempts == 1 and jobs[0].progress_seconds == 0
    await worker.process_next(factory)
    await worker.process_next(factory)
    batch, jobs = await _statuses()
    assert batch.status == "done" and batch.done_count == 2
    assert [j.status for j in jobs] == ["done", "done"]


async def test_repeated_browser_loss_fails_only_that_item(env, fake_lms):
    from app.lms.errors import BrowserLost
    from app.services.playback import BROWSER_LOSS_LIMIT

    await enqueue(await add_user("a"), count=2)
    play = AsyncMock(side_effect=[BrowserLost("oom")] * BROWSER_LOSS_LIMIT + [None])
    factory = AsyncMock(return_value=SimpleNamespace(play=play))
    for _ in range(BROWSER_LOSS_LIMIT):
        await worker.process_next(factory)
    batch, jobs = await _statuses()
    assert batch.status == "queued"
    assert [j.status for j in jobs] == ["failed", "queued"]
    assert "Chrome" in jobs[0].error and batch.failed_count == 1
    await worker.process_next(factory)
    batch, jobs = await _statuses()
    assert [j.status for j in jobs] == ["failed", "done"]
    assert batch.status == "partial" and batch.failed_count == 1 and batch.done_count == 1


async def test_browser_lost_on_last_item_finishes_batch(env, fake_lms):
    from app.lms.errors import BrowserLost
    from app.services.playback import BROWSER_LOSS_LIMIT

    await enqueue(await add_user("a"), count=1)
    factory = AsyncMock(return_value=SimpleNamespace(play=AsyncMock(side_effect=BrowserLost("oom"))))
    for _ in range(BROWSER_LOSS_LIMIT):
        await worker.process_next(factory)
    batch, jobs = await _statuses()
    assert batch.status == "failed" and batch.finished_at is not None
    assert [j.status for j in jobs] == ["failed"]
    assert await worker.process_next(factory) is None


class FakeManager:
    def __init__(self, fail=False):
        self.fail, self.released, self.idle_checks, self.recycles = fail, 0, 0, 0

    async def acquire(self):
        from app.worker.browser import BrowserUnavailable
        if self.fail:
            raise BrowserUnavailable("no chrome")
        return "browser"

    def release(self):
        self.released += 1

    async def close_if_idle(self):
        self.idle_checks += 1

    async def recycle_if_old(self):
        self.recycles += 1


def _loop(manager, play=None, clock=None):
    alerts = []

    async def alert(text):
        alerts.append(text)

    settings = SimpleNamespace(playback_gap_seconds=(10, 30))
    loop = worker.Loop(manager, lambda b: SimpleNamespace(play=play or AsyncMock()), settings,
                       alert=alert, clock=clock or (lambda: 0.0))
    return loop, alerts


async def test_loop_closes_idle_browser_only_when_queue_empty(env, fake_lms):
    await enqueue(await add_user("a"), count=2)
    manager = FakeManager()
    loop, _ = _loop(manager)
    assert await loop.step() == 20
    assert await loop.step() == 20
    assert manager.idle_checks == 0 and manager.released == 2 and manager.recycles == 2
    assert await loop.step() == worker.IDLE_POLL_SECONDS
    assert manager.idle_checks == 1


async def test_loop_backs_off_and_alerts_once_per_hour(env, fake_lms):
    await enqueue(await add_user("a"))
    now = [0.0]
    manager = FakeManager(fail=True)
    loop, alerts = _loop(manager, clock=lambda: now[0])
    delays = [await loop.step() for _ in range(6)]
    assert delays == [30, 60, 120, 240, 300, 300]
    assert len(alerts) == 1
    now[0] = 3600
    await loop.step()
    assert len(alerts) == 2
    manager.fail = False
    assert await loop.step() == 20  # 회복하면 평소 영상 간격으로 돌아간다
    _, jobs = await _statuses()
    assert [j.status for j in jobs] == ["done", "queued"]
    assert loop.backoff == 0


async def test_queue_estimate_cache_reuses_until_ttl(env, monkeypatch):
    from app.services.playback import QueueEstimateCache

    calls = []

    async def fake(db):
        calls.append(1)
        return {len(calls): {}}

    monkeypatch.setattr(playback, "queue_estimates", fake)
    now = [0.0]
    cache = QueueEstimateCache(ttl=3, clock=lambda: now[0])
    first = await cache.get(None)
    now[0] = 2.9
    assert await cache.get(None) is first and len(calls) == 1
    now[0] = 3.0
    assert await cache.get(None) == {2: {}}
    cache.invalidate()
    assert await cache.get(None) == {3: {}}


async def test_new_batch_appears_in_queue_immediately_despite_cache(env, monkeypatch):
    api, _ = env
    headers = await _signup(api)
    await enqueue(await add_user("other"))
    assert (await api.get("/playback/batches?limit=10", headers=headers)).status_code == 200  # 캐시를 채운다

    async def pending(*args, **kwargs):
        return [video(1)]

    monkeypatch.setattr(playback, "pending_videos_from_db", pending)
    created = await api.post("/playback/batches", json={"scope": "all"}, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["queue_position"] == 2
    detail = (await api.get(f"/playback/batches/{created.json()['id']}", headers=headers)).json()
    assert detail["queue_position"] == 2 and detail["jobs"][0]["expected_end"]
    # 취소도 바로 반영된다
    cancelled = await api.delete(f"/playback/batches/{created.json()['id']}", headers=headers)
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["queue_position"] is None
