from datetime import timedelta
from typing import Annotated

import json
import logging
import re
import time

from fastapi import APIRouter, Form, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthedUser, CurrentUser, Db, JobQueue, is_admin
from app.api.schemas import CredentialsOut, TokenOut, PersonalTokenIn
from app.db.base import utcnow
from app.db.models import LmsCredential, User
from app.lms.errors import InvalidCredentials, LmsError
from app.lms import auth, personal_token
from app.security import create_access_token, encrypt, decrypt
from app.services.access import access_retry_at, has_access
from app.services.activity import bump_daily
from app.services.lms_access import (
    CredentialsChanged, link_session, managed_token_values, needs_managed_token, preserve_sso, save_credentials,
)

router = APIRouter()
log = logging.getLogger(__name__)

# 로그인은 매번 학교 SSO로 확인 요청이 가므로, 학번별·IP별 실패 횟수를 제한한다.
STUDENT_ID = re.compile(r"\d{8,10}")
MAX_FAILURES = 5  # 학번별
MAX_IP_FAILURES = 20  # IP별(여러 학번에 비밀번호를 대입하는 것을 막는다)
FAILURE_WINDOW = timedelta(minutes=10)


class AttemptLimiter:
    """키(학번, IP)별 최근 시도 시각을 센다.

    LMS 확인 전에 자리를 먼저 잡으므로(acquire) 동시 요청도 한도를 넘지 못한다.
    성공하거나 학교 서버 오류면 자리를 돌려주고(release), 틀린 비밀번호만 실패로 남는다.
    """

    def __init__(self, clock=time.monotonic, window: float = FAILURE_WINDOW.total_seconds(), max_keys: int = 10_000):
        self._clock, self._window, self._max_keys = clock, window, max_keys
        self._hits: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._hits.get(key, ()) if now - t < self._window]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def blocked(self, limits: dict[str, int]) -> bool:
        now = self._clock()
        return any(len(self._recent(k, now)) >= n for k, n in limits.items())

    def acquire(self, limits: dict[str, int]) -> float | None:
        """모든 키가 한도 아래면 시도를 기록하고 표식을 돌려준다. 하나라도 차 있으면 None."""
        now = self._clock()
        if len(self._hits) >= self._max_keys:  # 오래된 키를 비워 메모리가 끝없이 늘지 않게
            for key in list(self._hits):
                self._recent(key, now)
        if any(len(self._recent(k, now)) >= n for k, n in limits.items()):
            return None
        for key in limits:
            self._hits.setdefault(key, []).append(now)
        return now

    def release(self, limits: dict[str, int], mark: float) -> None:
        for key in limits:
            hits = self._hits.get(key, [])
            if mark in hits:
                hits.remove(mark)


limiter = AttemptLimiter()


CONSENT_REQUIRED = {
    "code": "consent_required",
    "message": "처음 로그인합니다. 학교 로그인 세션 보관과 자동 재생 관련 안내에 동의해 주세요.",
}


