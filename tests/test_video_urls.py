"""재생할 때 실제로 쓰인 영상 주소 기록(video_urls)과 관리자 조회."""

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import func, select

from app.db import base as db_base
from app.db.models import PlaybackBatch, User, VideoUrl
from app.lms.media import CONTENT_PHP
from app.lms.player import MediaWatch, lecture_media_url
from app.lms.todo import VideoTodo
from app.services import playback
from app.services.video_urls import record_video_url
from tests.test_admin_api import _admin
from tests.test_api import _signup, env  # noqa: F401

MP4 = "https://ssuin-object.commonscdn.com/a/media_files/lecture.mp4"


def _video(item=10, title="3강 정렬") -> VideoTodo:
    return VideoTodo(
        kind="video", course_id=1, course_name="자료구조", module_id=1, module_title="3주차", module_position=3,
        module_item_id=item, item_position=1, item_id=100, title=title, duration=600,
        view_url="https://canvas.ssu.ac.kr/x", due_at=None, deadline=datetime.now(UTC), unlock_at=None,
    )


def test_lecture_media_url_accepts_only_school_mp4():
    assert lecture_media_url(MP4) == MP4
    assert lecture_media_url(MP4 + "?t=1") == MP4 + "?t=1"
    assert lecture_media_url("https://commons.ssu.ac.kr/viewer/uniplayer/preloader.mp4") is None
    assert lecture_media_url("https://evil.example/a.mp4") is None
    assert lecture_media_url("http://ssuin-object.commonscdn.com/a.mp4") is None
    assert lecture_media_url("https://ssuin-object.commonscdn.com/a.m3u8") is None
    assert lecture_media_url("blob:https://commons.ssu.ac.kr/1234") is None
    assert lecture_media_url("") is None


async def test_media_watch_reports_once_with_content_id():
    calls = []

    async def callback(video, content_id, url):
        calls.append((content_id, url))

    watch = MediaWatch(_video(), callback)
    await watch.report(MP4)  # content_id를 아직 모른다
    assert calls == []
    watch.on_request(SimpleNamespace(url="https://commons.ssu.ac.kr/other.php?content_id=zzz"))
    watch.on_request(SimpleNamespace(url=f"{CONTENT_PHP}?content_id=bad%20id"))
    assert watch.content_id is None
    watch.on_request(SimpleNamespace(url=f"{CONTENT_PHP}?content_id=abc_123&_=1"))
    watch.on_request(SimpleNamespace(url=f"{CONTENT_PHP}?content_id=later"))
    assert watch.content_id == "abc_123"

    # 스트리밍(blob:)이면 응답한 mp4 요청을 쓴다. 실패한 응답과 프리로더는 무시한다.
    watch.on_response(SimpleNamespace(url=MP4, status=404))
    watch.on_response(SimpleNamespace(url="https://commons.ssu.ac.kr/uniplayer/preloader.mp4", status=200))
    await watch.report("blob:https://commons.ssu.ac.kr/1")
    assert calls == []
    watch.on_response(SimpleNamespace(url=MP4, status=206))
    await watch.report("blob:https://commons.ssu.ac.kr/1")
    await watch.report(MP4)  # 한 번 시도에 한 번만
    assert calls == [("abc_123", MP4)]


async def test_media_watch_prefers_current_src_and_survives_callback_error():
    async def broken(video, content_id, url):
        raise RuntimeError("db down")

    watch = MediaWatch(_video(), broken)
    watch.on_request(SimpleNamespace(url=f"{CONTENT_PHP}?content_id=c1"))
    watch.on_response(SimpleNamespace(url="https://ssuin-object.commonscdn.com/other.mp4", status=200))
    assert watch.media_url(MP4) == MP4
    await watch.report(MP4)  # 예외를 삼킨다(재생은 계속)
    assert watch.reported


async def _rows(db):
    return (await db.scalars(select(VideoUrl).order_by(VideoUrl.id))).all()


async def test_record_video_url_keeps_one_row_per_video(env, monkeypatch):  # noqa: F811
    await _admin(env, monkeypatch)  # 사용자 1(관리자), 2
    async with db_base.sessionmaker()() as db:
        await record_video_url(db, 1, _video(), "c1", MP4)
        await record_video_url(db, 2, _video(item=99, title="다른 과목 같은 영상"), "c1", MP4 + "?v=2")
        await record_video_url(db, 2, _video(item=11, title="4강"), "c2", MP4)
        rows = await _rows(db)
    assert len(rows) == 2
    first = rows[0]
    assert (first.content_id, first.attempts, first.first_user_id, first.last_user_id) == ("c1", 2, 1, 2)
    assert first.media_url == MP4 + "?v=2"  # 주소는 최신으로
    assert (first.title, first.module_item_id) == ("3강 정렬", 10)  # 처음 기록한 항목은 그대로
    assert first.last_seen_at >= first.first_seen_at
    assert rows[1].attempts == 1


