from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select, union

from app.api.deps import AdminUser, Db, is_admin
from app.config import get_settings
from app.db.models import (CourseRow, LmsCredential, NotificationChannel, PlaybackBatch, PlaybackJob,
                           Transcript, User, UserDailyActivity)
from app.services.activity import local_day
from app.services.admin_data import (activity_union, as_utc, clean_error, failed_at, failed_job_filter,
                                     period_start, problem_union)

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserOut(BaseModel):
    student_id: str
    created_at: datetime
    last_login_at: datetime | None
    login_count: int
    lms_status: str | None


class AdminUsersOut(BaseModel):
    total: int
    users: list[AdminUserOut]


class AdminCourseSettingsOut(BaseModel):
    name: str
    active: bool
    auto_play: bool


class AdminChannelSettingsOut(BaseModel):
    kind: str
    enabled: bool
    created_at: datetime


class AdminLmsSettingsOut(BaseModel):
    status: str
    last_login_at: datetime | None
    last_synced_at: datetime | None
    next_sync_at: datetime | None


class AdminSettingsOut(BaseModel):
    student_id: str
    is_admin: bool
    consent_at: datetime
    lms: AdminLmsSettingsOut | None
    courses: list[AdminCourseSettingsOut]
    channels: list[AdminChannelSettingsOut]


