"""Server-managed Canvas personal token, issued and revoked with the password-login web session."""
import logging

import httpx
import pytest
import respx

from app.lms import personal_token as pt
from app.lms.auth import new_http_client
from app.lms.errors import LmsError

B = pt.BASE
SECRET = "managed-canary-token"


def _client_with_csrf():
    http = new_http_client()
    http.cookies.set("_csrf_token", "abc%2B%3D", domain="canvas.ssu.ac.kr")
    http.cookies.set("_normandy_session", "web-session", domain="canvas.ssu.ac.kr")
    return http


def _profile(login_id="20201234"):
    return respx.get(B + "/api/v1/users/self/profile").respond(200, json={"id": 7, "login_id": login_id})


@respx.mock
async def test_issue_on_new_canvas_api_uses_csrf_and_verifies_identity(caplog):
    caplog.set_level(logging.DEBUG)
    create = respx.post(B + "/api/v1/users/self/tokens").respond(
        200, json={"id": 42, "visible_token": SECRET, "expires_at": None})
    profile = _profile()
    async with _client_with_csrf() as http:
        created = await pt.issue_managed_token(http, "20201234")
    assert created.token == SECRET and created.token_id == "42" and created.expires_at is None
    req = create.calls.last.request
    assert req.headers["x-csrf-token"] == "abc+=" and "token%5Bpurpose%5D=" in req.content.decode()
    assert profile.calls.last.request.headers["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in repr(created) and SECRET not in caplog.text


@respx.mock
async def test_issue_falls_back_to_profile_web_route_on_old_canvas():
    api = respx.post(B + "/api/v1/users/self/tokens").respond(404, text="<html>not found</html>")
    web = respx.post(B + "/profile/tokens").respond(200, json={"id": 5, "visible_token": SECRET, "app_name": "x"})
    _profile()
    async with _client_with_csrf() as http:
        created = await pt.issue_managed_token(http, "20201234")
    assert api.called and created.token == SECRET and created.token_id == "5"
    req = web.calls.last.request
    assert req.headers["x-csrf-token"] == "abc+=" and "access_token%5Bpurpose%5D=" in req.content.decode()


@respx.mock
async def test_issue_parses_expiry_and_legacy_token_field():
    respx.post(B + "/api/v1/users/self/tokens").respond(
        200, json={"id": 1, "token": SECRET, "expires_at": "2027-01-01T00:00:00Z"})
    _profile()
    async with _client_with_csrf() as http:
        created = await pt.issue_managed_token(http, "20201234")
    assert created.token == SECRET and created.expires_at.year == 2027


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
async def test_issue_restricted_by_school(status):
    respx.post(B + "/api/v1/users/self/tokens").respond(status, json={"errors": "nope"})
    async with _client_with_csrf() as http:
        with pytest.raises(pt.ManagedTokenUnavailable):
            await pt.issue_managed_token(http, "20201234")


@respx.mock
async def test_issue_fails_when_both_routes_missing():
    respx.post(B + "/api/v1/users/self/tokens").respond(404)
    respx.post(B + "/profile/tokens").respond(404)
    async with _client_with_csrf() as http:
        with pytest.raises(LmsError, match="HTTP 404"):
            await pt.issue_managed_token(http, "20201234")


@respx.mock
async def test_issue_fetches_csrf_when_missing():
    settings = respx.get(B + "/profile/settings").respond(
        200, text="<html></html>", headers={"Set-Cookie": "_csrf_token=fresh; Path=/; Domain=canvas.ssu.ac.kr"})
    create = respx.post(B + "/api/v1/users/self/tokens").respond(200, json={"id": 1, "visible_token": SECRET})
    _profile()
    async with new_http_client() as http:
        await pt.issue_managed_token(http, "20201234")
    assert settings.called and create.calls.last.request.headers["x-csrf-token"] == "fresh"


@respx.mock
async def test_identity_mismatch_revokes_new_token_with_login_session():
    respx.post(B + "/api/v1/users/self/tokens").respond(200, json={"id": 9, "visible_token": SECRET})
    _profile("other")
    revoke = respx.delete(B + "/profile/tokens/9").respond(200, json={})
    async with _client_with_csrf() as http:
        with pytest.raises(pt.TokenIdentityMismatch):
            await pt.issue_managed_token(http, "20201234")
    req = revoke.calls.last.request
    assert req.headers["x-csrf-token"] == "abc+=" and "authorization" not in req.headers


@respx.mock
async def test_bad_response_shape_is_lms_error():
    respx.post(B + "/api/v1/users/self/tokens").respond(200, json={"id": 1})
    async with _client_with_csrf() as http:
        with pytest.raises(LmsError):
            await pt.issue_managed_token(http, "20201234")


@pytest.mark.parametrize("status, ok", [(200, True), (204, True), (404, True), (302, False), (422, False)])
@respx.mock
async def test_revoke_uses_login_session(status, ok):
    respx.delete(B + "/profile/tokens/4").respond(status, headers={"Location": B + "/login"} if status == 302 else {})
    if status == 302:
        respx.get(B + "/login").respond(200, text="<html>login</html>")
    async with _client_with_csrf() as http:
        assert await pt.revoke_token(http, "4") is ok


@respx.mock
async def test_revoke_is_best_effort():
    respx.delete(B + "/profile/tokens/1").mock(side_effect=httpx.ConnectError("down"))
    async with _client_with_csrf() as http:
        assert await pt.revoke_token(http, "1") is False
