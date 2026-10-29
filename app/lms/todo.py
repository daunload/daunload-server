"""미수강 영상과 미제출 과제·퀴즈 판정.

영상 규칙은 chlwhdtn03/LMS-API의 TodoService.toCommonsTodoList,
과제 규칙은 같은 레포의 assignment-todo-policy.md를 따른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.lms.models import Assignment, AssignmentGroup, Course, Module, Submission


def parse_dt(value: str | None) -> datetime | None:
    """None/빈 문자열은 None, 형식이 틀리면 ValueError."""
    if value is None or not value.strip():
        return None
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_or_none(value: str | None) -> datetime | None:
    try:
        return parse_dt(value)
    except ValueError:
        return None


def _is_unlocked(value: str | None, now: datetime) -> bool:
    # 레퍼런스와 동일: 누락이나 형식 오류면 열린 것으로 본다.
    unlock = _parse_or_none(value)
    return unlock is None or now >= unlock


@dataclass(frozen=True)
class VideoTodo:
    kind: Literal["video"]
    course_id: int
    course_name: str
    module_id: int
    module_title: str
    module_position: int
    module_item_id: int
    item_position: int
    item_id: int
    title: str
    duration: float
    view_url: str
    due_at: datetime | None
    deadline: datetime
    unlock_at: datetime | None

    @property
    def order_key(self) -> tuple[int, int]:
        """LMS 화면 순서: 주차(모듈) 위치 → 모듈 안 항목 위치."""
        return (self.module_position, self.item_position)


@dataclass(frozen=True)
class AssignmentTodo:
    kind: Literal["assignment", "quiz"]
    course_id: int
    course_name: str
    assignment_id: int
    title: str
    group_name: str
    due_at: datetime | None
    deadline: datetime
    unlock_at: datetime | None
    html_url: str | None


Todo = VideoTodo | AssignmentTodo


def pending_videos(course: Course, modules: list[Module], now: datetime) -> list[VideoTodo]:
    """기간 안에 있고 아직 완료하지 않은 commons 영상을 LMS 화면 순서대로 반환한다."""
    result: list[VideoTodo] = []
    for module in modules:
        if not _is_unlocked(module.unlock_at, now):
            continue
        for item in module.module_items:
            data = item.content_data
            if data is None or data.item_content_type != "commons":
                continue
            if data.item_content_data is None or data.item_content_data.duration is None:
                continue
            if data.use_attendance is False or item.completed is True:
                continue
            deadline_raw = data.late_at if data.late_at and data.late_at.strip() else data.due_at
            deadline = _parse_or_none(deadline_raw)
            if deadline is None or deadline <= now:
                continue
            if not _is_unlocked(data.unlock_at, now):
                continue
            unlock_raw = data.unlock_at if data.unlock_at and data.unlock_at.strip() else module.unlock_at
            result.append(
                VideoTodo(
                    kind="video",
                    course_id=course.id,
                    course_name=course.name,
                    module_id=module.module_id or 0,
                    module_title=module.title or "",
                    module_position=module.position or 0,
                    module_item_id=item.module_item_id or 0,
                    item_position=item.position or 0,
                    item_id=data.item_id or item.content_id or 0,
                    title=data.title or item.title or "",
                    duration=data.item_content_data.duration,
                    view_url=data.item_content_data.view_url or "",
                    due_at=_parse_or_none(data.due_at),
                    deadline=deadline,
                    unlock_at=_parse_or_none(unlock_raw),
                )
            )
    result.sort(key=lambda v: v.order_key)
    return result


def assignment_todo(
    course: Course,
    group_name: str,
    assignment: Assignment,
    submission: Submission | None,
    now: datetime,
) -> AssignmentTodo | None:
    # 1. 제출 상태: 확실히 미제출인 것만
    if submission is None or submission.workflow_state != "unsubmitted":
        return None
    if submission.excused or (submission.submitted_at or "").strip():
        return None
    # 2. 공개·잠금
    if assignment.published is False or assignment.workflow_state in {"unpublished", "deleted"}:
        return None
    if assignment.locked_for_user is True:
        return None
    # 3. 공개 시작
    try:
        unlock = parse_dt(assignment.unlock_at)
    except ValueError:
        return None
    if unlock is not None and unlock > now:
        return None
    # 4. 일반 마감: 상세 due_at → cached_due_date
    due = _parse_or_none(assignment.due_at) or _parse_or_none(submission.cached_due_date)
    # 5. 종료 기준: late_at → 일반 마감. late_at 값이 있는데 형식이 틀리면 제외.
    if assignment.late_at is not None and assignment.late_at.strip():
        try:
            end = parse_dt(assignment.late_at)
        except ValueError:
            return None
    else:
        end = due
    if end is None or not now < end:
        return None
    if unlock is not None and unlock >= end:
        return None
    return AssignmentTodo(
        kind="quiz" if assignment.is_quiz else "assignment",
        course_id=course.id,
        course_name=course.name,
        assignment_id=assignment.id,
        title=assignment.name,
        group_name=group_name,
        due_at=due,
        deadline=end,
        unlock_at=unlock,
        html_url=assignment.html_url,
    )


# ----------------------------------------------------------------------
# 미제출 과제 목록 (마감이 지난 것 포함)
# ----------------------------------------------------------------------

SubmissionState = Literal["unsubmitted", "submitted", "excused", "unknown"]
# open: 제출 가능 / overdue: 마감 지남 / upcoming: 아직 열리지 않음 / locked: 잠김 / no_due: 마감 없음
UnsubmittedStatus = Literal["open", "overdue", "upcoming", "locked", "no_due"]
UNSUBMITTED_STATUSES: tuple[str, ...] = ("open", "overdue", "upcoming", "locked", "no_due")


def submission_state(submission: Submission | None) -> SubmissionState:
    if submission is None:
        return "unknown"
    if submission.excused:
        return "excused"
    if (submission.submitted_at or "").strip() or submission.workflow_state in {"submitted", "pending_review", "graded"}:
        return "submitted"
    if submission.workflow_state == "unsubmitted":
        return "unsubmitted"
    return "unknown"


def assignment_deadlines(assignment: Assignment, submission: Submission | None) -> tuple[datetime | None, datetime | None]:
    """(일반 마감, 제출 종료). 종료는 late_at → 일반 마감 순서이고 lock_at은 쓰지 않는다(참고 레포 정책)."""
    due = _parse_or_none(assignment.due_at) or _parse_or_none(submission.cached_due_date if submission else None)
    return due, _parse_or_none(assignment.late_at) or due


def unsubmitted_status(
    *,
    state: str,
    published: bool | None,
    assignment_state: str | None,
    locked_for_user: bool | None,
    unlock_at: datetime | None,
    deadline: datetime | None,
    now: datetime,
) -> UnsubmittedStatus | None:
    """미제출 과제의 상태. 미제출이 아니거나(제출·면제·상태 불명) 비공개 과제면 None."""
    if state != "unsubmitted":
        return None
    if published is False or assignment_state in {"unpublished", "deleted"}:
        return None
    if unlock_at is not None and unlock_at > now:
        return "upcoming"
    if deadline is None:
        return "no_due"
    if now >= deadline:
        return "overdue"
    return "locked" if locked_for_user else "open"


def pending_assignments(
    course: Course, groups: list[AssignmentGroup], submissions: list[Submission], now: datetime
) -> list[AssignmentTodo]:
    by_assignment = {s.assignment_id: s for s in submissions if s.assignment_id}
    seen: set[int] = set()
    result: list[AssignmentTodo] = []
    for group in groups:
        for a in group.assignments:
            if a.id in seen:
                continue
            seen.add(a.id)
            todo = assignment_todo(course, group.name, a, by_assignment.get(a.id), now)
            if todo:
                result.append(todo)
    return result


def collect_todos(
    course: Course,
    modules: list[Module],
    groups: list[AssignmentGroup],
    submissions: list[Submission],
    now: datetime,
) -> list[Todo]:
    todos: list[Todo] = [*pending_videos(course, modules, now), *pending_assignments(course, groups, submissions, now)]
    todos.sort(key=lambda t: t.deadline)
    return todos


def format_todo(todo: Todo) -> str:
    deadline = todo.deadline.astimezone().strftime("%m/%d %H:%M")
    if isinstance(todo, VideoTodo):
        minutes = int(todo.duration // 60)
        return f"[영상] {todo.module_title} / {todo.title} ({minutes}분) ~{deadline}"
    label = "퀴즈" if todo.kind == "quiz" else "과제"
    return f"[{label}] {todo.title} ~{deadline}"
