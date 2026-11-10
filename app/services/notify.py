"""알림 발송 (Discord 웹훅, Telegram 봇). notification_log로 중복 발송을 막는다."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.config import get_settings
from app.db.models import NotificationChannel, NotificationLog, TodoRow, User
from app.security import decrypt

log = logging.getLogger(__name__)
# 웹훅 주소에는 토큰이 들어 있다. httpx는 INFO로 요청 주소를 그대로 남기므로 경고 이상만 남긴다.
logging.getLogger("httpx").setLevel(logging.WARNING)


def _safe_error(e: Exception) -> str:
    """로그용 오류 설명. httpx 오류 메시지에는 웹훅 주소(토큰)가 들어 있으므로 종류와 상태 코드만 남긴다."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"{type(e).__name__} {e.response.status_code}"
    return type(e).__name__

REMINDER_HOURS = (24, 3)
KIND_LABEL = {"video": "영상", "assignment": "과제", "quiz": "퀴즈"}


async def send_to_channel(channel: NotificationChannel, text: str, http: httpx.AsyncClient | None = None) -> None:
    target = decrypt(channel.target_enc)
    own = http is None
    http = http or httpx.AsyncClient(timeout=10)
    try:
        if channel.kind == "discord":
            resp = await http.post(target, json={"content": text[:2000]})
        elif channel.kind == "telegram":
            token, chat_id = target.split("|", 1)
            resp = await http.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text[:4000]})
        else:
            raise ValueError(f"알 수 없는 채널: {channel.kind}")
        resp.raise_for_status()
    finally:
        if own:
            await http.aclose()


async def notify_user(db: AsyncSession, user_id: int, text: str, key: str | None = None) -> bool:
    """key가 있으면 한 번만 보낸다. 실제로 보냈으면 True. 승인되지 않은 사용자에게는 보내지 않는다."""
    from app.services.access import user_has_access  # access가 notify를 가져오므로 순환을 피한다

    if not await user_has_access(db, user_id):
        return False  # 키도 남기지 않는다. 승인된 뒤에는 같은 알림을 보낼 수 있다.
    if key is not None:
        db.add(NotificationLog(user_id=user_id, key=key))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return False
    channels = (
        await db.scalars(
            select(NotificationChannel).where(NotificationChannel.user_id == user_id, NotificationChannel.enabled)
        )
    ).all()
    for ch in channels:
        try:
            await send_to_channel(ch, text)
        except Exception as e:  # 한 채널 실패가 다른 채널을 막지 않게
            log.warning("알림 발송 실패 user=%s channel=%s: %s", user_id, ch.id, _safe_error(e))
    return True


async def notify_admins_of_request(db: AsyncSession, user: User) -> None:
    """승인 요청을 모든 관리자의 사용 중인 디스코드 채널로 한 번씩 알린다. 실패는 기록만 한다."""
    text = f"[승인 요청] 학번 {user.student_id} 님이 서비스 사용 승인을 요청했어요. 관리자 화면에서 확인해 주세요."
    channels = (await db.scalars(
        select(NotificationChannel).join(User, User.id == NotificationChannel.user_id).where(
            User.student_id.in_(get_settings().admin_student_ids),
            NotificationChannel.kind == "discord", NotificationChannel.enabled).order_by(NotificationChannel.id)
    )).all()
    for ch in channels:
        try:
            await send_to_channel(ch, text)
        except Exception as e:
            log.warning("승인 요청 알림 실패 channel=%s: %s", ch.id, _safe_error(e))


# notify_user는 중복이면 롤백하고, 롤백은 세션의 ORM 객체를 모두 만료시킨다.
# 그 뒤 객체 속성을 읽으면 비동기 세션에서 지연 로딩(MissingGreenlet)이 일어나므로,
# 보낼 내용을 먼저 문자열로 뽑아 둔 뒤 보낸다.


async def notify_deadlines(db: AsyncSession, user_id: int, now: datetime | None = None) -> int:
    """마감 24시간 전, 3시간 전 알림. 보낸 개수를 반환한다."""
    now = now or utcnow()
    rows = (
        await db.scalars(
            select(TodoRow).where(
                TodoRow.user_id == user_id,
                TodoRow.active,
                TodoRow.deadline > now,
                TodoRow.deadline <= now + timedelta(hours=max(REMINDER_HOURS)),
            )
        )
    ).all()
    messages = []
    for row in rows:
        hours = min(h for h in REMINDER_HOURS if row.deadline - now <= timedelta(hours=h))
        label = KIND_LABEL.get(row.kind, row.kind)
        text = f"⏰ [{row.course_name}] {label} 마감 {hours}시간 이내: {row.title} (마감 {row.deadline.astimezone():%m/%d %H:%M})"
        messages.append((text, f"deadline:{row.kind}:{row.lms_item_id}:{hours}"))
    sent = 0
    for text, key in messages:
        if await notify_user(db, user_id, text, key=key):
            sent += 1
    return sent
