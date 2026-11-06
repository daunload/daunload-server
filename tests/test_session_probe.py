import json

import httpx
import respx

from app.lms.auth import CANVAS_BASE_URL, LmsSession
from app.lms.session_probe import measure


@respx.mock
async def test_probe_separates_auth_and_keeps_rotated_cookie(capsys, monkeypatch):
    async def no_sleep(_):
        pass
    monkeypatch.setattr("app.lms.session_probe.asyncio.sleep", no_sleep)
    cookie_requests = []
    bearer_requests = []

    def cookie_response(request):
        cookie_requests.append(request)
        return httpx.Response(200, text='while(1);{"id":1,"name":"PRIVATE_NAME"}',
                              headers={"set-cookie": "sid=ROTATED_SECRET; Path=/; Secure; HttpOnly"})

    def bearer_response(request):
        bearer_requests.append(request)
        return httpx.Response(200, json={"enrollment_terms": []},
                              headers={"set-cookie": "other=PRIVATE_COOKIE; Path=/"})

    respx.get(CANVAS_BASE_URL + "/api/v1/users/self/profile").mock(side_effect=cookie_response)
    respx.get(CANVAS_BASE_URL + "/learningx/api/v1/users/123/terms").mock(side_effect=bearer_response)
    await measure(LmsSession("123", "BEARER_SECRET", [
        {"name": "sid", "value": "COOKIE_SECRET", "domain": "canvas.ssu.ac.kr", "path": "/"}
    ]), 2, 60)
    assert len(cookie_requests) == len(bearer_requests) == 2
    assert all("authorization" not in r.headers for r in cookie_requests)
    assert "sid=ROTATED_SECRET" in cookie_requests[1].headers["cookie"]
    assert all("cookie" not in r.headers for r in bearer_requests)
    assert all(r.headers["authorization"] == "Bearer BEARER_SECRET" for r in bearer_requests)
    output = capsys.readouterr().out
    assert all(s not in output for s in ["PRIVATE", "SECRET", "123"])
    assert all(json.loads(line)["cookie"]["state"] == "authenticated" for line in output.splitlines())


@respx.mock
async def test_probe_stops_on_expired_session_without_login(capsys):
    respx.get(CANVAS_BASE_URL + "/api/v1/users/self/profile").respond(302, headers={
        "location": "https://smartid.ssu.ac.kr/login?secret=PRIVATE"})
    respx.get(CANVAS_BASE_URL + "/learningx/api/v1/users/123/terms").respond(401)
    await measure(LmsSession("123", "SECRET"), 10, 60)
    assert len(respx.calls) == 2
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    result = json.loads(output)
    assert result["cookie"]["state"] == "redirect_requires_review"
    assert result["bearer"]["state"] == "unauthorized"
