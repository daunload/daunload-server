from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import base as db_base
from app.db.models import (CourseRow, DownloadRecord, LmsCredential, PlaybackBatch, PlaybackJob, SavedVideo,
                           Transcript, UserDailyActivity)
from app.services.activity import local_day
from app.services.admin_data import clean_error, period_start
from tests.test_api import _signup, env  # noqa: F401

PATHS = ("/admin/overview", "/admin/activities", "/admin/problems")


def test_clean_error_strips_url_path_and_limits_length():
    assert clean_error("실패 https://cdn.test/a/b.mp4?token=SECRET 끝") == "실패 https://cdn.test 끝"
    assert clean_error("x https://cdn.test?token=SECRET y") == "x https://cdn.test y"
    assert clean_error("https://user:pw@cdn.test/a") == "https://cdn.test"
    assert len(clean_error("가" * 3000)) == 2000
    assert clean_error(None) is None


async def _admin(env, monkeypatch):
    api, _ = env
    monkeypatch.setattr(get_settings(), "admin_student_ids", ["20201234"])
    admin = await _signup(api)
    other = await _signup(api, "20209999")  # user id 2
    async with db_base.sessionmaker()() as db:  # 가입 로그인이 남긴 오늘 기록은 지표 계산에 섞이지 않게 지운다
        await db.execute(UserDailyActivity.__table__.delete())
        await db.commit()
    return api, admin, other


async def _set_lms(db, **values):
    """가입할 때 만들어진 user 2의 LMS 줄을 바꾼다."""
    cred = await db.get(LmsCredential, 2)
    for key, value in values.items():
        setattr(cred, key, value)
    await db.commit()


def _batch(user_id, scope="all", status="done", created=None, **kw):
    return PlaybackBatch(user_id=user_id, scope=scope, status=status, created_at=created or datetime.now(UTC), **kw)


def _job(batch_id, user_id, status="done", n=0, **kw):
    kw.setdefault("title", f"영상{n}")
    return PlaybackJob(batch_id=batch_id, user_id=user_id, position=n, lms_course_id=1, module_item_id=n,
                       duration=kw.pop("duration", 60), video={"url": "PRIVATE-URL"}, status=status, **kw)


async def test_admin_new_endpoints_require_admin(env, monkeypatch):
    api, admin, other = await _admin(env, monkeypatch)
    for path in PATHS:
        assert (await api.get(path)).status_code == 401
        assert (await api.get(path, headers=other)).status_code == 403
        r = await api.get(path, headers=admin)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert (await api.get(path + "?days=14", headers=admin)).status_code == 422
        assert (await api.post(path, headers=admin)).status_code == 405


async def test_overview_empty_service(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    r = await api.get("/admin/overview?days=30", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failure_rate"] is None and body["tracking_since"] is None
    assert len(body["series"]) == 30
    assert all(s["playback_done"] == s["logins"] == s["downloads"] == 0 for s in body["series"])
    assert body["total_users"] == 2 and body["active_users"] == 0
    assert body["playback_done"] == body["playback_failed"] == body["problem_count"] == 0


async def test_overview_metrics(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    now = datetime.now(UTC)
    async with db_base.sessionmaker()() as db:
        # user 1: 수동 재생, user 2: auto 재생만
        manual, auto = _batch(1), _batch(2, scope="auto")
        db.add_all([manual, auto])
        await db.flush()
        db.add_all([
            _job(manual.id, 1, n=1, duration=100, started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1)),
            _job(manual.id, 1, "failed", n=2, started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1)),
            _job(auto.id, 2, n=3, duration=50, started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1)),
            _job(auto.id, 2, n=4, duration=50, started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1)),
        ])
        db.add_all([
            UserDailyActivity(user_id=1, day=local_day(), logins=1, downloads=2),
            UserDailyActivity(user_id=2, day=local_day() - timedelta(days=1), logins=0, downloads=3),
            UserDailyActivity(user_id=2, day=local_day() - timedelta(days=40), logins=1, downloads=9),
        ])
        await db.commit()
    body = (await api.get("/admin/overview", headers=admin)).json()
    assert body["active_users"] == 2  # user 1 수동 재생·로그인, user 2 다운로드(auto만으로는 아님)
    assert body["playback_done"] == 3 and body["playback_auto"] == 2 and body["playback_seconds"] == 200
    assert body["playback_failed"] == 1 and body["failure_rate"] == 0.25
    assert body["downloads"] == 5
    assert body["tracking_since"] == str(local_day() - timedelta(days=40))
    assert body["series"][-1] == {"day": str(local_day()), "playback_done": 3, "logins": 1, "downloads": 2}
    assert len(body["series"]) == 7 and len(body["recent_activity"]) == 4
    assert body["problem_count"] == 1 and body["recent_problems"][0]["kind"] == "playback"
    # auto 재생만 한 사용자(로그인·다운로드 기록 없음)는 active가 아니다
    async with db_base.sessionmaker()() as db:
        await db.execute(UserDailyActivity.__table__.delete())
        await db.commit()
    assert (await api.get("/admin/overview", headers=admin)).json()["active_users"] == 1


