"""사용자 한 명의 LMS 데이터를 DB에 동기화한다."""

from __future__ import annotations

import logging
import random
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import utcnow
from app.db.models import AnnouncementRow, AssignmentRow, CourseRow, LmsCredential, TodoRow
from app.lms.errors import LmsError
from app.lms.models import AssignmentGroup, Course, Submission
from app.lms.todo import (
    AssignmentTodo,
    VideoTodo,
    assignment_deadlines,
    collect_todos,
    parse_dt,
    submission_state,
)
from app.services.lms_access import lms_client_for
from app.services.saved_videos import ResolveBudget, save_course_videos

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    first_sync: bool
    course_count: int = 0
    todo_count: int = 0
    new_announcements: list[AnnouncementRow] = field(default_factory=list)
    new_video_course_ids: set[int] = field(default_factory=set)


async def sync_user(db: AsyncSession, user_id: int, now: datetime | None = None) -> SyncResult:
    now = now or utcnow()
    cred = await db.get(LmsCredential, user_id)
    result = SyncResult(first_sync=cred is None or cred.last_synced_at is None)

    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(lms_client_for(db, user_id))
        # 강의 영상 주소 저장용(commons.ssu.ac.kr, 로그인 불필요)
        media_http = await stack.enter_async_context(httpx.AsyncClient(follow_redirects=True, timeout=20.0))
        budget = ResolveBudget()
        term = await client.get_current_term(now)
        courses = await client.get_courses(term.id)
        statuses = {s.course.id: s for s in await client.get_learn_statuses(term.id)}

        course_rows = {r.lms_course_id: r for r in await _rows(db, CourseRow, user_id)}
        todo_rows = {(r.kind, r.lms_item_id): r for r in await _rows(db, TodoRow, user_id)}
        known_announcements = {r.lms_id for r in await _rows(db, AnnouncementRow, user_id)}
        assignment_rows = {r.assignment_id: r for r in await _rows(db, AssignmentRow, user_id)}
        seen_courses: set[int] = set()
        seen_todos: set[tuple[str, int]] = set()

        for course in courses:
            seen_courses.add(course.id)
            row = course_rows.get(course.id)
            if row is None:
                row = CourseRow(user_id=user_id, lms_course_id=course.id, auto_play=False)
                db.add(row)
            row.term_id, row.name, row.professors, row.active = course.term_id, course.name, course.professors, True
            status = statuses.get(course.id)
            if status:
                row.attendance = [
                    {"week": s.position, "statuses": [sub.status for sub in s.subsections]}
                    for s in status.sections
                ]

            modules = await client.get_modules(course.id)
            await save_course_videos(db, media_http, user_id, course, modules, budget)
            groups = await client.get_assignment_groups(course.id)
            try:
                submissions = await client.get_submissions(course.id)
                await _sync_assignments(db, user_id, course, groups, submissions, assignment_rows)
            except LmsError:  # 권한이 없으면 과제 판정을 건너뛴다(레퍼런스의 permissionFailed). 기존 과제 기록은 유지
                submissions = []
            for todo in collect_todos(course, modules, groups, submissions, now):
                key = (todo.kind, _lms_item_id(todo))
                seen_todos.add(key)
                trow = todo_rows.get(key)
                if trow is None:
                    trow = TodoRow(user_id=user_id, kind=todo.kind, lms_item_id=key[1])
                    db.add(trow)
                    todo_rows[key] = trow
                    if isinstance(todo, VideoTodo):
                        result.new_video_course_ids.add(course.id)
                _apply_todo(trow, todo)

            try:
                announcements = await client.get_announcements(course.id)
            except LmsError as e:
                log.warning("공지 조회 실패 %s: %s", course.id, e)
                announcements = []
            for a in announcements:
                if a.id in known_announcements:
                    continue
                known_announcements.add(a.id)
                arow = AnnouncementRow(
                    user_id=user_id, lms_course_id=course.id, course_name=course.name, lms_id=a.id,
                    title=a.title, message=a.message, url=a.html_url or a.url, author=a.user_name,
                    posted_at=_safe_dt(a.posted_at or a.created_at),
                    attachments=[{"name": x.display_name, "url": x.url} for x in a.attachments],
                )
                db.add(arow)
                result.new_announcements.append(arow)

        for lms_course_id, row in course_rows.items():
            if lms_course_id not in seen_courses:
                row.active = False
        for key, trow in todo_rows.items():
            if key not in seen_todos:
                trow.active = False

        result.course_count = len(courses)
        result.todo_count = len(seen_todos)

    cred = await db.get(LmsCredential, user_id)
    s = get_settings()
    cred.last_synced_at = now
    cred.next_sync_at = now + timedelta(
        minutes=s.sync_interval_minutes + random.uniform(-s.sync_jitter_minutes, s.sync_jitter_minutes)
    )
    await db.commit()
    if result.first_sync:  # 첫 동기화 때 기존 공지를 한꺼번에 알리지 않는다
        result.new_announcements = []
        result.new_video_course_ids = set()
    return result


