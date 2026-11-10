"""마감(due_at, late_at)이 없는 공개 영상도 미수강 할 일이고 재생할 수 있다."""
from datetime import UTC, datetime, timedelta

import pytest
from playwright.async_api import expect
from sqlalchemy import select

from app.db import base as db_base
from app.db.base import utcnow
from app.db.models import PlaybackJob, TodoRow
from app.lms.batch import PlannedItem, build_plan, order_videos
from app.lms.models import Course, Module
from app.lms.todo import VideoTodo, format_todo, pending_videos
from app.services import playback
from app.services.notify import notify_deadlines
from tests.test_api import _signup, env  # noqa: F401  (fixture)
from tests.test_web import dashboard  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
COURSE = Course(id=1, name="과목")
FUTURE = "2026-10-20T00:00:00Z"
PAST = "2026-10-01T00:00:00Z"


def _modules(**content):
    data = {"item_id": 7, "title": "영상", "item_content_type": "commons", "use_attendance": True,
            "item_content_data": {"duration": 60, "view_url": "https://lms.test/v"}, **content}
    return [Module.model_validate({"module_id": 1, "title": "1주차", "position": 1, "module_items": [
        {"module_item_id": 11, "position": 1, "title": "영상", "completed": False, "content_data": data}]})]


@pytest.mark.parametrize("due, late, included, deadline", [
    (None, None, True, None),
    ("", "", True, None),
    ("  ", None, True, None),
    (FUTURE, None, True, FUTURE),
    (None, FUTURE, True, FUTURE),
    (PAST, None, False, None),
    (None, PAST, False, None),
    (PAST, FUTURE, True, FUTURE),
    ("not-a-date", None, False, None),
    (None, "not-a-date", False, None),
])
def test_pending_videos_deadline_cases(due, late, included, deadline):
    videos = pending_videos(COURSE, _modules(due_at=due, late_at=late), NOW)
    assert bool(videos) is included
    if included:
        expected = datetime.fromisoformat(deadline.replace("Z", "+00:00")) if deadline else None
        assert videos[0].deadline == expected


@pytest.mark.parametrize("due, late, included", [
    (FUTURE, None, True),   # 출석 인정 기간 없이 마감만 있는 영상
    (None, FUTURE, True),
    (PAST, None, False),
    (None, None, False),    # 출석도 마감도 없는 참고 영상
])
def test_deadline_only_video_without_attendance(due, late, included):
    videos = pending_videos(COURSE, _modules(use_attendance=False, due_at=due, late_at=late), NOW)
    assert bool(videos) is included


def test_no_deadline_video_still_waits_for_unlock():
    assert pending_videos(COURSE, _modules(unlock_at="2026-10-08T00:00:00Z"), NOW) == []
    assert len(pending_videos(COURSE, _modules(unlock_at="2026-10-01T00:00:00Z"), NOW)) == 1


def _video(item, deadline, course=1):
    return VideoTodo("video", course, "과목", 1, "1주차", 1, item, item, item, f"영상 {item}", 60,
                     "https://lms.test/v", None, deadline, None)


def test_courses_with_deadlines_play_first_and_no_deadline_never_at_risk():
    soon = NOW + timedelta(days=1)
    ordered = order_videos([_video(1, None, course=1), _video(2, soon, course=2)])
    assert [v.course_id for v in ordered] == [2, 1]
    item = PlannedItem(0, _video(1, None), NOW, NOW + timedelta(days=99))
    assert item.at_risk is False
    plan = build_plan([_video(1, None)], NOW)
    assert plan.warnings == [] and "마감 없음" in format_todo(_video(1, None))


def test_video_json_round_trip_keeps_missing_deadline():
    video = _video(1, None)
    assert playback._video_from_json(playback._video_to_json(video)) == video


async def _seed(user_id):
    now = utcnow()
    def row(item, deadline):
        return TodoRow(user_id=user_id, kind="video", lms_item_id=item, lms_course_id=1, course_name="과목",
                       title=f"영상 {item}", deadline=deadline, due_at=None, unlock_at=None, active=True,
                       url="https://lms.test/v", module_id=1, module_title="1주차", module_position=1,
                       item_position=item, item_id=item, duration=60)
    async with db_base.sessionmaker()() as db:
        db.add_all([row(1, None), row(2, now + timedelta(days=30)), row(3, now - timedelta(days=1))])
        await db.commit()


async def test_api_lists_and_plays_no_deadline_video(env):  # noqa: F811
    api, _ = env
    headers = await _signup(api)
    await _seed(1)
    todos = (await api.get("/todos", headers=headers)).json()
    assert [(t["lms_item_id"], t["deadline"] is None) for t in todos] == [(2, False), (1, True)]  # 지난 3번 제외, 마감 없음은 뒤로

    plan = (await api.get("/playback/batches/preview?scope=all", headers=headers)).json()
    assert sorted(i["module_item_id"] for i in plan["items"]) == [1, 2]
    no_deadline = next(i for i in plan["items"] if i["module_item_id"] == 1)
    assert no_deadline["deadline"] is None and no_deadline["at_risk"] is False

    week = (await api.get("/playback/batches/preview?scope=week", headers=headers)).json()
    assert 1 not in [i["module_item_id"] for i in week["items"]]  # 이번 주로 묶을 날짜가 없다
    assert 1 not in [v["lms_item_id"] for v in (await api.get("/week", headers=headers)).json()["videos"]]

    created = await api.post("/playback/items/1", headers=headers)
    assert created.status_code == 201, created.text
    batch = (await api.get(f"/playback/batches/{created.json()['id']}", headers=headers))
    assert batch.status_code == 200, batch.text
    assert batch.json()["jobs"][0]["deadline"] is None
    assert batch.json()["deadline_at_risk"] is False
    async with db_base.sessionmaker()() as db:
        job = await db.scalar(select(PlaybackJob))
        assert job.deadline is None
        assert await notify_deadlines(db, 1) == 0  # 마감 알림 대상이 아니다


NO_DEADLINE = {"kind": "video", "lms_course_id": 1, "lms_item_id": 90, "course_name": "컴퓨터프로그래밍",
               "title": "마감 없는 강의", "deadline": None, "due_at": None, "duration": 600,
               "module_title": "9주차", "url": "https://lms.ssu.ac.kr/courses/1"}


@pytest.mark.browser
async def test_home_shows_no_deadline_last(dashboard):  # noqa: F811
    page, data = dashboard
    item = {**data["/playback/batches/preview"]["items"][0], "lms_course_id": 2, "course_name": "다른 과목",
            "module_item_id": 90, "title": "마감 없는 강의", "deadline": None}
    data["/playback/batches/preview"]["items"].append(item)
    await page.goto("http://dashboard.test/")
    row = page.locator(".task").last
    await expect(row).to_contain_text("마감 없는 강의")
    await expect(row).to_contain_text("마감 없음")
    await expect(page.locator("#view")).not_to_contain_text("1970")


@pytest.mark.browser
async def test_playback_plan_says_no_deadline(dashboard):  # noqa: F811
    page, _ = dashboard
    await page.goto("http://dashboard.test/#playback")
    html = await page.evaluate("""planHtml({ items: [{ position: 0, title: 'a', course_name: 'c', module_title: 'm', duration: 60,
      expected_end: new Date().toISOString(), deadline: null, at_risk: false }], warnings: [], total_seconds: 60,
      expected_end: new Date().toISOString() })""")
    assert "마감 없음" in html and "1970" not in html
