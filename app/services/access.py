"""관리자 승인 여부. 사용 권한 판단은 모두 여기를 거친다."""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import utcnow
from app.db.models import PlaybackBatch, User
from app.services.notify import notify_admins_of_request

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


# 요청 상태 전이. 허용되는 현재 상태 -> 바뀌는 상태.
Action = Literal["approve", "reject", "revoke"]
DECISIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "approve": (("pending", "none", "rejected", "revoked"), "approved"),
    "reject": (("pending",), "rejected"),
    "revoke": (("approved",), "revoked"),
}


async def _transition(db: AsyncSession, user: User, expected: str, **values) -> bool:
    """현재 상태가 expected일 때만 바꾼다. 동시에 다른 처리가 먼저 끝났으면 False."""
    result = await db.execute(
        update(User).where(User.id == user.id, User.access_status == expected).values(**values),
        execution_options={"synchronize_session": False})
    if result.rowcount == 0:
        await db.rollback()
        return False
    return True


async def request_access(db: AsyncSession, user: User) -> None:
    """none, revoked, 24시간이 지난 rejected -> pending. 이미 pending이면 아무것도 바꾸지 않는다."""
    now = utcnow()
    if has_access(user):  # 승인된 사용자와 관리자
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "이미 승인된 계정입니다.")
    if user.access_status == "pending":
        return
    retry_at = access_retry_at(user)
    if retry_at is not None and now < retry_at:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, {
            "message": "거절된 뒤 24시간이 지나야 다시 요청할 수 있습니다.", "access_retry_at": retry_at.isoformat()})
    previous = user.access_status
    if not await _transition(db, user, previous, access_status="pending", access_previous_status=previous,
                             access_requested_at=now):
        await db.refresh(user)  # 그 사이 상태가 바뀌었다
        if user.access_status == "pending":
            return
        raise HTTPException(status.HTTP_409_CONFLICT, "상태가 바뀌었습니다. 다시 확인해 주세요.")
    await db.commit()
    await db.refresh(user)
    await notify_admins_of_request(db, user)


async def decide_access(db: AsyncSession, target: User, action: Action, admin: User) -> bool:
    """관리자의 결정. 승인으로 바뀌었으면 True(호출한 쪽이 전체 동기화를 큐에 넣는다)."""
    if is_admin(target):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "관리자 계정은 바꿀 수 없습니다.")
    allowed, new_status = DECISIONS[action]
    current = target.access_status
    conflict = HTTPException(status.HTTP_409_CONFLICT, "현재 상태에서는 할 수 없는 결정입니다.")
    if current not in allowed:
        raise conflict
    values = {"access_status": new_status, "access_decided_at": utcnow(), "access_decided_by": admin.student_id}
    if action == "reject":
        values["access_reject_count"] = User.access_reject_count + 1
    if not await _transition(db, target, current, **values):
        raise conflict
    if action == "revoke":  # 대기·진행 중인 재생은 멈춘다
        await db.execute(update(PlaybackBatch).where(
            PlaybackBatch.user_id == target.id, PlaybackBatch.status.in_(("queued", "running"))
        ).values(cancel_requested=True))
    await db.commit()
    await db.refresh(target)
    return action == "approve"
