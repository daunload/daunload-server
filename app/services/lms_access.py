"""사용자별 LMS 접근: 학습 세션 저장, 세션 캐시 재사용, SSO 재발급.

학교 비밀번호는 저장하지 않는다. 로그인할 때 받은 세션(SSO 토큰 포함)만 암호화해 보관하고,
학습 세션이 만료되면 LmsClient가 SSO 토큰으로 다시 발급받는다. SSO도 만료되면 사용자가 다시 로그인해야 한다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import LmsCredential
from app.lms.auth import LmsSession
from app.lms.client import LmsClient
from app.lms.errors import InvalidCredentials, LmsError, SsoExpired
from app.security import decrypt, encrypt

RELOGIN_MESSAGE = "학교 로그인이 만료되었습니다. 다시 로그인해 주세요."


class NoCredentials(LmsError):
    """LMS 계정이 등록되지 않았다."""


async def link_session(db: AsyncSession, user_id: int, student_id: str, session: LmsSession) -> LmsCredential:
    """학교 로그인으로 받은 세션을 저장한다(커밋 포함). 비밀번호는 받지 않는다."""
    cred = await db.get(LmsCredential, user_id)
    if cred is None:
        cred = LmsCredential(user_id=user_id, student_id=student_id)
        db.add(cred)
    cred.student_id = student_id
    cred.session_enc = encrypt(json.dumps(session.to_dict()))
    cred.status = "ok"
    cred.last_error, cred.last_error_at = None, None
    cred.last_login_at = utcnow()
    cred.next_sync_at = utcnow()
    await db.commit()
    return cred


@asynccontextmanager
async def lms_client_for(db: AsyncSession, user_id: int) -> AsyncIterator[LmsClient]:
    """저장된 세션으로 클라이언트를 만든다. 학습 세션이 만료되면 클라이언트가 SSO로 다시 발급받고,
    끝날 때 바뀐 세션을 다시 저장한다. SSO까지 만료되면 invalid로 표시하고 세션을 지운다."""
    cred = await db.get(LmsCredential, user_id)
    if cred is None:
        raise NoCredentials("LMS 계정을 먼저 등록하세요.")
    if cred.status == "invalid" or not cred.session_enc:
        raise SsoExpired(RELOGIN_MESSAGE)
    client = LmsClient(LmsSession.from_dict(json.loads(decrypt(cred.session_enc))))

    before = client.session
    try:
        yield client
    except InvalidCredentials as e:
        await _mark(db, cred, "invalid", RELOGIN_MESSAGE if isinstance(e, SsoExpired) else str(e))
        raise
    finally:
        await client.close()
    if client.session is not before:
        cred.session_enc = encrypt(json.dumps(client.session.to_dict()))
        cred.last_login_at = utcnow()
    cred.status, cred.last_error, cred.last_error_at = "ok", None, None
    await db.commit()


async def _mark(db: AsyncSession, cred: LmsCredential, status: str, error: str) -> None:
    cred.status = status
    cred.last_error = error
    cred.last_error_at = utcnow()
    if status == "invalid":
        cred.session_enc = None
    await db.commit()
