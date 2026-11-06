from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import AuthedUser, Db
from app.services.access import access_retry_at, request_access

router = APIRouter(prefix="/access", tags=["access"])


class AccessRequestOut(BaseModel):
    access_status: str
    access_requested_at: datetime | None
    access_retry_at: datetime | None


@router.post("/request", response_model=AccessRequestOut)
async def post_request(user: AuthedUser, db: Db):
    """승인 전에도 열려 있다. 요청은 관리자에게 알려진다."""
    await request_access(db, user)
    return AccessRequestOut(access_status=user.access_status, access_requested_at=user.access_requested_at,
                            access_retry_at=access_retry_at(user))
