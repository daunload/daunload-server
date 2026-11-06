"""관리자 승인 전 사용자 차단 (설계 2026-11-06 5, 6, 12장)."""

import re
from datetime import UTC, datetime, timedelta

import pytest
import respx
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import update
from starlette.routing import Mount

from app.config import get_settings
from app.db import base as db_base
from app.db.models import SavedVideo, User
from app.security import create_download_token
from app.services.access import access_retry_at, has_access
from tests.test_api import _signup, env  # noqa: F401

ACCESS_REQUIRED = {"code": "ACCESS_REQUIRED", "message": "관리자 승인이 필요합니다."}

# 승인 없이 열어 두는 경로(설계 6장 "차단"). 관리자 경로는 AdminUser가 따로 막는다.
OPEN = {
    ("POST", "/auth/login"),
    ("GET", "/me"),
    ("DELETE", "/me"),
    ("GET", "/health"),  # 서비스 상태 확인
    ("GET", "/"),  # 대시보드 HTML(정적 파일)
    ("GET", "/index.html"),
}


@pytest.fixture
def approve_test_users():
    """이 파일에서는 제품 기본값(none)을 그대로 쓴다. 승인 상태는 테스트마다 명시적으로 만든다."""
    yield


async def _set_status(student_id: str, status: str) -> None:
    async with db_base.sessionmaker()() as db:
        await db.execute(update(User).where(User.student_id == student_id).values(access_status=status))
        await db.commit()


def _all_routes(app):
    """(메서드, 경로) 전부. 포함된 라우터 안까지 펼친다. 정적 파일 마운트 말고 다른 종류가 있으면 실패한다."""
    for ctx in iter_route_contexts(app.routes):
        if isinstance(ctx.original_route, Mount):
            continue
        assert isinstance(ctx.original_route, APIRoute), ctx
        for method in ctx.methods - {"HEAD"}:
            yield method, ctx.path


def _user_routes(app):
    return sorted({(m, p) for m, p in _all_routes(app) if not p.startswith("/admin/") and (m, p) not in OPEN})


def _signed(user_id: int, path: str) -> str:
    if path == "/audio/{token}":
        data = {"kind": "audio", "media_url": "https://ssuin-object.commonscdn.com/a.mp4", "file_name": "a.mp4"}
    else:
        data = {"media_url": "https://ssuin-object.commonscdn.com/a.mp4", "file_name": "a.mp4"}
    return path.replace("{token}", create_download_token(user_id, data))


def test_default_status_is_none_in_production():
    assert User.__table__.c.access_status.default.arg == "none"
    assert User.__table__.c.access_status.server_default.arg == "none"
    assert User.__table__.c.access_reject_count.server_default.arg == "0"


def test_access_retry_at_only_for_rejected(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20200001"])
    decided = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)
    user = User(student_id="20201234", access_status="rejected", access_decided_at=decided)
    assert access_retry_at(user) == decided + timedelta(hours=24)
    assert not has_access(user)
    for status in ("none", "pending", "approved", "revoked"):
        user.access_status = status
        assert access_retry_at(user) is None
        assert has_access(user) == (status == "approved")
    admin = User(student_id="20200001", access_status="rejected", access_decided_at=decided)
    assert has_access(admin) and access_retry_at(admin) is None


@respx.mock  # 막히지 않으면 학교 서버로 나가는 요청이 실패로 드러난다
async def test_unapproved_user_is_blocked_everywhere(env):
    api, _ = env
    h = await _signup(api)
    app = api._transport.app
    routes = _user_routes(app)
    assert len(routes) >= 37  # 경로를 실제로 훑었는지(작성 시점 37개)
    assert OPEN <= set(_all_routes(app))  # 열린 경로 목록이 실제 경로와 맞는지
    for status in ("none", "pending", "rejected", "revoked"):
        await _set_status("20201234", status)
        for method, path in routes:
            url = _signed(1, path) if "{token}" in path else re.sub(r"\{[^}]+\}", "1", path)
            kwargs = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
            r = await api.request(method, url, headers=h, **kwargs)
            assert r.status_code == 403, (status, method, path, r.status_code, r.text)
            assert r.json()["detail"] == ACCESS_REQUIRED, (status, method, path)


