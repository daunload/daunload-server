from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import base as db_base
from app.db.models import (AssignmentRow, CourseRow, DownloadRecord, LmsCredential, NotificationChannel,
                           NotificationLog, PlaybackBatch, PlaybackJob, TodoRow, Transcript,
                           User, UserDailyActivity)
from app.services.activity import local_day
from app.services.saved_videos import link_saved_video
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
    return PlaybackJob(batch_id=batch_id, user_id=user_id, position=n, lms_course_id=kw.pop("lms_course_id", 1), module_item_id=n,
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
    # 오늘 한국 자정에 맞춘다. now - 1시간은 00시~01시에 실행하면 어제가 되어 오늘 칸 검사가 깨진다.
    midnight = period_start(1)
    today = local_day(midnight)
    async with db_base.sessionmaker()() as db:
        # user 1: 수동 재생, user 2: auto 재생만
        manual, auto = _batch(1), _batch(2, scope="auto")
        db.add_all([manual, auto])
        await db.flush()
        db.add_all([
            _job(manual.id, 1, n=1, duration=100, started_at=midnight, finished_at=midnight),
            _job(manual.id, 1, "failed", n=2, started_at=midnight, finished_at=midnight),
            _job(auto.id, 2, n=3, duration=50, started_at=midnight, finished_at=midnight),
            _job(auto.id, 2, n=4, duration=50, started_at=midnight, finished_at=midnight),
        ])
        db.add_all([
            UserDailyActivity(user_id=1, day=today, logins=1, downloads=2),
            UserDailyActivity(user_id=2, day=today - timedelta(days=1), logins=0, downloads=3),
            UserDailyActivity(user_id=2, day=today - timedelta(days=40), logins=1, downloads=9),
        ])
        await db.commit()
    body = (await api.get("/admin/overview", headers=admin)).json()
    assert body["active_users"] == 2  # user 1 수동 재생·로그인, user 2 다운로드(auto만으로는 아님)
    assert body["playback_done"] == 3 and body["playback_auto"] == 2 and body["playback_seconds"] == 200
    assert body["playback_failed"] == 1 and body["failure_rate"] == 0.25
    assert body["downloads"] == 5
    assert body["tracking_since"] == str(today - timedelta(days=40))
    by_day = {s["day"]: s for s in body["series"]}
    assert by_day[str(today)] == {"day": str(today), "playback_done": 3, "logins": 1, "downloads": 2}
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
    assert line["title"] == "all"  # 묶음 줄의 제목은 범위 값이고, 화면이 이름(전체 재생 묶음)을 붙인다


async def test_problems_course_filter_matches_jobs_in_all_and_auto_batches(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    t = _recent()
    async with db_base.sessionmaker()() as db:
        everything = _batch(2, scope="all", status="failed", created=t, finished_at=t, error="중단됨")
        auto = _batch(2, scope="auto", status="failed", created=t, finished_at=t, error="자동 중단")
        course = _batch(2, scope="course", lms_course_id=7, status="failed", created=t, finished_at=t, error="과목 중단")
        db.add_all([everything, auto, course])
        await db.flush()
        db.add_all([
            _job(everything.id, 2, "failed", n=1, lms_course_id=5),  # 시도 전 중단: 재생 문제는 아니다
            _job(everything.id, 2, "done", n=2, lms_course_id=6, started_at=t, finished_at=t),
            _job(auto.id, 2, "failed", n=3, lms_course_id=6),
            _job(course.id, 2, "failed", n=4, lms_course_id=7),
        ])
        await db.commit()

    def scopes(body):
        return sorted(p["title"] for p in body["problems"] if p["kind"] == "batch")
    five = (await api.get("/admin/problems?lms_course_id=5", headers=admin)).json()
    assert scopes(five) == ["all"] and five["counts"]["batch"] == 1
    assert scopes((await api.get("/admin/problems?lms_course_id=6", headers=admin)).json()) == ["all", "auto"]
    assert scopes((await api.get("/admin/problems?lms_course_id=7", headers=admin)).json()) == ["course"]
    assert (await api.get("/admin/problems?lms_course_id=8", headers=admin)).json()["total"] == 0
    assert scopes((await api.get("/admin/problems", headers=admin)).json()) == ["all", "auto", "course"]


async def test_problems_lms_ignores_period_and_resolve_respects_it(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    now = datetime.now(UTC)
    old = now - timedelta(days=200)
    async with db_base.sessionmaker()() as db:
        await _set_lms(db, status="invalid", last_error="인증 실패", last_error_at=old)

        for n, failed_at in ((1, now - timedelta(days=1)), (2, old), (3, None)):
            await link_saved_video(db, 2, lms_course_id=1, course_name="과목", module_item_id=n, module_title="m",
                                   title=f"저장{n}", content_id="c", file_name="f", resolve_error="못 찾음",
                                   resolve_failed_at=failed_at)
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


# ---- 사용자 목록과 상세 ----
FORBIDDEN_KEYS = {"password_hash", "password_enc", "session_enc", "target_enc", "token_version", "media_url",
                  "video", "text", "segments", "score", "points_possible", "target"}
DETAIL = ("summary", "courses", "todos", "assignments", "playback", "downloads", "saved-videos",
          "transcripts", "notifications")


def _keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


def _recent():
    """어제 새벽 1시(한국). 시각과 상관없이 항상 과거이고 7일 안이다."""
    return period_start(2) + timedelta(hours=1)


def _stale():
    return period_start(7) - timedelta(hours=1)


async def _seed_two_users(db):
    """user 1: 과목2, 재생 완료3, 다운로드(2+3회), 전사2, 채널2. user 2: 과목1, 재생 완료1."""
    t = _recent()
    db.add_all([
        CourseRow(user_id=1, lms_course_id=1, name="A"), CourseRow(user_id=1, lms_course_id=2, name="B"),
        CourseRow(user_id=2, lms_course_id=1, name="C"),
    ])
    b1, b2 = _batch(1), _batch(2)
    db.add_all([b1, b2])
    await db.flush()
    db.add_all([
        _job(b1.id, 1, n=1, started_at=t, finished_at=t), _job(b1.id, 1, n=2, started_at=t, finished_at=t),
        _job(b1.id, 1, n=3, started_at=t, finished_at=t), _job(b2.id, 2, n=4, started_at=t, finished_at=t),
        DownloadRecord(user_id=1, lms_course_id=1, item_key="v1", kind="video", title="d1", count=2, last_at=t),
        DownloadRecord(user_id=1, lms_course_id=1, item_key="v2", kind="video", title="d2", count=3, last_at=t),
        Transcript(user_id=1, lms_course_id=1, module_item_id=1, title="t1", course_name="A", text="PRIVATE", model="m"),
        Transcript(user_id=1, lms_course_id=1, module_item_id=2, title="t2", course_name="A", text="PRIVATE", model="m"),
        NotificationChannel(user_id=1, kind="discord", target_enc="PRIVATE"),
        NotificationChannel(user_id=1, kind="telegram", target_enc="PRIVATE"),
    ])
    await db.commit()


async def test_users_list_counts_without_mixing_users(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    async with db_base.sessionmaker()() as db:
        await _seed_two_users(db)
    r = await api.get("/admin/users", headers=admin)
    rows = {u["student_id"]: u for u in r.json()["users"]}
    assert r.json()["total"] == 2
    a, b = rows["20201234"], rows["20209999"]
    assert (a["course_count"], a["playback_done"], a["playback_failed"], a["download_count"],
            a["transcript_count"], sorted(a["channels"])) == (2, 3, 0, 5, 2, ["discord", "telegram"])
    assert (b["course_count"], b["playback_done"], b["download_count"], b["transcript_count"], b["channels"]) == (1, 1, 0, 0, [])
    assert a["lms_status"] == "ok" and "login_count" in a and "created_at" in a


async def test_users_list_marks_admin_rows(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    async with db_base.sessionmaker()() as db:
        (await db.get(User, 2)).access_status = "approved"
        await db.commit()
    rows = {u["student_id"]: u for u in (await api.get("/admin/users", headers=admin)).json()["users"]}
    assert rows["20201234"]["is_admin"] is True and rows["20201234"]["access_status"] == "approved"
    assert rows["20209999"]["is_admin"] is False and rows["20209999"]["access_status"] == "approved"


async def test_users_filters_and_sort(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    now = datetime.now(UTC)
    async with db_base.sessionmaker()() as db:
        await _set_lms(db, status="invalid", last_error="x")
        b = _batch(1, status="failed")
        db.add(b)
        await db.flush()
        db.add(_job(b.id, 1, "failed", n=1, started_at=_recent(), finished_at=_recent()))
        await db.commit()

    def ids(r):
        return [u["student_id"] for u in r.json()["users"]]
    # 둘 다 문제 있음: user 1은 최근 실패, user 2는 LMS 비정상
    assert set(ids(await api.get("/admin/users?has_problem=true", headers=admin))) == {"20201234", "20209999"}
    assert ids(await api.get("/admin/users?lms_status=invalid", headers=admin)) == ["20209999"]
    assert ids(await api.get("/admin/users?has_problem=false", headers=admin)) == []
    async with db_base.sessionmaker()() as db:  # 실패가 7일 밖이고 LMS가 정상이면 문제 없음
        job = await db.get(PlaybackJob, 1)
        job.started_at = job.finished_at = _stale()
        await _set_lms(db, status="ok")
    assert ids(await api.get("/admin/users?has_problem=true", headers=admin)) == []
    assert set(ids(await api.get("/admin/users?has_problem=false", headers=admin))) == {"20201234", "20209999"}
    assert (await api.get("/admin/users?has_problem=true&q=zzz", headers=admin)).json()["total"] == 0
    # 세 정렬이 서로 다른 순서가 되게: 활동 user2>user3>user1, 가입 user3>user1>user2, 학번 user1<user2<user3
    await _signup(api, "20205555")  # user 3
    async with db_base.sessionmaker()() as db:
        u1, u2, u3 = [await db.get(User, i) for i in (1, 2, 3)]
        u1.last_login_at, u2.last_login_at, u3.last_login_at = now - timedelta(days=3), now - timedelta(days=1), now - timedelta(days=2)
        u1.created_at, u2.created_at, u3.created_at = now - timedelta(days=5), now - timedelta(days=9), now - timedelta(days=1)
        await db.commit()
    assert ids(await api.get("/admin/users", headers=admin)) == ["20209999", "20205555", "20201234"]
    assert ids(await api.get("/admin/users?sort=created", headers=admin)) == ["20205555", "20201234", "20209999"]
    assert ids(await api.get("/admin/users?sort=student_id", headers=admin)) == ["20201234", "20205555", "20209999"]
    page = (await api.get("/admin/users?sort=created&limit=1&offset=1", headers=admin)).json()
    assert page["total"] == 3 and [x["student_id"] for x in page["users"]] == ["20201234"]
    for q in ("sort=x", "limit=101", "offset=-1"):
        assert (await api.get("/admin/users?" + q, headers=admin)).status_code == 422


async def test_user_detail_routes_require_admin_and_404(env, monkeypatch):
    api, admin, other = await _admin(env, monkeypatch)
    for name in DETAIL:
        path = f"/admin/users/20209999/{name}"
        assert (await api.get(path)).status_code == 401
        assert (await api.get(path, headers=other)).status_code == 403
        r = await api.get(path, headers=admin)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", path
        assert (await api.get(f"/admin/users/00000000/{name}", headers=admin)).status_code == 404
        assert (await api.post(path, headers=admin)).status_code == 405
        if name != "summary":
            assert (await api.get(path + "?limit=101", headers=admin)).status_code == 422
            assert (await api.get(path + "?offset=-1", headers=admin)).status_code == 422
            assert set(r.json()) >= {"total", "items"}
    # 예전 설정 경로는 새 화면이 쓰지 않아 지웠다
    assert (await api.get("/admin/users/20209999/settings", headers=admin)).status_code == 404


async def test_user_detail_never_exposes_private_fields(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    t = _recent()
    async with db_base.sessionmaker()() as db:
        await _set_lms(db, last_error="실패 https://lms.test/PRIVATE/x?token=PRIVATE 끝", last_error_at=t)
        batch = _batch(2, error="https://cdn.test/PRIVATE?k=PRIVATE")
        db.add(batch)
        await db.flush()
        db.add_all([
            _job(batch.id, 2, "failed", n=1, started_at=t, finished_at=t, error="http://cdn.test/PRIVATE/a.mp4?t=PRIVATE"),
            Transcript(user_id=2, lms_course_id=1, module_item_id=1, title="t", course_name="c",
                       text="PRIVATE", segments=[{"start": 0, "end": 1, "text": "PRIVATE"}], model="m"),
            NotificationChannel(user_id=2, kind="discord", target_enc="PRIVATE"),
            NotificationLog(user_id=2, key="deadline:1"),
            AssignmentRow(user_id=2, lms_course_id=1, course_name="c", assignment_id=1, title="a", kind="assignment",
                          submission_state="submitted", score=99, points_possible=100),
            TodoRow(user_id=2, lms_course_id=1, course_name="c", kind="video", lms_item_id=1, title="v", deadline=None),
            CourseRow(user_id=2, lms_course_id=1, name="c"),
            DownloadRecord(user_id=2, lms_course_id=1, item_key="v1", kind="video", title="d"),
        ])
        await link_saved_video(db, 2, lms_course_id=1, course_name="c", module_item_id=1, module_title="m", title="s",
                               content_id="c", file_name="f", media_url="https://cdn.test/PRIVATE.mp4",
                               resolve_error="https://cdn.test/PRIVATE?x=PRIVATE")
        await db.commit()
    for name in DETAIL:
        r = await api.get(f"/admin/users/20209999/{name}", headers=admin)
        assert r.status_code == 200
        assert "PRIVATE" not in r.text, name
        assert not FORBIDDEN_KEYS & set(_keys(r.json())), (name, FORBIDDEN_KEYS & set(_keys(r.json())))
    assert (await api.get("/admin/users/20209999/todos", headers=admin)).json()["items"][0]["deadline"] is None
    assert (await api.get("/admin/users/20209999/summary", headers=admin)).json()["lms"]["last_error"] == "실패 https://lms.test 끝"


async def test_user_summary_fields(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    async with db_base.sessionmaker()() as db:
        await _seed_two_users(db)
        db.add(TodoRow(user_id=1, lms_course_id=1, course_name="A", kind="video", lms_item_id=1, title="v", active=True))
        db.add(TodoRow(user_id=1, lms_course_id=1, course_name="A", kind="video", lms_item_id=2, title="w", active=False))
        # 마감이 지났지만 다음 동기화가 아직 지우지 않은 줄: 학생 /todos처럼 세지 않는다
        db.add(TodoRow(user_id=1, lms_course_id=1, course_name="A", kind="video", lms_item_id=3, title="past",
                       active=True, deadline=datetime.now(UTC) - timedelta(minutes=1)))
        db.add(TodoRow(user_id=1, lms_course_id=1, course_name="A", kind="quiz", lms_item_id=4, title="future",
                       active=True, deadline=datetime.now(UTC) + timedelta(days=1)))
        await db.commit()
    body = (await api.get("/admin/users/20201234/summary", headers=admin)).json()
    assert body["student_id"] == "20201234" and body["is_admin"] is True
    assert body["lms"]["status"] == "ok" and {"consent_at", "created_at", "login_count", "last_login_at"} <= set(body)
    assert body["counts"] == {"courses": 2, "todos": 2, "playback_done": 3, "playback_failed": 0, "downloads": 5,
                              "saved_videos": 0, "transcripts": 2, "channels": 2}
    # 할 일 탭은 지난 줄과 사라진 줄도 그대로 보여 준다
    assert (await api.get("/admin/users/20201234/todos", headers=admin)).json()["total"] == 4
    student = (await api.get("/todos", headers=admin)).json()
    assert len(student) == body["counts"]["todos"]


async def test_playback_tab_groups_batches_and_cleans_errors(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    t = _recent()
    async with db_base.sessionmaker()() as db:
        b1 = _batch(2, status="partial", created=t, error="중단 https://cdn.test/a/b?token=SECRET")
        b2 = _batch(2, status="done", created=t - timedelta(hours=1))
        old = _batch(2, status="done", created=_stale())
        other_user = _batch(1)
        db.add_all([b1, b2, old, other_user])
        await db.flush()
        db.add_all([
            _job(b1.id, 2, "failed", n=1, started_at=t, finished_at=t, error="x https://cdn.test/p/q?token=SECRET y", attempts=2),
            _job(b1.id, 2, "done", n=2, started_at=t, finished_at=t),
            _job(b2.id, 2, n=3), _job(old.id, 2, n=4), _job(other_user.id, 1, n=5),
        ])
        await db.commit()
    r = await api.get("/admin/users/20209999/playback", headers=admin)
    body = r.json()
    assert body["total"] == 3 and "token=SECRET" not in r.text and "/p/q" not in r.text
    first = body["items"][0]
    assert first["status"] == "partial" and first["error"] == "중단 https://cdn.test"
    assert [j["title"] for j in first["jobs"]] == ["영상1", "영상2"]
    assert first["jobs"][0]["error"] == "x https://cdn.test y" and first["jobs"][0]["attempts"] == 2
    assert first["jobs"][0]["deadline"] is None
    assert [len(i["jobs"]) for i in body["items"]] == [2, 1, 1]
    assert (await api.get("/admin/users/20209999/playback?status=done", headers=admin)).json()["total"] == 3
    assert (await api.get("/admin/users/20209999/playback?lms_course_id=1", headers=admin)).json()["total"] == 3
    assert (await api.get("/admin/users/20209999/playback?status=bogus", headers=admin)).status_code == 422
    assert (await api.get("/admin/users/20209999/playback?days=7", headers=admin)).json()["total"] == 2
    # 일부 실패라 partial인 묶음도 status=failed로 나오고, 안의 job은 모두 돌려준다
    failed = (await api.get("/admin/users/20209999/playback?status=failed", headers=admin)).json()
    assert failed["total"] == 1 and failed["items"][0]["status"] == "partial" and len(failed["items"][0]["jobs"]) == 2
    page = (await api.get("/admin/users/20209999/playback?limit=1&offset=1", headers=admin)).json()
    assert page["total"] == 3 and len(page["items"]) == 1


async def test_playback_course_filter_matches_jobs_in_all_and_auto_batches(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    t = _recent()
    async with db_base.sessionmaker()() as db:
        everything = _batch(2, scope="all", status="partial", created=t)  # 묶음에는 lms_course_id가 없다
        auto = _batch(2, scope="auto", created=t - timedelta(hours=1))
        db.add_all([everything, auto])
        await db.flush()
        db.add_all([
            _job(everything.id, 2, "failed", n=1, started_at=t, finished_at=t, lms_course_id=5),
            _job(everything.id, 2, "done", n=2, lms_course_id=6),
            _job(auto.id, 2, "done", n=3, lms_course_id=5),
        ])
        await db.commit()
    body = (await api.get("/admin/users/20209999/playback?lms_course_id=5", headers=admin)).json()
    assert body["total"] == 2 and len(body["items"][0]["jobs"]) == 2
    assert (await api.get("/admin/users/20209999/playback?lms_course_id=6", headers=admin)).json()["total"] == 1
    both = (await api.get("/admin/users/20209999/playback?lms_course_id=5&status=failed", headers=admin)).json()
    assert both["total"] == 1 and both["items"][0]["scope"] == "all"
    assert (await api.get("/admin/users/20209999/playback?lms_course_id=7", headers=admin)).json()["total"] == 0


async def test_saved_videos_report_only_whether_address_exists(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    async with db_base.sessionmaker()() as db:
        for n, kw in ((1, {"media_url": "https://cdn.test/PRIVATE.mp4"}), (2, {"resolve_error": "실패 https://x.test/a?b"})):
            await link_saved_video(db, 2, lms_course_id=1, course_name="c", module_item_id=n, module_title="1주차",
                                   title=f"s{n}", content_id="c", file_name="f.mp4", **kw)
        await db.commit()
    r = await api.get("/admin/users/20209999/saved-videos", headers=admin)
    items = {i["title"]: i for i in r.json()["items"]}
    assert items["s1"]["has_media_url"] is True and items["s2"]["has_media_url"] is False
    assert "media_url" not in items["s1"] and "PRIVATE" not in r.text
    assert items["s2"]["resolve_error"] == "실패 https://x.test"
    assert (await api.get("/admin/users/20209999/summary", headers=admin)).json()["counts"]["saved_videos"] == 2


async def test_course_todo_assignment_download_transcript_notification_tabs(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    t = _recent()
    async with db_base.sessionmaker()() as db:
        db.add_all([
            CourseRow(user_id=2, lms_course_id=1, name="C", professors="교수", auto_play=True,
                      attendance=[{"week": 1, "statuses": ["attendance"]}]),
            AssignmentRow(user_id=2, lms_course_id=1, course_name="C", assignment_id=1, title="a", kind="quiz",
                          submission_state="unsubmitted", missing=True, deadline=None),
            DownloadRecord(user_id=2, lms_course_id=1, item_key="v1", kind="video", title="d", count=2, first_at=t, last_at=t),
            Transcript(user_id=2, lms_course_id=1, module_item_id=1, title="t", course_name="C", text="x", model="small", duration=5),
            NotificationChannel(user_id=2, kind="telegram", target_enc="x", enabled=False),
            NotificationLog(user_id=2, key="k1"), NotificationLog(user_id=1, key="other"),
        ])
        await db.commit()
    get = lambda name: api.get(f"/admin/users/20209999/{name}", headers=admin)  # noqa: E731
    c = (await get("courses")).json()["items"][0]
    assert (c["name"], c["professors"], c["auto_play"], c["attendance"]) == ("C", "교수", True, [{"week": 1, "statuses": ["attendance"]}])
    a = (await get("assignments")).json()["items"][0]
    assert a["submission_state"] == "unsubmitted" and a["missing"] is True and a["deadline"] is None
    d = (await get("downloads")).json()["items"][0]
    assert d["title"] == "d" and d["count"] == 2 and d["kind"] == "video"
    tr = (await get("transcripts")).json()["items"][0]
    assert tr["model"] == "small" and tr["title"] == "t"
    n = (await get("notifications")).json()
    assert n["channels"] == [{"kind": "telegram", "enabled": False, "created_at": n["channels"][0]["created_at"]}]
    assert [i["key"] for i in n["items"]] == ["k1"] and n["total"] == 1


async def test_users_token_status_and_filter(env, monkeypatch):
    api, admin, _ = await _admin(env, monkeypatch)
    await _signup(api, "20205555")  # user 3: LMS 연결 없음으로 바꾼다
    async with db_base.sessionmaker()() as db:
        cred1 = await db.get(LmsCredential, 1)
        cred1.personal_token_enc, cred1.personal_token_status, cred1.personal_token_managed = "enc", "ok", True
        await db.delete(await db.get(LmsCredential, 3))
        await db.commit()

    def rows(r):
        return {u["student_id"]: u["token_status"] for u in r.json()["users"]}
    assert rows(await api.get("/admin/users", headers=admin)) == {"20201234": "ok", "20209999": "none", "20205555": None}
    assert rows(await api.get("/admin/users?token_missing=true", headers=admin)) == {"20209999": "none"}
    assert set(rows(await api.get("/admin/users?token_missing=false", headers=admin))) == {"20201234", "20205555"}
    async with db_base.sessionmaker()() as db:
        await _set_lms(db, personal_token_enc="enc", personal_token_status="invalid")
    assert rows(await api.get("/admin/users?token_missing=true", headers=admin)) == {"20209999": "invalid"}

    lms = (await api.get("/admin/users/20201234/summary", headers=admin)).json()["lms"]
    assert lms["token_status"] == "ok" and lms["token_managed"] is True
    assert (await api.get("/admin/users/20209999/summary", headers=admin)).json()["lms"]["token_status"] == "invalid"
