from datetime import timedelta

import respx
from sqlalchemy import select

from app.db import base as db_base
from app.db.base import utcnow
from app.db.models import CourseRow, LmsCredential, PlaybackBatch, PlaybackJob, TodoRow, User
from app.worker import main as worker
from tests.test_api import COURSE_ID, WEBHOOK, LmsMock, _signup, env  # noqa: F401  (env는 fixture)


class Q:
    def __init__(self):
        self.jobs = []

    async def enqueue(self, function, *args, job_id=None):
        self.jobs.append((function, args, job_id))


async def _setup(api):
    h = await _signup(api)
    await api.post("/notifications/channels", headers=h, json={"kind": "discord", "webhook_url": WEBHOOK})
    return h


@respx.mock
async def test_sync_job_creates_auto_batch_only_for_auto_courses(env):
    api, _ = env
    h = await _setup(api)
    respx.post(WEBHOOK).respond(204)
    LmsMock().install()
    ctx = {"queue": Q()}

    assert await worker.sync_user_job(ctx, 1) == "ok"
    assert ctx["queue"].jobs == []  # 자동 수강 꺼짐(기본값)

    await api.put(f"/courses/{COURSE_ID}/auto-play", headers=h, json={"enabled": True})
    assert await worker.sync_user_job(ctx, 1) == "ok"
    assert ctx["queue"].jobs == []
    async with db_base.sessionmaker()() as db:
        batch = await db.scalar(select(PlaybackBatch))
        assert batch.scope == "auto" and batch.total == 3

    # 진행 중인 묶음이 있으면 또 만들지 않는다
    assert await worker.sync_user_job(ctx, 1) == "ok"
    async with db_base.sessionmaker()() as db:
        assert len((await db.scalars(select(PlaybackBatch))).all()) == 1


@respx.mock
async def test_auto_batch_skips_recent_failures(env):
    api, _ = env
    h = await _setup(api)
    respx.post(WEBHOOK).respond(204)
    LmsMock().install()
    await worker.sync_user_job({"queue": Q()}, 1)
    await api.put(f"/courses/{COURSE_ID}/auto-play", headers=h, json={"enabled": True})

    async with db_base.sessionmaker()() as db:
        batch = await worker.create_auto_batch(db, 1)
        jobs = (await db.scalars(select(PlaybackJob).where(PlaybackJob.batch_id == batch.id))).all()
        for j in jobs:
            j.status = "failed" if j.module_item_id == 21 else "done"
            j.finished_at = utcnow()
        batch.status = "partial"
        await db.commit()

        again = await worker.create_auto_batch(db, 1)
        assert again.total == 3 - 1  # 21번은 쿨다운 동안 제외 (11, 12는 LMS mock상 아직 미완료)


@respx.mock
async def test_auto_batch_gives_up_after_three_attempted_failures(env):
    """마감 없는 영상은 미수강 목록에서 빠지지 않는다. 자동 묶음에서 시도해 3번 실패하면 자동으로는 더 넣지 않는다."""
    api, _ = env
    h = await _setup(api)
    respx.post(WEBHOOK).respond(204)
    LmsMock().install()
    await worker.sync_user_job({"queue": Q()}, 1)
    await api.put(f"/courses/{COURSE_ID}/auto-play", headers=h, json={"enabled": True})
    long_ago = utcnow() - worker.AUTO_RETRY_COOLDOWN - timedelta(hours=1)  # 쿨다운은 이미 지났다

    async def run_auto(fail: dict[int, str]):
        async with db_base.sessionmaker()() as db:
            batch = await worker.create_auto_batch(db, 1)
            jobs = (await db.scalars(select(PlaybackJob).where(PlaybackJob.batch_id == batch.id))).all()
            for j in jobs:
                j.status = fail.get(j.module_item_id, "done")
                j.started_at = j.finished_at = long_ago
            batch.status = "partial"
            await db.commit()
            return sorted(j.module_item_id for j in jobs)

    async with db_base.sessionmaker()() as db:  # 시도하지 못하고 끝난 실패(started_at 없음)는 세지 않는다
        never = PlaybackBatch(user_id=1, scope="auto", status="failed", total=1)
        db.add(never)
        await db.flush()
        db.add(PlaybackJob(batch_id=never.id, user_id=1, position=0, lms_course_id=COURSE_ID, module_item_id=21,
                           title="x", duration=60, video={}, status="failed", finished_at=long_ago))
        await db.commit()
    assert await run_auto({21: "failed", 11: "failed"}) == [11, 12, 21]
    assert await run_auto({21: "locked", 11: "failed"}) == [11, 12, 21]
    assert await run_auto({21: "failed"}) == [11, 12, 21]
    # 4번째 자동 묶음: 21번만 빠진다. 11번은 실패가 2번뿐이라 그대로 들어간다.
    assert await run_auto({}) == [11, 12]

    # 수동 재생은 여전히 21번을 넣는다
    r = await api.post("/playback/batches", headers=h, json={"scope": "all"})
    assert r.status_code == 201, r.text
    async with db_base.sessionmaker()() as db:
        manual = await db.get(PlaybackBatch, r.json()["id"])
        assert manual.scope == "all"
        ids = (await db.scalars(select(PlaybackJob.module_item_id).where(PlaybackJob.batch_id == manual.id))).all()
        assert 21 in ids