async def test_db_hook_records_url_for_batch_owner(env, monkeypatch):  # noqa: F811
    await _admin(env, monkeypatch)
    async with db_base.sessionmaker()() as db:
        batch = PlaybackBatch(user_id=2, scope="item", status="running")
        db.add(batch)
        await db.commit()
        batch_id = batch.id
    hooks = playback._db_hooks(batch_id)
    await hooks.on_media(_video(), "c9", MP4)
    await hooks.on_media(_video(), "c9", MP4)  # 재시도
    async with db_base.sessionmaker()() as db:
        rows = await _rows(db)
        assert await db.scalar(select(func.count()).select_from(VideoUrl)) == 1
    assert (rows[0].first_user_id, rows[0].attempts) == (2, 2)


async def test_admin_video_urls_list(env, monkeypatch):  # noqa: F811
    api, admin, other = await _admin(env, monkeypatch)
    assert (await api.get("/admin/video-urls")).status_code == 401
    assert (await api.get("/admin/video-urls", headers=other)).status_code == 403
    await _signup(api, "20200001")  # user id 3
    async with db_base.sessionmaker()() as db:
        await record_video_url(db, 2, _video(), "c1", MP4)
        await record_video_url(db, 3, _video(item=11, title="4강 100%_탐색"), "c2", MP4)
        await record_video_url(db, 2, _video(item=11), "c2", MP4)
        await db.delete(await db.get(User, 3))  # 사용자가 지워져도 기록은 남는다
        await db.commit()

    r = await api.get("/admin/video-urls", headers=admin)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["total"] == 2
    latest = body["items"][0]  # 마지막 시도가 최근인 순서
    assert latest["content_id"] == "c2" and latest["attempts"] == 2
    assert latest["media_url"] == MP4
    assert (latest["first_student_id"], latest["last_student_id"]) == (None, "20209999")
    assert latest["course_name"] == "자료구조" and latest["title"] == "4강 100%_탐색"

    found = (await api.get("/admin/video-urls?q=100%_", headers=admin)).json()
    assert [i["content_id"] for i in found["items"]] == ["c2"]
    assert (await api.get("/admin/video-urls?q=c1", headers=admin)).json()["total"] == 1
    paged = (await api.get("/admin/video-urls?limit=1&offset=1", headers=admin)).json()
    assert paged["total"] == 2 and [i["content_id"] for i in paged["items"]] == ["c1"]


async def test_admin_all_saved_videos(env, monkeypatch):  # noqa: F811
    from app.db.models import SavedVideo

    api, admin, other = await _admin(env, monkeypatch)
    assert (await api.get("/admin/saved-videos")).status_code == 401
    assert (await api.get("/admin/saved-videos", headers=other)).status_code == 403

    def row(user_id, item, cid, **kw):
        return SavedVideo(user_id=user_id, lms_course_id=1, course_name="자료구조", module_item_id=item, module_title="3주차",
                          title=kw.pop("title", f"영상{item}"), content_id=cid, file_name="a.mp4", **kw)
    async with db_base.sessionmaker()() as db:
        db.add_all([
            row(1, 1, "c1", media_url=MP4, resolved_at=datetime.now(UTC)),
            row(2, 1, "c1", media_url=MP4, resolved_at=datetime.now(UTC)),  # 같은 영상, 다른 사용자
            row(2, 2, "c2", resolve_error="형식 오류 https://cdn.test/a?token=S", resolve_failed_at=datetime.now(UTC)),
            row(2, 3, "c3", title="100%_특수"),
        ])
        await db.commit()

    body = (await api.get("/admin/saved-videos", headers=admin)).json()
    assert body["total"] == 4
    assert body["counts"] == {"saved": 2, "pending": 1, "failed": 1}
    assert body["summary"] == {"rows": 4, "videos": 3, "users": 2}
    by_cid = {i["content_id"]: i for i in body["items"] if i["student_id"] == "20209999"}
    assert by_cid["c1"]["holders"] == 2 and by_cid["c2"]["holders"] == 1
    assert by_cid["c1"]["media_url"] == MP4
    assert by_cid["c2"]["resolve_error"] == "형식 오류 https://cdn.test"  # 오류 속 주소는 줄인다

    failed = (await api.get("/admin/saved-videos?status=failed", headers=admin)).json()
    assert [i["content_id"] for i in failed["items"]] == ["c2"] and failed["total"] == 1
    assert failed["counts"]["saved"] == 2  # 상태 버튼의 건수는 상태 조건과 무관하다
    mine = (await api.get("/admin/saved-videos?student_id=20201234", headers=admin)).json()
    assert mine["total"] == 1 and mine["summary"] == {"rows": 1, "videos": 1, "users": 1}
    assert mine["items"][0]["holders"] == 2  # 같은 영상을 가진 사람은 전체 기준
    assert (await api.get("/admin/saved-videos?q=100%_", headers=admin)).json()["total"] == 1
    paged = (await api.get("/admin/saved-videos?limit=1&offset=3", headers=admin)).json()
    assert paged["total"] == 4 and len(paged["items"]) == 1
    assert (await api.get("/admin/saved-videos?status=bad", headers=admin)).status_code == 422
