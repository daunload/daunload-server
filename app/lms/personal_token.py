"""Canvas personal token -> Canvas cookies -> signed LearningX LTI launch.

No password, browser execution, video launch or attendance writes are involved.
Secrets and remote response bodies must never appear in exceptions or logs.
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from app.lms.auth import CANVAS_BASE_URL as BASE, LmsSession, export_cookies, find_api_token, new_http_client
from app.lms.errors import InvalidCredentials, LmsError

log = logging.getLogger(__name__)
_private_request = ContextVar("private_school_auth_request", default=False)


class _AuthLogFilter(logging.Filter):
    def filter(self, record):
        return not _private_request.get()


_log_filter = _AuthLogFilter()


class PersonalTokenInvalid(InvalidCredentials):
    """The personal token was rejected by the identity endpoint."""


class TokenIdentityMismatch(InvalidCredentials):
    """The token belongs to another school account."""


def safe_url(value: str, base: str = BASE) -> str:
    url = urljoin(base, value)
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname != "canvas.ssu.ac.kr" or p.port not in (None, 443)
            or p.username or p.password):
        raise LmsError("학교 인증 연결 주소가 올바르지 않습니다.")
    return url


class LaunchForms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms: list[tuple[dict, dict]] = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = (attrs, {})
            self.forms.append(self.current)
        elif tag == "input" and self.current and attrs.get("name"):
            self.current[1][attrs["name"]] = attrs.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


async def _request(http, method, url, **kwargs):
    url = safe_url(url)
    # Redirects are inspected before any secrets are sent to their destination.
    for _ in range(9):
        try:
            # httpx INFO logs include the one-time session URL; httpcore DEBUG can
            # include response cookies. Suppress only this task's auth requests.
            for name in list(logging.Logger.manager.loggerDict):
                if name == "httpx" or name.startswith("httpcore"):
                    logger = logging.getLogger(name)
                    if _log_filter not in logger.filters:
                        logger.addFilter(_log_filter)
            context = _private_request.set(True)
            try:
                response = await http.request(method, url, follow_redirects=False, **kwargs)
            finally:
                _private_request.reset(context)
        except httpx.HTTPError:
            raise LmsError("학교 인증 서버에 연결하지 못했습니다. 잠시 후 다시 시도하세요.") from None
        if not response.is_redirect:
            return response
        url = safe_url(response.headers.get("location", ""), url)
        # A signed launch must not be replayed by a 307/308 redirect.
        if method != "GET" and response.status_code in (307, 308):
            raise LmsError("학교 인증 연결 방식이 변경되었습니다.")
        method, kwargs = "GET", {}
    raise LmsError("학교 인증 연결이 너무 많이 반복되었습니다.")


def _json(response):
    if response.status_code != 200:
        raise LmsError(f"학교 인증 조회에 실패했습니다 (HTTP {response.status_code}).")
    try:
        return json.loads(response.text.lstrip().removeprefix("while(1);"))
    except ValueError:
        raise LmsError("학교 인증 응답 형식이 변경되었습니다.") from None


async def _pages(http, path, params=None):
    rows = []
    for _ in range(20):
        response = await _request(http, "GET", path, params=params)
        data = _json(response)
        if not isinstance(data, list):
            raise LmsError("학교 목록 응답 형식이 변경되었습니다.")
        rows.extend(data)
        path = response.links.get("next", {}).get("url")
        if not path:
            return rows
        params = None
    raise LmsError("학교 목록 조회 한도를 초과했습니다.")


async def session_from_token(token: str, student_id: str) -> LmsSession:
    """Validate identity and all four LearningX read APIs before returning a session."""
    try:
        return await _session_from_token(token, student_id)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise LmsError("학교 인증 응답 형식이 변경되었습니다.") from None


async def _session_from_token(token: str, student_id: str) -> LmsSession:
    async with new_http_client() as http:
        header = {"Authorization": f"Bearer {token}"}
        response = await _request(http, "GET", "/api/v1/users/self/profile", headers=header)
        if response.status_code == 401:
            raise PersonalTokenInvalid("개인 액세스 토큰이 만료되었거나 폐기되었습니다. 새 토큰을 등록하세요.")
        profile = _json(response)
        if not isinstance(profile, dict) or not profile.get("id"):
            raise LmsError("학교 사용자 정보를 확인하지 못했습니다.")
        if str(profile.get("login_id") or profile.get("sis_user_id") or "") != student_id:
            raise TokenIdentityMismatch("로그인한 학번과 액세스 토큰의 계정이 다릅니다.")
        http.cookies.clear()
        data = _json(await _request(http, "GET", "/login/session_token", headers=header))
        if not isinstance(data, dict) or not isinstance(data.get("session_url"), str):
            raise LmsError("Canvas 웹 세션을 생성하지 못했습니다.")
        await _request(http, "GET", data["session_url"])
        identity = _json(await _request(http, "GET", "/api/v1/users/self/profile"))
        if not isinstance(identity, dict) or identity.get("id") != profile["id"]:
            raise TokenIdentityMismatch("생성된 학교 세션의 사용자가 일치하지 않습니다.")
        courses = await _pages(http, "/api/v1/courses", {"enrollment_state": "active", "per_page": 100})
        courses.sort(key=lambda c: int(c.get("enrollment_term_id") or 0), reverse=True)
        for course in courses:
            cid = int(course["id"])
            response = await _request(http, "GET", f"/api/v1/courses/{cid}/tabs")
            if response.status_code in (403, 404):
                continue
            tabs = _json(response)
            if not isinstance(tabs, list):
                raise LmsError("학교 메뉴 응답 형식이 변경되었습니다.")
            # Prefer the known label, but discover the actual form action dynamically.
            tabs.sort(key=lambda t: t.get("label") != "주차학습")
            for tab in tabs:
                if not str(tab.get("id", "")).startswith("context_external_tool") or not tab.get("html_url"):
                    continue
                page = await _request(http, "GET", tab["html_url"])
                if page.status_code in (403, 404):
                    continue
                if page.status_code != 200:
                    raise LmsError("학교 학습 메뉴를 열지 못했습니다.")
                parser = LaunchForms()
                parser.feed(page.text)
                for attrs, fields in parser.forms:
                    if urlsplit(attrs.get("action", "")).path != "/learningx/lti/modulebuilder":
                        continue
                    action = safe_url(attrs["action"], str(page.url))
                    if attrs.get("method", "").lower() != "post" or not fields.get("oauth_signature"):
                        raise LmsError("학교 학습 인증 폼을 확인하지 못했습니다.")
                    result = await _request(http, "POST", action, data=fields)
                    if result.status_code != 200:
                        raise LmsError("LearningX 인증 연결에 실패했습니다.")
                    bearer = find_api_token([str(result.url)], http.cookies, result.headers.get_list("set-cookie"))
                    if not bearer:
                        raise LmsError("LearningX 인증 토큰이 발급되지 않았습니다.")
                    # Keep cookies out of these checks: prove Bearer auth independently.
                    async with new_http_client() as api:
                        async def read(path, params=None):
                            api.cookies.clear()
                            return _json(await _request(api, "GET", path, params=params,
                                                        headers={"Authorization": f"Bearer {bearer}"}))
                        terms = await read(f"/learningx/api/v1/users/{quote(student_id, safe='')}/terms",
                                           {"include_invited_course_contained": "true"})
                        if isinstance(terms, dict):
                            terms = terms.get("enrollment_terms")
                        if not isinstance(terms, list) or not terms:
                            raise LmsError("LearningX 학기 정보를 확인하지 못했습니다.")
                        term_id = course.get("enrollment_term_id") or terms[-1]["id"]
                        enrolled = await read("/learningx/api/v1/learn_activities/courses", {"term_ids[]": term_id})
                        attendance = await read("/learningx/api/v1/learn_activities/learnstatus",
                                                {"term_ids": term_id, "type": "subsection"})
                        modules = await read(f"/learningx/api/v1/courses/{cid}/modules", {"include_detail": "true"})
                        if isinstance(attendance, dict):
                            attendance = attendance.get("learnstatuses")
                        if not all(isinstance(x, list) for x in (enrolled, attendance, modules)):
                            raise LmsError("LearningX 학습 정보를 확인하지 못했습니다.")
                    return LmsSession(user_id=student_id, bearer_token=bearer, cookies=export_cookies(http.cookies))
        raise LmsError("연결 가능한 주차학습 메뉴가 없습니다. 수강 과목을 확인하세요.")


# ---------------------------------------------------------------------------
# Server-managed personal token (Canvas Access Tokens API)
# ---------------------------------------------------------------------------

MANAGED_TOKEN_PURPOSE = "SSU LMS 자동 수강 (서버 관리)"


class ManagedTokenUnavailable(LmsError):
    """Canvas refused to create a token for this account (e.g. restricted by the school)."""


@dataclass
class ManagedToken:
    token: str
    token_id: str
    expires_at: datetime | None

    def __repr__(self) -> str:  # never print the secret
        return f"ManagedToken(token_id={self.token_id!r}, expires_at={self.expires_at!r})"


def _parse_created(response) -> ManagedToken:
    if response.status_code in (401, 403):
        raise ManagedTokenUnavailable("학교 Canvas가 액세스 토큰 자동 발급을 허용하지 않습니다.")
    if response.status_code != 200:
        raise LmsError(f"학교 토큰 발급에 실패했습니다 (HTTP {response.status_code}).")
    data = _json(response)
    if not isinstance(data, dict):
        raise LmsError("학교 토큰 발급 응답 형식이 변경되었습니다.")
    token = data.get("visible_token") or data.get("token")
    token_id = data.get("id")
    if not isinstance(token, str) or not token or token_id is None:
        raise LmsError("학교 토큰 발급 응답 형식이 변경되었습니다.")
    expires = data.get("expires_at")
    try:
        expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00")) if isinstance(expires, str) else None
    except ValueError:
        expires_at = None
    return ManagedToken(token=token, token_id=str(token_id), expires_at=expires_at)


async def _csrf_header(http: httpx.AsyncClient) -> dict:
    """Canvas cookie-authenticated writes need X-CSRF-Token from the _csrf_token cookie."""
    def cookie():
        return next((c.value for c in http.cookies.jar if c.name == "_csrf_token" and c.value
                     and (c.domain or "").lstrip(".") == "canvas.ssu.ac.kr"), None)
    value = cookie()
    if value is None:
        await _request(http, "GET", "/profile/settings", headers={"Accept": "text/html"})
        value = cookie()
    if value is None:
        raise LmsError("학교 Canvas 보안 토큰을 확인하지 못했습니다.")
    return {"X-CSRF-Token": unquote(value)}


async def _verify(token: str, student_id: str) -> None:
    async with new_http_client() as api:
        response = await _request(api, "GET", "/api/v1/users/self/profile",
                                  headers={"Authorization": f"Bearer {token}"})
        profile = _json(response)
    if not isinstance(profile, dict) or str(profile.get("login_id") or profile.get("sis_user_id") or "") != student_id:
        raise TokenIdentityMismatch("발급된 토큰의 계정이 로그인한 학번과 다릅니다.")


# Newer Canvas exposes an API; the SSU Canvas (older) answers 404 there and only has the profile page's web
# routes. Measured on SSU (2026-11-09): those routes work only with a password-login web session. A session
# made from a token (/login/session_token) is redirected to /login, and the Bearer DELETE API returns 422.
# So tokens are created and revoked only during a password login; there is no rotation.
API_TOKENS = "/api/v1/users/self/tokens"
WEB_TOKENS = "/profile/tokens"


async def _create(http: httpx.AsyncClient, student_id: str) -> ManagedToken:
    """Create with the login session in `http`, then prove the new token belongs to this student."""
    headers = {**await _csrf_header(http), "Accept": "application/json"}
    response = await _request(http, "POST", API_TOKENS, headers=headers, data={"token[purpose]": MANAGED_TOKEN_PURPOSE})
    if response.status_code == 404:
        response = await _request(http, "POST", WEB_TOKENS, headers=headers,
                                  data={"access_token[purpose]": MANAGED_TOKEN_PURPOSE})
    created = _parse_created(response)
    try:
        await _verify(created.token, student_id)
    except LmsError:
        await revoke_token(http, created.token_id)
        raise
    return created


async def issue_managed_token(http: httpx.AsyncClient, student_id: str) -> ManagedToken:
    """Create a personal token with the Canvas web session that a fresh password login left in `http`."""
    try:
        return await _create(http, student_id)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise LmsError("학교 토큰 발급 응답 형식이 변경되었습니다.") from None


async def revoke_token(http: httpx.AsyncClient, token_id: str) -> bool:
    """Best effort, with the password-login session in `http`. Returns False instead of raising."""
    try:
        headers = {**await _csrf_header(http), "Accept": "application/json"}
        response = await _request(http, "DELETE", f"{WEB_TOKENS}/{quote(str(token_id), safe='')}", headers=headers)
        # A session that may not manage tokens is redirected to the login page, which ends as HTTP 200.
        landed_on_login = urlsplit(str(response.url)).path.startswith("/login")
        ok = response.status_code in (200, 204, 404) and not landed_on_login  # 404: already gone
    except LmsError:
        ok = False
    log.info("managed token revoke result=%s", "ok" if ok else "failed")
    return ok
