from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, Db
from app.api.schemas import (
    AnnouncementOut,
    AssignmentOut,
    AutoPlayIn,
    CourseOut,
    TodoOut,
    WeekOut,
)
from app.db.base import utcnow
from app.db.models import AnnouncementRow, AssignmentRow, CourseRow, TodoRow
from app.lms.errors import InvalidCredentials, LmsError
from app.lms.todo import UNSUBMITTED_STATUSES, unsubmitted_status
from app.services.lms_access import NoCredentials, lms_client_for
from app.services.week import week_bounds

router = APIRouter()


@router.get("/courses", response_model=list[CourseOut], tags=["courses"])
async def list_courses(user: CurrentUser, db: Db):
    rows = await db.scalars(
        select(CourseRow).where(CourseRow.user_id == user.id, CourseRow.active).order_by(CourseRow.name)
    )
    return rows.all()


async def _course(db: Db, user_id: int, course_id: int) -> CourseRow:
    row = await db.scalar(select(CourseRow).where(CourseRow.user_id == user_id, CourseRow.lms_course_id == course_id))
    if row is None:
        raise HTTPException(404, "과목을 찾을 수 없습니다. 먼저 동기화하세요.")
    return row


@router.get("/courses/{course_id}/attendance", tags=["courses"])
async def course_attendance(course_id: int, user: CurrentUser, db: Db):
    row = await _course(db, user.id, course_id)
    return {"lms_course_id": course_id, "name": row.name, "weeks": row.attendance or []}


@router.get("/courses/{course_id}/modules", tags=["courses"])
async def course_modules(course_id: int, user: CurrentUser, db: Db):
    """LMS에서 실시간으로 조회한 모듈(주차)과 항목, 완료 여부."""
    await _course(db, user.id, course_id)
    try:
        async with lms_client_for(db, user.id) as client:
            modules = await client.get_modules(course_id)
    except (NoCredentials, InvalidCredentials) as e:
        raise HTTPException(400, str(e)) from e
    except LmsError as e:
        raise HTTPException(502, f"LMS 조회 실패: {e}") from e
    return [m.model_dump() for m in modules]


@router.put("/courses/{course_id}/auto-play", response_model=CourseOut, tags=["courses"])
async def set_auto_play(course_id: int, body: AutoPlayIn, user: CurrentUser, db: Db):
    row = await _course(db, user.id, course_id)
    row.auto_play = body.enabled
    await db.commit()
    return row


@router.get("/todos", response_model=list[TodoOut], tags=["todos"])
async def list_todos(
    user: CurrentUser, db: Db, kind: Literal["video", "assignment", "quiz"] | None = None, course_id: int | None = None
):
    stmt = select(TodoRow).where(TodoRow.user_id == user.id, TodoRow.active, TodoRow.deadline > utcnow())
    if kind:
        stmt = stmt.where(TodoRow.kind == kind)
    if course_id:
        stmt = stmt.where(TodoRow.lms_course_id == course_id)
    return (await db.scalars(stmt.order_by(TodoRow.deadline))).all()


AssignmentFilter = Literal["unsubmitted", "open", "overdue", "upcoming", "locked", "no_due", "submitted", "all"]
_STATUS_ORDER = {"open": 0, "locked": 1, "overdue": 2, "upcoming": 3, "no_due": 4, "submitted": 5, "excused": 6, "unknown": 7}


async def assignment_outs(db, user_id: int, course_id: int | None = None) -> list[AssignmentOut]:
    """현재 학기 과목의 공개된 과제를 지금 시각 기준 상태와 함께 반환한다(정렬 포함)."""
    stmt = select(AssignmentRow).where(AssignmentRow.user_id == user_id)
    if course_id:
        stmt = stmt.where(AssignmentRow.lms_course_id == course_id)
    active_courses = set((await db.scalars(
        select(CourseRow.lms_course_id).where(CourseRow.user_id == user_id, CourseRow.active)
    )).all())
    now = utcnow()
    out: list[AssignmentOut] = []
    for r in (await db.scalars(stmt)).all():
        if r.lms_course_id not in active_courses:
            continue
        if r.published is False or r.assignment_state in {"unpublished", "deleted"}:
            continue
        st = unsubmitted_status(
            state=r.submission_state, published=r.published, assignment_state=r.assignment_state,
            locked_for_user=r.locked_for_user, unlock_at=r.unlock_at, deadline=r.deadline, now=now,
        ) or r.submission_state
        out.append(AssignmentOut(
            status=st, kind=r.kind, lms_course_id=r.lms_course_id, course_name=r.course_name,
            assignment_id=r.assignment_id, title=r.title, group_name=r.group_name, due_at=r.due_at,
            deadline=r.deadline, lock_at=r.lock_at, unlock_at=r.unlock_at, submitted_at=r.submitted_at,
            missing=r.missing, score=r.score, points_possible=r.points_possible, url=r.url,
        ))
    out.sort(key=_assignment_sort_key)
    return out