async def _sync_assignments(
    db: AsyncSession,
    user_id: int,
    course: Course,
    groups: list[AssignmentGroup],
    submissions: list[Submission],
    existing: dict[int, AssignmentRow],
) -> None:
    by_assignment = {s.assignment_id: s for s in submissions if s.assignment_id}
    seen: set[int] = set()
    for group in groups:
        for a in group.assignments:
            if a.id in seen:
                continue
            seen.add(a.id)
            sub = by_assignment.get(a.id)
            row = existing.get(a.id)
            if row is None:
                row = AssignmentRow(user_id=user_id, assignment_id=a.id)
                db.add(row)
                existing[a.id] = row
            due, deadline = assignment_deadlines(a, sub)
            row.lms_course_id, row.course_name = course.id, course.name
            row.title, row.group_name = a.name, group.name
            row.kind = "quiz" if a.is_quiz else "assignment"
            row.submission_state = submission_state(sub)
            row.submitted_at = _safe_dt(sub.submitted_at) if sub else None
            row.missing = bool(sub and sub.missing)
            row.score = sub.score if sub else None
            row.points_possible = a.points_possible
            row.due_at, row.deadline = due, deadline
            row.lock_at, row.unlock_at = _safe_dt(a.lock_at), _safe_dt(a.unlock_at)
            row.published, row.assignment_state, row.locked_for_user = a.published, a.workflow_state, a.locked_for_user
            row.url = a.html_url
    for assignment_id, row in list(existing.items()):
        if row.lms_course_id == course.id and assignment_id not in seen:  # LMS에서 사라진 과제
            await db.delete(row)
            del existing[assignment_id]


def _lms_item_id(todo: VideoTodo | AssignmentTodo) -> int:
    return todo.module_item_id if isinstance(todo, VideoTodo) else todo.assignment_id


def _apply_todo(row: TodoRow, todo: VideoTodo | AssignmentTodo) -> None:
    row.lms_course_id = todo.course_id
    row.course_name = todo.course_name
    row.title = todo.title
    row.deadline = todo.deadline
    row.due_at = todo.due_at
    row.unlock_at = todo.unlock_at
    row.active = True
    if isinstance(todo, VideoTodo):
        row.url = todo.view_url
        row.module_id = todo.module_id
        row.module_title = todo.module_title
        row.module_position = todo.module_position
        row.item_position = todo.item_position
        row.item_id = todo.item_id
        row.duration = todo.duration
    else:
        row.url = todo.html_url


def video_from_row(row: TodoRow) -> VideoTodo:
    return VideoTodo(
        kind="video", course_id=row.lms_course_id, course_name=row.course_name,
        module_id=row.module_id or 0, module_title=row.module_title or "", module_position=row.module_position or 0,
        module_item_id=row.lms_item_id, item_position=row.item_position or 0, item_id=row.item_id or 0,
        title=row.title, duration=row.duration or 0, view_url=row.url or "",
        due_at=row.due_at, deadline=row.deadline, unlock_at=row.unlock_at,
    )


async def _rows(db: AsyncSession, model, user_id: int) -> list:
    return list((await db.scalars(select(model).where(model.user_id == user_id))).all())


def _safe_dt(value: str | None) -> datetime | None:
    try:
        return parse_dt(value)
    except ValueError:
        return None
