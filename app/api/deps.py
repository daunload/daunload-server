from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import User
from app.security import create_access_token, decode_access_token, should_renew
from app.services.access import has_access, is_admin

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")

Db = Annotated[AsyncSession, Depends(get_db)]


RENEWED_TOKEN_HEADER = "X-Access-Token"


async def authed_user(db: Db, response: Response, token: Annotated[str, Depends(oauth2)]) -> User:
    """로그인만 확인한다. 승인 없이 열어 두는 경로(GET·DELETE /me, 로그아웃, 권한 요청)만 AuthedUser로 쓴다."""
    decoded = decode_access_token(token)
    user = await db.get(User, decoded[0]) if decoded else None
    if user is None or user.token_version != decoded[1]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.", headers={"WWW-Authenticate": "Bearer"})
    if should_renew(decoded[2]):
        # 쓰는 동안은 로그인이 끊기지 않게 한다. Response를 직접 반환하는 경로에는 붙지 않지만 다음 요청에서 갱신된다.
        response.headers[RENEWED_TOKEN_HEADER] = create_access_token(user.id, user.token_version)
    return user


AuthedUser = Annotated[User, Depends(authed_user)]

ACCESS_REQUIRED = {"code": "ACCESS_REQUIRED", "message": "관리자 승인이 필요합니다."}


def access_denied() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, ACCESS_REQUIRED)


async def current_user(user: AuthedUser) -> User:
    """로그인 + 관리자 승인. 사용자 API의 기본값이라, 새 경로도 따로 열지 않는 한 막힌다."""
    if not has_access(user):
        raise access_denied()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def admin_user(user: AuthedUser) -> User:
    if not is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "관리자 권한이 필요합니다.")
    return user


AdminUser = Annotated[User, Depends(admin_user)]


class Queue:
    """arq 작업 큐. 테스트에서는 app.state.queue를 가짜로 바꾼다."""

    def __init__(self, redis) -> None:
        self.redis = redis

    async def enqueue(self, function: str, *args: Any, job_id: str | None = None) -> None:
        await self.redis.enqueue_job(function, *args, _job_id=job_id)


def get_queue(request: Request) -> Queue:
    return request.app.state.queue


JobQueue = Annotated[Queue, Depends(get_queue)]
