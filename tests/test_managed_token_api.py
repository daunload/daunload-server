"""Login issues a server-managed token; it is reused, and reissued when dead. Canvas-side revocation only
happens with the password-login session, so removing a token or the account keeps the Canvas token."""
import pytest
from sqlalchemy import update

from app.db import base as db_base
from app.db.models import LmsCredential, User
from app.lms import auth, personal_token as pt
from app.lms.errors import LmsError
from app.security import decrypt, encrypt
from tests.test_api import env, _signup  # noqa: F401


@pytest.fixture
def canvas(monkeypatch):
    state = {"issued": [], "revoked": [], "issue_error": None}

    async def issue(http, student_id):
        assert http is not None  # always the live password-login session
        if state["issue_error"]:
            raise state["issue_error"]
        n = len(state["issued"]) + 1
        state["issued"].append(student_id)
        return pt.ManagedToken(token=f"managed-secret-{n}", token_id=str(n), expires_at=None)

    async def revoke(http, token_id):
        assert http is not None and not http.is_closed  # revocation needs the open login session
        state["revoked"].append(token_id)
        return True

    monkeypatch.setattr(pt, "issue_managed_token", issue)
    monkeypatch.setattr(pt, "revoke_token", revoke)
    return state


async def _cred() -> LmsCredential:
    async with db_base.sessionmaker()() as db:
        return await db.get(LmsCredential, 1)


async def _set(**values):
    async with db_base.sessionmaker()() as db:
        await db.execute(update(LmsCredential).where(LmsCredential.user_id == 1).values(**values))
        await db.commit()


async def _login(api):
    r = await api.post("/auth/login", data={"username": "20201234", "password": "right"})
    assert r.status_code == 200, r.text


async def _ready(api) -> dict:
    """가입 직후에는 사용자 행이 없어 발급하지 않는다. 승인된 사용자의 다음 로그인에서 발급된다."""
    h = await _signup(api)
    await _login(api)
    return h


async def test_first_signup_and_unapproved_users_get_no_token(env, canvas):
    api, _ = env
    await _signup(api)
    assert canvas["issued"] == []
    async with db_base.sessionmaker()() as db:
        await db.execute(update(User).where(User.id == 1).values(access_status="pending"))
        await db.commit()
    await _login(api)
    assert canvas["issued"] == [] and (await _cred()).personal_token_enc is None


async def test_login_issues_encrypted_managed_token_once(env, canvas):
    api, _ = env
    h = await _ready(api)
    cred = await _cred()
    assert cred.personal_token_managed and cred.personal_token_status == "ok" and cred.personal_token_id == "1"
    assert cred.personal_token_enc != "managed-secret-1" and decrypt(cred.personal_token_enc) == "managed-secret-1"
    r = await api.get("/lms/credentials", headers=h)
    assert r.json()["personal_token_managed"] and "managed-secret" not in r.text
    await _login(api)
    assert canvas["issued"] == ["20201234"] and canvas["revoked"] == []  # working token is reused


async def test_dead_managed_token_is_reissued_and_old_revoked_in_login_session(env, canvas):
    api, _ = env
    await _ready(api)
    await _set(personal_token_status="invalid")
    await _login(api)
    cred = await _cred()
    assert decrypt(cred.personal_token_enc) == "managed-secret-2" and cred.personal_token_status == "ok"
    assert cred.personal_token_id == "2" and canvas["revoked"] == ["1"]


async def test_user_registered_token_is_never_replaced(env, canvas):
    api, _ = env
    await _signup(api)
    await _set(personal_token_enc=encrypt("mine"), personal_token_status="ok", personal_token_managed=False)
    await _login(api)
    assert canvas["issued"] == [] and decrypt((await _cred()).personal_token_enc) == "mine"


async def test_dead_user_token_is_replaced_without_revoking_it(env, canvas):
    api, _ = env
    await _signup(api)
    await _set(personal_token_enc=encrypt("mine"), personal_token_status="invalid", personal_token_managed=False)
    await _login(api)
    cred = await _cred()
    assert cred.personal_token_managed and decrypt(cred.personal_token_enc) == "managed-secret-1"
    assert canvas["revoked"] == []  # never touch a token the user created


@pytest.mark.parametrize("error", [pt.ManagedTokenUnavailable("restricted"), LmsError("down"),
                                   pt.TokenIdentityMismatch("other")])
async def test_issue_failure_does_not_block_login(env, canvas, error):
    api, _ = env
    h = await _signup(api)
    canvas["issue_error"] = error
    await _login(api)
    assert (await api.get("/me", headers=h)).status_code == 200
    cred = await _cred()
    assert cred.personal_token_enc is None and not cred.personal_token_managed and cred.session_enc


async def test_lost_save_race_revokes_new_token(env, canvas, monkeypatch):
    api, _ = env
    await _signup(api)
    from app.api import routes_account
    real = routes_account.save_credentials

    async def lose_token_save(db, cred, version, **values):
        if "personal_token_managed" in values:
            return False
        return await real(db, cred, version, **values)
    monkeypatch.setattr(routes_account, "save_credentials", lose_token_save)
    await _login(api)
    assert canvas["revoked"] == ["1"] and (await _cred()).personal_token_enc is None


async def test_manual_registration_replaces_managed_without_canvas_call(env, canvas, monkeypatch):
    api, _ = env
    h = await _ready(api)

    async def fake_session(token, student_id):
        return auth.LmsSession(user_id=student_id, bearer_token="XN", cookies=[])
    monkeypatch.setattr(pt, "session_from_token", fake_session)
    r = await api.put("/lms/access-token", headers=h, json={"access_token": "mine"})
    assert r.status_code == 200 and not r.json()["personal_token_managed"]
    cred = await _cred()
    assert decrypt(cred.personal_token_enc) == "mine" and cred.personal_token_id is None
    assert canvas["revoked"] == []


async def test_remove_and_account_delete_only_clear_local_copy(env, canvas):
    api, _ = env
    h = await _ready(api)
    r = await api.delete("/lms/access-token", headers=h)
    assert r.status_code == 200 and not r.json()["personal_token_managed"]
    cred = await _cred()
    assert cred.personal_token_enc is None and cred.personal_token_id is None
    await _login(api)  # removed → next login issues again
    assert canvas["issued"] == ["20201234", "20201234"]
    assert (await api.delete("/me", headers=h)).status_code == 204
    assert await _cred() is None and canvas["revoked"] == []