@respx.mock
async def test_auto_give_up_counts_only_recent_failures(env):
    """자동 묶음의 실패 횟수는 최근 AUTO_GIVE_UP_WINDOW 안의 것만 센다. 오래전 실패(예: LMS 장애)는 다시 세지 않는다."""
    api, _ = env
    h = await _setup(api)
    respx.post(WEBHOOK).respond(204)
    LmsMock().install()
    await worker.sync_user_job({"queue": Q()}, 1)
    await api.put(f"/courses/{COURSE_ID}/auto-play", headers=h, json={"enabled": True})
    old = utcnow() - worker.AUTO_GIVE_UP_WINDOW - timedelta(hours=1)
    recent = utcnow() - worker.AUTO_RETRY_COOLDOWN - timedelta(hours=1)

    async def failures(item, when, count):
        async with db_base.sessionmaker()() as db:
            for _ in range(count):
                b = PlaybackBatch(user_id=1, scope="auto", status="failed", total=1)
                db.add(b)
                await db.flush()
                db.add(PlaybackJob(batch_id=b.id, user_id=1, position=0, lms_course_id=COURSE_ID, module_item_id=item,
                                   title="x", duration=60, video={}, status="failed", started_at=when, finished_at=when))
            await db.commit()

    await failures(21, old, 3)  # 기간 밖: 다시 자동으로 시도한다
    await failures(11, old, 2)
    await failures(11, recent, 1)  # 기간 안 1번 + 기간 밖 2번: 아직 3번이 아니다
    await failures(12, recent, 3)  # 기간 안 3번: 뺀다
    async with db_base.sessionmaker()() as db:
        batch = await worker.create_auto_batch(db, 1)
        ids = sorted((await db.scalars(select(PlaybackJob.module_item_id).where(PlaybackJob.batch_id == batch.id))).all())
    assert ids == [11, 21]


@respx.mock
async def test_manual_failures_do_not_count_toward_auto_give_up(env):
    api, _ = env
    h = await _setup(api)
    respx.post(WEBHOOK).respond(204)
    LmsMock().install()
    await worker.sync_user_job({"queue": Q()}, 1)
    await api.put(f"/courses/{COURSE_ID}/auto-play", headers=h, json={"enabled": True})
    long_ago = utcnow() - worker.AUTO_RETRY_COOLDOWN - timedelta(hours=1)
    async with db_base.sessionmaker()() as db:
        for _ in range(3):
            manual = PlaybackBatch(user_id=1, scope="all", status="failed", total=1)
            db.add(manual)
            await db.flush()
            db.add(PlaybackJob(batch_id=manual.id, user_id=1, position=0, lms_course_id=COURSE_ID, module_item_id=21,
                               title="x", duration=60, video={}, status="failed", started_at=long_ago,
                               finished_at=long_ago))
        await db.commit()
        batch = await worker.create_auto_batch(db, 1)
        ids = (await db.scalars(select(PlaybackJob.module_item_id).where(PlaybackJob.batch_id == batch.id))).all()
        assert sorted(ids) == [11, 12, 21]


