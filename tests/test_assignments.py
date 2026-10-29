from datetime import UTC, datetime, timedelta

import pytest
import respx

from app.db import base as db_base
from app.lms.models import Submission
from app.lms.todo import submission_state, unsubmitted_status
from app.services.sync import sync_user
from tests.test_api import LmsMock, _signup, env, iso  # noqa: F401  (env는 fixture)

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def _status(**kw):
    base = dict(state="unsubmitted", published=True, assignment_state="published", locked_for_user=False,
                unlock_at=None, deadline=NOW + timedelta(days=1), now=NOW)
    return unsubmitted_status(**{**base, **kw})


@pytest.mark.parametrize(("kw", "expected"), [
    ({}, "open"),
    ({"deadline": NOW - timedelta(minutes=1)}, "overdue"),
    ({"deadline": NOW}, "overdue"),  # 종료 시각과 같으면 지난 것
    ({"unlock_at": NOW + timedelta(hours=1)}, "upcoming"),
    ({"deadline": None}, "no_due"),
    ({"locked_for_user": True}, "locked"),
    ({"locked_for_user": True, "deadline": NOW - timedelta(days=1)}, "overdue"),
    ({"state": "submitted"}, None),
    ({"state": "excused"}, None),
    ({"state": "unknown"}, None),
    ({"published": False}, None),
    ({"assignment_state": "deleted"}, None),
])
def test_unsubmitted_status(kw, expected):
    assert _status(**kw) == expected


def test_submission_state():
    assert submission_state(None) == "unknown"
    assert submission_state(Submission(workflow_state="unsubmitted")) == "unsubmitted"
    assert submission_state(Submission(workflow_state="graded")) == "submitted"
    assert submission_state(Submission(workflow_state="unsubmitted", submitted_at="2026-10-01T00:00:00Z")) == "submitted"
    assert submission_state(Submission(workflow_state="unsubmitted", excused=True)) == "excused"
    assert submission_state(Submission(workflow_state="weird")) == "unknown"


@respx.mock
async def test_unsubmitted_list_includes_overdue_and_updates_on_sync(env):
    api, _ = env
    h = await _signup(api)
    now = datetime.now(UTC)
    lms = LmsMock()
    lms.assignments = [
        {"id": "1", "name": "열린 과제", "due_at": iso(now + timedelta(days=2)), "published": True},
        {"id": "2", "name": "놓친 과제", "due_at": iso(now - timedelta(days=3)), "lock_at": iso(now + timedelta(days=5)), "published": True},
        {"id": "3", "name": "지각 가능", "due_at": iso(now - timedelta(days=1)), "late_at": iso(now + timedelta(days=1)), "published": True},
        {"id": "4", "name": "다음 주 퀴즈", "unlock_at": iso(now + timedelta(days=4)), "due_at": iso(now + timedelta(days=6)),
         "submission_types": ["online_quiz"], "published": True},
        {"id": "5", "name": "마감 없는 과제", "published": True},
        {"id": "6", "name": "제출한 과제", "due_at": iso(now - timedelta(days=1)), "published": True},
        {"id": "7", "name": "제출 기록 없음", "due_at": iso(now - timedelta(days=1)), "published": True},
        {"id": "8", "name": "비공개", "due_at": iso(now + timedelta(days=1)), "published": False},
        {"id": "9", "name": "가장 최근에 놓친 과제", "due_at": iso(now - timedelta(hours=2)), "published": True},
    ]
    lms.submissions = [{"assignment_id": str(i), "workflow_state": "unsubmitted"} for i in (1, 2, 3, 4, 5, 8, 9)]
    lms.submissions.append({"assignment_id": "6", "workflow_state": "graded", "submitted_at": iso(now - timedelta(days=2)), "score": 9})
    lms.submissions[1]["missing"] = True
    lms.install()
    async with db_base.sessionmaker()() as db:
        await sync_user(db, 1)

    items = (await api.get("/assignments", headers=h)).json()
    assert [(a["title"], a["status"]) for a in items] == [
        ("지각 가능", "open"),           # late_at까지 제출 가능
        ("열린 과제", "open"),
        ("가장 최근에 놓친 과제", "overdue"),  # 마감 지남은 최근 것부터
        ("놓친 과제", "overdue"),          # lock_at은 제출 종료로 보지 않음
        ("다음 주 퀴즈", "upcoming"),
        ("마감 없는 과제", "no_due"),
    ]
    # 열리기 전은 열리는 순서대로(마감이 없어도)
    lms.assignments.append({"id": "10", "name": "가-먼저 열리는 실습", "unlock_at": iso(now + timedelta(days=1)), "published": True})
    lms.submissions.append({"assignment_id": "10", "workflow_state": "unsubmitted"})
    async with db_base.sessionmaker()() as db:
        await sync_user(db, 1)
    upcoming = [a["title"] for a in (await api.get("/assignments", headers=h, params={"status": "upcoming"})).json()]
    assert upcoming == ["가-먼저 열리는 실습", "다음 주 퀴즈"]
    by_title = {a["title"]: a for a in items}
    assert by_title["놓친 과제"]["missing"] is True
    assert by_title["다음 주 퀴즈"]["kind"] == "quiz"

    overdue = (await api.get("/assignments", headers=h, params={"status": "overdue"})).json()
    assert {a["title"] for a in overdue} == {"놓친 과제", "가장 최근에 놓친 과제"}
    submitted = (await api.get("/assignments", headers=h, params={"status": "submitted"})).json()
    assert [(a["title"], a["score"]) for a in submitted] == [("제출한 과제", 9)]
    everything = {a["title"]: a["status"] for a in (await api.get("/assignments", headers=h, params={"status": "all"})).json()}
    assert everything["제출 기록 없음"] == "unknown" and "비공개" not in everything

    # 제출하면 다음 동기화에서 목록에서 빠지고, LMS에서 사라진 과제는 지워진다
    lms.submissions[0] = {"assignment_id": "1", "workflow_state": "submitted", "submitted_at": iso(now)}
    lms.assignments = [a for a in lms.assignments if a["id"] != "5"]
    async with db_base.sessionmaker()() as db:
        await sync_user(db, 1)
    titles = [a["title"] for a in (await api.get("/assignments", headers=h)).json()]
    assert "열린 과제" not in titles and "마감 없는 과제" not in titles


@respx.mock
async def test_submissions_failure_keeps_previous_assignments(env):
    api, _ = env
    h = await _signup(api)
    lms = LmsMock()
    lms.install()
    async with db_base.sessionmaker()() as db:
        await sync_user(db, 1)
    assert len((await api.get("/assignments", headers=h)).json()) == 1
    lms.submissions_status = 403  # 제출 정보 조회 권한 오류
    async with db_base.sessionmaker()() as db:
        await sync_user(db, 1)
    assert len((await api.get("/assignments", headers=h)).json()) == 1  # 실패해도 기존 기록 유지
