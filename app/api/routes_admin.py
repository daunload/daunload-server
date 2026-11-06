from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import and_, exists, func, not_, or_, select, union

from app.api.deps import AdminUser, Db, is_admin
from app.config import get_settings
from app.db.models import (AssignmentRow, CourseRow, DownloadRecord, LmsCredential, NotificationChannel,
                           NotificationLog, PlaybackBatch, PlaybackJob, SavedVideo, TodoRow, Transcript, User,
                           UserDailyActivity)
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
    course_count: int
    playback_done: int
    playback_failed: int
    download_count: int
    transcript_count: int
    channels: list[str]


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


def _recent_failure(user_col):
    """최근 7일 안에 4장 정의의 재생 실패가 있는 사용자 조건."""
    return exists().where(
        PlaybackJob.user_id == user_col, PlaybackBatch.id == PlaybackJob.batch_id,
        failed_job_filter(), failed_at() >= period_start(7))


async def _by_user(db, user_ids, value, *where):
    """user_id별 집계 한 줄: value(집계식)를 user_id IN (...)으로 항목마다 따로 구한다."""
    column = where[0]
    rows = (await db.execute(select(column, value).where(column.in_(user_ids), *where[1:]).group_by(column))).all()
    return {user_id: int(n or 0) for user_id, n in rows}