@router.get("/assignments", response_model=list[AssignmentOut], tags=["todos"])
async def list_assignments(
    user: CurrentUser, db: Db, status: AssignmentFilter = "unsubmitted", course_id: int | None = None
):
    """과제·퀴즈 목록. 기본은 미제출 전체(마감 지난 것 포함). 상태는 지금 시각 기준으로 계산한다.

    - open 제출 가능, locked 잠김(기간 안이지만 열리지 않음), overdue 마감 지남,
      upcoming 아직 열리지 않음, no_due 마감 없음
    - 제출 여부를 확인할 수 없는 과제(unknown)는 미제출에 넣지 않는다
    """
    items = await assignment_outs(db, user.id, course_id)
    if status == "unsubmitted":
        return [a for a in items if a.status in UNSUBMITTED_STATUSES]
    if status == "all":
        return items
    return [a for a in items if a.status == status]


@router.get("/week", response_model=WeekOut, tags=["todos"])
async def this_week(user: CurrentUser, db: Db):
    """이번 주(월~일) 안에 들어야 할 강의와 내야 할 과제·퀴즈.

    - videos: 지금 볼 수 있고 이번 주 안에 마감되는 미수강 영상(마감순). '이번 주 강의 전체 재생'의 대상과 같다.
    - assignments: 이번 주 안에 마감되는 미제출 과제(제출 가능·잠김·열리기 전)
    - missed: 이번 주에 이미 마감이 지난 미제출 과제
    """
    now = utcnow()
    start, end = week_bounds(now)
    videos = (await db.scalars(
        select(TodoRow).where(
            TodoRow.user_id == user.id, TodoRow.kind == "video", TodoRow.active,
            TodoRow.deadline > now, TodoRow.deadline < end,
        ).order_by(TodoRow.deadline, TodoRow.module_position, TodoRow.item_position)
    )).all()
    videos = [v for v in videos if v.unlock_at is None or v.unlock_at <= now]
    items = await assignment_outs(db, user.id)
    in_week = [a for a in items if a.deadline and start <= a.deadline < end]
    return WeekOut(
        start=start, end=end,
        videos=[TodoOut.model_validate(v) for v in videos],
        video_seconds=sum(v.duration or 0 for v in videos),
        assignments=[a for a in in_week if a.status in ("open", "locked", "upcoming")],
        missed=[a for a in in_week if a.status == "overdue"],
    )


def _assignment_sort_key(a: AssignmentOut) -> tuple:
    """상태 순서 → 시각 → 제목.
    시각은 열리기 전이면 열리는 시각, 마감 지남이면 최근 마감부터, 나머지는 가까운 마감부터(없으면 뒤로)."""
    when = a.unlock_at if a.status == "upcoming" else a.deadline
    ts = when.timestamp() if when else float("inf")
    if a.status == "overdue":
        ts = -ts
    return (_STATUS_ORDER.get(a.status, 9), ts, a.title)


@router.get("/announcements", response_model=list[AnnouncementOut], tags=["announcements"])
async def list_announcements(
    user: CurrentUser, db: Db, since: datetime | None = None, course_id: int | None = None, limit: int = 50
):
    stmt = select(AnnouncementRow).where(AnnouncementRow.user_id == user.id)
    if since:
        stmt = stmt.where(AnnouncementRow.posted_at >= since)
    if course_id:
        stmt = stmt.where(AnnouncementRow.lms_course_id == course_id)
    stmt = stmt.order_by(AnnouncementRow.posted_at.desc()).limit(min(limit, 200))
    return (await db.scalars(stmt)).all()
