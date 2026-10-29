from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter

from app.lms.models import Assignment, AssignmentGroup, Course, Module, Submission
from app.lms.todo import (
    AssignmentTodo,
    VideoTodo,
    assignment_todo,
    collect_todos,
    parse_dt,
    pending_assignments,
    pending_videos,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
COURSE = Course(id=45446, name="리눅스시스템프로그래밍")


@pytest.fixture
def modules(fixture):
    return TypeAdapter(list[Module]).validate_python(fixture("modules.json"))


@pytest.fixture
def groups(fixture):
    return TypeAdapter(list[AssignmentGroup]).validate_python(fixture("assignment_groups.json"))


@pytest.fixture
def submissions(fixture):
    return TypeAdapter(list[Submission]).validate_python(fixture("submissions.json"))


def test_parse_dt():
    assert parse_dt(None) is None
    assert parse_dt("  ") is None
    assert parse_dt("2026-10-07T12:00:00Z") == NOW
    assert parse_dt("2026-10-07T21:00:00+09:00") == NOW
    with pytest.raises(ValueError):
        parse_dt("not-a-date")


def test_pending_videos_filters_and_orders(modules):
    videos = pending_videos(COURSE, modules, NOW)
    assert [v.title for v in videos] == ["1주차 영상", "2주차 영상 1", "2주차 영상 2"]
    assert all(isinstance(v, VideoTodo) for v in videos)


def test_pending_videos_deadline_prefers_late_at(modules):
    by_title = {v.title: v for v in pending_videos(COURSE, modules, NOW)}
    assert by_title["2주차 영상 2"].deadline == parse_dt("2026-10-27T14:59:59Z")
    # late_at이 빈 문자열이면 due_at
    assert by_title["2주차 영상 1"].deadline == parse_dt("2026-10-20T14:59:59Z")


def test_pending_videos_includes_after_due_until_late_at(modules):
    after_due = datetime(2026, 10, 22, tzinfo=UTC)
    titles = [v.title for v in pending_videos(COURSE, modules, after_due)]
    assert "2주차 영상 2" in titles  # late_at 전
    assert "2주차 영상 1" not in titles  # due_at 지남, late_at 없음


def test_pending_videos_module_unlock(modules):
    later = datetime(2026, 10, 15, tzinfo=UTC)
    assert "3주차 영상 (미공개)" in [v.title for v in pending_videos(COURSE, modules, later)]


def test_pending_videos_exact_deadline_excluded(modules):
    at_deadline = parse_dt("2026-10-10T14:59:59Z")
    assert "1주차 영상" not in [v.title for v in pending_videos(COURSE, modules, at_deadline)]


def test_pending_assignments(groups, submissions):
    todos = pending_assignments(COURSE, groups, submissions, NOW)
    assert [(t.title, t.kind) for t in todos] == [("과제1 (미제출)", "assignment"), ("퀴즈1", "quiz")]


def _sub(**kw):
    return Submission(assignment_id=1, workflow_state="unsubmitted", **kw)


def test_assignment_unknown_submission_excluded():
    a = Assignment(id=1, name="a", due_at="2026-10-20T00:00:00Z")
    assert assignment_todo(COURSE, "g", a, None, NOW) is None


def test_assignment_excused_excluded():
    a = Assignment(id=1, name="a", due_at="2026-10-20T00:00:00Z")
    assert assignment_todo(COURSE, "g", a, _sub(excused=True), NOW) is None


def test_assignment_late_at_extends_deadline():
    a = Assignment(id=1, name="a", due_at="2026-10-01T00:00:00Z", late_at="2026-10-20T00:00:00Z")
    todo = assignment_todo(COURSE, "g", a, _sub(), NOW)
    assert isinstance(todo, AssignmentTodo)
    assert todo.due_at == parse_dt("2026-10-01T00:00:00Z")
    assert todo.deadline == parse_dt("2026-10-20T00:00:00Z")


def test_assignment_lock_at_does_not_extend_deadline():
    a = Assignment(id=1, name="a", due_at="2026-10-01T00:00:00Z", lock_at="2026-10-30T00:00:00Z")
    assert assignment_todo(COURSE, "g", a, _sub(), NOW) is None


def test_assignment_invalid_late_at_excluded():
    a = Assignment(id=1, name="a", due_at="2026-10-20T00:00:00Z", late_at="garbage")
    assert assignment_todo(COURSE, "g", a, _sub(), NOW) is None


def test_assignment_cached_due_date_fallback():
    a = Assignment(id=1, name="a")
    todo = assignment_todo(COURSE, "g", a, _sub(cached_due_date="2026-10-20T00:00:00Z"), NOW)
    assert todo is not None and todo.deadline == parse_dt("2026-10-20T00:00:00Z")


def test_assignment_future_unlock_excluded():
    a = Assignment(id=1, name="a", unlock_at="2026-10-08T00:00:00Z", due_at="2026-10-20T00:00:00Z")
    assert assignment_todo(COURSE, "g", a, _sub(), NOW) is None


def test_assignment_no_dates_excluded():
    assert assignment_todo(COURSE, "g", Assignment(id=1, name="a"), _sub(), NOW) is None


def test_collect_todos_sorted_by_deadline(modules, groups, submissions):
    todos = collect_todos(COURSE, modules, groups, submissions, NOW)
    deadlines = [t.deadline for t in todos]
    assert deadlines == sorted(deadlines)
    assert todos[0].title == "과제1 (미제출)"