@respx.mock
async def test_invalid_password_marks_and_notifies_once(env, monkeypatch):
    from app.lms.errors import InvalidCredentials

    api, _ = env
    await _setup(api)
    discord = respx.post(WEBHOOK).respond(204)

    monkeypatch.setattr("app.services.sync.lms_client_for", _raising_ctx(InvalidCredentials("틀림")))
    assert await worker.sync_user_job({"queue": Q()}, 1) == "invalid"
    assert await worker.sync_user_job({"queue": Q()}, 1) == "invalid"
    assert len(discord.calls) == 1


def _raising_ctx(exc):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def ctx(db, user_id):
        raise exc
        yield

    return ctx


async def test_schedule_syncs_skips_users_without_auto_play(env):
    api, _ = env
    await _setup(api)
    ctx = {"queue": Q()}
    async with db_base.sessionmaker()() as db:
        cred = await db.get(LmsCredential, 1)
        cred.next_sync_at = utcnow() - timedelta(minutes=1)
        db.add(PlaybackBatch(user_id=1, scope="all", status="running", total=0))
        await db.commit()
    assert await worker.schedule_syncs(ctx) == 0  # 자동 재생 과목이 없으면 동기화하지 않는다
    assert ctx["queue"].jobs == []


async def test_deadline_reminders_survive_duplicate_rollbacks(env):
    """중복 알림으로 롤백된 뒤에도 다음 항목을 처리해야 한다(이전엔 MissingGreenlet으로 크론이 실패)."""
    from app.db.models import TodoRow
    from app.services.notify import notify_deadlines

    api, _ = env
    await _setup(api)
    now = utcnow()
    async with db_base.sessionmaker()() as db:
        for i in range(3):
            db.add(TodoRow(user_id=1, lms_course_id=1, course_name="c", kind="assignment", lms_item_id=i,
                           title=f"과제{i}", deadline=now + timedelta(hours=2)))
        await db.commit()

    with respx.mock:
        respx.post(WEBHOOK).respond(204)
        async with db_base.sessionmaker()() as db:
            assert await notify_deadlines(db, 1) == 3
            assert await notify_deadlines(db, 1) == 0  # 모두 중복 → 롤백이 반복돼도 실패하지 않아야 함
    assert await worker.deadline_reminders({}) == 0


@respx.mock
async def test_auto_sync_creates_auto_batch(env):
    api, _ = env
    h = await _setup(api)
    respx.post(WEBHOOK).respond(204)
    LmsMock().install()
    await worker.sync_user_job({"queue": Q()}, 1)
    await api.put(f"/courses/{COURSE_ID}/auto-play", headers=h, json={"enabled": True})
    assert await worker.auto_sync_user_job({"queue": Q()}, 1) == "ok"
    async with db_base.sessionmaker()() as db:
        batch = await db.scalar(select(PlaybackBatch))
        assert batch.scope == "auto" and batch.total == 3


async def test_full_sync_job_not_dropped_by_auto_sync(env):
    """arq는 같은 job_id가 큐에 있으면 새 작업을 버린다. 전체와 자동 동기화는 ID가 달라야 한다."""
    from arq.connections import ArqRedis  # noqa: F401
    from app.api.deps import Queue

    seen: dict[str, object] = {}

    class Redis:
        async def enqueue_job(self, function, *args, _job_id=None, **kw):
            if _job_id in seen:
                return None  # arq처럼 중복 ID는 버린다
            seen[_job_id] = function
            return object()

    q = Queue(Redis())
    await q.enqueue("auto_sync_user_job", 1, job_id="autosync:1")
    await q.enqueue("sync_user_job", 1, job_id="sync:1")
    assert seen == {"autosync:1": "auto_sync_user_job", "sync:1": "sync_user_job"}


async def test_unapproved_get_no_notifications(env):
    from app.services.notify import notify_deadlines, notify_user

    api, _ = env
    await _setup(api)
    async with db_base.sessionmaker()() as db:
        db.add(TodoRow(user_id=1, lms_course_id=1, course_name="c", kind="assignment", lms_item_id=1,
                       title="과제", deadline=utcnow() + timedelta(hours=2)))
        user = await db.get(User, 1)
        user.access_status = "revoked"
        await db.commit()
    with respx.mock:
        discord = respx.post(WEBHOOK).respond(204)
        async with db_base.sessionmaker()() as db:
            assert await notify_user(db, 1, "hi", key="k") is False
            assert await notify_deadlines(db, 1) == 0
        assert await worker.deadline_reminders({}) == 0
        assert len(discord.calls) == 0
        async with db_base.sessionmaker()() as db:  # 승인되면 같은 키로 보낼 수 있다
            (await db.get(User, 1)).access_status = "approved"
            await db.commit()
            assert await notify_user(db, 1, "hi", key="k") is True
        assert len(discord.calls) == 1