@router.post("/auth/login", response_model=TokenOut, tags=["auth"])
async def login(
    request: Request,
    db: Db,
    queue: JobQueue,
    username: Annotated[str, Form(description="학번")],
    password: Annotated[str, Form(description="통합 로그인 비밀번호(학교 로그인에만 쓰고 저장하지 않는다)")],
    agree_terms: Annotated[bool, Form(description="첫 로그인 때 필요한 동의")] = False,
):
    """학번과 학교 비밀번호로 로그인한다.

    매번 학교 SSO로 실제 로그인해 본인임을 확인한다. 비밀번호는 이 요청 안에서만 쓰고 DB·로그에 남기지 않으며,
    발급받은 학습 세션(SSO 토큰 포함)만 암호화해 저장한다. 세션이 만료되면 서버가 SSO 토큰으로 다시 발급받는다.
    """
    student_id = username.strip()
    if not STUDENT_ID.fullmatch(student_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "학번 또는 비밀번호가 틀렸습니다.")
    limits = {f"id:{student_id}": MAX_FAILURES, f"ip:{request.client.host if request.client else '-'}": MAX_IP_FAILURES}
    if limiter.blocked(limits):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "로그인 실패가 많습니다. 10분 뒤에 다시 시도하세요.")
    user = await db.scalar(select(User).where(User.student_id == student_id))
    if user is None and not agree_terms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, CONSENT_REQUIRED)
    mark = limiter.acquire(limits)
    if mark is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "로그인 실패가 많습니다. 10분 뒤에 다시 시도하세요.")
    existing = await db.get(LmsCredential, user.id) if user else None
    # 승인 전에는 학교 계정에 아무것도 만들지 않는다(승인 후 다음 로그인에 발급).
    issue_token = user is not None and has_access(user) and needs_managed_token(existing)
    managed = None
    # SSU Canvas는 비밀번호 로그인 세션에서만 토큰을 발급·폐기할 수 있으므로 요청 끝까지 이 세션을 유지한다.
    async with auth.new_http_client() as http:
        try:
            session = await auth.login(http, student_id, password)
            if issue_token:
                managed = await _issue_managed_token(http, student_id)
        except InvalidCredentials as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "학번 또는 비밀번호가 틀렸습니다.") from e
        except LmsError as e:
            limiter.release(limits, mark)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LMS에 로그인하지 못했습니다: {e}") from e
        finally:
            del password
        limiter.release(limits, mark)

        if user is None:
            user = User(student_id=student_id, consent_at=utcnow())
            db.add(user)
            await db.flush()
        cred = await db.get(LmsCredential, user.id)
        relinked = cred is None or cred.status != "ok"
        try:
            cred = await link_session(db, user.id, student_id, session)
        except CredentialsChanged as e:
            if managed:
                await personal_token.revoke_token(http, managed.token_id)
            raise HTTPException(409, str(e)) from None
        if managed:
            old_id = cred.personal_token_id if cred.personal_token_managed else None
            if await save_credentials(db, cred, cred.credential_version, **managed_token_values(managed)):
                if old_id:
                    await personal_token.revoke_token(http, old_id)  # 더 이상 쓰지 않는 이전 서버 관리 토큰
            else:
                await personal_token.revoke_token(http, managed.token_id)
            del managed
    # 처음 연동했거나 만료 후 다시 연동한 경우에만 동기화한다. 승인 전에는 학교 정보를 가져오지 않는다.
    if relinked and has_access(user):
        await queue.enqueue("sync_user_job", user.id, job_id=f"sync:{user.id}")
    return await _issue_token(db, user)


async def _issue_managed_token(http, student_id: str):
    """실패해도 로그인은 계속한다(학교가 토큰 발급을 막았거나 일시 장애). 이 경우 SSO 복구와 수동 등록이 남는다."""
    try:
        managed = await personal_token.issue_managed_token(http, student_id)
        log.info("managed token issue result=ok")
        return managed
    except personal_token.ManagedTokenUnavailable:
        log.info("managed token issue result=restricted")
    except LmsError:
        log.info("managed token issue result=unavailable")
    return None


async def _issue_token(db: AsyncSession, user: User) -> TokenOut:
    user_id, version = user.id, user.token_version
    await _record_login(db, user_id)
    return TokenOut(access_token=create_access_token(user_id, version))


@router.post("/auth/logout", status_code=204, tags=["auth"])
async def logout(user: AuthedUser, db: Db):
    """이 사용자의 모든 기기 토큰(다운로드 완료 영수증 포함)을 폐기한다."""
    await db.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1)
                     .execution_options(synchronize_session=False))
    await db.commit()


@router.get("/me", tags=["auth"])
async def me(user: AuthedUser):
    """승인 전에도 열려 있다. 화면은 access_status를 보고 앱 또는 권한 화면을 띄운다."""
    return {
        "student_id": user.student_id, "consent_at": user.consent_at, "is_admin": is_admin(user),
        "access_status": "approved" if has_access(user) else user.access_status,
        "access_requested_at": user.access_requested_at,
        "access_retry_at": access_retry_at(user),
    }


async def _record_login(db: AsyncSession, user_id: int) -> None:
    await db.execute(update(User).where(User.id == user_id).values(
        last_login_at=utcnow(), login_count=User.login_count + 1,
    ))
    await bump_daily(db, user_id, logins=1)
    await db.commit()


