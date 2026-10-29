import base64

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.lms import auth
from app.lms.errors import InvalidCredentials, LmsParseError


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


@respx.mock
async def test_login_invalid_credentials():
    respx.post(auth.SSO_LOGIN_URL).respond(200, text="<script>alert('비밀번호 오류')</script>")
    async with auth.new_http_client() as client:
        with pytest.raises(InvalidCredentials):
            await auth.login(client, "1", "bad")
