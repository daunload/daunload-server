"""LMS 조회 클라이언트 (chlwhdtn03/LMS-API의 LmsCourseClient.kt 이식).

- /learningx/* 는 Bearer 토큰, /api/v1/* 는 세션 쿠키로 인증한다.
- 세션이 만료되면(401, 로그인 페이지로 리다이렉트) 자격증명이 있을 때 한 번 재로그인한다.
- 요청 사이에 최소 간격을 두어 학교 서버에 부담을 주지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, TypeAdapter

from app.lms import auth
from app.lms.auth import CANVAS_BASE_URL, LmsSession
from app.lms.errors import LmsError, SessionExpired
from app.lms.models import (
    Announcement,
    AssignmentGroup,
    Course,
    LearnStatus,
    Module,
    Submission,
    Term,
)

T = TypeVar("T", bound=BaseModel)

MAX_PAGES = 20


class LmsClient:
    def __init__(
        self,
        session: LmsSession,
        *,
        password: str | None = None,
        http: httpx.AsyncClient | None = None,
        min_interval: float = 0.3,
    ) -> None:
        self.session = session
        self._password = password
        self._http = http or auth.new_http_client()
        self._owns_http = http is None
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        auth.import_cookies(self._http, session.cookies)

    @classmethod
    async def login(cls, user_id: str, password: str, **kwargs) -> LmsClient:
        http = kwargs.pop("http", None) or auth.new_http_client()
        session = await auth.login(http, user_id, password)
        client = cls(session, password=password, http=http, **kwargs)
        client._owns_http = True
        return client

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> LmsClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # 요청 공통 처리
    # ------------------------------------------------------------------

    async def relogin(self) -> None:
        if not self._password:
            raise SessionExpired("세션이 만료되었고 재로그인할 자격증명이 없습니다.")
        self._http.cookies.clear()
        self.session = await auth.login(self._http, self.session.user_id, self._password)

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self._last_request + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _request(self, url: str, *, bearer: bool, params: Any = None, headers: dict | None = None) -> httpx.Response:
        for attempt in range(2):
            await self._throttle()
            h = dict(headers or {})
            h.setdefault("Accept", "application/json")
            if bearer and self.session.bearer_token:
                h["Authorization"] = f"Bearer {self.session.bearer_token}"
            resp = await self._http.get(url, params=params, headers=h)
            if not _looks_expired(resp, bearer):
                break
            if attempt == 0 and self._password:
                await self.relogin()
                continue
            raise SessionExpired(f"세션이 만료되었습니다 ({resp.status_code} {resp.url}).")
        if resp.status_code >= 400:
            raise LmsError(f"LMS 요청 실패 ({resp.status_code}): {url}")
        return resp

    async def get_json(self, path: str, *, bearer: bool = False, params: Any = None, headers: dict | None = None) -> Any:
        resp = await self._request(_canvas(path), bearer=bearer, params=params, headers=headers)
        return _json(resp)

    async def get_paginated(self, path: str, *, params: Any = None, headers: dict | None = None) -> list:
        """Canvas /api/v1 목록 응답의 Link 헤더(rel=next)를 따라가며 모두 모은다."""
        url: str | None = _canvas(path)
        items: list = []
        for _ in range(MAX_PAGES):
            resp = await self._request(url, bearer=False, params=params, headers=headers)
            data = _json(resp)
            if not isinstance(data, list):
                raise LmsError(f"목록 응답이 아닙니다: {path}")
            items.extend(data)
            url = resp.links.get("next", {}).get("url")
            params = None  # next URL에 쿼리가 모두 들어 있다
            if not url:
                break
        return items

    # ------------------------------------------------------------------
    # 조회 API
    # ------------------------------------------------------------------

    async def get_terms(self) -> list[Term]:
        data = await self.get_json(
            f"/learningx/api/v1/users/{self.session.user_id}/terms",
            bearer=True,
            params={"include_invited_course_contained": "true"},
        )
        if isinstance(data, dict):
            data = data.get("enrollment_terms", [])
        return _parse_list(Term, data)

    async def get_current_term(self, now: datetime | None = None) -> Term:
        terms = await self.get_terms()
        if not terms:
            raise LmsError("수강 중인 학기가 없습니다.")
        return pick_current_term(terms, now or datetime.now(UTC))

    async def get_courses(self, term_id: int) -> list[Course]:
        data = await self.get_json(
            "/learningx/api/v1/learn_activities/courses", bearer=True, params={"term_ids[]": term_id}
        )
        return _parse_list(Course, data)

    async def get_learn_statuses(self, term_id: int) -> list[LearnStatus]:
        data = await self.get_json(
            "/learningx/api/v1/learn_activities/learnstatus",
            bearer=True,
            params={"term_ids": term_id, "type": "subsection"},
        )
        return _parse_list(LearnStatus, data.get("learnstatuses", []) if isinstance(data, dict) else data)

    async def get_modules(self, course_id: int) -> list[Module]:
        data = await self.get_json(
            f"/learningx/api/v1/courses/{course_id}/modules", bearer=True, params={"include_detail": "true"}
        )
        return _parse_list(Module, data)

    async def get_assignment_groups(self, course_id: int) -> list[AssignmentGroup]:
        params = [
            ("exclude_response_fields[]", "description"),
            ("exclude_response_fields[]", "rubric"),
            ("include[]", "assignments"),
            ("include[]", "discussion_topic"),
            ("override_assignment_dates", "true"),
            ("per_page", "50"),
        ]
        data = await self.get_paginated(f"/api/v1/courses/{course_id}/assignment_groups", params=params)
        return _parse_list(AssignmentGroup, data)

    async def get_submissions(self, course_id: int) -> list[Submission]:
        data = await self.get_paginated(
            f"/api/v1/courses/{course_id}/students/submissions", params={"per_page": "100"}
        )
        return _parse_list(Submission, data)

    async def get_announcements(self, course_id: int) -> list[Announcement]:
        params = [
            ("only_announcements", "true"),
            ("per_page", "40"),
            ("filter_by", "all"),
            ("no_avatar_fallback", "1"),
            ("include[]", "sections_user_count"),
            ("include[]", "sections"),
        ]
        data = await self.get_paginated(
            f"/api/v1/courses/{course_id}/discussion_topics",
            params=params,
            headers={"Referer": f"{CANVAS_BASE_URL}/courses/{course_id}/announcements"},
        )
        return _parse_list(Announcement, data)


# ----------------------------------------------------------------------
# 도우미
# ----------------------------------------------------------------------


def pick_current_term(terms: list[Term], now: datetime) -> Term:
    from app.lms.todo import parse_dt

    def bounds(t: Term) -> tuple[datetime | None, datetime | None]:
        try:
            return parse_dt(t.start_at), parse_dt(t.end_at)
        except ValueError:
            return None, None

    for t in terms:
        start, end = bounds(t)
        if start and end and start <= now <= end:
            return t
    started = [t for t in terms if (s := bounds(t)[0]) and s <= now]
    if started:
        return max(started, key=lambda t: bounds(t)[0])
    return max(terms, key=lambda t: t.id)


def _canvas(path: str) -> str:
    return path if path.startswith("http") else f"{CANVAS_BASE_URL}{path}"


def _looks_expired(resp: httpx.Response, bearer: bool = False) -> bool:
    if resp.status_code == 401:
        return True
    html = "text/html" in resp.headers.get("content-type", "")
    # LearningX는 Bearer 토큰(xn_api_token)이 만료되면 401이 아니라 400/403 HTML("Invalid request.")을 준다.
    # Canvas 쿠키 세션은 살아 있어도 이 토큰만 먼저 만료되므로 재로그인해서 새 토큰을 받는다.
    if bearer and html and resp.status_code in (400, 403):
        return True
    return "/login" in resp.url.path and html


def _json(resp: httpx.Response) -> Any:
    if resp.text.startswith("while(1);"):  # Canvas JSON 하이재킹 방지 접두사
        import json

        return json.loads(resp.text[len("while(1);") :])
    try:
        return resp.json()
    except ValueError as e:
        raise LmsError(f"JSON 응답이 아닙니다: {resp.url}") from e


def _parse_list(model: type[T], data: Any) -> list[T]:
    return TypeAdapter(list[model]).validate_python(data or [])


# ----------------------------------------------------------------------
# CLI: python -m app.lms.client
# ----------------------------------------------------------------------


async def _main() -> None:
    import getpass

    from app.config import get_settings
    from app.lms.todo import collect_todos, format_todo

    settings = get_settings()
    user_id = settings.lms_id or input("학번: ")
    password = settings.lms_password or getpass.getpass("비밀번호: ")

    async with await LmsClient.login(user_id, password) as client:
        term = await client.get_current_term()
        print(f"# 학기: {term.name} (id={term.id})")
        courses = await client.get_courses(term.id)
        statuses = {s.course.id: s for s in await client.get_learn_statuses(term.id)}
        now = datetime.now(UTC)
        for course in courses:
            print(f"\n## {course.name} — {course.professors or ''}")
            status = statuses.get(course.id)
            if status:
                weeks = " ".join(
                    f"{sec.position}주:" + "".join(_mark(s.status) for s in sec.subsections)
                    for sec in status.sections
                    if sec.subsections
                )
                print(f"출석: {weeks}")
            modules = await client.get_modules(course.id)
            groups = await client.get_assignment_groups(course.id)
            try:
                submissions = await client.get_submissions(course.id)
            except LmsError:
                submissions = []
            for todo in collect_todos(course, modules, groups, submissions, now):
                print(" - " + format_todo(todo))
            announcements = await client.get_announcements(course.id)
            for a in announcements[:3]:
                print(f" * 공지: {a.title} ({(a.posted_at or '')[:10]})")


def _mark(status: str | None) -> str:
    return {"attendance": "O", "late": "△", "absent": "X"}.get(status or "", "-")


if __name__ == "__main__":
    asyncio.run(_main())