async def test_open_paths_work_without_access(env):
    api, _ = env
    h = await _signup(api)
    r = await api.get("/me", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["access_status"] == "none"
    assert body["access_requested_at"] is None and body["access_retry_at"] is None
    assert (await api.delete("/me", headers=h)).status_code == 204
    assert (await api.get("/me", headers=h)).status_code == 401


async def test_me_reports_retry_time_after_rejection(env):
    api, _ = env
    h = await _signup(api)
    decided = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)
    requested = decided - timedelta(hours=1)
    async with db_base.sessionmaker()() as db:
        await db.execute(update(User).where(User.id == 1).values(
            access_status="rejected", access_decided_at=decided, access_requested_at=requested))
        await db.commit()
    body = (await api.get("/me", headers=h)).json()
    assert body["access_status"] == "rejected"
    assert datetime.fromisoformat(body["access_requested_at"]) == requested
    assert datetime.fromisoformat(body["access_retry_at"]) == decided + timedelta(hours=24)


async def test_admin_is_always_approved(env, monkeypatch):
    api, _ = env
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    h = await _signup(api)
    assert (await api.get("/todos", headers=h)).status_code == 200
    assert (await api.get("/admin/overview", headers=h)).status_code == 200
    body = (await api.get("/me", headers=h)).json()
    assert body["access_status"] == "approved" and body["is_admin"] is True and body["access_retry_at"] is None


async def test_admin_routes_still_require_admin_for_approved_users(env):
    api, _ = env
    h = await _signup(api)
    await _set_status("20201234", "approved")
    r = await api.get("/admin/overview", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "관리자 권한이 필요합니다."


async def test_login_does_not_queue_sync_for_unapproved(env):
    api, queue = env
    await _signup(api)
    await _signup(api, "20209999")
    assert [j for j in queue.jobs if j[0] == "sync_user_job"] == []

    # 학교 비밀번호가 바뀌어 다시 LMS로 확인하는 로그인: 승인된 사용자만 동기화한다.
    await _set_status("20201234", "approved")
    api.school_password["current"] = "new"
    for sid in ("20201234", "20209999"):
        r = await api.post("/auth/login", data={"username": sid, "password": "new"})
        assert r.status_code == 200, r.text
    assert [j for j in queue.jobs if j[0] == "sync_user_job"] == [("sync_user_job", (1,), "sync:1")]


async def test_admin_login_queues_sync(env, monkeypatch):
    api, queue = env
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    await _signup(api)
    assert queue.jobs[-1] == ("sync_user_job", (1,), "sync:1")


async def test_sync_button_blocked_for_unapproved(env):
    api, queue = env
    h = await _signup(api)
    r = await api.post("/sync", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == ACCESS_REQUIRED
    assert queue.jobs == []


@respx.mock  # 막히지 않으면 학교 서버로 나가는 요청이 실패로 드러난다
async def test_signed_links_check_owner_access(env):
    api, _ = env
    h = await _signup(api)
    await _set_status("20201234", "approved")
    async with db_base.sessionmaker()() as db:
        db.add(SavedVideo(user_id=1, lms_course_id=45446, course_name="리눅스", module_item_id=11, module_title="1주차",
                          title="1강", content_id="m11", media_url="https://ssuin-object.commonscdn.com/a.mp4",
                          file_name="a.mp4"))
        await db.commit()
    download = (await api.post("/saved-videos/1/download-link", headers=h)).json()["url"]
    audio = (await api.post("/saved-videos/1/audio-link", headers=h)).json()["url"]
    await _set_status("20201234", "revoked")
    for url in (download, audio):
        r = await api.get(url)
        assert r.status_code == 403 and r.json()["detail"] == ACCESS_REQUIRED, (url, r.text)


@respx.mock  # 막히지 않으면 학교 서버로 나가는 요청이 실패로 드러난다
async def test_signed_link_of_deleted_user_is_rejected(env):
    api, _ = env
    h = await _signup(api)
    url = _signed(1, "/downloads/{token}")
    assert (await api.delete("/me", headers=h)).status_code == 204
    r = await api.get(url)
    assert r.status_code == 403 and r.json()["detail"] == ACCESS_REQUIRED


async def test_revoked_access_blocks_existing_token(env):
    api, _ = env
    h = await _signup(api)
    await _set_status("20201234", "approved")
    assert (await api.get("/todos", headers=h)).status_code == 200
    await _set_status("20201234", "revoked")
    r = await api.get("/todos", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == ACCESS_REQUIRED
