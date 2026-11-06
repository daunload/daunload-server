"""Opt-in real PostgreSQL check of every admin GET query; each test owns an isolated schema.

QUEUE_TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/test_admin_postgres.py
"""
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, text

from app.config import get_settings
from app.db.base import Base, init_engine, sessionmaker
from app.db.models import (AssignmentRow, CourseRow, DownloadRecord, LmsCredential, NotificationChannel,
                           NotificationLog, PlaybackBatch, PlaybackJob, SavedVideo, TodoRow, Transcript,
                           User, UserDailyActivity, utcnow)
from app.main import create_app
from app.security import create_access_token
from app.services.activity import bump_daily, local_day

pytestmark = pytest.mark.skipif(not os.getenv("QUEUE_TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test service")


@pytest.fixture
async def database(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "queue-test-secret-queue-test-secret!")
    get_settings.cache_clear()
    schema = "admin_test_" + uuid.uuid4().hex
    engine = init_engine(os.environ["QUEUE_TEST_DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine.update_execution_options(schema_translate_map={None: schema})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
        get_settings.cache_clear()


async def _seed():
    now = utcnow()
    async with sessionmaker()() as db:
        admin = User(student_id="20201234", password_hash="x", consent_at=now, last_login_at=now, login_count=1)
        user = User(student_id="20209999", password_hash="x", consent_at=now, last_login_at=now - timedelta(days=1))
        quiet = User(student_id="20205555", password_hash="x", consent_at=now)  # 로그인 기록 없음(NULL 정렬)
        db.add_all([admin, user, quiet])
        await db.flush()
        db.add_all([
            LmsCredential(user_id=admin.id, student_id="20201234", password_enc="x", status="ok"),
            LmsCredential(user_id=user.id, student_id="20209999", password_enc="x", status="error",
                          last_error="LMS 오류", last_error_at=now),
        ])
        ok_batch = PlaybackBatch(user_id=user.id, scope="all", status="done", created_at=now, started_at=now, finished_at=now)
        bad_batch = PlaybackBatch(user_id=user.id, scope="course", lms_course_id=1, status="failed", created_at=now,
                                  started_at=now, finished_at=now, error="중단됨")
        db.add_all([ok_batch, bad_batch])
        await db.flush()

        def job(batch, n, status, **kw):
            return PlaybackJob(batch_id=batch.id, user_id=user.id, position=n, lms_course_id=1, module_item_id=n,
                               title=f"영상{n}", duration=60, video={"url": "PRIVATE"}, status=status,
                               started_at=now, finished_at=now, **kw)
        db.add_all([
            job(ok_batch, 1, "done"),
            job(bad_batch, 2, "failed", error="실패 https://cdn.test/a?token=S"),
            job(bad_batch, 3, "locked"),
        ])
        db.add_all([
            CourseRow(user_id=user.id, lms_course_id=1, name="과목"),
            TodoRow(user_id=user.id, lms_course_id=1, course_name="과목", kind="video", lms_item_id=1, title="v"),
            AssignmentRow(user_id=user.id, lms_course_id=1, course_name="과목", assignment_id=1, title="a",
                          kind="assignment", submission_state="unsubmitted"),
            DownloadRecord(user_id=user.id, lms_course_id=1, item_key="v1", kind="video", title="d", count=3),
            Transcript(user_id=user.id, lms_course_id=1, module_item_id=1, course_name="과목", title="t",
                       text="PRIVATE", model="m"),
            SavedVideo(user_id=user.id, lms_course_id=1, course_name="과목", module_item_id=1, module_title="m",
                       title="s", content_id="c", file_name="f.mp4", resolve_error="형식 오류", resolve_failed_at=now),
            NotificationChannel(user_id=user.id, kind="discord", target_enc="PRIVATE"),
            NotificationLog(user_id=user.id, key="k"),
            UserDailyActivity(user_id=user.id, day=local_day() - timedelta(days=1), logins=1, downloads=2),
        ])
        await db.commit()
        return admin.id


async def test_every_admin_get_route_runs_on_postgres(database, monkeypatch):
    admin_id = await _seed()
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = create_app(lifespan)
    app.state.queue = SimpleNamespace()
    headers = {"Authorization": "Bearer " + create_access_token(admin_id)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        async def get(path):
            r = await api.get(path, headers=headers)
            assert r.status_code == 200, f"{path}: {r.text}"
            return r.json()

        for days in (7, 30, 90):
            body = await get(f"/admin/overview?days={days}")
            assert len(body["series"]) == days and body["total_users"] == 3
            assert body["playback_done"] == 1 and body["playback_failed"] == 2 and body["problem_count"] >= 3
            assert body["downloads"] == 2 and body["recent_activity"] and body["recent_problems"]

        for kind, expected in (("all", 5), ("playback", 3), ("download", 1), ("transcript", 1)):
            body = await get(f"/admin/activities?kind={kind}&days=30")
            assert body["total"] == expected and len(body["activities"]) == expected, kind
        assert (await get("/admin/activities?student_id=20209999"))["total"] == 5

        for kind in ("all", "playback", "batch", "lms", "resolve"):
            body = await get(f"/admin/problems?kind={kind}&days=30")
            assert body["total"] >= 1 and body["problems"], kind
        assert (await get("/admin/problems?kind=playback"))["total"] == 2
        assert (await get("/admin/problems?student_id=20209999&lms_course_id=1"))["total"] >= 2

        for sort, order in (("activity", ["20201234", "20209999", "20205555"]),
                            ("created", None), ("student_id", ["20201234", "20205555", "20209999"])):
            body = await get(f"/admin/users?sort={sort}")
            assert body["total"] == 3
            if order:
                assert [u["student_id"] for u in body["users"]] == order, sort
        assert (await get("/admin/users?lms_status=error"))["total"] == 1
        assert (await get("/admin/users?lms_status=ok"))["total"] == 1
        assert (await get("/admin/users?has_problem=true"))["total"] == 1
        assert (await get("/admin/users?has_problem=false"))["total"] == 2
        assert (await get("/admin/users?q=9999"))["total"] == 1

        base = "/admin/users/20209999"
        assert (await get(f"{base}/settings"))["lms"]["status"] == "error"
        for kind, expected in (("all", 5), ("playback", 3), ("download", 1), ("transcript", 1)):
            assert (await get(f"{base}/activities?kind={kind}"))["total"] == expected, kind
        summary = await get(f"{base}/summary")
        assert summary["counts"]["playback_done"] == 1 and summary["counts"]["playback_failed"] == 2
        for sub in ("courses", "todos", "assignments", "downloads", "saved-videos", "transcripts"):
            assert (await get(f"{base}/{sub}"))["total"] == 1, sub
        notes = await get(f"{base}/notifications")
        assert notes["total"] == 1 and len(notes["channels"]) == 1
        assert (await get(f"{base}/playback"))["total"] == 2
        assert (await get(f"{base}/playback?status=failed&lms_course_id=1&days=30"))["total"] == 1
        for status in ("done", "locked", "queued"):
            assert (await get(f"{base}/playback?status={status}"))["total"] == (0 if status == "queued" else 1)


async def test_bump_daily_twice_sums(database):
    async with sessionmaker()() as db:
        user = User(student_id="20201234", password_hash="x", consent_at=utcnow())
        db.add(user)
        await db.commit()
        await bump_daily(db, user.id, logins=1)
        await bump_daily(db, user.id, downloads=2)
        await bump_daily(db, user.id, logins=1, downloads=1)
        await db.commit()
        row = (await db.execute(select(UserDailyActivity))).scalar_one()
        assert (row.logins, row.downloads) == (2, 3)