@router.get("/users", response_model=AdminUsersOut)
async def list_users(
    admin: AdminUser, db: Db, response: Response,
    q: str = Query(default="", max_length=32),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    filters = [User.student_id.contains(q.strip(), autoescape=True)]
    total = await db.scalar(select(func.count()).select_from(User).where(*filters))
    rows = (await db.execute(
        select(User, LmsCredential.status).outerjoin(LmsCredential, LmsCredential.user_id == User.id)
        .where(*filters).order_by(User.last_login_at.desc().nulls_last(), User.id.desc())
        .offset(offset).limit(limit)
    )).all()
    return AdminUsersOut(total=total, users=[
        AdminUserOut(student_id=u.student_id, created_at=u.created_at,
                     last_login_at=u.last_login_at, login_count=u.login_count, lms_status=status)
        for u, status in rows
    ])


@router.get("/users/{student_id}/settings", response_model=AdminSettingsOut)
async def user_settings(student_id: str, admin: AdminUser, db: Db, response: Response):
    response.headers["Cache-Control"] = "no-store"
    user = await db.scalar(select(User).where(User.student_id == student_id))
    if user is None:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    # Select only display fields; credentials and notification targets are never decrypted.
    lms = (await db.execute(select(
        LmsCredential.status, LmsCredential.last_login_at,
        LmsCredential.last_synced_at, LmsCredential.next_sync_at,
    ).where(LmsCredential.user_id == user.id))).mappings().one_or_none()
    courses = (await db.execute(select(
        CourseRow.name, CourseRow.active, CourseRow.auto_play,
    ).where(CourseRow.user_id == user.id).order_by(CourseRow.active.desc(), CourseRow.name, CourseRow.id))).mappings().all()
    channels = (await db.execute(select(
        NotificationChannel.kind, NotificationChannel.enabled, NotificationChannel.created_at,
    ).where(NotificationChannel.user_id == user.id).order_by(NotificationChannel.id))).mappings().all()
    return AdminSettingsOut(
        student_id=user.student_id, is_admin=is_admin(user), consent_at=user.consent_at,
        lms=AdminLmsSettingsOut(**lms) if lms is not None else None,
        courses=[AdminCourseSettingsOut(**row) for row in courses],
        channels=[AdminChannelSettingsOut(**row) for row in channels],
    )


class AdminActivityOut(BaseModel):
    id: int
    kind: str
    title: str
    course_name: str | None
    status: str
    recorded_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    count: int


class AdminActivitiesOut(BaseModel):
    total: int
    counts: dict[str, int]
    activities: list[AdminActivityOut]


@router.get("/users/{student_id}/activities", response_model=AdminActivitiesOut)
async def user_activities(
    student_id: str, admin: AdminUser, db: Db, response: Response,
    kind: Literal["all", "playback", "download", "transcript"] = "all",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    user_id = await db.scalar(select(User.id).where(User.student_id == student_id))
    if user_id is None:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    # Only metadata is selected: no media URLs, raw errors, or transcript contents.
    activities = activity_union(user_id)
    counts = {"playback": 0, "download": 0, "transcript": 0}
    counts.update(dict((await db.execute(
        select(activities.c.kind, func.count()).group_by(activities.c.kind)
    )).all()))
    query = select(activities)
    if kind != "all":
        query = query.where(activities.c.kind == kind)
    rows = (await db.execute(query.order_by(
        activities.c.recorded_at.desc(), activities.c.kind, activities.c.id.desc(),
    ).offset(offset).limit(limit))).mappings().all()
    return AdminActivitiesOut(
        total=sum(counts.values()) if kind == "all" else counts[kind], counts=counts,
        activities=[AdminActivityOut(**row) for row in rows],
    )


def _period_days(days: int = 7) -> int:
    if days not in (7, 30, 90):
        raise HTTPException(422, "days는 7, 30, 90 중 하나여야 합니다.")
    return days


class AdminAllActivityOut(AdminActivityOut):
    student_id: str


class AdminAllActivitiesOut(BaseModel):
    total: int
    counts: dict[str, int]
    activities: list[AdminAllActivityOut]


class AdminProblemOut(BaseModel):
    kind: str
    occurred_at: datetime | None
    student_id: str
    course_name: str | None
    title: str
    error: str | None
    not_run: int


class AdminProblemsOut(BaseModel):
    total: int
    counts: dict[str, int]
    problems: list[AdminProblemOut]


class AdminSeriesOut(BaseModel):
    day: date
    playback_done: int
    logins: int
    downloads: int


class AdminOverviewOut(BaseModel):
    days: int
    total_users: int
    active_users: int
    playback_done: int
    playback_auto: int
    playback_seconds: float
    downloads: int
    playback_failed: int
    failure_rate: float | None
    tracking_since: date | None
    problem_count: int
    series: list[AdminSeriesOut]
    recent_activity: list[AdminAllActivityOut]
    recent_problems: list[AdminProblemOut]


async def _all_activities(db, activities, start, kind: str, student_id: str | None):
    """기간·학번으로 거른 활동의 (종류별 건수, 필터된 쿼리). 건수에는 종류 필터를 적용하지 않는다."""
    base = select(activities, User.student_id).join(User, User.id == activities.c.user_id).where(
        activities.c.recorded_at >= start)
    if student_id:
        base = base.where(User.student_id == student_id)
    sub = base.subquery()
    counts = {"playback": 0, "download": 0, "transcript": 0}
    counts.update(dict((await db.execute(select(sub.c.kind, func.count()).group_by(sub.c.kind))).all()))
    return counts, select(sub).where(sub.c.kind == kind) if kind != "all" else select(sub), sub


def _problem_rows(rows) -> list[AdminProblemOut]:
    out = []
    for row in rows:
        title = f"재생 묶음 ({row['title']})" if row["kind"] == "batch" else row["title"]
        out.append(AdminProblemOut(
            kind=row["kind"], occurred_at=row["occurred_at"] and as_utc(row["occurred_at"]),
            student_id=row["student_id"], course_name=row["course_name"], title=title,
            error=clean_error(row["error"]), not_run=row["not_run"]))
    return out


def _problem_order(sub):
    return (sub.c.occurred_at.desc().nulls_last(), sub.c.kind, sub.c.ref_id.desc())


@router.get("/overview", response_model=AdminOverviewOut)
async def overview(admin: AdminUser, db: Db, response: Response, days: int = Depends(_period_days)):
    response.headers["Cache-Control"] = "no-store"
    start = period_start(days)
    first_day = local_day() - timedelta(days=days - 1)
    total_users = await db.scalar(select(func.count()).select_from(User))
    daily = (await db.execute(select(
        UserDailyActivity.day, func.sum(UserDailyActivity.logins), func.sum(UserDailyActivity.downloads),
    ).where(UserDailyActivity.day >= first_day).group_by(UserDailyActivity.day))).all()
    by_day = {d: (int(logins or 0), int(downloads or 0)) for d, logins, downloads in daily}
    active = union(
        select(UserDailyActivity.user_id).where(
            UserDailyActivity.day >= first_day, (UserDailyActivity.logins > 0) | (UserDailyActivity.downloads > 0)),
        select(PlaybackJob.user_id).join(PlaybackBatch, PlaybackBatch.id == PlaybackJob.batch_id).where(
            PlaybackJob.started_at >= start, PlaybackBatch.scope != "auto"),
        select(Transcript.user_id).where(Transcript.updated_at >= start),
    ).subquery()
    active_users = await db.scalar(select(func.count()).select_from(active))
    done = (await db.execute(
        select(PlaybackJob.finished_at, PlaybackJob.duration, PlaybackBatch.scope)
        .join(PlaybackBatch, PlaybackBatch.id == PlaybackJob.batch_id)
        .where(PlaybackJob.status == "done", PlaybackJob.finished_at >= start)
    )).all()
    tz = ZoneInfo(get_settings().timezone)
    done_by_day: dict[date, int] = defaultdict(int)
    for finished_at, _, _ in done:
        done_by_day[as_utc(finished_at).astimezone(tz).date()] += 1
    failed = await db.scalar(
        select(func.count()).select_from(PlaybackJob)
        .join(PlaybackBatch, PlaybackBatch.id == PlaybackJob.batch_id)
        .where(failed_job_filter(), failed_at() >= start))
    tracking_since = await db.scalar(select(func.min(UserDailyActivity.day)))
    problems = problem_union(start)
    problem_count = await db.scalar(select(func.count()).select_from(problems))
    recent_problems = (await db.execute(
        select(problems).order_by(*_problem_order(problems)).limit(5))).mappings().all()
    activities = activity_union(None)
    _, query, sub = await _all_activities(db, activities, start, "all", None)
    recent = (await db.execute(query.order_by(
        sub.c.recorded_at.desc(), sub.c.kind, sub.c.id.desc()).limit(10))).mappings().all()
    series = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        logins, downloads = by_day.get(day, (0, 0))
        series.append(AdminSeriesOut(day=day, playback_done=done_by_day.get(day, 0), logins=logins, downloads=downloads))
    return AdminOverviewOut(
        days=days, total_users=total_users, active_users=active_users,
        playback_done=len(done), playback_auto=sum(1 for _, _, scope in done if scope == "auto"),
        playback_seconds=float(sum(duration for _, duration, _ in done)),
        downloads=sum(downloads for _, downloads in by_day.values()),
        playback_failed=failed, failure_rate=failed / (len(done) + failed) if len(done) + failed else None,
        tracking_since=tracking_since, problem_count=problem_count, series=series,
        recent_activity=[AdminAllActivityOut(**row) for row in recent],
        recent_problems=_problem_rows(recent_problems),
    )


@router.get("/activities", response_model=AdminAllActivitiesOut)
async def all_activities(
    admin: AdminUser, db: Db, response: Response,
    kind: Literal["all", "playback", "download", "transcript"] = "all",
    student_id: str | None = Query(default=None, max_length=32),
    days: int = Depends(_period_days),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    counts, query, sub = await _all_activities(db, activity_union(None), period_start(days), kind, student_id)
    rows = (await db.execute(query.order_by(
        sub.c.recorded_at.desc(), sub.c.kind, sub.c.id.desc()).offset(offset).limit(limit))).mappings().all()
    return AdminAllActivitiesOut(
        total=sum(counts.values()) if kind == "all" else counts[kind], counts=counts,
        activities=[AdminAllActivityOut(**row) for row in rows],
    )


@router.get("/problems", response_model=AdminProblemsOut)
async def problems(
    admin: AdminUser, db: Db, response: Response,
    kind: Literal["all", "playback", "batch", "lms", "resolve"] = "all",
    student_id: str | None = Query(default=None, max_length=32),
    lms_course_id: int | None = None,
    days: int = Depends(_period_days),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    union_sub = problem_union(period_start(days))
    filtered = select(union_sub)
    if student_id:
        filtered = filtered.where(union_sub.c.student_id == student_id)
    if lms_course_id is not None:
        filtered = filtered.where(union_sub.c.lms_course_id == lms_course_id)
    sub = filtered.subquery()
    counts = {"playback": 0, "batch": 0, "lms": 0, "resolve": 0}
    counts.update(dict((await db.execute(select(sub.c.kind, func.count()).group_by(sub.c.kind))).all()))
    query = select(sub) if kind == "all" else select(sub).where(sub.c.kind == kind)
    rows = (await db.execute(query.order_by(*_problem_order(sub)).offset(offset).limit(limit))).mappings().all()
    return AdminProblemsOut(
        total=sum(counts.values()) if kind == "all" else counts[kind], counts=counts,
        problems=_problem_rows(rows),
    )
