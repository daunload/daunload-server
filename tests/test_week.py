from datetime import UTC, datetime, timedelta

import pytest

from app.db import base as db_base
from app.db.base import utcnow
from app.db.models import AssignmentRow, CourseRow, TodoRow
from app.services.week import week_bounds
from tests.test_api import _signup, env  # noqa: F401  (env는 fixture)


@pytest.mark.parametrize(("now", "start"), [
    (datetime(2026, 10, 7, 3, tzinfo=UTC), datetime(2026, 10, 4, 15, tzinfo=UTC)),       # 수요일 → 10/5(월) 00:00 KST
    (datetime(2026, 10, 11, 14, 59, tzinfo=UTC), datetime(2026, 10, 4, 15, tzinfo=UTC)),  # 일요일 23:59 KST는 같은 주
    (datetime(2026, 10, 11, 15, 0, tzinfo=UTC), datetime(2026, 10, 11, 15, tzinfo=UTC)),  # 월요일 00:00 KST부터 다음 주
    (datetime(2026, 10, 4, 20, tzinfo=UTC), datetime(2026, 10, 4, 15, tzinfo=UTC)),       # UTC로는 일요일이지만 KST 월요일
])
def test_week_bounds_kst(now, start):
    s, e = week_bounds(now, "Asia/Seoul")
    assert s == start and e == start + timedelta(days=7)


def _video(uid, item, deadline, *, course=1, module_pos=1, item_pos=1, unlock=None, active=True):
    return TodoRow(user_id=uid, lms_course_id=course, course_name=f"과목{course}", kind="video", lms_item_id=item,
                   title=f"영상{item}", deadline=deadline, url=f"https://canvas.ssu.ac.kr/v/{item}", module_id=module_pos,
                   module_title=f"{module_pos}주차", module_position=module_pos, item_position=item_pos, item_id=item,
                   duration=600, unlock_at=unlock, active=active)


def _assignment(uid, aid, deadline, state="unsubmitted", unlock=None, course=1):
    return AssignmentRow(user_id=uid, lms_course_id=course, course_name=f"과목{course}", assignment_id=aid, title=f"과제{aid}",
                         kind="assignment", submission_state=state, deadline=deadline, due_at=deadline, unlock_at=unlock,
                         published=True, assignment_state="published", locked_for_user=False)


async def _seed():
    now = utcnow()
    start, end = week_bounds(now)
    async with db_base.sessionmaker()() as db:
        db.add_all([CourseRow(user_id=1, lms_course_id=1, name="과목1"), CourseRow(user_id=1, lms_course_id=2, name="과목2")])
        db.add_all([
            _video(1, 11, end - timedelta(minutes=1), module_pos=2),                 # 이번 주 마지막 순간 마감
            _video(1, 12, now + timedelta(minutes=30), course=2),                    # 곧 마감
            _video(1, 13, end + timedelta(hours=1)),                                  # 다음 주 마감 → 제외
            _video(1, 14, end - timedelta(hours=1), unlock=now + timedelta(hours=1)),  # 아직 안 열림 → 제외
            _video(1, 15, end - timedelta(hours=2), active=False),                   # 이미 완료 → 제외
        ])
        db.add_all([
            _assignment(1, 21, now + timedelta(minutes=10)),                          # 제출 가능
            _assignment(1, 22, end - timedelta(minutes=1), unlock=now + timedelta(hours=1)),  # 이번 주 열림
            _assignment(1, 23, end + timedelta(days=1)),                              # 다음 주 → 제외
            _assignment(1, 24, now + timedelta(hours=1), state="submitted"),          # 제출함 → 제외
            _assignment(1, 25, now - timedelta(minutes=1)),                           # 방금 마감 지남(이번 주에 놓침)
        ])
        await db.commit()
    return now, start, end


async def test_week_endpoint(env):
    api, _ = env
    h = await _signup(api)
    now, start, end = await _seed()
    week = (await api.get("/week", headers=h)).json()
    assert datetime.fromisoformat(week["start"]) == start and datetime.fromisoformat(week["end"]) == end
    assert [v["lms_item_id"] for v in week["videos"]] == [12, 11]  # 마감순
    assert week["video_seconds"] == 1200
    assert [(a["assignment_id"], a["status"]) for a in week["assignments"]] == [(21, "open"), (22, "upcoming")]
    # 월요일 00:00 직후 1분 안에 테스트가 돌면 그 마감은 지난주라 '이번 주에 놓친 과제'가 아니다
    expected_missed = [25] if now - timedelta(minutes=1) >= start else []
    assert [a["assignment_id"] for a in week["missed"]] == expected_missed


async def test_week_batch_plays_only_this_weeks_videos(env):
    api, queue = env
    h = await _signup(api)
    await _seed()
    preview = (await api.get("/playback/batches/preview", headers=h, params={"scope": "week"})).json()
    # 과목은 가장 이른 마감부터(과목2), 과목 안에서는 화면 순서
    assert [i["module_item_id"] for i in preview["items"]] == [12, 11]
    r = await api.post("/playback/batches", headers=h, json={"scope": "week"})
    assert r.status_code == 201 and r.json()["scope"] == "week" and r.json()["total"] == 2
    assert not any(job[0] == "run_batch_job" for job in queue.jobs)  # 재생은 DB 큐


async def test_all_playable_preview_and_queue_include_next_week_but_exclude_unavailable(env):
    api, _ = env
    headers = await _signup(api)
    await _signup(api, "20209999")
    now, _, _ = await _seed()
    async with db_base.sessionmaker()() as db:
        db.add_all([_video(1, 16, now - timedelta(seconds=1)),
                    _video(2, 99, now + timedelta(days=1))])
        await db.commit()
    preview = await api.get("/playback/batches/preview?scope=all", headers=headers)
    assert preview.status_code == 200
    ids = {i["module_item_id"] for i in preview.json()["items"]}
    assert ids == {11, 12, 13}
    r = await api.post("/playback/batches", headers=headers, json={"scope": "all"})
    assert r.status_code == 201 and r.json()["total"] == 3
    detail = (await api.get(f"/playback/batches/{r.json()['id']}", headers=headers)).json()
    assert {j["module_item_id"] for j in detail["jobs"]} == ids
    assert (await api.post("/playback/batches", headers=headers, json={"scope": "all"})).status_code == 409


async def test_confirmed_playback_removes_only_owners_completed_todos(env):
    from app.lms.batch import ItemOutcome, build_plan
    from app.services import playback
    from sqlalchemy import select
    api, _ = env
    headers = await _signup(api)
    await _signup(api, "20209999")
    now, _, _ = await _seed()
    async with db_base.sessionmaker()() as db:
        db.add(_video(2, 11, now + timedelta(days=1)))
        await db.commit()
        videos = await playback.pending_videos_from_db(db, 1, "all")
        batch = await playback.create_batch(db, 1, "all", videos)
    hooks = playback._db_hooks(batch.id)
    for item in build_plan(videos, now).items:
        position, video = item.position, item.video
        status = "done" if video.module_item_id == 11 else "skipped" if video.module_item_id == 13 else "failed"
        await hooks.on_item_done(ItemOutcome(position, video, status))
    preview = (await api.get("/playback/batches/preview?scope=all", headers=headers)).json()
    assert [i["module_item_id"] for i in preview["items"]] == [12]
    async with db_base.sessionmaker()() as db:
        other = await db.scalar(select(TodoRow).where(TodoRow.user_id == 2, TodoRow.lms_item_id == 11))
        assert other.active is True
