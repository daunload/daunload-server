"""어드민 대시보드가 쓰는 조회 조건과 응답 정리 도구. 조회 전용이다."""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, String, and_, cast, func, literal, null, select, union_all

from app.config import get_settings
from app.db.models import (CourseRow, DownloadRecord, LmsCredential, PlaybackBatch, PlaybackJob,
                           SavedVideo, SavedVideoItem, Transcript, User)
from app.services.activity import local_day

ERROR_LIMIT = 2000
_URL = re.compile(r"(https?://)([^\s/?#]*)\S*", re.IGNORECASE)


def clean_error(text: str | None) -> str | None:
    """오류 문자열에서 URL의 경로·쿼리·계정 정보를 지우고 2000자로 자른다. 저장값은 건드리지 않는다."""
    if text is None:
        return None
    cleaned = _URL.sub(lambda m: m.group(1) + m.group(2).rsplit("@", 1)[-1], text)
    return cleaned[:ERROR_LIMIT]


def as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def period_start(days: int) -> datetime:
    """오늘을 포함해 days일이 되는 첫 날의 한국 시간 자정(UTC로 환산)."""
    first = local_day() - timedelta(days=days - 1)
    return datetime.combine(first, time.min, tzinfo=ZoneInfo(get_settings().timezone)).astimezone(UTC)


def failed_job_filter():
    """시도한(started_at이 있는) failed/locked job. 시도 없이 중단된 job은 세지 않는다."""
    return and_(PlaybackJob.status.in_(("failed", "locked")), PlaybackJob.started_at.is_not(None))


def failed_at():
    """실패 시각: job의 finished_at, 없으면 묶음의 finished_at. PlaybackBatch를 join한 쿼리에서 쓴다."""
    return func.coalesce(PlaybackJob.finished_at, PlaybackBatch.finished_at)


def activity_union(user_id: int | None):
    """재생·다운로드·전사 활동의 UNION. user_id가 None이면 전체 사용자. 메타데이터만 고른다."""
    def scope(query, column):
        return query if user_id is None else query.where(column == user_id)

    playback = scope(select(
        PlaybackJob.id.label("id"), literal("playback").label("kind"),
        PlaybackJob.title.label("title"), CourseRow.name.label("course_name"),
        PlaybackJob.status.label("status"), PlaybackBatch.created_at.label("recorded_at"),
        PlaybackJob.started_at.label("started_at"), PlaybackJob.finished_at.label("finished_at"),
        literal(1).label("count"), PlaybackJob.user_id.label("user_id"),
    ).join(PlaybackBatch, PlaybackBatch.id == PlaybackJob.batch_id).outerjoin(
        CourseRow, (CourseRow.user_id == PlaybackJob.user_id)
        & (CourseRow.lms_course_id == PlaybackJob.lms_course_id),
    ), PlaybackJob.user_id)
    downloads = scope(select(
        DownloadRecord.id, literal("download"), DownloadRecord.title, CourseRow.name,
        literal("link_issued"), DownloadRecord.last_at, literal(None), literal(None), DownloadRecord.count,
        DownloadRecord.user_id,
    ).outerjoin(CourseRow, (CourseRow.user_id == DownloadRecord.user_id)
                & (CourseRow.lms_course_id == DownloadRecord.lms_course_id)), DownloadRecord.user_id)
    transcripts = scope(select(
        Transcript.id, literal("transcript"), Transcript.title, Transcript.course_name,
        literal("saved"), Transcript.updated_at, literal(None), literal(None), literal(1),
        Transcript.user_id,
    ), Transcript.user_id)
    return union_all(playback, downloads, transcripts).subquery()


def problem_union(start: datetime):
    """문제 네 종류의 UNION. LMS 문제는 현재 상태라 기간을 적용하지 않는다."""
    def cols(kind, ref_id, occurred_at, student_id, course_id, course_name, title, error, not_run):
        return (literal(kind).label("kind"), ref_id.label("ref_id"), occurred_at.label("occurred_at"),
                student_id.label("student_id"), course_id.label("lms_course_id"),
                course_name.label("course_name"), title.label("title"), error.label("error"),
                not_run.label("not_run"))

    course_join = lambda user_col, course_col: (  # noqa: E731
        (CourseRow.user_id == user_col) & (CourseRow.lms_course_id == course_col))
    no_course = cast(null(), String)
    playback = select(*cols(
        "playback", PlaybackJob.id, failed_at(), User.student_id, PlaybackJob.lms_course_id,
        CourseRow.name, PlaybackJob.title, PlaybackJob.error, literal(0),
    )).select_from(PlaybackJob).join(PlaybackBatch, PlaybackBatch.id == PlaybackJob.batch_id).join(
        User, User.id == PlaybackJob.user_id,
    ).outerjoin(CourseRow, course_join(PlaybackJob.user_id, PlaybackJob.lms_course_id)).where(
        failed_job_filter(), failed_at() >= start)
    not_run = select(func.count()).where(
        PlaybackJob.batch_id == PlaybackBatch.id, PlaybackJob.status == "failed",
        PlaybackJob.started_at.is_(None)).scalar_subquery()
    batch_when = func.coalesce(PlaybackBatch.finished_at, PlaybackBatch.created_at)
    batch = select(*cols(
        "batch", PlaybackBatch.id, batch_when, User.student_id, PlaybackBatch.lms_course_id,
        CourseRow.name, PlaybackBatch.scope, PlaybackBatch.error, not_run,
    )).select_from(PlaybackBatch).join(User, User.id == PlaybackBatch.user_id).outerjoin(
        CourseRow, course_join(PlaybackBatch.user_id, PlaybackBatch.lms_course_id),
    ).where(PlaybackBatch.error.is_not(None), PlaybackBatch.error != "", batch_when >= start)
    lms = select(*cols(
        "lms", LmsCredential.user_id, LmsCredential.last_error_at, User.student_id,
        cast(null(), Integer), no_course, literal("LMS 연결"), LmsCredential.last_error, literal(0),
    )).select_from(LmsCredential).join(User, User.id == LmsCredential.user_id).where(
        LmsCredential.status != "ok")
    resolve = select(*cols(
        "resolve", SavedVideo.id, SavedVideoItem.resolve_failed_at, User.student_id, SavedVideoItem.lms_course_id,
        SavedVideoItem.course_name, SavedVideoItem.title, SavedVideoItem.resolve_error, literal(0),
    )).select_from(SavedVideo).join(SavedVideoItem, SavedVideoItem.id == SavedVideo.item_id).join(
        User, User.id == SavedVideo.user_id).where(SavedVideoItem.resolve_failed_at >= start)
    return union_all(playback, batch, lms, resolve).subquery()
