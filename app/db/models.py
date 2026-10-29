from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # 학교 비밀번호의 해시. 다음 로그인부터 LMS에 접속하지 않고 확인하는 데 쓴다.
    password_hash: Mapped[str] = mapped_column(String(255))
    consent_at: Mapped[datetime]  # 계정 정보 보관·학칙 안내 동의 시각
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LmsCredential(Base):
    __tablename__ = "lms_credentials"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32))
    password_enc: Mapped[str] = mapped_column(Text)  # Fernet
    session_enc: Mapped[str | None] = mapped_column(Text)  # Fernet(JSON LmsSession) 캐시
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok / invalid / error
    last_error: Mapped[str | None] = mapped_column(Text)
    last_login_at: Mapped[datetime | None]
    last_synced_at: Mapped[datetime | None]
    next_sync_at: Mapped[datetime | None] = mapped_column(index=True)


class CourseRow(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("user_id", "lms_course_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lms_course_id: Mapped[int] = mapped_column(Integer)
    term_id: Mapped[int | None]
    name: Mapped[str] = mapped_column(String(255))
    professors: Mapped[str | None] = mapped_column(String(255))
    auto_play: Mapped[bool] = mapped_column(Boolean, default=False)
    # [{"week": 1, "statuses": ["attendance", "absent"]}, ...]
    attendance: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # 현재 학기 과목 여부
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TodoRow(Base):
    """미수강 영상과 미제출 과제·퀴즈. active=False면 마지막 동기화에서 사라진(완료·마감) 항목."""

    __tablename__ = "todos"
    __table_args__ = (UniqueConstraint("user_id", "kind", "lms_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lms_course_id: Mapped[int] = mapped_column(Integer, index=True)
    course_name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # video / assignment / quiz
    lms_item_id: Mapped[int] = mapped_column(Integer)  # 영상은 module_item_id, 과제는 assignment_id
    title: Mapped[str] = mapped_column(String(500))
    deadline: Mapped[datetime]
    due_at: Mapped[datetime | None]
    unlock_at: Mapped[datetime | None]
    url: Mapped[str | None] = mapped_column(Text)
    # 영상 전용
    module_id: Mapped[int | None]
    module_title: Mapped[str | None] = mapped_column(String(255))
    module_position: Mapped[int | None]
    item_position: Mapped[int | None]
    item_id: Mapped[int | None]
    duration: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AssignmentRow(Base):
    """과제·퀴즈와 내 제출 상태(제출했든 안 했든 모두). 미제출 상태는 조회할 때 현재 시각으로 계산한다."""

    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("user_id", "assignment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lms_course_id: Mapped[int] = mapped_column(Integer, index=True)
    course_name: Mapped[str] = mapped_column(String(255))
    assignment_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    group_name: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # assignment / quiz
    submission_state: Mapped[str] = mapped_column(String(16))  # unsubmitted / submitted / excused / unknown
    submitted_at: Mapped[datetime | None]
    missing: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float | None] = mapped_column(Float)
    points_possible: Mapped[float | None] = mapped_column(Float)
    due_at: Mapped[datetime | None]
    deadline: Mapped[datetime | None]  # 제출 종료(late_at → 일반 마감)
    lock_at: Mapped[datetime | None]
    unlock_at: Mapped[datetime | None]
    published: Mapped[bool | None] = mapped_column(Boolean)
    assignment_state: Mapped[str | None] = mapped_column(String(16))
    locked_for_user: Mapped[bool | None] = mapped_column(Boolean)
    url: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AnnouncementRow(Base):
    __tablename__ = "announcements"
    __table_args__ = (UniqueConstraint("user_id", "lms_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lms_course_id: Mapped[int] = mapped_column(Integer)
    course_name: Mapped[str] = mapped_column(String(255))
    lms_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    message: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    posted_at: Mapped[datetime | None] = mapped_column(index=True)
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PlaybackBatch(Base):
    __tablename__ = "playback_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(16))  # item / module / course / all / auto
    lms_course_id: Mapped[int | None]
    module_id: Mapped[int | None]
    # queued / running / done / partial / failed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    done_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    current_position: Mapped[int | None]
    expected_end: Mapped[datetime | None]
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class PlaybackJob(Base):
    __tablename__ = "playback_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("playback_batches.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    lms_course_id: Mapped[int] = mapped_column(Integer)
    module_item_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    duration: Mapped[float] = mapped_column(Float)
    deadline: Mapped[datetime]
    video: Mapped[dict[str, Any]] = mapped_column(JSON)  # VideoTodo 직렬화
    # queued / playing / done / failed / skipped / locked / cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress_seconds: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # discord / telegram
    target_enc: Mapped[str] = mapped_column(Text)  # 웹훅 URL 또는 "봇토큰|chat_id" (Fernet)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class NotificationLog(Base):
    """같은 알림을 두 번 보내지 않기 위한 기록."""

    __tablename__ = "notification_log"
    __table_args__ = (UniqueConstraint("user_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SavedVideo(Base):
    """시청기간 안에 찾아 둔 강의 영상 주소. 기간이 끝나 LMS에서 조회할 수 없게 된 뒤에도 본인이 내려받는 데 쓴다."""

    __tablename__ = "saved_videos"
    __table_args__ = (UniqueConstraint("user_id", "lms_course_id", "module_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lms_course_id: Mapped[int] = mapped_column(Integer)
    course_name: Mapped[str] = mapped_column(String(255))
    module_item_id: Mapped[int] = mapped_column(Integer)
    module_title: Mapped[str] = mapped_column(String(255))
    module_position: Mapped[int] = mapped_column(Integer, default=0)
    item_position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(500))
    content_id: Mapped[str] = mapped_column(String(64))
    media_url: Mapped[str | None] = mapped_column(Text)  # 아직 찾지 못했으면 None
    file_name: Mapped[str] = mapped_column(String(500))
    duration: Mapped[float | None] = mapped_column(Float)
    size: Mapped[int | None] = mapped_column(BigInteger)  # 2GB를 넘을 수 있다
    resolve_error: Mapped[str | None] = mapped_column(Text)  # 주소를 찾지 못한 이유(지원하지 않는 형식 등)
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
