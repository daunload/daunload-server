"""다운로드 이력. 링크를 발급할 때 기록한다(브라우저 저장 완료는 알 수 없다)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import DownloadRecord


def video_key(module_item_id: int) -> str:
    return f"v{module_item_id}"


async def record_download(db: AsyncSession, user_id: int, course_id: int, item_key: str, kind: str, title: str) -> None:
    row = await db.scalar(select(DownloadRecord).where(
        DownloadRecord.user_id == user_id, DownloadRecord.lms_course_id == course_id, DownloadRecord.item_key == item_key))
    now = utcnow()
    if row is None:
        db.add(DownloadRecord(user_id=user_id, lms_course_id=course_id, item_key=item_key, kind=kind, title=title,
                              count=1, first_at=now, last_at=now))
    else:
        row.count, row.last_at, row.title = row.count + 1, now, title
    await db.commit()


async def downloaded_map(db: AsyncSession, user_id: int, course_id: int | None = None) -> dict[tuple[int, str], datetime]:
    stmt = select(DownloadRecord.lms_course_id, DownloadRecord.item_key, DownloadRecord.last_at).where(
        DownloadRecord.user_id == user_id)
    if course_id is not None:
        stmt = stmt.where(DownloadRecord.lms_course_id == course_id)
    return {(c, k): t for c, k, t in (await db.execute(stmt)).all()}
