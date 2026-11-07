"""Encrypted, versioned LMS credentials and token/SSO session recovery."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import LmsCredential
from app.lms import auth, personal_token
from app.lms.auth import LmsSession
from app.lms.client import LmsClient
from app.lms.errors import InvalidCredentials, LmsError, SsoExpired
from app.security import decrypt, encrypt

RELOGIN_MESSAGE = "학교 로그인이 만료되었습니다. 다시 로그인하거나 계정에서 새 개인 토큰을 등록해 주세요."
log = logging.getLogger(__name__)


class NoCredentials(LmsError):
    """No linked school account."""


class CredentialsChanged(LmsError):
    """A concurrent login or token edit superseded this operation."""


def preserve_sso(session: LmsSession, old: LmsSession) -> None:
    """Keep school SSO fallback cookies without replacing fresh Canvas/LearningX cookies."""
    session.sso_token = old.sso_token or auth.sso_token(old.cookies)
    fresh_names = {c["name"] for c in session.cookies} | {"xn_api_token"}
    session.cookies.extend(c for c in auth.sso_cookies(old.cookies) if c["name"] not in fresh_names)


async def save_credentials(db: AsyncSession, cred: LmsCredential, version: int, **values) -> bool:
    """Compare-and-swap: old workers cannot restore removed/replaced credentials."""
    result = await db.execute(update(LmsCredential).where(
        LmsCredential.user_id == cred.user_id, LmsCredential.credential_version == version,
    ).values(**values, credential_version=version + 1).execution_options(synchronize_session=False))
    await db.commit()
    # A simultaneous account deletion is also a lost CAS, not a refresh error.
    await db.scalar(select(LmsCredential).where(LmsCredential.user_id == cred.user_id)
                    .execution_options(populate_existing=True))
    return result.rowcount == 1


async def link_session(db: AsyncSession, user_id: int, student_id: str, session: LmsSession) -> LmsCredential:
    cred = await db.get(LmsCredential, user_id)
    if cred is None:
        cred = LmsCredential(user_id=user_id, student_id=student_id)
        db.add(cred)
        await db.flush()
    if not await save_credentials(db, cred, cred.credential_version,
        student_id=student_id, session_enc=encrypt(json.dumps(session.to_dict())), status="ok",
        last_error=None, last_error_at=None, last_login_at=utcnow(), next_sync_at=utcnow(),
    ):
        raise CredentialsChanged("인증 정보가 변경되었습니다. 다시 로그인해 주세요.")
    return cred


@asynccontextmanager
async def lms_client_for(db: AsyncSession, user_id: int) -> AsyncIterator[LmsClient]:
    cred = await db.get(LmsCredential, user_id)
    if cred is None:
        raise NoCredentials("LMS 계정을 먼저 등록하세요.")
    has_token = bool(cred.personal_token_enc and cred.personal_token_status == "ok")
    if (cred.status == "invalid" or not cred.session_enc) and not has_token:
        raise SsoExpired(RELOGIN_MESSAGE)
    version = cred.credential_version
    session = (LmsSession.from_dict(json.loads(decrypt(cred.session_enc))) if cred.session_enc
               else LmsSession(user_id=cred.student_id, bearer_token=""))
    token_invalid = False

    async def renew(old: LmsSession) -> LmsSession:
        nonlocal token_invalid
        token_error = None
        if has_token and not token_invalid:
            try:
                renewed = await personal_token.session_from_token(decrypt(cred.personal_token_enc), cred.student_id)
                preserve_sso(renewed, old)
                log.info("LMS recovery method=personal_token result=ok")
                return renewed
            except personal_token.PersonalTokenInvalid as exc:
                token_invalid = True
                token_error = exc
                log.info("LMS recovery method=personal_token result=invalid")
            except LmsError as exc:
                token_error = exc
                log.info("LMS recovery method=personal_token result=unavailable")
        try:
            renewed = await auth.renew_from_sso(old)
            log.info("LMS recovery method=sso result=ok")
            return renewed
        except SsoExpired:
            # A temporary token failure must not invalidate otherwise usable credentials.
            if token_error is not None and not isinstance(token_error, personal_token.PersonalTokenInvalid):
                raise LmsError("학습 연결을 복구하지 못했습니다. 잠시 후 다시 시도하세요.") from None
            raise SsoExpired(RELOGIN_MESSAGE) from None

    client = LmsClient(session, renew=renew if has_token else None)
    before = client.session
    values = {}
    try:
        if cred.status == "invalid" or not cred.session_enc:
            await client.relogin()
        yield client
        values.update(status="ok", last_error=None, last_error_at=None)
    except InvalidCredentials:
        values.update(status="invalid", session_enc=None, last_error=RELOGIN_MESSAGE, last_error_at=utcnow())
        raise
    finally:
        await client.close()
        if token_invalid:
            values["personal_token_status"] = "invalid"
        if client.session is not before and values.get("status") != "invalid":
            values.update(session_enc=encrypt(json.dumps(client.session.to_dict())), last_login_at=utcnow(),
                          status="ok", last_error=None, last_error_at=None)
        if values:
            await save_credentials(db, cred, version, **values)


async def _mark(db: AsyncSession, cred: LmsCredential, status: str, error: str) -> None:
    values = dict(status=status, last_error=error, last_error_at=utcnow())
    if status == "invalid":
        values["session_enc"] = None
    await save_credentials(db, cred, cred.credential_version, **values)