@router.delete("/me", status_code=204, tags=["auth"])
async def delete_me(user: AuthedUser, db: Db):
    """계정과 저장된 학교 로그인 세션, 동기화한 데이터를 모두 지운다.
    서버가 만든 Canvas 토큰은 비밀번호 로그인 세션 없이 폐기할 수 없으므로, Canvas 설정에서 지우도록 화면에서 안내한다."""
    await db.delete(user)
    await db.commit()


@router.get("/lms/credentials", response_model=CredentialsOut, tags=["lms"])
async def get_credentials(user: CurrentUser, db: Db):
    cred = await db.get(LmsCredential, user.id)
    if cred is None:
        raise HTTPException(404, "LMS 계정 정보가 없습니다. 다시 로그인하세요.")
    return cred


@router.put("/lms/access-token", response_model=CredentialsOut, tags=["lms"])
async def register_personal_token(body: PersonalTokenIn, user: CurrentUser, db: Db, queue: JobQueue):
    cred = await db.get(LmsCredential, user.id)
    if cred is None:
        raise HTTPException(400, "학교 계정에 먼저 로그인하세요.")
    version = cred.credential_version
    token = body.access_token.get_secret_value().strip()
    if not token or len(token) > 4096 or any(c.isspace() or ord(c) < 32 for c in token):
        raise HTTPException(400, "올바른 개인 액세스 토큰을 입력하세요.")
    limits = {f"token:{user.id}": MAX_FAILURES}
    mark = limiter.acquire(limits)
    if mark is None:
        raise HTTPException(429, "토큰 확인 요청이 많습니다. 10분 뒤에 다시 시도하세요.")
    try:
        session = await personal_token.session_from_token(token, user.student_id)
    except InvalidCredentials as e:
        raise HTTPException(400, str(e)) from None
    except LmsError as e:
        limiter.release(limits, mark)
        raise HTTPException(502, str(e)) from None
    limiter.release(limits, mark)
    if cred.session_enc:
        old = auth.LmsSession.from_dict(json.loads(decrypt(cred.session_enc)))
        preserve_sso(session, old)
    saved = await save_credentials(db, cred, version,
        personal_token_enc=encrypt(token), personal_token_status="ok", personal_token_registered_at=utcnow(),
        personal_token_managed=False, personal_token_id=None, personal_token_expires_at=None,
        session_enc=encrypt(json.dumps(session.to_dict())), status="ok", last_error=None, last_error_at=None,
        last_login_at=utcnow(), next_sync_at=utcnow(),
    )
    del token
    if not saved:
        raise HTTPException(409, "인증 정보가 변경되었습니다. 새로고침한 뒤 다시 등록하세요.")
    await queue.enqueue("sync_user_job", user.id, job_id=f"sync:{user.id}")
    return cred


@router.delete("/lms/access-token", response_model=CredentialsOut, tags=["lms"])
async def remove_personal_token(user: CurrentUser, db: Db):
    cred = await db.get(LmsCredential, user.id)
    if cred is None:
        raise HTTPException(404, "LMS 계정 정보가 없습니다.")
    # SSU Canvas는 토큰에서 만든 세션으로 폐기할 수 없다. 서비스의 암호문만 지우고, Canvas 쪽 삭제는 화면에서 안내한다.
    if not await save_credentials(db, cred, cred.credential_version,
        personal_token_enc=None, personal_token_status="none", personal_token_registered_at=None,
        personal_token_managed=False, personal_token_id=None, personal_token_expires_at=None,
    ):
        raise HTTPException(409, "인증 정보가 변경되었습니다. 새로고침한 뒤 다시 제거하세요.")
    return cred


@router.post("/sync", status_code=202, tags=["lms"])
async def request_sync(user: CurrentUser, db: Db, queue: JobQueue):
    if await db.get(LmsCredential, user.id) is None:
        raise HTTPException(400, "LMS 계정 정보가 없습니다. 다시 로그인하세요.")
    await queue.enqueue("sync_user_job", user.id, job_id=f"sync:{user.id}")
    return {"queued": True}
