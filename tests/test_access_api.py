"""관리자 승인 전 사용자 차단 (설계 2026-11-06 5, 6, 12장)."""

import re
from datetime import UTC, datetime, timedelta

import pytest
import respx
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import select, update
from starlette.routing import Mount

from app.config import get_settings
from app.db import base as db_base
from app.db.models import LmsCredential, NotificationChannel, PlaybackBatch, SavedVideo, User
from app.security import create_download_token, encrypt
from app.services.access import access_retry_at, has_access
from tests.test_api import _signup, env  # noqa: F401

ACCESS_REQUIRED = {"code": "ACCESS_REQUIRED", "message": "관리자 승인이 필요합니다."}

# 승인 없이 열어 두는 경로(설계 6장 "차단"). 관리자 경로는 AdminUser가 따로 막는다.
OPEN = {
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),  # 승인 대기 화면에도 로그아웃이 있다
    ("GET", "/me"),
    ("DELETE", "/me"),
    ("POST", "/access/request"),
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
    assert len(routes) >= 34  # 경로를 실제로 훑었는지(작성 시점 34개)
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

    # 학교 로그인이 만료된 뒤 다시 연동하는 로그인: 승인된 사용자만 동기화한다.
    await _set_status("20201234", "approved")
    async with db_base.sessionmaker()() as db:
        await db.execute(update(LmsCredential).values(status="invalid", session_enc=None))
        await db.commit()
    for sid in ("20201234", "20209999"):
        r = await api.post("/auth/login", data={"username": sid, "password": "right"})
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
    await _set_status("20201234", "revoked")
    r = await api.get(download)
    assert r.status_code == 403 and r.json()["detail"] == ACCESS_REQUIRED, r.text


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


# ---- 요청과 관리자 결정 (설계 6장) ----

ADMIN_ID = "20200001"
ADMIN2_ID = "20200002"
WEBHOOK = "https://discord.com/api/webhooks/1/a"
WEBHOOK2 = "https://discord.com/api/webhooks/2/b"
NOW = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    """서비스가 쓰는 현재 시각을 고정한다. now["t"]를 바꿔 시간을 흘려 보낸다."""
    now = {"t": NOW}
    monkeypatch.setattr("app.services.access.utcnow", lambda: now["t"])
    return now


