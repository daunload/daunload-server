import base64
import json

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.lms import auth
from app.lms.errors import InvalidCredentials, LmsError, LmsParseError, SsoExpired


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key, fmt) -> str:
    return key.private_bytes(serialization.Encoding.PEM, fmt, serialization.NoEncryption()).decode()


def _encrypt(key, text: str) -> str:
    return base64.b64encode(key.public_key().encrypt(text.encode(), padding.PKCS1v15())).decode()


def _js_escaped(pem: str) -> str:
    return pem.strip().replace("\n", "\\n")


# --- 파서 ------------------------------------------------------------------


def test_extract_sso_token_decodes_escapes():
    body = "<script>location.href='https://lms/?sToken=abcx2Bdefx2Fx3Dx5Fx78x79x7A&sIdno=1'</script>"
    assert auth.extract_sso_token(body) == "abc+def/=_xyz"


def test_extract_sso_token_stops_at_quote():
    assert auth.extract_sso_token("var u='?sToken=TOKEN';") == "TOKEN"


def test_extract_sso_token_missing():
    assert auth.extract_sso_token("<html>로그인 실패</html>") == ""


def test_extract_sso_token_from_cookies():
    headers = ["a=1; path=/", "sToken=ZZZ; path=/; HttpOnly"]
    assert auth.extract_sso_token_from_cookies(headers) == "ZZZ"


def test_extract_iframe_src_relative_and_absolute():
    assert auth.extract_iframe_src('iframe.src="/learningx/login?x=1";') == (
        "https://canvas.ssu.ac.kr/learningx/login?x=1"
    )
    assert auth.extract_iframe_src('iframe.src = "https://canvas.ssu.ac.kr/a";') == "https://canvas.ssu.ac.kr/a"


def test_extract_iframe_src_missing_raises():
    with pytest.raises(LmsParseError):
        auth.extract_iframe_src("<html></html>")


def test_extract_authenticity_token_unescapes():
    body = '<input type="hidden" name="authenticity_token" value="ab&#43;c/d==" />'
    assert auth.extract_authenticity_token(body) == "ab+c/d=="


@pytest.mark.parametrize("fmt", [serialization.PrivateFormat.TraditionalOpenSSL, serialization.PrivateFormat.PKCS8])
def test_decrypt_password_from_js_escaped_pem(rsa_key, fmt):
    pem = _js_escaped(_pem(rsa_key, fmt))
    body = f'<script>window.loginCryption("{_encrypt(rsa_key, "canvas-pw")}", "{pem}")</script>'
    cipher, raw_pem = auth.extract_login_cryption(body)
    assert auth.decrypt_password(raw_pem, cipher) == "canvas-pw"


def test_decrypt_password_single_line_pem(rsa_key):
    pem = _pem(rsa_key, serialization.PrivateFormat.TraditionalOpenSSL)
    body = "".join(pem.strip().splitlines()[1:-1])
    one_line = f"-----BEGIN RSA PRIVATE KEY-----{body}-----END RSA PRIVATE KEY-----"
    assert auth.decrypt_password(one_line, _encrypt(rsa_key, "pw")) == "pw"


def test_extract_login_cryption_missing_raises():
    with pytest.raises(LmsParseError):
        auth.extract_login_cryption("<html></html>")


# --- 전체 로그인 흐름 (respx로 모킹) ----------------------------------------


@respx.mock
async def test_login_full_flow(rsa_key):
    user_id = "20201234"
    pem = _js_escaped(_pem(rsa_key, serialization.PrivateFormat.TraditionalOpenSSL))
    redirect = "https://canvas.ssu.ac.kr/learningx/login/from_cc?result=ok"

    respx.post(auth.SSO_LOGIN_URL).respond(200, text="location.href='x?sToken=TOKx2B1&sIdno=1'")
    gw = respx.get(auth.LMS_GATEWAY_URL).respond(200, text='<script>iframe.src="/learningx/login/from_cc?result=ok";</script>')
    respx.get(redirect).respond(
        200,
        headers={"Set-Cookie": "xn_api_token=BEARER123; domain=.ssu.ac.kr; path=/"},
        text=(
            f'<input name="authenticity_token" value="AUTH" />'
            f'<script>window.loginCryption("{_encrypt(rsa_key, "decrypted-pw")}", "{pem}")</script>'
        ),
    )
    canvas_login = respx.post("https://canvas.ssu.ac.kr/login/canvas").respond(
        200, headers={"Set-Cookie": "_normandy_session=SESS; domain=canvas.ssu.ac.kr; path=/"}
    )
    respx.get("https://canvas.ssu.ac.kr/?login_success=1").respond(200)
    respx.get(f"https://lms.ssu.ac.kr/api/v1/users/{user_id}").respond(
        200, json={"user_login": user_id, "user_name": "홍길동", "dept_name": "컴퓨터학부", "user_email": ""}
    )

    async with auth.new_http_client() as client:
        session = await auth.login(client, user_id, "pw")

    assert session.bearer_token == "BEARER123"
    assert gw.calls.last.request.url.params["sToken"] == "TOK+1"
    form = httpx.QueryParams(canvas_login.calls.last.request.content.decode())
    assert form["pseudonym_session[password]"] == "decrypted-pw"
    assert form["authenticity_token"] == "AUTH"
    assert {c["name"] for c in session.cookies} >= {"xn_api_token", "_normandy_session"}
    assert session.sso_token == "TOK+1"
    assert "\"pw\"" not in json.dumps(session.to_dict())  # 학교 비밀번호는 세션에 남지 않는다


@respx.mock
async def test_login_invalid_credentials():
    respx.post(auth.SSO_LOGIN_URL).respond(200, text="<script>alert('비밀번호 오류')</script>")
    async with auth.new_http_client() as client:
        with pytest.raises(InvalidCredentials):
            await auth.login(client, "1", "bad")


