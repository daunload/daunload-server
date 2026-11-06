from datetime import timedelta
from typing import Annotated

import re
import time

from fastapi import APIRouter, Form, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthedUser, CurrentUser, Db, JobQueue, is_admin
from app.api.schemas import CredentialsOut, TokenOut
from app.db.base import utcnow
from app.db.models import LmsCredential, User
from app.lms.errors import InvalidCredentials, LmsError
from app.lms import auth
from app.security import create_access_token
from app.services.access import access_retry_at, has_access
from app.services.activity import bump_daily
from app.services.lms_access import link_session

router = APIRouter()

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
    try:
        async with auth.new_http_client() as http:
            session = await auth.login(http, student_id, password)
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
    await link_session(db, user.id, student_id, session)
    # 처음 연동했거나 만료 후 다시 연동한 경우에만 동기화한다. 승인 전에는 학교 정보를 가져오지 않는다.
    if relinked and has_access(user):
        await queue.enqueue("sync_user_job", user.id, job_id=f"sync:{user.id}")
    return await _issue_token(db, user)


async def _issue_token(db: AsyncSession, user: User) -> TokenOut:
    user_id, version = user.id, user.token_version
    await _record_login(db, user_id)
    return TokenOut(access_token=create_access_token(user_id, version))


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
    """계정과 저장된 학교 로그인 세션, 동기화한 데이터를 모두 지운다."""
    await db.delete(user)
    await db.commit()


@router.get("/lms/credentials", response_model=CredentialsOut, tags=["lms"])
async def get_credentials(user: CurrentUser, db: Db):
    cred = await db.get(LmsCredential, user.id)
    if cred is None:
        raise HTTPException(404, "LMS 계정 정보가 없습니다. 다시 로그인하세요.")
    return cred


@router.post("/sync", status_code=202, tags=["lms"])
async def request_sync(user: CurrentUser, db: Db, queue: JobQueue):
    if await db.get(LmsCredential, user.id) is None:
        raise HTTPException(400, "LMS 계정 정보가 없습니다. 다시 로그인하세요.")
    await queue.enqueue("sync_user_job", user.id, job_id=f"sync:{user.id}")
    return {"queued": True}
