"""강의 영상 주소를 시청기간 안에 DB에 저장해 둔다.

동기화할 때 모듈 목록에서 열린 영상을 찾아 content.php로 mp4 주소를 받아 둔다.
영상 정보(SavedVideoItem)는 과목의 모듈 항목마다 한 줄이라 같은 과목을 듣는 사용자들이 함께 쓰고,
사용자마다는 "볼 수 있었던 영상" 연결(SavedVideo)만 둔다. 같은 content_id의 주소를 이미 찾았으면 학교 서버를 다시 부르지 않는다.
LMS에서 영상이 사라지거나 과목이 지난 학기로 넘어가도 행은 지우지 않는다.
학교 CDN이 주소를 계속 열어 둘지는 보장되지 않는다. 받지 못하면 다운로드 때 오류로 알린다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import SavedVideo, SavedVideoItem
from app.lms.media import MediaError, course_videos, resolve_media_url
from app.lms.models import Course, Module

log = logging.getLogger(__name__)

# 한 번 동기화할 때 content.php를 호출하는 최대 횟수. 남은 영상은 다음 동기화에서 이어서 찾는다.
RESOLVE_PER_SYNC = 20


@dataclass
class ResolveBudget:
    left: int = RESOLVE_PER_SYNC


def _insert(db: AsyncSession):
    return pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


async def save_course_videos(
    db: AsyncSession, http: httpx.AsyncClient, user_id: int, course: Course, modules: list[Module], budget: ResolveBudget
) -> None:
    videos = [v for v in course_videos(course, modules) if v.available]
    if not videos:
        return
    insert = _insert(db)
    # 다른 사용자의 동기화가 같은 항목을 동시에 만들 수 있어 충돌하면 그냥 둔다(아래에서 다시 읽는다).
    await db.execute(insert(SavedVideoItem).values([
        dict(lms_course_id=course.id, module_item_id=v.module_item_id, content_id=v.content_id, course_name=v.course_name,
             module_title=v.module_title, module_position=v.module_position, item_position=v.item_position, title=v.title,
             file_name=v.file_name, duration=v.duration, size=v.size, created_at=utcnow())
        for v in videos
    ]).on_conflict_do_nothing(index_elements=[SavedVideoItem.lms_course_id, SavedVideoItem.module_item_id]))
    items = {
        r.module_item_id: r
        for r in (await db.scalars(select(SavedVideoItem).where(
            SavedVideoItem.lms_course_id == course.id,
            SavedVideoItem.module_item_id.in_([v.module_item_id for v in videos]),
        ))).all()
    }
    for v in videos:
        row = items[v.module_item_id]
        if row.content_id != v.content_id:  # 교수가 영상을 바꿨다
            row.content_id, row.media_url, row.resolve_error, row.resolved_at = v.content_id, None, None, None
            row.resolve_failed_at = None
        row.course_name, row.module_title, row.title = v.course_name, v.module_title, v.title
        row.module_position, row.item_position = v.module_position, v.item_position
        row.file_name, row.duration, row.size = v.file_name, v.duration, v.size
    await db.execute(insert(SavedVideo).values([
        dict(user_id=user_id, item_id=row.id, created_at=utcnow()) for row in items.values()
    ]).on_conflict_do_nothing(index_elements=[SavedVideo.user_id, SavedVideo.item_id]))
    # 여러 사용자가 함께 쓰는 행이다. 학교 서버를 부르기 전에 커밋해서, 같은 과목을 듣는 다른 사용자의 동기화가
    # 이 트랜잭션(삽입한 키·갱신한 행의 잠금)을 기다리지 않게 한다.
    await db.commit()

    unresolved = [row for row in items.values() if not (row.media_url or row.resolve_error)]
    if not unresolved:
        return
    # 다른 분반 등에서 같은 영상을 이미 찾았으면 그 결과를 쓴다(content_id별로 한 번에 조회).
    known: dict[str, SavedVideoItem] = {}
    for other in (await db.scalars(select(SavedVideoItem).where(
        SavedVideoItem.content_id.in_({row.content_id for row in unresolved}),
        SavedVideoItem.id.not_in([row.id for row in unresolved]),
        (SavedVideoItem.media_url.is_not(None)) | (SavedVideoItem.resolve_error.is_not(None)),
    ).order_by(SavedVideoItem.id))).all():
        known.setdefault(other.content_id, other)
    await db.commit()  # 아래 HTTP 호출 동안 트랜잭션을 열어 두지 않는다

    resolved: list[tuple[SavedVideoItem, dict]] = []
    for row in unresolved:
        if (k := known.get(row.content_id)) is not None:
            resolved.append((row, dict(media_url=k.media_url, resolve_error=k.resolve_error,
                                       resolved_at=k.resolved_at, resolve_failed_at=k.resolve_failed_at)))
            continue
        if budget.left <= 0:
            continue
        budget.left -= 1
        try:
            resolved.append((row, dict(media_url=await resolve_media_url(http, row.content_id),
                                       resolved_at=utcnow(), resolve_failed_at=None)))
        except MediaError as e:  # 지원하지 않는 형식: 다시 시도하지 않는다
            resolved.append((row, dict(resolve_error=str(e), resolve_failed_at=utcnow())))
        except httpx.HTTPError as e:  # 일시 오류: 다음 동기화에서 다시
            log.warning("영상 주소 저장 실패 %s/%s: %s", course.id, row.module_item_id, e)
    for row, values in resolved:
        for k, v in values.items():
            setattr(row, k, v)
    if resolved:
        await db.commit()


async def link_saved_video(db: AsyncSession, user_id: int, **item: object) -> SavedVideo:
    """항목(lms_course_id, module_item_id로 찾거나 새로 만든다)을 사용자에게 연결한다. 테스트·관리 스크립트용."""
    row = await db.scalar(select(SavedVideoItem).where(
        SavedVideoItem.lms_course_id == item["lms_course_id"], SavedVideoItem.module_item_id == item["module_item_id"]))
    if row is None:
        row = SavedVideoItem(**item)
        db.add(row)
        await db.flush()
    link = SavedVideo(user_id=user_id, item_id=row.id)
    db.add(link)
    await db.flush()
    return link