# --- SSO 재발급 (비밀번호 없이) ---------------------------------------------

REDIRECT = "https://canvas.ssu.ac.kr/learningx/login/from_cc?result=ok"
OLD_COOKIES = [
    {"name": "sToken", "value": "SSO-TOK", "domain": ".ssu.ac.kr", "path": "/"},
    {"name": "_normandy_session", "value": "OLD-CANVAS", "domain": "canvas.ssu.ac.kr", "path": "/"},
    {"name": "xn_api_token", "value": "OLD-BEARER", "domain": "lms.ssu.ac.kr", "path": "/"},
]


def _old_session(**kw) -> auth.LmsSession:
    return auth.LmsSession(user_id="20201234", bearer_token="OLD-BEARER", cookies=[c.copy() for c in OLD_COOKIES], **kw)


def _canvas_routes(rsa_key):
    pem = _js_escaped(_pem(rsa_key, serialization.PrivateFormat.TraditionalOpenSSL))
    page = respx.get(REDIRECT).respond(
        200,
        headers={"Set-Cookie": "xn_api_token=NEW-BEARER; domain=.ssu.ac.kr; path=/"},
        text=f'<script>window.loginCryption("{_encrypt(rsa_key, "canvas-pw")}", "{pem}")</script>',
    )
    respx.post("https://canvas.ssu.ac.kr/login/canvas").respond(
        200, headers={"Set-Cookie": "_normandy_session=NEW-CANVAS; domain=canvas.ssu.ac.kr; path=/"})
    respx.get("https://canvas.ssu.ac.kr/?login_success=1").respond(200)
    respx.get("https://lms.ssu.ac.kr/api/v1/users/20201234").respond(200, json={"user_login": "20201234"})
    return page


@respx.mock
async def test_renew_from_sso_issues_new_session_without_password(rsa_key, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("학교 비밀번호 로그인은 호출하면 안 된다")
    monkeypatch.setattr(auth, "login", forbidden)
    sso_post = respx.post(auth.SSO_LOGIN_URL)
    gw = respx.get(auth.LMS_GATEWAY_URL).respond(200, text='<script>iframe.src="/learningx/login/from_cc?result=ok";</script>')
    page = _canvas_routes(rsa_key)

    session = await auth.renew_from_sso(_old_session())

    assert not sso_post.called
    assert gw.calls.last.request.url.params["sToken"] == "SSO-TOK"
    assert gw.calls.last.request.url.params["sIdno"] == "20201234"
    # 이전 Canvas·포털 쿠키는 보내지 않는다
    sent = gw.calls.last.request.headers.get("cookie", "") + page.calls.last.request.headers.get("cookie", "")
    assert "OLD-CANVAS" not in sent and "OLD-BEARER" not in sent
    assert session.bearer_token == "NEW-BEARER" and session.sso_token == "SSO-TOK"
    assert {c["value"] for c in session.cookies} >= {"NEW-CANVAS", "SSO-TOK"}


@respx.mock
async def test_renew_prefers_stored_sso_token_and_picks_up_rotation(rsa_key):
    gw = respx.get(auth.LMS_GATEWAY_URL).respond(
        200, headers={"Set-Cookie": "sToken=ROTATED; domain=.ssu.ac.kr; path=/"},
        text='<script>iframe.src="/learningx/login/from_cc?result=ok";</script>')
    _canvas_routes(rsa_key)
    session = await auth.renew_from_sso(_old_session(sso_token="STORED"))
    assert gw.calls.last.request.url.params["sToken"] == "STORED"
    assert session.sso_token == "ROTATED"


async def test_renew_without_sso_token_requires_login():
    with pytest.raises(SsoExpired):
        await auth.renew_from_sso(auth.LmsSession("20201234", "B", [OLD_COOKIES[1]]))


@respx.mock
async def test_renew_gateway_without_handoff_means_sso_expired():
    respx.get(auth.LMS_GATEWAY_URL).respond(200, text="<html>로그인이 필요합니다</html>")
    with pytest.raises(SsoExpired):
        await auth.renew_from_sso(_old_session())


@respx.mock
async def test_renew_redirect_to_school_login_means_sso_expired():
    respx.get(auth.LMS_GATEWAY_URL).respond(302, headers={"Location": "https://smartid.ssu.ac.kr/Symtra_sso/smln.asp"})
    respx.get("https://smartid.ssu.ac.kr/Symtra_sso/smln.asp").respond(200, text="<form></form>")
    with pytest.raises(SsoExpired):
        await auth.renew_from_sso(_old_session())


@respx.mock
async def test_renew_rejects_handoff_to_foreign_origin():
    respx.get(auth.LMS_GATEWAY_URL).respond(200, text='<script>iframe.src="https://evil.example/login";</script>')
    with pytest.raises(LmsError) as e:
        await auth.renew_from_sso(_old_session())
    assert not isinstance(e.value, SsoExpired)


@respx.mock
@pytest.mark.parametrize("respond", [
    lambda r: r.respond(503),
    lambda r: r.mock(side_effect=httpx.ConnectError("down")),
])
async def test_renew_server_errors_are_transient(respond):
    respond(respx.get(auth.LMS_GATEWAY_URL))
    with pytest.raises(LmsError) as e:
        await auth.renew_from_sso(_old_session())
    assert not isinstance(e.value, InvalidCredentials)


def test_session_round_trip_keeps_sso_token_and_reads_legacy_data():
    s = auth.LmsSession("1", "B", [], "SSO")
    assert auth.LmsSession.from_dict(s.to_dict()) == s
    assert auth.LmsSession.from_dict({"user_id": "1", "bearer_token": "B"}).sso_token == ""