async def test_worker_cancels_batch_of_unapproved_owner(env):
    from app.services.playback import run_batch

    api, _ = env
    await _setup(api)
    async with db_base.sessionmaker()() as db:
        b = PlaybackBatch(user_id=1, scope="all", status="queued", total=1)
        db.add(b)
        await db.flush()
        db.add(PlaybackJob(batch_id=b.id, user_id=1, position=0, lms_course_id=COURSE_ID, module_item_id=11,
                           title="x", duration=60, video={}, status="queued"))
        (await db.get(User, 1)).access_status = "revoked"
        await db.commit()
        batch_id = b.id

    class Boom:
        def __getattr__(self, name):
            raise AssertionError("재생하면 안 된다")

    assert await run_batch(batch_id, Boom()) == "cancelled"
    async with db_base.sessionmaker()() as db:
        assert (await db.get(PlaybackBatch, batch_id)).status == "cancelled"
        assert (await db.scalar(select(PlaybackJob))).status == "cancelled"


async def test_jobs_denied_for_unapproved_user(env):
    api, _ = env
    await _setup(api)
    async with db_base.sessionmaker()() as db:
        (await db.get(User, 1)).access_status = "pending"
        await db.commit()
    assert await worker.sync_user_job({}, 1) == "denied"
    assert await worker.auto_sync_user_job({}, 1) == "denied"


class FakeRedis:
    """arq 락에 쓰는 redis 명령(set nx/ex, exists, delete)만 흉내 낸다."""

    def __init__(self):
        self.keys: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    async def exists(self, *keys):
        return sum(k in self.keys for k in keys)

    async def delete(self, *keys):
        for k in keys:
            self.keys.pop(k, None)


async def test_auto_sync_waits_for_queued_or_running_full_sync(env):
    api, _ = env
    await _setup(api)
    redis = FakeRedis()
    redis.keys["arq:job:sync:1"] = "queued"  # 전체 동기화가 큐에 있거나 돌고 있다
    assert await worker.auto_sync_user_job({"redis": redis}, 1) == "busy"
    assert "synclock:1" not in redis.keys  # 락을 남기지 않는다


async def test_full_sync_deferred_not_dropped_while_auto_sync_runs(env, monkeypatch):
    """자동 동기화가 도는 중에 전체 동기화가 들어오면 Retry로 미뤄지고, 끝난 뒤에는 돈다."""
    import asyncio

    from arq.worker import Retry

    api, _ = env
    await _setup(api)
    redis = FakeRedis()
    ctx = {"redis": redis, "queue": Q()}
    entered, release, calls = asyncio.Event(), asyncio.Event(), []

    async def gated_sync(db, user_id, now=None, course_ids=None):
        calls.append(course_ids)
        entered.set()
        await release.wait()
        from app.services.sync import SyncResult
        return SyncResult(first_sync=False)

    async with db_base.sessionmaker()() as db:
        db.add(CourseRow(user_id=1, lms_course_id=COURSE_ID, term_id=46, name="A", auto_play=True, active=True))
        await db.commit()
    monkeypatch.setattr(worker, "sync_user", gated_sync)

    auto = asyncio.create_task(worker.auto_sync_user_job(ctx, 1))
    await entered.wait()
    import pytest
    with pytest.raises(Retry):  # 전체 동기화는 버려지지도 실패하지도 않고 미뤄진다
        await worker.sync_user_job(ctx, 1)
    assert calls == [{COURSE_ID}]  # 겹쳐 돌지 않았다
    release.set()
    assert await auto == "ok"
    assert "synclock:1" not in redis.keys
    assert await worker.sync_user_job(ctx, 1) == "ok"
    assert calls == [{COURSE_ID}, None]
