"""재생 워커가 실제로 튼 영상 주소를 기록한다(조회 전용, 다운로드에는 쓰지 않는다).

같은 영상(content_id)은 한 줄만 둔다. 다시 재생을 시도하면 주소·마지막 시각·사용자·시도 횟수만 바뀐다.
동시에 두 사용자가 같은 영상을 틀어도 content_id 고유 제약과 INSERT … ON CONFLICT로 한 줄로 합친다.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import VideoUrl
from app.lms.todo import VideoTodo


async def record_video_url(db: AsyncSession, user_id: int, video: VideoTodo, content_id: str, media_url: str) -> None:
    now = utcnow()
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    stmt = insert(VideoUrl).values(
        content_id=content_id, media_url=media_url, lms_course_id=video.course_id, module_item_id=video.module_item_id,
        course_name=video.course_name[:255], title=video.title[:500], first_user_id=user_id, last_user_id=user_id,
        attempts=1, first_seen_at=now, last_seen_at=now,
    )
    await db.execute(stmt.on_conflict_do_update(index_elements=[VideoUrl.content_id], set_={
        "media_url": stmt.excluded.media_url, "last_user_id": stmt.excluded.last_user_id,
        "last_seen_at": stmt.excluded.last_seen_at, "attempts": VideoUrl.attempts + 1,
    }))
    await db.commit()
