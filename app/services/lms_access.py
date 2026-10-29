"""사용자별 LMS 접근: 자격증명 검증·저장, 세션 캐시 재사용."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import LmsCredential
from app.lms import auth
from app.lms.auth import LmsSession
from app.lms.client import LmsClient
from app.lms.errors import InvalidCredentials, LmsError
from app.security import decrypt, encrypt


class NoCredentials(LmsError):
    """LMS 계정이 등록되지 않았다."""


async def save_credentials(db: AsyncSession, user_id: int, student_id: str, password: str) -> LmsCredential:
    """실제로 로그인해 본 뒤 저장한다. 틀리면 InvalidCredentials."""
    async with auth.new_http_client() as http:
        session = await auth.login(http, student_id, password)
    cred = await db.get(LmsCredential, user_id)
    if cred is None:
        cred = LmsCredential(user_id=user_id, student_id=student_id, password_enc="")
        db.add(cred)
    cred.student_id = student_id
    cred.password_enc = encrypt(password)
    cred.session_enc = encrypt(json.dumps(session.to_dict()))
    cred.status = "ok"
    cred.last_error = None
    cred.last_login_at = utcnow()
    cred.next_sync_at = utcnow()
    await db.commit()
    return cred


@asynccontextmanager
async def lms_client_for(db: AsyncSession, user_id: int) -> AsyncIterator[LmsClient]:
    """캐시된 세션으로 클라이언트를 만든다. 만료되면 클라이언트가 저장된 비밀번호로 재로그인하고,
    끝날 때 바뀐 세션을 다시 저장한다."""
    cred = await db.get(LmsCredential, user_id)
    if cred is None:
        raise NoCredentials("LMS 계정을 먼저 등록하세요.")
    if cred.status == "invalid":
        raise InvalidCredentials("저장된 LMS 비밀번호가 틀렸습니다. 다시 등록하세요.")
    password = decrypt(cred.password_enc)

    try:
        if cred.session_enc:
            session = LmsSession.from_dict(json.loads(decrypt(cred.session_enc)))
            client = LmsClient(session, password=password)
        else:
            client = await LmsClient.login(cred.student_id, password)
            cred.last_login_at = utcnow()
    except InvalidCredentials as e:
        await _mark(db, cred, "invalid", str(e))
        raise

    before = client.session
    try:
        yield client
    except InvalidCredentials as e:
        await _mark(db, cred, "invalid", str(e))
        raise
    finally:
        await client.close()
    if client.session is not before:
        cred.session_enc = encrypt(json.dumps(client.session.to_dict()))
        cred.last_login_at = utcnow()
    cred.status, cred.last_error = "ok", None
    await db.commit()


async def _mark(db: AsyncSession, cred: LmsCredential, status: str, error: str) -> None:
    cred.status = status
    cred.last_error = error
    if status == "invalid":
        cred.session_enc = None
    await db.commit()
