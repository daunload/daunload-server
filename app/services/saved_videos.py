"""강의 영상 주소를 시청기간 안에 DB에 저장해 둔다.

동기화할 때 모듈 목록에서 열린 영상을 찾아 content.php로 mp4 주소를 받아 둔다.
LMS에서 영상이 사라지거나 과목이 지난 학기로 넘어가도 행은 지우지 않는다.
학교 CDN이 주소를 계속 열어 둘지는 보장되지 않는다. 받지 못하면 다운로드 때 오류로 알린다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import SavedVideo
from app.lms.media import MediaError, course_videos, resolve_media_url
from app.lms.models import Course, Module

log = logging.getLogger(__name__)

# 한 번 동기화할 때 content.php를 호출하는 최대 횟수. 남은 영상은 다음 동기화에서 이어서 찾는다.
RESOLVE_PER_SYNC = 20


@dataclass
class ResolveBudget:
    left: int = RESOLVE_PER_SYNC


async def save_course_videos(
    db: AsyncSession, http: httpx.AsyncClient, user_id: int, course: Course, modules: list[Module], budget: ResolveBudget
) -> None:
    rows = {
        r.module_item_id: r
        for r in (await db.scalars(
            select(SavedVideo).where(SavedVideo.user_id == user_id, SavedVideo.lms_course_id == course.id)
        )).all()
    }
    for v in course_videos(course, modules):
        if not v.available:
            continue
        row = rows.get(v.module_item_id)
        if row is None:
            row = SavedVideo(user_id=user_id, lms_course_id=course.id, module_item_id=v.module_item_id, content_id=v.content_id)
            db.add(row)
            rows[v.module_item_id] = row
        elif row.content_id != v.content_id:  # 교수가 영상을 바꿨다
            row.content_id, row.media_url, row.resolve_error, row.resolved_at = v.content_id, None, None, None
        row.course_name, row.module_title, row.title = v.course_name, v.module_title, v.title
        row.module_position, row.item_position = v.module_position, v.item_position
        row.file_name, row.duration, row.size = v.file_name, v.duration, v.size

    for row in rows.values():
        if row.media_url or row.resolve_error or budget.left <= 0:
            continue
        budget.left -= 1
        try:
            row.media_url = await resolve_media_url(http, row.content_id)
            row.resolved_at = utcnow()
        except MediaError as e:  # 지원하지 않는 형식: 다시 시도하지 않는다
            row.resolve_error = str(e)
        except httpx.HTTPError as e:  # 일시 오류: 다음 동기화에서 다시
            log.warning("영상 주소 저장 실패 %s/%s: %s", course.id, row.module_item_id, e)
