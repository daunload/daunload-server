"""Opt-in real PostgreSQL check of every admin GET query; each test owns an isolated schema.

QUEUE_TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/test_admin_postgres.py
"""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.config import get_settings
from app.db.base import Base, init_engine, sessionmaker
from app.db.models import (AssignmentRow, CourseRow, DownloadRecord, LmsCredential, NotificationChannel,
                           NotificationLog, PlaybackBatch, PlaybackJob, SavedVideo, TodoRow, Transcript,
                           User, UserDailyActivity, utcnow)
from app.main import create_app
from app.security import create_access_token
from app.services.access import decide_access
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
        admin = User(student_id="20201234", consent_at=now, last_login_at=now, login_count=1)
        user = User(student_id="20209999", consent_at=now, last_login_at=now - timedelta(days=1))
        quiet = User(student_id="20205555", consent_at=now)  # 로그인 기록 없음(NULL 정렬)
        db.add_all([admin, user, quiet])
        await db.flush()
        db.add_all([
            LmsCredential(user_id=admin.id, student_id="20201234", status="ok"),
            LmsCredential(user_id=user.id, student_id="20209999", status="error",
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
        for kind, expected in (("all", 5), ("playback", 3), ("download", 1), ("transcript", 1)):
            assert (await get(f"{base}/activities?kind={kind}"))["total"] == expected, kind
        summary = await get(f"{base}/summary")
        assert summary["lms"]["status"] == "error"
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
        user = User(student_id="20201234", consent_at=utcnow())
        db.add(user)
        await db.commit()
        await bump_daily(db, user.id, logins=1)
        await bump_daily(db, user.id, downloads=2)
        await bump_daily(db, user.id, logins=1, downloads=1)
        await db.commit()
        row = (await db.execute(select(UserDailyActivity))).scalar_one()
        assert (row.logins, row.downloads) == (2, 3)


def _admin_app():
    @asynccontextmanager
    async def lifespan(app):
        yield

    enqueued = []

    async def enqueue(*args, **kwargs):
        enqueued.append(args)

    app = create_app(lifespan)
    app.state.queue = SimpleNamespace(enqueue=enqueue)
    return app, enqueued


async def test_access_queries_and_decisions_on_postgres(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    now = utcnow()
    async with sessionmaker()() as db:
        users = {
            sid: User(student_id=sid, consent_at=now, access_status=st, access_requested_at=when,
                      access_previous_status=prev)
            for sid, st, when, prev in (
                ("20201234", "pending", now, "none"),  # 관리자는 대기 목록에 나오지 않는다
                ("20200001", "pending", now - timedelta(hours=2), "none"),
                ("20200002", "pending", now - timedelta(hours=1), "rejected"),
                ("20200003", "approved", None, None),
                ("20200004", "none", None, None),
                ("20200005", "revoked", None, None),
            )
        }
        db.add_all(users.values())
        await db.commit()
        admin_id = users["20201234"].id
    app, enqueued = _admin_app()
    headers = {"Authorization": "Bearer " + create_access_token(admin_id)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        async def get(path):
            r = await api.get(path, headers=headers)
            assert r.status_code == 200, f"{path}: {r.text}"
            return r.json()

        async def post(student_id, action):
            return await api.post(f"/admin/users/{student_id}/access", json={"action": action}, headers=headers)

        queue = await get("/admin/access-requests")
        assert [i["student_id"] for i in queue["items"]] == ["20200001", "20200002"] and queue["total"] == 2
        assert (await get("/admin/overview"))["pending_count"] == 2
        for status, expected in (("pending", 2), ("approved", 2), ("none", 1), ("revoked", 1), ("rejected", 0)):
            body = await get(f"/admin/users?access={status}")
            assert body["total"] == expected, status
        rows = {u["student_id"]: u for u in (await get("/admin/users"))["users"]}
        assert rows["20201234"]["is_admin"] is True and rows["20200003"]["access_status"] == "approved"

        r = await post("20200001", "approve")
        assert r.status_code == 200 and r.json()["access_status"] == "approved" and r.json()["access_decided_by"] == "20201234"
        assert enqueued == [("sync_user_job", users["20200001"].id)]
        r = await post("20200002", "reject")
        assert r.json()["access_status"] == "rejected"
        assert (await post("20200002", "reject")).status_code == 409
        assert (await post("20200004", "reject")).status_code == 409
        assert (await post("20200003", "revoke")).json()["access_status"] == "revoked"
        assert (await post("20201234", "revoke")).status_code == 400
        assert (await get("/admin/overview"))["pending_count"] == 0  # 관리자 본인의 pending 행은 세지 않는다


async def test_concurrent_decisions_on_same_pending_user(database, monkeypatch):
    """같은 대기 사용자에게 승인과 거절이 동시에 오면 하나만 이기고 나머지는 409."""
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    now = utcnow()
    async with sessionmaker()() as db:
        admin = User(student_id="20201234", consent_at=now)
        target = User(student_id="20209999", consent_at=now, access_status="pending",
                      access_requested_at=now, access_previous_status="none")
        db.add_all([admin, target])
        await db.commit()
        admin, target_id = (await db.get(User, admin.id)), target.id

    pids = []

    async def decide(action):
        async with sessionmaker()() as db:
            user = await db.get(User, target_id)  # 둘 다 pending 상태를 읽는다
            pids.append((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
            try:
                return action, await decide_access(db, user, action, admin)
            except HTTPException as e:
                return action, e.status_code

    async def both_waiting():
        if len(pids) < 2:
            return False
        async with sessionmaker()() as db:
            return (await db.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND pid = ANY(:pids)"
            ), {"pids": list(pids)})).scalar_one() == 2

    # 행 잠금을 쥔 채 두 결정이 모두 UPDATE에서 기다리게 한 뒤 풀어 준다: 시간에 기대지 않고 겹침을 만든다.
    async with sessionmaker()() as holder:
        await holder.execute(select(User).where(User.id == target_id).with_for_update())
        tasks = [asyncio.create_task(decide("approve")), asyncio.create_task(decide("reject"))]
        try:
            for _ in range(200):
                if await both_waiting():
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("두 결정이 행 잠금에서 기다리지 않았다")
        except BaseException:
            for t in tasks:
                t.cancel()
            await holder.rollback()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        await holder.rollback()
    results = dict(await asyncio.gather(*tasks))

    winners = [a for a, r in results.items() if r != 409]
    assert len(winners) == 1, results
    winner = winners[0]
    loser = "reject" if winner == "approve" else "approve"
    assert results[loser] == 409, results
    assert results[winner] is (winner == "approve"), results  # 승인은 True(동기화 필요), 거절은 False
    async with sessionmaker()() as db:
        row = await db.get(User, target_id)
        assert row.access_status == {"approve": "approved", "reject": "rejected"}[winner]
        assert row.access_decided_by == "20201234" and row.access_decided_at is not None
        assert row.access_reject_count == (1 if winner == "reject" else 0)


async def test_video_urls_upsert_and_admin_list_on_postgres(database, monkeypatch):
    from datetime import UTC, datetime

    from app.db.models import VideoUrl
    from app.lms.todo import VideoTodo
    from app.services.video_urls import record_video_url

    admin_id = await _seed()
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    video = VideoTodo(kind="video", course_id=1, course_name="과목", module_id=1, module_title="1주차", module_position=1,
                      module_item_id=1, item_position=1, item_id=1, title="영상", duration=60, view_url="https://x",
                      due_at=None, deadline=datetime.now(UTC), unlock_at=None)
    url = "https://ssuin-object.commonscdn.com/a.mp4"

    async def record(user_id):
        async with sessionmaker()() as db:
            await record_video_url(db, user_id, video, "c1", url)

    await asyncio.gather(*(record(admin_id) for _ in range(5)))  # 동시에 같은 영상: 한 줄로 합친다
    async with sessionmaker()() as db:
        rows = (await db.scalars(select(VideoUrl))).all()
    assert len(rows) == 1 and rows[0].attempts == 5

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = create_app(lifespan)
    app.state.queue = SimpleNamespace()
    headers = {"Authorization": "Bearer " + create_access_token(admin_id)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        body = (await api.get("/admin/video-urls?q=영", headers=headers)).json()
        saved = (await api.get("/admin/saved-videos?status=failed&q=s&student_id=20209999", headers=headers)).json()
    assert body["total"] == 1 and body["items"][0]["first_student_id"] == "20201234"
    assert saved["total"] == 1 and saved["items"][0]["holders"] == 1 and saved["summary"]["users"] == 1
