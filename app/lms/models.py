"""LMS 응답 모델.

필드 이름은 LMS 응답 JSON 그대로 둔다. 날짜는 판정 규칙에서 '누락'과 '형식 오류'를
구분해야 하므로 문자열로 받고 app.lms.todo의 parse_dt로 해석한다.
Canvas는 id를 문자열로 주기도 하는데, pydantic이 int로 변환한다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LmsModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Term(LmsModel):
    id: int
    name: str | None = None
    start_at: str | None = None
    end_at: str | None = None


class Activity(LmsModel):
    total_unread_announcements: int = 0
    total_announcements: int = 0
    total_incompleted_commons_resources: int = 0
    total_incompleted_movies: int = 0
    total_unsubmitted_assignments: int = 0


class Course(LmsModel):
    id: int
    term_id: int | None = None
    name: str = ""
    professors: str | None = None
    course_format: str | None = None
    course_format_custom_name: str | None = None
    total_students: int | None = None
    ended: bool = False
    activities: Activity | None = None


class ItemContentData(LmsModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    duration: float | None = None
    view_url: str | None = None
    # Commons 콘텐츠 식별자. 아직 열리지 않은 항목은 "not_open"으로 온다.
    content_id: str | None = None
    content_type: str | None = None  # movie / everlec / video / mp4 / readystream / pdf ...
    total_file_size: int | None = None
    thumbnail_url: str | None = None
    file_name: str | None = None  # content_type이 "file"인 commons 자료의 원래 파일 이름


class ContentData(LmsModel):
    item_id: int | None = None
    item_content_type: str | None = None
    item_content_data: ItemContentData | None = None
    url: str | None = None  # wiki_page 항목의 페이지 slug
    title: str | None = None
    description: str | None = None
    use_attendance: bool | None = True
    due_at: str | None = None
    late_at: str | None = None
    unlock_at: str | None = None


class ModuleItem(LmsModel):
    module_item_id: int | None = None
    title: str | None = None
    content_type: str | None = None
    content_id: int | None = None
    content_data: ContentData | None = None
    position: int | None = 0
    completed: bool | None = False


class Module(LmsModel):
    module_id: int | None = None
    title: str | None = None
    position: int | None = 0
    unlock_at: str | None = None
    module_items: list[ModuleItem] = Field(default_factory=list)


class SubSection(LmsModel):
    subsection_id: int
    subsection_title: str | None = None
    position: int = 0
    status: str | None = None  # attendance / absent / late / ...


class Section(LmsModel):
    section_id: int
    section_title: str | None = None
    position: int = 0
    is_upcoming: bool = False
    subsections: list[SubSection] = Field(default_factory=list)


class CourseRef(LmsModel):
    id: int
    term_id: int | None = None


class LearnStatus(LmsModel):
    course: CourseRef
    sections: list[Section] = Field(default_factory=list)


class Assignment(LmsModel):
    id: int
    name: str = ""
    course_id: int | None = None
    assignment_group_id: int | None = None
    due_at: str | None = None
    unlock_at: str | None = None
    lock_at: str | None = None
    late_at: str | None = None
    points_possible: float | None = None
    submission_types: list[str] = Field(default_factory=list)
    is_quiz_assignment: bool | None = None
    published: bool | None = None
    workflow_state: str | None = None
    locked_for_user: bool | None = None
    html_url: str | None = None

    @property
    def is_quiz(self) -> bool:
        return bool(self.is_quiz_assignment) or "online_quiz" in self.submission_types


class AssignmentGroup(LmsModel):
    id: int | None = None
    name: str = ""
    assignments: list[Assignment] = Field(default_factory=list)


class Attachment(LmsModel):
    id: int | None = None
    display_name: str | None = None
    filename: str | None = None
    content_type: str | None = Field(default=None, alias="content-type")
    url: str | None = None
    size: int | None = None


class Submission(LmsModel):
    assignment_id: int | None = None
    workflow_state: str | None = None
    submitted_at: str | None = None
    cached_due_date: str | None = None
    submission_type: str | None = None
    late: bool | None = False
    missing: bool | None = False
    excused: bool | None = False
    score: float | None = None
    attempt: int | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class Announcement(LmsModel):
    id: int
    title: str = ""
    message: str | None = None
    url: str | None = None
    html_url: str | None = None
    posted_at: str | None = None
    created_at: str | None = None
    read_state: str | None = None
    user_name: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class UserInfo(LmsModel):
    user_login: str
    user_name: str | None = None
    dept_name: str | None = None
    user_email: str | None = None
