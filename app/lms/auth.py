"""숭실대 SSO → LMS → Canvas 로그인.

chlwhdtn03/LMS-API의 LmsAuthService.kt 흐름을 옮겼다.
1. smartid SSO 로그인으로 sToken을 받는다.
2. lms.ssu.ac.kr 게이트웨이에서 Canvas 로그인 페이지 주소(iframe.src)를 얻는다.
3. 그 페이지에서 Bearer 토큰(xn_api_token), 암호화된 비밀번호와 RSA 개인키, authenticity_token을 얻는다.
4. 비밀번호를 복호화해서 Canvas에 로그인하고 세션 쿠키를 얻는다.
5. /api/v1/users/{id}로 로그인한 사용자가 맞는지 확인한다.
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.lms.errors import InvalidCredentials, LmsError, LmsParseError

CANVAS_BASE_URL = "https://canvas.ssu.ac.kr"
LMS_BASE_URL = "https://lms.ssu.ac.kr"
SSO_LOGIN_URL = "https://smartid.ssu.ac.kr/Symtra_sso/smln_pcs.asp"
LMS_GATEWAY_URL = f"{LMS_BASE_URL}/xn-sso/gw-cb.php"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
HTML_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)


@dataclass
class LmsSession:
    """로그인 결과. 쿠키는 재사용(캐시, Playwright 주입)을 위해 직렬화 가능한 형태로 보관한다."""

    user_id: str
    bearer_token: str
    cookies: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"user_id": self.user_id, "bearer_token": self.bearer_token, "cookies": self.cookies}

    @classmethod
    def from_dict(cls, data: dict) -> LmsSession:
        return cls(user_id=data["user_id"], bearer_token=data["bearer_token"], cookies=data.get("cookies", []))


def new_http_client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(30.0),
        headers={"User-Agent": USER_AGENT},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 파싱 함수 (네트워크 없이 테스트할 수 있도록 분리)
# ---------------------------------------------------------------------------

_SSO_ESCAPE = re.compile(r"x(2B|2F|3D|5F|78|79|7A)")


def extract_sso_token(body: str) -> str:
    if "sToken=" not in body:
        return ""
    raw = body.split("sToken=", 1)[1].split("&", 1)[0].split("'", 1)[0]
    return _SSO_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), raw)


def extract_sso_token_from_cookies(set_cookie_headers: list[str]) -> str:
    for header in set_cookie_headers:
        if "sToken=" in header:
            return header.split("sToken=", 1)[1].split(";", 1)[0]
    return ""


def extract_iframe_src(body: str) -> str:
    match = re.search(r'iframe\.src\s*=\s*"([^"]+)"', body)
    if not match:
        raise LmsParseError("LMS 게이트웨이 응답에서 iframe.src를 찾지 못했습니다.")
    url = match.group(1)
    if not url.startswith("http"):
        url = CANVAS_BASE_URL + (url if url.startswith("/") else f"/{url}")
    return url


_LOGIN_CRYPTION = re.compile(r'window\.loginCryption\(\s*"([^"]*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)')


def extract_login_cryption(body: str) -> tuple[str, str]:
    """(암호문 base64, PEM 개인키)를 반환한다."""
    match = _LOGIN_CRYPTION.search(body)
    if not match:
        raise LmsParseError("Canvas 로그인 페이지에서 loginCryption을 찾지 못했습니다.")
    return match.group(1), match.group(2)


_AUTH_TOKEN = re.compile(
    r"""<input\b(?=[^>]*\bname=["']authenticity_token["'])(?=[^>]*\bvalue=["']([^"']+)["'])[^>]*>""",
    re.IGNORECASE,
)


def extract_authenticity_token(body: str) -> str:
    match = _AUTH_TOKEN.search(body)
    return html.unescape(match.group(1)) if match else ""


_PEM_BLOCK = re.compile(r"-----BEGIN ([A-Z ]+)-----(.*?)-----END \1-----", re.DOTALL)


def normalize_pem(raw: str) -> bytes:
    """JS 문자열로 내려온 PEM(\\n 이스케이프, 한 줄짜리 본문 등)을 표준 PEM으로 정리한다."""
    text = raw.replace("\\n", "\n").replace("\\r", "").replace("\\/", "/").strip()
    match = _PEM_BLOCK.search(text)
    if not match:
        raise LmsParseError("RSA 개인키 형식을 인식하지 못했습니다.")
    label, body = match.group(1), re.sub(r"\s+", "", match.group(2))
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return ("\n".join([f"-----BEGIN {label}-----", *lines, f"-----END {label}-----"]) + "\n").encode()


def decrypt_password(pem: str, cipher_b64: str) -> str:
    key = serialization.load_pem_private_key(normalize_pem(pem), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise LmsParseError("RSA 개인키가 아닙니다.")
    plain = key.decrypt(base64.b64decode(cipher_b64), padding.PKCS1v15())
    return plain.decode("utf-8")


def find_api_token(urls: list[str], cookie_jar: httpx.Cookies, set_cookie_headers: list[str]) -> str:
    for url in urls:
        token = parse_qs(urlparse(url).query).get("api_token", [""])[0]
        if token:
            return token
    for cookie in cookie_jar.jar:
        if cookie.name == "xn_api_token" and cookie.value:
            return cookie.value
    for header in set_cookie_headers:
        if "xn_api_token=" in header:
            return header.split("xn_api_token=", 1)[1].split(";", 1)[0]
    return ""


def export_cookies(cookie_jar: httpx.Cookies) -> list[dict[str, str]]:
    return [
        {"name": c.name, "value": c.value or "", "domain": c.domain, "path": c.path or "/"}
        for c in cookie_jar.jar
        if c.domain.endswith("ssu.ac.kr")
    ]


def import_cookies(client: httpx.AsyncClient, cookies: list[dict[str, str]]) -> None:
    for c in cookies:
        client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))


# ---------------------------------------------------------------------------
# 로그인
# ---------------------------------------------------------------------------


async def login(client: httpx.AsyncClient, user_id: str, password: str) -> LmsSession:
    # 1. SSO
    sso = await client.post(SSO_LOGIN_URL, data={"userid": user_id, "pwd": password})
    s_token = extract_sso_token(sso.text) or extract_sso_token_from_cookies(
        _all_set_cookies(sso)
    )
    if not s_token:
        raise InvalidCredentials("학번 또는 비밀번호가 일치하지 않습니다.")

    # 2. LMS 게이트웨이
    gateway = await client.get(LMS_GATEWAY_URL, params={"sToken": s_token, "sIdno": user_id})
    redirect_url = extract_iframe_src(gateway.text)

    # 3. Canvas 로그인 페이지 (Bearer 토큰, 암호화된 비밀번호)
    page = await client.get(redirect_url)
    bearer = find_api_token(
        [redirect_url, str(page.url), *(str(r.url) for r in page.history)],
        client.cookies,
        _all_set_cookies(page),
    )
    if not bearer:
        raise LmsError("API 토큰(xn_api_token)을 얻지 못했습니다. 다시 시도해 주세요.")

    cipher_b64, pem = extract_login_cryption(page.text)
    canvas_password = decrypt_password(pem, cipher_b64)
    authenticity_token = extract_authenticity_token(page.text)

    # 4. Canvas 로그인
    form = {
        "utf8": "✓",
        "redirect_to_ssl": "1",
        "after_login_url": "",
        "pseudonym_session[unique_id]": user_id,
        "pseudonym_session[password]": canvas_password,
        "pseudonym_session[remember_me]": "0",
    }
    if authenticity_token:
        form["authenticity_token"] = authenticity_token
    await client.post(
        f"{CANVAS_BASE_URL}/login/canvas",
        data=form,
        headers={
            "Origin": CANVAS_BASE_URL,
            "Referer": redirect_url,
            "Cache-Control": "max-age=0",
            "Accept": HTML_ACCEPT,
        },
    )
    await client.get(f"{CANVAS_BASE_URL}/?login_success=1", headers={"Referer": redirect_url})

    # 5. 검증
    info = await get_user_info(client, user_id)
    if info.get("user_login") != user_id:
        raise LmsError("로그인 사용자 검증에 실패했습니다.")

    return LmsSession(user_id=user_id, bearer_token=bearer, cookies=export_cookies(client.cookies))


async def get_user_info(client: httpx.AsyncClient, user_id: str) -> dict:
    resp = await client.get(f"{LMS_BASE_URL}/api/v1/users/{user_id}")
    if resp.status_code != 200:
        raise LmsError(f"사용자 정보 조회 실패 ({resp.status_code})")
    try:
        return resp.json()
    except ValueError as e:
        raise LmsError("사용자 정보 응답이 JSON이 아닙니다. 로그인이 되지 않았을 수 있습니다.") from e


def _all_set_cookies(resp: httpx.Response) -> list[str]:
    headers: list[str] = []
    for r in [*resp.history, resp]:
        headers.extend(r.headers.get_list("set-cookie"))
    return headers


# ---------------------------------------------------------------------------
# CLI: python -m app.lms.auth
# ---------------------------------------------------------------------------


async def _main() -> None:
    import getpass

    from app.config import get_settings

    settings = get_settings()
    user_id = settings.lms_id or input("학번: ")
    password = settings.lms_password or getpass.getpass("비밀번호: ")
    async with new_http_client() as client:
        session = await login(client, user_id, password)
        info = await get_user_info(client, user_id)
    print(f"로그인 성공: {info.get('user_name')} ({info.get('dept_name')}), 학번 {session.user_id}")
    print(f"Bearer 토큰 확보: {'예' if session.bearer_token else '아니오'}, 쿠키 {len(session.cookies)}개")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
