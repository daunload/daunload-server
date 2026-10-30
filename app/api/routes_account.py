from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, Db, JobQueue, is_admin
from app.api.schemas import CredentialsOut, TokenOut
from app.db.base import utcnow
from app.db.models import LmsCredential, User
from app.lms.errors import InvalidCredentials, LmsError
from app.security import create_access_token, hash_password, verify_password
from app.services.lms_access import save_credentials

router = APIRouter()

# 틀린 비밀번호는 매번 학교 SSO로 확인 요청이 가므로, 학번별 실패 횟수를 제한한다.
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=10)
_failures: dict[str, list[datetime]] = {}


def _check_throttle(student_id: str) -> None:
    now = utcnow()
    recent = [t for t in _failures.get(student_id, []) if now - t < FAILURE_WINDOW]
    _failures[student_id] = recent
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "로그인 실패가 많습니다. 10분 뒤에 다시 시도하세요.")


def _record_failure(student_id: str) -> None:
    _failures.setdefault(student_id, []).append(utcnow())


CONSENT_REQUIRED = {
    "code": "consent_required",
    "message": "처음 로그인합니다. 학교 비밀번호 보관과 자동 재생 관련 안내에 동의해 주세요.",
}


@router.post("/auth/login", response_model=TokenOut, tags=["auth"])
async def login(
    db: Db,
    queue: JobQueue,
    username: Annotated[str, Form(description="학번")],
    password: Annotated[str, Form(description="숭실 통합 로그인 비밀번호")],
    agree_terms: Annotated[bool, Form(description="첫 로그인 때 필요한 동의")] = False,
):
    """학번과 학교 비밀번호로 로그인한다.

    저장된 해시와 맞으면 LMS에 접속하지 않는다. 처음이거나 비밀번호가 바뀐 경우에는
    실제 LMS 로그인으로 확인하고 계정 정보(암호화)를 저장·갱신한다.
    """
    student_id = username.strip()
    _check_throttle(student_id)
    user = await db.scalar(select(User).where(User.student_id == student_id))
    if user is not None and verify_password(password, user.password_hash):
        await _record_login(db, user.id)
        return TokenOut(access_token=create_access_token(user.id))

    if user is None:
        if not agree_terms:
            raise HTTPException(status.HTTP_403_FORBIDDEN, CONSENT_REQUIRED)
        user = User(student_id=student_id, password_hash="", consent_at=utcnow())
        db.add(user)
        await db.flush()
    try:
        user.password_hash = hash_password(password)
        await save_credentials(db, user.id, student_id, password)  # LMS 로그인 확인 후 함께 커밋
    except InvalidCredentials as e:
        await db.rollback()
        _record_failure(student_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "학번 또는 비밀번호가 틀렸습니다.") from e
    except LmsError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LMS에 로그인하지 못했습니다: {e}") from e
    await _record_login(db, user.id)
    await queue.enqueue("sync_user_job", user.id, job_id=f"sync:{user.id}")
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/me", tags=["auth"])
async def me(user: CurrentUser):
    return {"student_id": user.student_id, "consent_at": user.consent_at, "is_admin": is_admin(user)}


async def _record_login(db: AsyncSession, user_id: int) -> None:
    await db.execute(update(User).where(User.id == user_id).values(
        last_login_at=utcnow(), login_count=User.login_count + 1,
    ))
    await db.commit()


@router.delete("/me", status_code=204, tags=["auth"])
async def delete_me(user: CurrentUser, db: Db):
    """계정과 저장된 학교 비밀번호, 동기화한 데이터를 모두 지운다."""
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
