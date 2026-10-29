"""브라우저 Whisper 전사 결과 저장·조회. 전사는 브라우저에서 하고 서버는 결과만 보관한다."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.deps import CurrentUser, Db
from app.db.base import utcnow
from app.db.models import CourseRow, SavedVideo, Transcript

router = APIRouter(tags=["transcripts"])

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_SEGMENTS = 20_000


class Segment(BaseModel):
    start: float
    end: float | None = None
    text: str


class TranscriptIn(BaseModel):
    course_name: str = Field(max_length=255)
    title: str = Field(max_length=500)
    text: str
    segments: list[Segment] = Field(default_factory=list, max_length=MAX_SEGMENTS)
    model: str = Field(max_length=100)
    duration: float | None = None


class TranscriptSummary(BaseModel):
    id: int
    lms_course_id: int
    module_item_id: int
    course_name: str
    title: str
    model: str
    duration: float | None
    updated_at: datetime


class TranscriptOut(TranscriptSummary):
    text: str
    segments: list[Segment]


def _summary(t: Transcript) -> TranscriptSummary:
    return TranscriptSummary(**{k: getattr(t, k) for k in TranscriptSummary.model_fields})


async def _can_write(db, user_id: int, course_id: int, module_item_id: int) -> bool:
    """수강 중인 과목이거나, 저장해 둔 영상(지난 학기 포함)이면 쓸 수 있다."""
    if await db.scalar(select(CourseRow.id).where(CourseRow.user_id == user_id, CourseRow.lms_course_id == course_id)):
        return True
    return bool(await db.scalar(select(SavedVideo.id).where(
        SavedVideo.user_id == user_id, SavedVideo.lms_course_id == course_id, SavedVideo.module_item_id == module_item_id)))


@router.put("/courses/{course_id}/videos/{module_item_id}/transcript", response_model=TranscriptSummary)
async def save_transcript(course_id: int, module_item_id: int, request: Request, user: CurrentUser, db: Db):
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "전사 결과가 너무 큽니다 (최대 2MB).")
    try:
        data = TranscriptIn.model_validate_json(body)
    except ValidationError as e:
        raise RequestValidationError(e.errors(include_url=False)) from e
    if not await _can_write(db, user.id, course_id, module_item_id):
        raise HTTPException(404, "수강 중인 과목의 영상이 아닙니다.")
    row = await db.scalar(select(Transcript).where(
        Transcript.user_id == user.id, Transcript.lms_course_id == course_id, Transcript.module_item_id == module_item_id))
    if row is None:
        row = Transcript(user_id=user.id, lms_course_id=course_id, module_item_id=module_item_id)
        db.add(row)
    row.course_name, row.title, row.text, row.model, row.duration = (
        data.course_name, data.title, data.text, data.model, data.duration)
    row.segments = [s.model_dump() for s in data.segments]
    row.updated_at = utcnow()
    await db.commit()
    await db.refresh(row)
    return _summary(row)


@router.get("/transcripts", response_model=list[TranscriptSummary])
async def list_transcripts(user: CurrentUser, db: Db, course_id: int | None = None):
    stmt = select(Transcript).where(Transcript.user_id == user.id)
    if course_id is not None:
        stmt = stmt.where(Transcript.lms_course_id == course_id)
    stmt = stmt.order_by(Transcript.updated_at.desc())
    return [_summary(t) for t in (await db.scalars(stmt)).all()]


async def _own(db, user_id: int, transcript_id: int) -> Transcript:
    row = await db.get(Transcript, transcript_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "전사 결과를 찾을 수 없습니다.")
    return row


@router.get("/transcripts/{transcript_id}", response_model=TranscriptOut)
async def get_transcript(transcript_id: int, user: CurrentUser, db: Db):
    row = await _own(db, user.id, transcript_id)
    return TranscriptOut(**_summary(row).model_dump(), text=row.text, segments=row.segments)


@router.delete("/transcripts/{transcript_id}", status_code=204)
async def delete_transcript(transcript_id: int, user: CurrentUser, db: Db):
    await db.delete(await _own(db, user.id, transcript_id))
    await db.commit()
    return Response(status_code=204)
