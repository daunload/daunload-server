from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CredentialsOut(Orm):
    student_id: str
    status: str
    last_error: str | None
    last_login_at: datetime | None
    last_synced_at: datetime | None
    next_sync_at: datetime | None


class CourseOut(Orm):
    lms_course_id: int
    name: str
    professors: str | None
    auto_play: bool
    attendance: list[dict[str, Any]] | None


class AutoPlayIn(BaseModel):
    enabled: bool


class TodoOut(Orm):
    kind: str
    lms_course_id: int
    course_name: str
    lms_item_id: int
    title: str
    deadline: datetime
    due_at: datetime | None
    url: str | None
    module_id: int | None
    module_title: str | None
    duration: float | None


class AssignmentOut(BaseModel):
    status: str  # open / overdue / upcoming / locked / no_due (미제출) 또는 submitted / excused / unknown
    kind: str
    lms_course_id: int
    course_name: str
    assignment_id: int
    title: str
    group_name: str | None
    due_at: datetime | None
    deadline: datetime | None
    lock_at: datetime | None
    unlock_at: datetime | None
    submitted_at: datetime | None
    missing: bool
    score: float | None
    points_possible: float | None
    url: str | None


class WeekOut(BaseModel):
    start: datetime
    end: datetime
    videos: list[TodoOut]
    video_seconds: float
    assignments: list[AssignmentOut]
    missed: list[AssignmentOut]


class AnnouncementOut(Orm):
    lms_id: int
    lms_course_id: int
    course_name: str
    title: str
    message: str | None
    url: str | None
    author: str | None
    posted_at: datetime | None
    attachments: list[dict[str, Any]] | None


class PlanItemOut(BaseModel):
    position: int
    lms_course_id: int
    course_name: str
    module_id: int
    module_title: str
    module_item_id: int
    title: str
    duration: float
    deadline: datetime
    expected_start: datetime
    expected_end: datetime
    at_risk: bool


class PlanOut(BaseModel):
    items: list[PlanItemOut]
    total_seconds: float
    expected_end: datetime | None
    warnings: list[str]


class BatchIn(BaseModel):
    scope: Literal["module", "course", "all", "week"]
    course_id: int | None = None
    module_id: int | None = None


class JobOut(Orm):
    expected_start: datetime | None = None
    expected_end: datetime | None = None
    position: int
    lms_course_id: int
    module_item_id: int
    title: str
    duration: float
    deadline: datetime
    status: str
    attempts: int
    progress_seconds: float
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class BatchOut(Orm):
    queue_position: int | None = None
    estimated_start: datetime | None = None
    estimated_wait_seconds: float | None = None
    deadline_at_risk: bool = False
    id: int
    scope: str
    lms_course_id: int | None
    module_id: int | None
    status: str
    total: int
    done_count: int
    failed_count: int
    skipped_count: int
    current_position: int | None
    expected_end: datetime | None
    cancel_requested: bool
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class BatchDetailOut(BatchOut):
    jobs: list[JobOut]


class ChannelIn(BaseModel):
    kind: Literal["discord", "telegram"]
    webhook_url: str | None = None  # discord
    bot_token: str | None = None  # telegram
    chat_id: str | None = None  # telegram


class ChannelOut(Orm):
    id: int
    kind: str
    enabled: bool
    created_at: datetime
