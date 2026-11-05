"""사용자별 일별 활동 기록(로그인·다운로드 횟수). 날짜는 설정된 시간대의 자정을 기준으로 한다."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import utcnow
from app.db.models import UserDailyActivity


def local_day(moment: datetime | None = None) -> date:
    return (moment or utcnow()).astimezone(ZoneInfo(get_settings().timezone)).date()


async def bump_daily(db: AsyncSession, user_id: int, logins: int = 0, downloads: int = 0) -> None:
    """오늘 줄에 횟수를 더한다. 동시 요청에도 안전하게 upsert하며 커밋은 호출자가 한다."""
    insert = postgresql.insert if db.bind.dialect.name == "postgresql" else sqlite.insert
    stmt = insert(UserDailyActivity).values(user_id=user_id, day=local_day(), logins=logins, downloads=downloads)
    await db.execute(stmt.on_conflict_do_update(
        index_elements=["user_id", "day"],
        set_={"logins": UserDailyActivity.logins + stmt.excluded.logins,
              "downloads": UserDailyActivity.downloads + stmt.excluded.downloads},
    ))


async def prune_daily(db: AsyncSession, keep_days: int = 90) -> int:
    """오늘로부터 keep_days일보다 오래된 줄을 지우고 지운 개수를 돌려준다."""
    result = await db.execute(delete(UserDailyActivity).where(UserDailyActivity.day < local_day() - timedelta(days=keep_days)))
    await db.commit()
    return result.rowcount
