"""관리자 승인 여부. 사용 권한 판단은 모두 여기를 거친다."""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User

ACCESS_STATUSES = ("none", "pending", "approved", "rejected", "revoked")
RETRY_AFTER = timedelta(hours=24)  # 거절된 뒤 다시 요청할 수 있을 때까지


def is_admin(user: User) -> bool:
    return user.student_id in get_settings().admin_student_ids


def has_access(user: User) -> bool:
    """관리자는 저장된 상태와 관계없이 항상 승인된 것으로 본다."""
    return is_admin(user) or user.access_status == "approved"


def access_retry_at(user: User) -> datetime | None:
    """거절된 사용자가 다시 요청할 수 있는 시각. 거절 상태가 아니면 None."""
    if has_access(user) or user.access_status != "rejected" or user.access_decided_at is None:
        return None
    return user.access_decided_at + RETRY_AFTER


async def user_has_access(db: AsyncSession, user_id: int) -> bool:
    """서명 링크처럼 로그인 토큰 없이 들어온 요청의 주인을 확인한다. 없는 사용자는 False."""
    user = await db.get(User, user_id)
    return user is not None and has_access(user)
