from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import User
from app.security import decode_access_token

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")

Db = Annotated[AsyncSession, Depends(get_db)]


async def current_user(db: Db, token: Annotated[str, Depends(oauth2)]) -> User:
    user_id = decode_access_token(token)
    user = await db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.", headers={"WWW-Authenticate": "Bearer"})
    return user


CurrentUser = Annotated[User, Depends(current_user)]


class Queue:
    """arq 작업 큐. 테스트에서는 app.state.queue를 가짜로 바꾼다."""

    def __init__(self, redis) -> None:
        self.redis = redis

    async def enqueue(self, function: str, *args: Any, job_id: str | None = None) -> None:
        await self.redis.enqueue_job(function, *args, _job_id=job_id)


def get_queue(request: Request) -> Queue:
    return request.app.state.queue


JobQueue = Annotated[Queue, Depends(get_queue)]
