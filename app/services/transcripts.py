"""전사 결과 조회 보조."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Transcript


async def transcript_map(db: AsyncSession, user_id: int, course_id: int | None = None) -> dict[tuple[int, int], int]:
    """(과목 ID, module_item_id) → 전사 ID"""
    stmt = select(Transcript.lms_course_id, Transcript.module_item_id, Transcript.id).where(Transcript.user_id == user_id)
    if course_id is not None:
        stmt = stmt.where(Transcript.lms_course_id == course_id)
    return {(c, m): i for c, m, i in (await db.execute(stmt)).all()}
