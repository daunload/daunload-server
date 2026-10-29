from datetime import timedelta

import jwt
from cryptography.fernet import Fernet, InvalidToken
from pwdlib import PasswordHash

from app.config import get_settings
from app.db.base import utcnow

_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _hasher.verify(password, hashed)


def create_access_token(user_id: int) -> str:
    s = get_settings()
    payload = {"sub": str(user_id), "exp": utcnow() + timedelta(minutes=s.jwt_expire_minutes)}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        if payload.get("purpose"):  # 다운로드 링크 토큰으로 API에 로그인할 수 없게
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


DOWNLOAD_LINK_MINUTES = 10


def create_download_token(user_id: int, data: dict) -> str:
    """브라우저가 헤더 없이 여는 다운로드 주소용 짧은 서명 토큰. 확인을 마친 항목 정보만 담는다."""
    payload = {"sub": str(user_id), "purpose": "download", "data": data,
               "exp": utcnow() + timedelta(minutes=DOWNLOAD_LINK_MINUTES)}
    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


def decode_download_token(token: str) -> tuple[int, dict] | None:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        if payload.get("purpose") != "download":
            return None
        return int(payload["sub"]), payload["data"]
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise RuntimeError("FERNET_KEY가 설정되지 않았습니다. Fernet.generate_key()로 만들어 .env에 넣으세요.")
    return Fernet(key)


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("암호화된 값을 풀 수 없습니다. FERNET_KEY가 바뀌었는지 확인하세요.") from e