async def test_period_starts_at_korean_midnight(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    start = period_start(7)
    assert start.astimezone(ZoneInfo("Asia/Seoul")).time().isoformat() == "00:00:00"
    assert start.astimezone(ZoneInfo("Asia/Seoul")).date() == local_day() - timedelta(days=6)
    async with db_base.sessionmaker()() as db:
        batch = _batch(2)
        db.add(batch)
        await db.flush()
        db.add_all([
            _job(batch.id, 2, n=1, started_at=start - timedelta(hours=1), finished_at=start - timedelta(seconds=1)),
            _job(batch.id, 2, n=2, started_at=start, finished_at=start),
        ])
        await db.commit()
    body = (await api.get("/admin/overview", headers=admin)).json()
    assert body["playback_done"] == 1
    assert body["series"][0]["playback_done"] == 1


async def test_failure_counts_only_attempted_jobs(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    now = datetime.now(UTC)
    async with db_base.sessionmaker()() as db:
        batch = _batch(2, status="failed", finished_at=now - timedelta(minutes=5), error="중단됨")
        db.add(batch)
        await db.flush()
        db.add_all([
            _job(batch.id, 2, "failed", n=1, started_at=now - timedelta(hours=1), finished_at=now - timedelta(minutes=30), error="실패1"),
            _job(batch.id, 2, "locked", n=2, started_at=now - timedelta(hours=1), finished_at=now - timedelta(minutes=29)),
            _job(batch.id, 2, "failed", n=3, error="미실행"),
            _job(batch.id, 2, "failed", n=4, error="미실행"),
            _job(batch.id, 2, "failed", n=5, error="미실행"),
        ])
        await db.commit()
    assert (await api.get("/admin/overview", headers=admin)).json()["playback_failed"] == 2
    body = (await api.get("/admin/problems", headers=admin)).json()
    assert body["counts"] == {"playback": 2, "batch": 1, "lms": 0, "resolve": 0}
    assert {p["title"] for p in body["problems"] if p["kind"] == "playback"} == {"영상1", "영상2"}
    line = next(p for p in body["problems"] if p["kind"] == "batch")
    assert line["not_run"] == 3 and line["error"] == "중단됨"


async def test_problems_lms_ignores_period_and_resolve_respects_it(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    now = datetime.now(UTC)
    old = now - timedelta(days=200)
    async with db_base.sessionmaker()() as db:
        await _set_lms(db, status="invalid", last_error="인증 실패", last_error_at=old)

        def video(n, failed_at):
            return SavedVideo(user_id=2, lms_course_id=1, course_name="과목", module_item_id=n, module_title="m",
                              title=f"저장{n}", content_id="c", file_name="f", resolve_error="못 찾음",
                              resolve_failed_at=failed_at)
        db.add_all([video(1, now - timedelta(days=1)), video(2, old), video(3, None)])
        await db.commit()
    body = (await api.get("/admin/problems", headers=admin)).json()
    assert body["counts"] == {"playback": 0, "batch": 0, "lms": 1, "resolve": 1}
    assert {p["title"] for p in body["problems"]} == {"LMS 연결", "저장1"}
    assert (await api.get("/admin/problems?days=90", headers=admin)).json()["counts"]["resolve"] == 1
    assert (await api.get("/admin/problems?kind=lms&student_id=20201234", headers=admin)).json()["total"] == 0
    assert (await api.get("/admin/problems?lms_course_id=1", headers=admin)).json()["counts"]["lms"] == 0


async def test_problems_clean_errors_and_hide_secrets(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    async with db_base.sessionmaker()() as db:
        await _set_lms(db, status="error", last_error="실패 https://lms.test/api/x?token=SECRET 끝")
    r = await api.get("/admin/problems", headers=admin)
    body = r.json()
    assert "token=SECRET" not in r.text and "password_enc" not in r.text and "session_enc" not in r.text
    assert body["problems"][0]["error"] == "실패 https://lms.test 끝"
    assert body["problems"][0]["occurred_at"] is None and body["problems"][0]["student_id"] == "20209999"


async def test_activities_filter_by_student_and_kind(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    now = datetime.now(UTC)
    async with db_base.sessionmaker()() as db:
        db.add(CourseRow(user_id=2, lms_course_id=1, name="사용자 과목"))
        batch = _batch(2, created=now - timedelta(days=1))
        old = _batch(2, created=now - timedelta(days=20))
        db.add_all([batch, old])
        await db.flush()
        db.add_all([
            _job(batch.id, 2, n=1, error="PRIVATE-ERROR", started_at=now, finished_at=now),
            _job(old.id, 2, n=2),
            DownloadRecord(user_id=2, lms_course_id=1, item_key="v1", kind="video", title="다운로드", count=3, last_at=now),
            DownloadRecord(user_id=1, lms_course_id=1, item_key="v1", kind="video", title="관리자 다운로드", last_at=now),
            Transcript(user_id=1, lms_course_id=1, module_item_id=1, title="전사", course_name="c",
                       text="PRIVATE-TEXT", segments=[], model="m", updated_at=now - timedelta(hours=1)),
        ])
        await db.commit()
    r = await api.get("/admin/activities", headers=admin)
    body = r.json()
    assert body["total"] == 4 and body["counts"] == {"playback": 1, "download": 2, "transcript": 1}
    assert {a["student_id"] for a in body["activities"]} == {"20201234", "20209999"}
    assert "PRIVATE" not in r.text and "error" not in body["activities"][0]
    mine = (await api.get("/admin/activities?student_id=20209999", headers=admin)).json()
    assert mine["total"] == 2 and {a["student_id"] for a in mine["activities"]} == {"20209999"}
    assert (await api.get("/admin/activities?days=30&student_id=20209999&kind=playback", headers=admin)).json()["total"] == 2
    page = (await api.get("/admin/activities?kind=download&limit=1&offset=1", headers=admin)).json()
    assert page["total"] == 2 and len(page["activities"]) == 1
    for q in ("kind=x", "limit=101", "offset=-1"):
        assert (await api.get("/admin/activities?" + q, headers=admin)).status_code == 422
