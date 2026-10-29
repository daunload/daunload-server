from datetime import UTC, datetime

import pytest
import respx

from app.lms import client as client_mod
from app.lms.auth import LmsSession
from app.lms.client import LmsClient, pick_current_term
from app.lms.errors import SessionExpired
from app.lms.models import Term

C = "https://canvas.ssu.ac.kr"


def _client(**kw) -> LmsClient:
    session = LmsSession(user_id="20201234", bearer_token="BEARER", cookies=[])
    return LmsClient(session, min_interval=0, **kw)


@respx.mock
async def test_learningx_uses_bearer(fixture):
    route = respx.get(f"{C}/learningx/api/v1/courses/1/modules").respond(200, json=fixture("modules.json"))
    async with _client() as c:
        modules = await c.get_modules(1)
    assert len(modules) == 3
    assert route.calls.last.request.headers["Authorization"] == "Bearer BEARER"
    assert route.calls.last.request.url.params["include_detail"] == "true"


@respx.mock
async def test_api_v1_does_not_send_bearer_and_paginates(fixture):
    page2 = f"{C}/api/v1/courses/1/discussion_topics?page=2&per_page=40"
    first = fixture("announcements.json")
    second = [{**first[0], "id": "205810", "title": "두번째"}]

    def handler(request):
        if request.url.params.get("page") == "2":
            return respx.MockResponse(200, json=second)
        return respx.MockResponse(200, json=first, headers={"Link": f'<{page2}>; rel="next"'})

    route = respx.get(f"{C}/api/v1/courses/1/discussion_topics").mock(side_effect=handler)
    async with _client() as c:
        items = await c.get_announcements(1)
    assert [a.title for a in items] == ["강의계획서 필독", "두번째"]
    assert items[0].attachments[0].content_type == "application/pdf"
    assert "Authorization" not in route.calls[0].request.headers
    assert route.calls[0].request.headers["Referer"].endswith("/courses/1/announcements")


@respx.mock
async def test_learn_statuses(fixture):
    respx.get(f"{C}/learningx/api/v1/learn_activities/learnstatus").respond(200, json=fixture("learnstatus.json"))
    async with _client() as c:
        statuses = await c.get_learn_statuses(46)
    assert statuses[0].sections[0].subsections[1].status == "absent"


@respx.mock
async def test_terms_wrapped_or_bare():
    route = respx.get(f"{C}/learningx/api/v1/users/20201234/terms")
    route.respond(200, json={"enrollment_terms": [{"id": 46, "name": "2026-2"}]})
    async with _client() as c:
        assert [t.id for t in await c.get_terms()] == [46]
    route.respond(200, json=[{"id": 47, "name": "x"}])
    async with _client() as c:
        assert [t.id for t in await c.get_terms()] == [47]


@respx.mock
async def test_while1_prefix_is_stripped():
    respx.get(f"{C}/api/v1/courses/1/students/submissions").respond(
        200, text='while(1);[{"assignment_id": "1", "workflow_state": "unsubmitted"}]'
    )
    async with _client() as c:
        subs = await c.get_submissions(1)
    assert subs[0].assignment_id == 1


@respx.mock
async def test_expired_without_password_raises():
    respx.get(f"{C}/learningx/api/v1/courses/1/modules").respond(401)
    async with _client() as c:
        with pytest.raises(SessionExpired):
            await c.get_modules(1)


@respx.mock
async def test_expired_relogins_once(monkeypatch, fixture):
    calls = {"login": 0}

    async def fake_login(http, user_id, password):
        calls["login"] += 1
        return LmsSession(user_id=user_id, bearer_token="NEW", cookies=[])

    monkeypatch.setattr(client_mod.auth, "login", fake_login)
    route = respx.get(f"{C}/learningx/api/v1/courses/1/modules")
    route.side_effect = [respx.MockResponse(401), respx.MockResponse(200, json=fixture("modules.json"))]
    async with _client(password="pw") as c:
        modules = await c.get_modules(1)
    assert calls["login"] == 1 and len(modules) == 3
    assert route.calls.last.request.headers["Authorization"] == "Bearer NEW"


def test_pick_current_term():
    terms = [
        Term(id=45, start_at="2026-03-01T00:00:00Z", end_at="2026-06-30T00:00:00Z"),
        Term(id=46, start_at="2026-09-01T00:00:00Z", end_at="2026-12-31T00:00:00Z"),
        Term(id=1, name="기본 학기"),
    ]
    assert pick_current_term(terms, datetime(2026, 10, 7, tzinfo=UTC)).id == 46
    assert pick_current_term(terms, datetime(2026, 7, 15, tzinfo=UTC)).id == 45


@respx.mock
async def test_expired_learningx_bearer_400_html_relogins(monkeypatch, fixture):
    """실제로 관찰된 경우: 쿠키 세션은 유효하지만 xn_api_token이 만료되어 400 HTML이 온다."""
    async def fake_login(http, user_id, password):
        return LmsSession(user_id=user_id, bearer_token="NEW", cookies=[])

    monkeypatch.setattr(client_mod.auth, "login", fake_login)
    route = respx.get(f"{C}/learningx/api/v1/courses/1/modules")
    route.side_effect = [
        respx.MockResponse(400, html="<title>Invalid request.</title>"),
        respx.MockResponse(200, json=fixture("modules.json")),
    ]
    async with _client(password="pw") as c:
        assert len(await c.get_modules(1)) == 3
    assert route.calls.last.request.headers["Authorization"] == "Bearer NEW"


@respx.mock
async def test_plain_400_on_canvas_api_is_not_treated_as_expired():
    respx.get(f"{C}/api/v1/courses/1/students/submissions").respond(400, json={"errors": "bad"})
    async with _client(password="pw") as c:
        from app.lms.errors import LmsError
        with pytest.raises(LmsError) as e:
            await c.get_submissions(1)
    assert not isinstance(e.value, SessionExpired)