@router.get("/users", response_model=AdminUsersOut)
async def list_users(
    admin: AdminUser, db: Db, response: Response,
    q: str = Query(default="", max_length=32),
    lms_status: str | None = Query(default=None, max_length=16),
    has_problem: bool | None = None,
    sort: Literal["activity", "created", "student_id"] = "activity",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    filters = [User.student_id.contains(q.strip(), autoescape=True)]
    if lms_status:
        filters.append(LmsCredential.status == lms_status)
    if has_problem is not None:
        problem = or_(and_(LmsCredential.user_id.is_not(None), LmsCredential.status != "ok"), _recent_failure(User.id))
        filters.append(problem if has_problem else not_(problem))
    base = select(User, LmsCredential.status).outerjoin(LmsCredential, LmsCredential.user_id == User.id).where(*filters)
    total = await db.scalar(select(func.count()).select_from(base.with_only_columns(User.id).subquery()))
    order = {
        "activity": (User.last_login_at.desc().nulls_last(), User.id.desc()),
        "created": (User.created_at.desc(), User.id.desc()),
        "student_id": (User.student_id, User.id),
    }[sort]
    rows = (await db.execute(base.order_by(*order).offset(offset).limit(limit))).all()
    ids = [u.id for u, _ in rows]
    courses = await _by_user(db, ids, func.count(), CourseRow.user_id)
    done = await _by_user(db, ids, func.count(), PlaybackJob.user_id, PlaybackJob.status == "done")
    failed = await _by_user(db, ids, func.count(), PlaybackJob.user_id, failed_job_filter())
    downloads = await _by_user(db, ids, func.sum(DownloadRecord.count), DownloadRecord.user_id)
    transcripts = await _by_user(db, ids, func.count(), Transcript.user_id)
    channels: dict[int, list[str]] = defaultdict(list)
    for user_id, kind in (await db.execute(select(NotificationChannel.user_id, NotificationChannel.kind).where(
            NotificationChannel.user_id.in_(ids)).order_by(NotificationChannel.id))).all():
        channels[user_id].append(kind)
    return AdminUsersOut(total=total, users=[
        AdminUserOut(student_id=u.student_id, created_at=u.created_at,
                     last_login_at=u.last_login_at, login_count=u.login_count, lms_status=status,
                     course_count=courses.get(u.id, 0), playback_done=done.get(u.id, 0),
                     playback_failed=failed.get(u.id, 0), download_count=downloads.get(u.id, 0),
                     transcript_count=transcripts.get(u.id, 0), channels=channels.get(u.id, []))
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


# ---- 사용자 상세 ----
class AdminLmsOut(BaseModel):
    status: str
    last_error: str | None
    last_error_at: datetime | None
    last_login_at: datetime | None
    last_synced_at: datetime | None
    next_sync_at: datetime | None


class AdminCountsOut(BaseModel):
    courses: int
    todos: int
    playback_done: int
    playback_failed: int
    downloads: int
    saved_videos: int
    transcripts: int
    channels: int


class AdminSummaryOut(BaseModel):
    student_id: str
    is_admin: bool
    consent_at: datetime
    created_at: datetime
    last_login_at: datetime | None
    login_count: int
    lms: AdminLmsOut | None
    counts: AdminCountsOut


class AdminCourseOut(BaseModel):
    lms_course_id: int
    name: str
    professors: str | None
    active: bool
    auto_play: bool
    attendance: list[dict] | None


class AdminTodoOut(BaseModel):
    kind: str
    title: str
    course_name: str
    deadline: datetime | None
    due_at: datetime | None
    done: bool  # 마지막 동기화에서 사라진(완료·마감) 항목


class AdminAssignmentOut(BaseModel):
    kind: str
    title: str
    course_name: str
    submission_state: str
    submitted_at: datetime | None
    missing: bool
    due_at: datetime | None
    deadline: datetime | None


class AdminDownloadOut(BaseModel):
    title: str
    kind: str
    count: int
    first_at: datetime
    last_at: datetime


class AdminSavedVideoOut(BaseModel):
    title: str
    course_name: str
    module_title: str
    file_name: str
    duration: float | None
    size: int | None
    has_media_url: bool
    resolve_error: str | None
    resolved_at: datetime | None
    resolve_failed_at: datetime | None


class AdminTranscriptOut(BaseModel):
    title: str
    course_name: str
    model: str
    duration: float | None
    created_at: datetime
    updated_at: datetime


class AdminJobOut(BaseModel):
    id: int
    title: str
    duration: float
    status: str
    attempts: int
    progress_seconds: float
    deadline: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class AdminBatchOut(BaseModel):
    id: int
    scope: str
    status: str
    lms_course_id: int | None
    total: int
    done_count: int
    failed_count: int
    skipped_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    jobs: list[AdminJobOut]


class AdminNotificationLogOut(BaseModel):
    key: str
    created_at: datetime


def _page_out[T: BaseModel](model: type[T]):
    class Page(BaseModel):
        total: int
        items: list[model]  # type: ignore[valid-type]
    return Page


class AdminNotificationsOut(_page_out(AdminNotificationLogOut)):  # type: ignore[misc]
    channels: list[AdminChannelSettingsOut]


async def _target_user(db, student_id: str) -> User:
    user = await db.scalar(select(User).where(User.student_id == student_id))
    if user is None:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    return user


async def _paged(db, model, columns, user_id, order, offset, limit, *where):
    """사용자 한 명의 행을 {total, items}로. 열을 직접 골라 제외 대상 컬럼은 읽지도 않는다."""
    cond = (model.user_id == user_id, *where)
    total = await db.scalar(select(func.count()).select_from(model).where(*cond))
    rows = (await db.execute(select(*columns).where(*cond).order_by(*order).offset(offset).limit(limit))).mappings().all()
    return total, rows


Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/users/{student_id}/summary", response_model=AdminSummaryOut)
async def user_summary(student_id: str, admin: AdminUser, db: Db, response: Response):
    response.headers["Cache-Control"] = "no-store"
    user = await _target_user(db, student_id)
    lms = (await db.execute(select(
        LmsCredential.status, LmsCredential.last_error, LmsCredential.last_error_at, LmsCredential.last_login_at,
        LmsCredential.last_synced_at, LmsCredential.next_sync_at,
    ).where(LmsCredential.user_id == user.id))).mappings().one_or_none()

    async def count(column, *where, value=None):
        return int(await db.scalar(select(value if value is not None else func.count()).where(column == user.id, *where)) or 0)
    counts = AdminCountsOut(
        courses=await count(CourseRow.user_id), todos=await count(TodoRow.user_id, TodoRow.active.is_(True)),
        playback_done=await count(PlaybackJob.user_id, PlaybackJob.status == "done"),
        playback_failed=await count(PlaybackJob.user_id, failed_job_filter()),
        downloads=await count(DownloadRecord.user_id, value=func.sum(DownloadRecord.count)),
        saved_videos=await count(SavedVideo.user_id), transcripts=await count(Transcript.user_id),
        channels=await count(NotificationChannel.user_id),
    )
    return AdminSummaryOut(
        student_id=user.student_id, is_admin=is_admin(user), consent_at=user.consent_at,
        created_at=user.created_at, last_login_at=user.last_login_at, login_count=user.login_count,
        lms=AdminLmsOut(**{**lms, "last_error": clean_error(lms["last_error"])}) if lms is not None else None,
        counts=counts,
    )


@router.get("/users/{student_id}/courses", response_model=_page_out(AdminCourseOut))
async def user_courses(student_id: str, admin: AdminUser, db: Db, response: Response,
                       offset: Offset = 0, limit: Limit = 20):
    response.headers["Cache-Control"] = "no-store"
    user = await _target_user(db, student_id)
    total, rows = await _paged(
        db, CourseRow, (CourseRow.lms_course_id, CourseRow.name, CourseRow.professors, CourseRow.active,
                        CourseRow.auto_play, CourseRow.attendance),
        user.id, (CourseRow.active.desc(), CourseRow.name, CourseRow.id), offset, limit)
    return {"total": total, "items": rows}


@router.get("/users/{student_id}/todos", response_model=_page_out(AdminTodoOut))
async def user_todos(student_id: str, admin: AdminUser, db: Db, response: Response,
                     offset: Offset = 0, limit: Limit = 20):
    response.headers["Cache-Control"] = "no-store"
    user = await _target_user(db, student_id)
    total, rows = await _paged(
        db, TodoRow, (TodoRow.kind, TodoRow.title, TodoRow.course_name, TodoRow.deadline, TodoRow.due_at,
                      (~TodoRow.active).label("done")),
        user.id, (TodoRow.active.desc(), TodoRow.deadline.asc().nulls_last(), TodoRow.id), offset, limit)
    return {"total": total, "items": rows}


@router.get("/users/{student_id}/assignments", response_model=_page_out(AdminAssignmentOut))
async def user_assignments(student_id: str, admin: AdminUser, db: Db, response: Response,
                           offset: Offset = 0, limit: Limit = 20):
    response.headers["Cache-Control"] = "no-store"
    user = await _target_user(db, student_id)
    total, rows = await _paged(
        db, AssignmentRow, (AssignmentRow.kind, AssignmentRow.title, AssignmentRow.course_name,
                            AssignmentRow.submission_state, AssignmentRow.submitted_at, AssignmentRow.missing,
                            AssignmentRow.due_at, AssignmentRow.deadline),
        user.id, (AssignmentRow.deadline.desc().nulls_last(), AssignmentRow.id), offset, limit)
    return {"total": total, "items": rows}


@router.get("/users/{student_id}/downloads", response_model=_page_out(AdminDownloadOut))
async def user_downloads(student_id: str, admin: AdminUser, db: Db, response: Response,
                         offset: Offset = 0, limit: Limit = 20):
    response.headers["Cache-Control"] = "no-store"
    user = await _target_user(db, student_id)
    total, rows = await _paged(
        db, DownloadRecord, (DownloadRecord.title, DownloadRecord.kind, DownloadRecord.count,
                             DownloadRecord.first_at, DownloadRecord.last_at),
        user.id, (DownloadRecord.last_at.desc(), DownloadRecord.id.desc()), offset, limit)
    return {"total": total, "items": rows}


@router.get("/users/{student_id}/saved-videos", response_model=_page_out(AdminSavedVideoOut))
async def user_saved_videos(student_id: str, admin: AdminUser, db: Db, response: Response,
                            offset: Offset = 0, limit: Limit = 20):
    response.headers["Cache-Control"] = "no-store"
    user = await _target_user(db, student_id)
    # media_url은 읽지 않고 존재 여부만 계산한다.
    total, rows = await _paged(
        db, SavedVideo, (SavedVideo.title, SavedVideo.course_name, SavedVideo.module_title, SavedVideo.file_name,
                         SavedVideo.duration, SavedVideo.size, SavedVideo.media_url.is_not(None).label("has_media_url"),
                         SavedVideo.resolve_error, SavedVideo.resolved_at, SavedVideo.resolve_failed_at),
        user.id, (SavedVideo.course_name, SavedVideo.module_position, SavedVideo.item_position, SavedVideo.id),
        offset, limit)
    return {"total": total, "items": [{**row, "resolve_error": clean_error(row["resolve_error"])} for row in rows]}


@router.get("/users/{student_id}/transcripts", response_model=_page_out(AdminTranscriptOut))
async def user_transcripts(student_id: str, admin: AdminUser, db: Db, response: Response,
                           offset: Offset = 0, limit: Limit = 20):
    response.headers["Cache-Control"] = "no-store"
    user = await _target_user(db, student_id)
    total, rows = await _paged(
        db, Transcript, (Transcript.title, Transcript.course_name, Transcript.model, Transcript.duration,
                         Transcript.created_at, Transcript.updated_at),
        user.id, (Transcript.updated_at.desc(), Transcript.id.desc()), offset, limit)
    return {"total": total, "items": rows}


@router.get("/users/{student_id}/notifications", response_model=AdminNotificationsOut)
async def user_notifications(student_id: str, admin: AdminUser, db: Db, response: Response,
                             offset: Offset = 0, limit: Limit = 20):
    response.headers["Cache-Control"] = "no-store"
    user = await _target_user(db, student_id)
    channels = (await db.execute(select(
        NotificationChannel.kind, NotificationChannel.enabled, NotificationChannel.created_at,
    ).where(NotificationChannel.user_id == user.id).order_by(NotificationChannel.id))).mappings().all()
    total, rows = await _paged(
        db, NotificationLog, (NotificationLog.key, NotificationLog.created_at), user.id,
        (NotificationLog.created_at.desc(), NotificationLog.id.desc()), offset, limit)
    return {"total": total, "items": rows, "channels": channels}


@router.get("/users/{student_id}/playback", response_model=_page_out(AdminBatchOut))
async def user_playback(
    student_id: str, admin: AdminUser, db: Db, response: Response,
    status: str | None = Query(default=None, max_length=16),
    lms_course_id: int | None = None,
    days: int | None = Query(default=None, ge=1, le=365),
    offset: Offset = 0, limit: Limit = 20,
):
    response.headers["Cache-Control"] = "no-store"
    user = await _target_user(db, student_id)
    where = []
    if status:
        where.append(PlaybackBatch.status == status)
    if lms_course_id is not None:
        where.append(PlaybackBatch.lms_course_id == lms_course_id)
    if days is not None:
        where.append(PlaybackBatch.created_at >= period_start(days))
    total, batches = await _paged(
        db, PlaybackBatch, (PlaybackBatch.id, PlaybackBatch.scope, PlaybackBatch.status, PlaybackBatch.lms_course_id,
                            PlaybackBatch.total, PlaybackBatch.done_count, PlaybackBatch.failed_count,
                            PlaybackBatch.skipped_count, PlaybackBatch.created_at, PlaybackBatch.started_at,
                            PlaybackBatch.finished_at, PlaybackBatch.error),
        user.id, (PlaybackBatch.created_at.desc(), PlaybackBatch.id.desc()), offset, limit, *where)
    jobs: dict[int, list[dict]] = defaultdict(list)
    # PlaybackJob.video(영상 주소 JSON)는 고르지 않는다.
    for row in (await db.execute(select(
        PlaybackJob.batch_id, PlaybackJob.id, PlaybackJob.title, PlaybackJob.duration, PlaybackJob.status,
        PlaybackJob.attempts, PlaybackJob.progress_seconds, PlaybackJob.deadline, PlaybackJob.started_at,
        PlaybackJob.finished_at, PlaybackJob.error,
    ).where(PlaybackJob.batch_id.in_([b["id"] for b in batches])).order_by(PlaybackJob.position, PlaybackJob.id)
    )).mappings():
        jobs[row["batch_id"]].append({**row, "error": clean_error(row["error"])})
    return {"total": total, "items": [
        {**b, "error": clean_error(b["error"]), "jobs": jobs.get(b["id"], [])} for b in batches]}
