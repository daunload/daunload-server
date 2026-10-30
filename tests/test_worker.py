from datetime import timedelta

import respx
from sqlalchemy import select

from app.db import base as db_base
from app.db.base import utcnow
from app.db.models import LmsCredential, PlaybackBatch, PlaybackJob
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


async def test_schedule_syncs(env):
    api, _ = env
    await _setup(api)
    ctx = {"queue": Q()}
    async with db_base.sessionmaker()() as db:
        cred = await db.get(LmsCredential, 1)
        cred.next_sync_at = utcnow() - timedelta(minutes=1)
        db.add(PlaybackBatch(user_id=1, scope="all", status="running", total=0))
        await db.commit()
    assert await worker.schedule_syncs(ctx) == 1
    assert ctx["queue"].jobs[-1] == ("sync_user_job", (1,), "sync:1")


async def test_deadline_reminders_survive_duplicate_rollbacks(env):
    """중복 알림으로 롤백된 뒤에도 다음 항목을 처리해야 한다(이전엔 MissingGreenlet으로 크론이 실패)."""
    from app.db.models import AnnouncementRow, TodoRow
    from app.services.notify import notify_announcements, notify_deadlines

    api, _ = env
    await _setup(api)
    now = utcnow()
    async with db_base.sessionmaker()() as db:
        for i in range(3):
            db.add(TodoRow(user_id=1, lms_course_id=1, course_name="c", kind="assignment", lms_item_id=i,
                           title=f"과제{i}", deadline=now + timedelta(hours=2)))
        anns = [AnnouncementRow(user_id=1, lms_course_id=1, course_name="c", lms_id=i, title=f"공지{i}") for i in range(3)]
        db.add_all(anns)
        await db.commit()

    with respx.mock:
        respx.post(WEBHOOK).respond(204)
        async with db_base.sessionmaker()() as db:
            assert await notify_deadlines(db, 1) == 3
            assert await notify_deadlines(db, 1) == 0  # 모두 중복 → 롤백이 반복돼도 실패하지 않아야 함
        async with db_base.sessionmaker()() as db:
            rows = list((await db.scalars(select(AnnouncementRow))).all())
            await notify_announcements(db, 1, rows)
            await notify_announcements(db, 1, rows)
    assert await worker.deadline_reminders({}) == 0
