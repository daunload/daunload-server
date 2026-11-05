from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, literal, select, union_all

from app.api.deps import AdminUser, Db, is_admin
from app.db.models import (CourseRow, DownloadRecord, LmsCredential, NotificationChannel,
                           PlaybackBatch, PlaybackJob, Transcript, User)

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
    playback = select(
        PlaybackJob.id.label("id"), literal("playback").label("kind"),
        PlaybackJob.title.label("title"), CourseRow.name.label("course_name"),
        PlaybackJob.status.label("status"), PlaybackBatch.created_at.label("recorded_at"),
        PlaybackJob.started_at.label("started_at"), PlaybackJob.finished_at.label("finished_at"),
        literal(1).label("count"),
    ).join(PlaybackBatch, PlaybackBatch.id == PlaybackJob.batch_id).outerjoin(
        CourseRow, (CourseRow.user_id == PlaybackJob.user_id)
        & (CourseRow.lms_course_id == PlaybackJob.lms_course_id),
    ).where(PlaybackJob.user_id == user_id)
    downloads = select(
        DownloadRecord.id, literal("download"), DownloadRecord.title, CourseRow.name,
        literal("link_issued"), DownloadRecord.last_at, literal(None), literal(None), DownloadRecord.count,
    ).outerjoin(CourseRow, (CourseRow.user_id == DownloadRecord.user_id)
                & (CourseRow.lms_course_id == DownloadRecord.lms_course_id)
    ).where(DownloadRecord.user_id == user_id)
    transcripts = select(
        Transcript.id, literal("transcript"), Transcript.title, Transcript.course_name,
        literal("saved"), Transcript.updated_at, literal(None), literal(None), literal(1),
    ).where(Transcript.user_id == user_id)
    activities = union_all(playback, downloads, transcripts).subquery()
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
