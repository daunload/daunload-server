"""API + 동기화 + 일괄 재생 + 알림 통합 테스트 (SQLite, LMS는 respx로 모킹)."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import get_settings
from app.db import base as db_base
from app.db.base import Base
from app.db.models import AnnouncementRow, NotificationLog
from app.lms.auth import LmsSession
from app.main import create_app

C = "https://canvas.ssu.ac.kr"
WEBHOOK = "https://discord.com/api/webhooks/1/abc"
COURSE_ID = 45446


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue(self, function, *args, job_id=None):
        self.jobs.append((function, args, job_id))


class LmsMock:
    """학기·과목·모듈·과제·공지 응답을 만든다. completed에 넣은 module_item_id는 완료로 응답."""

    def __init__(self):
        now = datetime.now(UTC)
        self.completed: set[int] = set()
        self.video_deadline = now + timedelta(days=5)
        self.assignment_deadline = now + timedelta(hours=2)
        self.announcements = [{"id": "1", "title": "첫 공지", "posted_at": iso(now - timedelta(days=3))}]
        self.assignments = [{"id": "1", "name": "과제1", "due_at": iso(self.assignment_deadline), "published": True}]
        self.submissions = [{"assignment_id": "1", "workflow_state": "unsubmitted"}]
        self.submissions_status = 200

    def install(self):
        now = datetime.now(UTC)
        respx.get(f"{C}/learningx/api/v1/users/20201234/terms").respond(
            200, json={"enrollment_terms": [{"id": 46, "name": "2026-2", "start_at": iso(now - timedelta(days=30)),
                                             "end_at": iso(now + timedelta(days=60))}]})
        respx.get(f"{C}/learningx/api/v1/learn_activities/courses").respond(
            200, json=[{"id": COURSE_ID, "term_id": 46, "name": "리눅스시스템프로그래밍", "professors": "교수"}])
        respx.get(f"{C}/learningx/api/v1/learn_activities/learnstatus").respond(200, json={
            "learnstatuses": [{"course": {"id": COURSE_ID}, "sections": [
                {"section_id": 1, "position": 1, "subsections": [{"subsection_id": 1, "position": 0, "status": "attendance"}]}]}],
            "total_count": 1})
        respx.get(f"{C}/learningx/api/v1/courses/{COURSE_ID}/modules").mock(side_effect=lambda r: httpx.Response(200, json=self.modules()))
        respx.get(f"{C}/api/v1/courses/{COURSE_ID}/assignment_groups").mock(
            side_effect=lambda r: httpx.Response(200, json=[{"name": "과제", "assignments": self.assignments}]))
        respx.get(f"{C}/api/v1/courses/{COURSE_ID}/students/submissions").mock(
            side_effect=lambda r: httpx.Response(self.submissions_status, json=self.submissions))
        respx.get(f"{C}/api/v1/courses/{COURSE_ID}/discussion_topics").mock(
            side_effect=lambda r: httpx.Response(200, json=self.announcements))

    def modules(self):
        def item(mid, pos, title):
            return {"module_item_id": mid, "position": pos, "completed": mid in self.completed,
                    "content_data": {"item_id": mid, "item_content_type": "commons", "title": title,
                                     "due_at": iso(self.video_deadline),
                                     "item_content_data": {"duration": 600, "view_url": f"{C}/v/{mid}"}}}
        return [
            {"module_id": 2, "title": "2주차", "position": 2, "module_items": [item(21, 1, "2주차 영상")]},
            {"module_id": 1, "title": "1주차", "position": 1, "module_items": [item(12, 2, "1주차 영상 B"), item(11, 1, "1주차 영상 A")]},
        ]


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("JWT_SECRET", "test-secret-test-secret-test-secret!")
    monkeypatch.setenv("VERIFY_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("PLAYBACK_GAP_SECONDS", "[0, 0]")
    monkeypatch.setenv("PLAYBACK_EXTRA_SECONDS", "0")
    get_settings.cache_clear()
    engine = db_base.init_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    lms_logins: list[str] = []
    school_password = {"current": "right"}  # 학교 쪽 현재 비밀번호

    async def fake_login(http, user_id, password):
        lms_logins.append(password)
        if password != school_password["current"]:
            from app.lms.errors import InvalidCredentials
            raise InvalidCredentials("틀림")
        return LmsSession(user_id=user_id, bearer_token="B", cookies=[])

    monkeypatch.setattr("app.lms.auth.login", fake_login)

    @asynccontextmanager
    async def no_lifespan(app):
        yield

    app = create_app(no_lifespan)
    app.state.queue = FakeQueue()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        api.lms_logins = lms_logins
        api.school_password = school_password
        yield api, app.state.queue
    await engine.dispose()
    get_settings.cache_clear()


async def _signup(api, student_id: str = "20201234") -> dict:
    """첫 로그인(동의 포함)으로 계정을 만든다."""
    r = await api.post("/auth/login", data={"username": student_id, "password": "right", "agree_terms": "true"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_first_login_requires_consent_and_verifies_with_lms(env):
    api, queue = env
    r = await api.post("/auth/login", data={"username": "20201234", "password": "right"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "consent_required"
    assert api.lms_logins == []  # 동의 전에는 학교 서버에 접속하지 않는다

    r = await api.post("/auth/login", data={"username": "20201234", "password": "wrong", "agree_terms": "true"})
    assert r.status_code == 401

    h = await _signup(api)
    assert queue.jobs[-1] == ("sync_user_job", (1,), "sync:1")
    assert (await api.get("/me", headers=h)).json()["student_id"] == "20201234"
    cred = (await api.get("/lms/credentials", headers=h)).json()
    assert cred["status"] == "ok" and cred["student_id"] == "20201234"
    async with db_base.sessionmaker()() as db:
        from app.db.models import LmsCredential
        assert "right" not in (await db.scalars(select(LmsCredential))).one().password_enc
    assert (await api.get("/courses")).status_code == 401


async def test_next_login_uses_hash_and_password_change_rechecks_lms(env):
    api, _ = env
    await _signup(api)
    calls = len(api.lms_logins)
    r = await api.post("/auth/login", data={"username": "20201234", "password": "right"})
    assert r.status_code == 200 and len(api.lms_logins) == calls  # LMS 접속 없음

    # 학교 비밀번호를 바꾼 경우: 해시가 다르니 LMS로 확인하고 갱신한다
    api.school_password["current"] = "new-right"
    r = await api.post("/auth/login", data={"username": "20201234", "password": "new-right"})
    assert r.status_code == 200 and api.lms_logins[-1] == "new-right"
    calls = len(api.lms_logins)
    assert (await api.post("/auth/login", data={"username": "20201234", "password": "new-right"})).status_code == 200
    assert len(api.lms_logins) == calls
    assert (await api.post("/auth/login", data={"username": "20201234", "password": "right"})).status_code == 401


async def test_failed_logins_are_throttled(env, monkeypatch):
    from app.api import routes_account

    monkeypatch.setattr(routes_account, "_failures", {})
    api, _ = env
    await _signup(api)
    for _ in range(routes_account.MAX_FAILURES):
        assert (await api.post("/auth/login", data={"username": "20201234", "password": "bad"})).status_code == 401
    calls = len(api.lms_logins)
    assert (await api.post("/auth/login", data={"username": "20201234", "password": "right"})).status_code == 429
    assert len(api.lms_logins) == calls


async def test_delete_me_removes_everything(env):
    api, _ = env
    h = await _signup(api)
    assert (await api.delete("/me", headers=h)).status_code == 204
    async with db_base.sessionmaker()() as db:
        from app.db.models import LmsCredential, User
        assert (await db.scalars(select(User))).all() == []
        assert (await db.scalars(select(LmsCredential))).all() == []


@respx.mock
async def test_full_flow_sync_batch_and_notifications(env):
    from app.services.notify import notify_announcements, notify_deadlines
    from app.services.playback import run_batch
    from app.services.sync import sync_user

    api, queue = env
    h = await _signup(api)
    discord = respx.post(WEBHOOK).respond(204)
    assert (await api.post("/notifications/channels", headers=h, json={"kind": "discord", "webhook_url": WEBHOOK})).status_code == 201

    lms = LmsMock()
    lms.install()

    # 1) 첫 동기화: 기존 공지는 알리지 않는다
    async with db_base.sessionmaker()() as db:
        result = await sync_user(db, 1)
    assert result.first_sync and result.new_announcements == []

    courses = (await api.get("/courses", headers=h)).json()
    assert courses[0]["lms_course_id"] == COURSE_ID and courses[0]["attendance"][0]["statuses"] == ["attendance"]
    todos = (await api.get("/todos", headers=h)).json()
    assert [t["kind"] for t in todos] == ["assignment", "video", "video", "video"]

    # 2) 새 공지 → 두 번째 동기화에서 알림
    lms.announcements.append({"id": "2", "title": "휴강 안내", "posted_at": iso(datetime.now(UTC))})
    async with db_base.sessionmaker()() as db:
        result = await sync_user(db, 1)
        assert [a.title for a in result.new_announcements] == ["휴강 안내"]
        await notify_announcements(db, 1, result.new_announcements)
        await notify_announcements(db, 1, result.new_announcements)  # 중복 발송 안 됨
        assert await notify_deadlines(db, 1) == 1  # 2시간 남은 과제 → 3시간 알림
        assert await notify_deadlines(db, 1) == 0
    sent = [json.loads(c.request.content)["content"] for c in discord.calls]
    assert any("휴강 안내" in s for s in sent) and sum("휴강 안내" in s for s in sent) == 1
    assert any("마감 3시간" in s for s in sent)

    # 3) 일괄 재생 미리보기: 과목 범위, LMS 화면 순서
    preview = (await api.get("/playback/batches/preview", headers=h, params={"scope": "course", "course_id": COURSE_ID})).json()
    assert [i["title"] for i in preview["items"]] == ["1주차 영상 A", "1주차 영상 B", "2주차 영상"]
    assert preview["warnings"] == []
    module_preview = (await api.get("/playback/batches/preview", headers=h,
                                    params={"scope": "module", "course_id": COURSE_ID, "module_id": 1})).json()
    assert len(module_preview["items"]) == 2
    assert (await api.get("/playback/batches/preview", headers=h, params={"scope": "module"})).status_code == 422

    # 4) 생성 → 중복 생성은 409
    r = await api.post("/playback/batches", headers=h, json={"scope": "course", "course_id": COURSE_ID})
    assert r.status_code == 201, r.text
    batch_id = r.json()["id"]
    assert not any(job[0] == "run_batch_job" for job in queue.jobs)  # DB 자체가 재생 큐
    assert (await api.post("/playback/batches", headers=h, json={"scope": "all"})).status_code == 409

    # 5) 워커 실행(가짜 플레이어): 첫 영상은 이미 완료된 상태로 바뀌어 있다고 가정
    lms.completed.add(11)
    played = []

    class FakePlayer:
        async def play(self, session, video, *, on_progress=None):
            played.append(video.title)
            if on_progress:
                await on_progress(video, 600, 600)
            if video.module_item_id != 21:  # 2주차 영상은 재생해도 LMS가 완료로 바꾸지 않음
                lms.completed.add(video.module_item_id)

    status = await run_batch(batch_id, FakePlayer())
    assert status == "partial"
    assert played == ["1주차 영상 B", "2주차 영상", "2주차 영상"]  # 실패 항목은 1회 재시도

    detail = (await api.get(f"/playback/batches/{batch_id}", headers=h)).json()
    assert [j["status"] for j in detail["jobs"]] == ["skipped", "done", "failed"]
    assert detail["done_count"] == 1 and detail["failed_count"] == 1 and detail["skipped_count"] == 1
    assert any("일괄 재생 일부 완료" in json.loads(c.request.content)["content"] for c in discord.calls)

    # 6) 끝난 뒤에는 새 묶음을 만들 수 있고, 시작 전 취소는 바로 cancelled
    r = await api.post(f"/playback/items/21", headers=h)
    assert r.status_code == 201
    r = await api.delete(f"/playback/batches/{r.json()['id']}", headers=h)
    assert r.json()["status"] == "cancelled"


async def test_corrupt_batch_items_fail_instead_of_staying_running(env):
    from app.db.models import PlaybackBatch, PlaybackJob
    from app.services.playback import run_batch

    api, _ = env
    await _signup(api)
    async with db_base.sessionmaker()() as db:
        b = PlaybackBatch(user_id=1, scope="all", status="running", total=1)
        db.add(b)
        await db.flush()
        db.add(PlaybackJob(batch_id=b.id, user_id=1, position=0, lms_course_id=1, module_item_id=1, title="x",
                           duration=1, deadline=datetime.now(UTC), video={}, status="queued"))
        await db.commit()
        batch_id = b.id
    assert await run_batch(batch_id, player=None) == "failed"
    async with db_base.sessionmaker()() as db:
        b = await db.get(PlaybackBatch, batch_id)
        assert b.status == "failed" and "데이터 오류" in b.error


async def test_other_users_batch_is_hidden(env):
    api, _ = env
    h = await _signup(api)
    assert (await api.get("/playback/batches/999", headers=h)).status_code == 404
    assert (await api.post("/playback/batches", headers=h, json={"scope": "all"})).status_code == 404


async def test_dashboard_is_served_and_api_still_wins(env):
    api, _ = env
    r = await api.get("/")
    assert r.status_code == 200 and "숭실 LMS" in r.text
    assert (await api.get("/app.js")).status_code == 200
    assert (await api.get("/health")).json() == {"ok": True}
    h = await _signup(api)
    assert (await api.get("/me", headers=h)).json()["student_id"] == "20201234"


async def test_admin_access_search_pagination_and_no_secrets(env, monkeypatch):
    api, _ = env
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    admin = await _signup(api)
    other = await _signup(api, "20209999")
    assert (await api.get("/admin/users")).status_code == 401
    assert (await api.get("/admin/users", headers=other)).status_code == 403
    assert (await api.get("/me", headers=admin)).json()["is_admin"] is True
    assert (await api.get("/me", headers=other)).json()["is_admin"] is False
    r = await api.get("/admin/users?limit=1", headers=admin)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["total"] == 2
    assert r.json()["users"][0]["student_id"] == "20209999"
    assert set(r.json()["users"][0]) == {
        "student_id", "created_at", "last_login_at", "login_count", "lms_status",
    }
    second = (await api.get("/admin/users?offset=1&limit=1", headers=admin)).json()
    assert second["users"][0]["student_id"] == "20201234"
    filtered = (await api.get("/admin/users?q=9999", headers=admin)).json()
    assert filtered["total"] == 1
    assert filtered["users"][0]["student_id"] == "20209999"
    assert (await api.get("/admin/users?q=%25", headers=admin)).json()["total"] == 0
    assert (await api.get("/admin/users?limit=101", headers=admin)).status_code == 422
    monkeypatch.setattr(get_settings(), "admin_student_ids", [])
    assert (await api.get("/admin/users", headers=admin)).status_code == 403


async def test_site_login_activity_counts_success_only(env, monkeypatch):
    from app.db.models import User
    api, _ = env
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    admin = await _signup(api)
    first = (await api.get("/admin/users", headers=admin)).json()["users"][0]
    assert first["login_count"] == 1 and first["last_login_at"]
    await api.get("/me", headers=admin)
    assert (await api.post("/auth/login", data={"username": "20201234", "password": "bad"})).status_code == 401
    row = (await api.get("/admin/users", headers=admin)).json()["users"][0]
    assert row["login_count"] == 1 and row["last_login_at"] == first["last_login_at"]
    assert (await api.post("/auth/login", data={"username": "20201234", "password": "right"})).status_code == 200
    api.school_password["current"] = "changed"
    assert (await api.post("/auth/login", data={"username": "20201234", "password": "changed"})).status_code == 200
    row = (await api.get("/admin/users", headers=admin)).json()["users"][0]
    assert row["login_count"] == 3
    assert row["last_login_at"] >= first["last_login_at"]
    # Legacy users have no invented login date; missing credentials are allowed.
    async with db_base.sessionmaker()() as db:
        db.add(User(student_id="legacy", password_hash="unused", consent_at=datetime.now(UTC)))
        await db.commit()
    legacy = (await api.get("/admin/users?q=legacy", headers=admin)).json()["users"][0]
    assert legacy["last_login_at"] is None and legacy["login_count"] == 0
    assert legacy["lms_status"] is None


async def test_admin_user_settings_are_scoped_read_only_and_hide_secrets(env, monkeypatch):
    from app.db.models import CourseRow, NotificationChannel
    api, _ = env
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    admin = await _signup(api)
    other = await _signup(api, "20209999")
    async with db_base.sessionmaker()() as db:
        db.add_all([
            CourseRow(user_id=1, lms_course_id=1, name="관리자 과목", auto_play=False),
            CourseRow(user_id=2, lms_course_id=1, name="자동 재생 과목", auto_play=True),
            CourseRow(user_id=2, lms_course_id=2, name="이전 과목", auto_play=False, active=False),
            NotificationChannel(user_id=1, kind="discord", target_enc="ADMIN-SECRET"),
            NotificationChannel(user_id=2, kind="telegram", target_enc="BOT-SECRET", enabled=False),
        ])
        await db.commit()
    url = "/admin/users/20209999/settings"
    assert (await api.get(url)).status_code == 401
    assert (await api.get(url, headers=other)).status_code == 403
    r = await api.get(url, headers=admin)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["student_id"] == "20209999" and body["is_admin"] is False
    assert body["consent_at"] and body["lms"]["status"] == "ok"
    assert set(body["lms"]) == {"status", "last_login_at", "last_synced_at", "next_sync_at"}
    assert body["courses"] == [
        {"name": "자동 재생 과목", "active": True, "auto_play": True},
        {"name": "이전 과목", "active": False, "auto_play": False},
    ]
    assert len(body["channels"]) == 1
    assert body["channels"][0]["kind"] == "telegram" and body["channels"][0]["enabled"] is False
    assert set(body["channels"][0]) == {"kind", "enabled", "created_at"}
    for secret in ("SECRET", "password", "session", "target_enc", "bearer_token"):
        assert secret not in r.text
    assert (await api.get("/admin/users/unknown/settings", headers=admin)).status_code == 404
    mine = (await api.get("/admin/users/20201234/settings", headers=admin)).json()
    assert mine["is_admin"] is True and mine["courses"][0]["name"] == "관리자 과목"
    assert (await api.patch(url, headers=admin, json={"is_admin": True})).status_code == 405


async def test_admin_settings_empty_user(env, monkeypatch):
    from app.db.models import User
    api, _ = env
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    admin = await _signup(api)
    async with db_base.sessionmaker()() as db:
        db.add(User(student_id="legacy", password_hash="unused", consent_at=datetime.now(UTC)))
        await db.commit()
    r = await api.get("/admin/users/legacy/settings", headers=admin)
    assert r.status_code == 200
    assert r.json()["lms"] is None and r.json()["courses"] == [] and r.json()["channels"] == []
