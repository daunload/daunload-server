from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, Db
from app.api.schemas import ChannelIn, ChannelOut
from app.db.models import NotificationChannel
from app.security import encrypt
from app.services.notify import send_to_channel

router = APIRouter(prefix="/notifications/channels", tags=["notifications"])


@router.post("", response_model=ChannelOut, status_code=201)
async def add_channel(body: ChannelIn, user: CurrentUser, db: Db):
    if body.kind == "discord":
        if not body.webhook_url or not body.webhook_url.startswith("https://discord.com/api/webhooks/"):
            raise HTTPException(422, "Discord 웹훅 URL(https://discord.com/api/webhooks/...)이 필요합니다.")
        target = body.webhook_url
    else:
        if not body.bot_token or not body.chat_id:
            raise HTTPException(422, "Telegram에는 bot_token과 chat_id가 필요합니다.")
        target = f"{body.bot_token}|{body.chat_id}"
    channel = NotificationChannel(user_id=user.id, kind=body.kind, target_enc=encrypt(target))
    db.add(channel)
    await db.commit()
    return channel


@router.get("", response_model=list[ChannelOut])
async def list_channels(user: CurrentUser, db: Db):
    return (await db.scalars(select(NotificationChannel).where(NotificationChannel.user_id == user.id))).all()


async def _own(db, user_id: int, channel_id: int) -> NotificationChannel:
    ch = await db.get(NotificationChannel, channel_id)
    if ch is None or ch.user_id != user_id:
        raise HTTPException(404, "알림 채널을 찾을 수 없습니다.")
    return ch


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(channel_id: int, user: CurrentUser, db: Db):
    await db.delete(await _own(db, user.id, channel_id))
    await db.commit()


@router.post("/{channel_id}/test", status_code=204)
async def test_channel(channel_id: int, user: CurrentUser, db: Db):
    try:
        await send_to_channel(await _own(db, user.id, channel_id), "✅ LMS 알림 테스트 메시지입니다.")
    except Exception as e:
        raise HTTPException(502, f"발송 실패: {e}") from e