@pytest.fixture
def admins(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_student_ids", [ADMIN_ID, ADMIN2_ID])


async def _user(student_id: str) -> User:
    async with db_base.sessionmaker()() as db:
        return await db.scalar(select(User).where(User.student_id == student_id))


async def _add_channel(student_id: str, kind: str, target: str, enabled: bool = True) -> None:
    user = await _user(student_id)
    async with db_base.sessionmaker()() as db:
        db.add(NotificationChannel(user_id=user.id, kind=kind, target_enc=encrypt(target), enabled=enabled))
        await db.commit()


async def test_request_transitions(env, clock):
    api, _ = env
    h = await _signup(api)

    r = await api.post("/access/request", headers=h)
    assert r.status_code == 200
    assert r.json()["access_status"] == "pending" and r.json()["access_retry_at"] is None
    assert datetime.fromisoformat(r.json()["access_requested_at"]) == NOW

    # pending 재요청: 200, 요청 시각 그대로
    clock["t"] = NOW + timedelta(hours=1)
    r = await api.post("/access/request", headers=h)
    assert r.status_code == 200 and r.json()["access_status"] == "pending"
    assert datetime.fromisoformat(r.json()["access_requested_at"]) == NOW

    # revoked -> pending
    await _set_status("20201234", "revoked")
    r = await api.post("/access/request", headers=h)
    assert r.status_code == 200 and r.json()["access_status"] == "pending"
    assert datetime.fromisoformat(r.json()["access_requested_at"]) == NOW + timedelta(hours=1)

    # approved -> 400
    await _set_status("20201234", "approved")
    assert (await api.post("/access/request", headers=h)).status_code == 400


async def test_request_after_rejection_waits_24_hours(env, clock):
    api, _ = env
    h = await _signup(api)
    async with db_base.sessionmaker()() as db:
        await db.execute(update(User).where(User.id == 1).values(access_status="rejected", access_decided_at=NOW))
        await db.commit()

    clock["t"] = NOW + timedelta(hours=23, minutes=59)
    r = await api.post("/access/request", headers=h)
    assert r.status_code == 429
    assert datetime.fromisoformat(r.json()["detail"]["access_retry_at"]) == NOW + timedelta(hours=24)
    assert (await api.get("/me", headers=h)).json()["access_status"] == "rejected"

    clock["t"] = NOW + timedelta(hours=24)
    r = await api.post("/access/request", headers=h)
    assert r.status_code == 200 and r.json()["access_status"] == "pending"


async def test_admin_request_is_treated_as_approved(env, admins):
    api, _ = env
    h = await _signup(api, ADMIN_ID)
    assert (await api.post("/access/request", headers=h)).status_code == 400


@respx.mock
async def test_request_notifies_every_admin_discord_once(env, admins, clock):
    api, _ = env
    await _signup(api, ADMIN_ID)
    await _signup(api, ADMIN2_ID)
    await _signup(api, "20203333")  # 관리자가 아닌 사용자의 채널
    await _add_channel(ADMIN_ID, "discord", WEBHOOK)
    await _add_channel(ADMIN_ID, "telegram", "token|1")  # 텔레그램은 제외
    await _add_channel(ADMIN2_ID, "discord", WEBHOOK2)
    await _add_channel(ADMIN2_ID, "discord", "https://discord.com/api/webhooks/3/off", enabled=False)
    await _add_channel("20203333", "discord", "https://discord.com/api/webhooks/4/other")
    hooks = {u: respx.post(u).respond(204) for u in (WEBHOOK, WEBHOOK2)}
    off = respx.post("https://discord.com/api/webhooks/3/off").respond(204)
    other = respx.post("https://discord.com/api/webhooks/4/other").respond(204)
    telegram = respx.post("https://api.telegram.org/bottoken/sendMessage").respond(200)
    h = await _signup(api)

    assert (await api.post("/access/request", headers=h)).status_code == 200
    assert [hooks[WEBHOOK].call_count, hooks[WEBHOOK2].call_count] == [1, 1]
    assert "20201234" in hooks[WEBHOOK].calls.last.request.content.decode()
    assert off.call_count == other.call_count == telegram.call_count == 0

    await api.post("/access/request", headers=h)  # pending 재요청은 알리지 않는다
    assert [hooks[WEBHOOK].call_count, hooks[WEBHOOK2].call_count] == [1, 1]

    await _set_status("20201234", "revoked")  # 회수 뒤 재요청은 다시 알린다
    await api.post("/access/request", headers=h)
    assert [hooks[WEBHOOK].call_count, hooks[WEBHOOK2].call_count] == [2, 2]


@respx.mock
async def test_request_succeeds_when_discord_fails(env, admins, clock):
    api, _ = env
    await _signup(api, ADMIN_ID)
    await _add_channel(ADMIN_ID, "discord", WEBHOOK)
    hook = respx.post(WEBHOOK).respond(500)
    h = await _signup(api)
    r = await api.post("/access/request", headers=h)
    assert r.status_code == 200 and r.json()["access_status"] == "pending"
    assert hook.call_count == 1


async def _admin_and_target(api, target_status: str):
    admin_h = await _signup(api, ADMIN_ID)
    target_h = await _signup(api)
    await _set_status("20201234", target_status)
    return admin_h, target_h


async def _decide(api, admin_h, action, student_id="20201234"):
    return await api.post(f"/admin/users/{student_id}/access", headers=admin_h, json={"action": action})


async def test_admin_decisions_table(env, admins, clock):
    api, _ = env
    allowed = {
        "approve": ({"pending", "none", "rejected", "revoked"}, "approved"),
        "reject": ({"pending"}, "rejected"),
        "revoke": ({"approved"}, "revoked"),
    }
    admin_h, _ = await _admin_and_target(api, "none")
    for action, (from_states, to) in allowed.items():
        for status in ACCESS_STATES:
            await _set_status("20201234", status)
            await _clear_decision()
            r = await _decide(api, admin_h, action)
            if status in from_states:
                assert r.status_code == 200, (action, status, r.text)
                body = r.json()
                assert body["student_id"] == "20201234" and body["access_status"] == to
                assert body["access_decided_by"] == ADMIN_ID
                assert datetime.fromisoformat(body["access_decided_at"]) == NOW
                user = await _user("20201234")
                assert user.access_decided_by == ADMIN_ID and user.access_decided_at == NOW
                assert user.access_reject_count == (1 if action == "reject" else 0)
            else:
                assert r.status_code == 409, (action, status, r.text)
                assert (await _user("20201234")).access_status == status


ACCESS_STATES = ("none", "pending", "approved", "rejected", "revoked")


async def _clear_decision() -> None:
    async with db_base.sessionmaker()() as db:
        await db.execute(update(User).where(User.student_id == "20201234").values(
            access_decided_at=None, access_decided_by=None, access_reject_count=0))
        await db.commit()


async def test_decision_validation_and_permissions(env, admins):
    api, _ = env
    admin_h, user_h = await _admin_and_target(api, "pending")
    assert (await _decide(api, user_h, "approve")).status_code == 403  # 일반 사용자
    assert (await _decide(api, admin_h, "approve", ADMIN2_ID)).status_code == 404  # 가입 전 관리자
    await _signup(api, ADMIN2_ID)
    assert (await _decide(api, admin_h, "revoke", ADMIN2_ID)).status_code == 400  # 관리자 대상
    assert (await _decide(api, admin_h, "nope")).status_code == 422
    assert (await _decide(api, admin_h, "approve", "99999999")).status_code == 404
    assert (await _user("20201234")).access_status == "pending"


async def test_approve_queues_full_sync(env, admins):
    api, queue = env
    admin_h, _ = await _admin_and_target(api, "pending")
    queue.jobs.clear()
    assert (await _decide(api, admin_h, "approve")).status_code == 200
    assert queue.jobs == [("sync_user_job", (2,), "sync:2")]
    queue.jobs.clear()
    assert (await _decide(api, admin_h, "approve")).status_code == 409  # 이미 승인됨
    assert queue.jobs == []


async def test_revoke_cancels_active_batch(env, admins):
    api, _ = env
    admin_h, _ = await _admin_and_target(api, "approved")
    async with db_base.sessionmaker()() as db:
        db.add(PlaybackBatch(user_id=2, scope="all", status="queued"))
        await db.commit()
    assert (await _decide(api, admin_h, "revoke")).status_code == 200
    async with db_base.sessionmaker()() as db:
        batch = (await db.scalars(select(PlaybackBatch))).one()
        assert batch.cancel_requested is True


async def test_access_requests_list_and_filters(env, admins, clock):
    api, _ = env
    admin_h = await _signup(api, ADMIN_ID)
    for i, (sid, status, prev, rejects, at) in enumerate([
        ("20210001", "pending", "revoked", 0, 2), ("20210002", "pending", "rejected", 2, 1),
        ("20210003", "pending", "none", 0, 3), ("20210004", "approved", None, 0, None),
    ]):
        await _signup(api, sid)
        async with db_base.sessionmaker()() as db:
            await db.execute(update(User).where(User.student_id == sid).values(
                access_status=status, access_previous_status=prev, access_reject_count=rejects,
                access_requested_at=None if at is None else NOW + timedelta(hours=at)))
            await db.commit()

    r = await api.get("/admin/access-requests", headers=admin_h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert [(i["student_id"], i["previous_status"], i["reject_count"]) for i in body["items"]] == [
        ("20210002", "rejected", 2), ("20210001", "revoked", 0), ("20210003", "none", 0)]
    assert datetime.fromisoformat(body["items"][0]["requested_at"]) == NOW + timedelta(hours=1)

    users = (await api.get("/admin/users?access=pending", headers=admin_h)).json()
    assert sorted(u["student_id"] for u in users["users"]) == ["20210001", "20210002", "20210003"]
    assert {u["access_status"] for u in users["users"]} == {"pending"}
    approved = (await api.get("/admin/users?access=approved", headers=admin_h)).json()
    assert sorted(u["student_id"] for u in approved["users"]) == [ADMIN_ID, "20210004"]  # 관리자는 approved
    assert (await api.get("/admin/users?access=bogus", headers=admin_h)).status_code == 422
    assert len((await api.get("/admin/users", headers=admin_h)).json()["users"]) == 5

    assert (await api.get("/admin/overview", headers=admin_h)).json()["pending_count"] == 3
    summary = (await api.get("/admin/users/20210001/summary", headers=admin_h)).json()
    assert summary["access_status"] == "pending"
    assert (await api.get(f"/admin/users/{ADMIN_ID}/summary", headers=admin_h)).json()["access_status"] == "approved"


async def test_concurrent_decisions_conflict(env, admins):
    api, _ = env
    admin_h, _ = await _admin_and_target(api, "pending")
    assert (await _decide(api, admin_h, "approve")).status_code == 200
    assert (await _decide(api, admin_h, "reject")).status_code == 409
    assert (await _user("20201234")).access_status == "approved"


async def test_approve_still_succeeds_when_queue_is_down(env, admins, monkeypatch, caplog):
    api, queue = env
    admin_h, _ = await _admin_and_target(api, "pending")

    async def down(*args, **kwargs):
        raise ConnectionError("redis://:s3cret@redis:6379 refused")

    monkeypatch.setattr(queue, "enqueue", down)
    with caplog.at_level("WARNING"):
        r = await _decide(api, admin_h, "approve")
    assert r.status_code == 200, r.text
    assert r.json()["access_status"] == "approved" and r.json()["sync_queued"] is False
    assert (await _user("20201234")).access_status == "approved"
    assert "s3cret" not in caplog.text and "동기화" in caplog.text
    assert (await _decide(api, admin_h, "approve")).status_code == 409  # 승인은 저장됐다


async def test_decision_reports_sync_queued(env, admins):
    api, _ = env
    admin_h, _ = await _admin_and_target(api, "pending")
    assert (await _decide(api, admin_h, "approve")).json()["sync_queued"] is True


@respx.mock
async def test_webhook_url_never_reaches_logs(env, admins, clock, caplog):
    api, _ = env
    secret = "SECRET-TOKEN-123"
    hook = f"https://discord.com/api/webhooks/9/{secret}"
    await _signup(api, ADMIN_ID)
    await _add_channel(ADMIN_ID, "discord", hook)
    respx.post(hook).respond(500)
    h = await _signup(api)
    with caplog.at_level("DEBUG"):
        assert (await api.post("/access/request", headers=h)).status_code == 200
    assert secret not in caplog.text and "discord.com/api/webhooks" not in caplog.text
    assert "500" in caplog.text  # 상태 코드는 남는다


async def test_audio_routes_are_gone(env):
    api, _ = env
    paths = {p for _, p in _all_routes(api._transport.app)}
    assert not any(p.startswith("/audio/") or p.endswith("/audio-link") for p in paths)
