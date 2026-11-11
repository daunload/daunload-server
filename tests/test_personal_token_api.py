import json

import pytest
from sqlalchemy import select, update

from app.db import base as db_base
from app.db.models import LmsCredential, User
from app.lms import auth, personal_token
from app.lms.errors import LmsError, SsoExpired
from app.security import decrypt, encrypt
from app.services.lms_access import lms_client_for, save_credentials
from tests.test_api import env, _signup  # noqa: F401


@pytest.fixture
def token_auth(monkeypatch):
    async def fake(token, student_id):
        if token == 'bad':
            raise personal_token.PersonalTokenInvalid('토큰 만료')
        if token == 'other':
            raise personal_token.TokenIdentityMismatch('다른 계정')
        if token == 'offline':
            raise LmsError('일시적 장애')
        return auth.LmsSession(user_id=student_id, bearer_token='XN', cookies=[])
    monkeypatch.setattr(personal_token, 'session_from_token', fake)


async def register(api, headers, token='private-secret'):
    return await api.put('/lms/access-token', headers=headers, json={'access_token': token})


async def test_register_replace_delete_and_login_preserves_token(env, token_auth):
    api, queue = env
    h = await _signup(api)
    r = await register(api, h)
    assert r.status_code == 200
    assert r.json()['personal_token_status'] == 'ok'
    assert r.json()['personal_token_registered']
    assert 'private-secret' not in r.text and 'XN' not in r.text
    assert queue.jobs[-1] == ('sync_user_job', (1,), 'sync:1')
    async with db_base.sessionmaker()() as db:
        cred = await db.get(LmsCredential, 1)
        assert cred.personal_token_enc != 'private-secret'
        assert decrypt(cred.personal_token_enc) == 'private-secret'
        assert 'private-secret' not in decrypt(cred.session_enc)
    for token, expected in [('bad', 400), ('other', 400), ('offline', 502)]:
        assert (await register(api, h, token)).status_code == expected
    await _signup(api)
    async with db_base.sessionmaker()() as db:
        cred = await db.get(LmsCredential, 1)
        assert decrypt(cred.personal_token_enc) == 'private-secret'
    r = await api.delete('/lms/access-token', headers=h)
    assert r.status_code == 200 and not r.json()['personal_token_registered']
    async with db_base.sessionmaker()() as db:
        cred = await db.get(LmsCredential, 1)
        assert cred.personal_token_enc is None and cred.session_enc


async def test_invalid_input_never_echoes_secret(env):
    api, _ = env
    h = await _signup(api)
    for payload in [{'access_token': {'private-secret': 1}}, {'access_token': 'private-secret\nfoo'}, {'access_token': ''}]:
        r = await api.put('/lms/access-token', headers=h, json=payload)
        assert r.status_code in (400, 422)
        assert 'private-secret' not in r.text


async def test_token_requires_approval_and_is_deleted_with_account(env, token_auth):
    api, _ = env
    h = await _signup(api)
    await register(api, h)
    async with db_base.sessionmaker()() as db:
        await db.execute(update(User).where(User.id == 1).values(access_status='revoked'))
        await db.commit()
    assert (await register(api, h)).status_code == 403
    assert (await api.delete('/lms/access-token', headers=h)).status_code == 403
    assert (await api.delete('/me', headers=h)).status_code == 204
    async with db_base.sessionmaker()() as db:
        assert await db.get(LmsCredential, 1) is None


async def test_token_recovery_persists_new_session(env, token_auth):
    api, _ = env
    h = await _signup(api)
    await register(api, h)
    async with db_base.sessionmaker()() as db:
        async with lms_client_for(db, 1) as client:
            await client.relogin()
            assert client.session.bearer_token == 'XN'
        cred = await db.get(LmsCredential, 1)
        assert json.loads(decrypt(cred.session_enc))['bearer_token'] == 'XN'


@pytest.mark.parametrize('token_failure,sso_ok,expected_status,expected_token_status', [
    ('invalid', True, 'ok', 'invalid'), ('invalid', False, 'invalid', 'invalid'),
    ('temporary', False, 'ok', 'ok'), ('temporary', True, 'ok', 'ok'),
])
async def test_recovery_fallback_classification(env, token_auth, monkeypatch, token_failure, sso_ok, expected_status, expected_token_status):
    api, _ = env
    h = await _signup(api)
    await register(api, h)
    async def fail_token(*args):
        if token_failure == 'invalid':
            raise personal_token.PersonalTokenInvalid('expired')
        raise LmsError('temporary')
    async def sso(old):
        if not sso_ok:
            raise SsoExpired('expired')
        return auth.LmsSession(old.user_id, 'SSO-NEW')
    monkeypatch.setattr(personal_token, 'session_from_token', fail_token)
    monkeypatch.setattr(auth, 'renew_from_sso', sso)
    async with db_base.sessionmaker()() as db:
        try:
            async with lms_client_for(db, 1) as client:
                await client.relogin()
        except LmsError as e:
            assert not sso_ok
            assert isinstance(e, SsoExpired) == (token_failure == 'invalid')
        cred = await db.get(LmsCredential, 1)
        assert cred.status == expected_status
        assert cred.personal_token_status == expected_token_status
        assert bool(cred.session_enc) == (expected_status == 'ok')


async def test_stale_worker_cannot_restore_removed_token_or_session(env, token_auth):
    api, _ = env
    h = await _signup(api)
    await register(api, h)
    async with db_base.sessionmaker()() as db:
        async with lms_client_for(db, 1) as client:
            old = await db.get(LmsCredential, 1)
            old_cipher = old.session_enc
            assert (await api.delete('/lms/access-token', headers=h)).status_code == 200
            client.session = auth.LmsSession('20201234', 'STALE')
        await db.refresh(old)
        assert old.personal_token_enc is None
        assert old.session_enc == old_cipher


async def test_stale_registration_cannot_overwrite_remove(env, token_auth, monkeypatch):
    api, _ = env
    h = await _signup(api)
    await register(api, h)
    async def concurrent(*args):
        assert (await api.delete('/lms/access-token', headers=h)).status_code == 200
        return auth.LmsSession('20201234', 'NEW')
    monkeypatch.setattr(personal_token, 'session_from_token', concurrent)
    assert (await register(api, h, 'replacement')).status_code == 409
    assert not (await api.get('/lms/credentials', headers=h)).json()['personal_token_registered']


async def test_valid_token_can_recover_missing_session(env, token_auth):
    api, _ = env
    h = await _signup(api)
    await register(api, h)
    async with db_base.sessionmaker()() as db:
        await db.execute(update(LmsCredential).where(LmsCredential.user_id == 1).values(session_enc=None, status='invalid'))
        await db.commit()
        async with lms_client_for(db, 1) as client:
            assert client.session.bearer_token == 'XN'
        cred = await db.get(LmsCredential, 1)
        assert cred.status == 'ok' and cred.session_enc


async def test_deleted_account_is_not_recreated_by_worker(env, token_auth):
    api, _ = env
    h = await _signup(api)
    await register(api, h)
    async with db_base.sessionmaker()() as db:
        async with lms_client_for(db, 1) as client:
            assert (await api.delete('/me', headers=h)).status_code == 204
            client.session = auth.LmsSession('20201234', 'STALE')
    async with db_base.sessionmaker()() as db:
        assert await db.get(LmsCredential, 1) is None


def test_preserve_sso_keeps_gateway_cookies_not_old_learningx():
    from app.services.lms_access import preserve_sso
    old = auth.LmsSession('20201234', 'OLD', cookies=[
        {'name': 'sToken', 'value': 'SSO', 'domain': '.ssu.ac.kr', 'path': '/'},
        {'name': 'smartid', 'value': 'GATEWAY', 'domain': 'smartid.ssu.ac.kr', 'path': '/'},
        {'name': 'xn_api_token', 'value': 'OLD', 'domain': '.ssu.ac.kr', 'path': '/'},
        {'name': '_normandy_session', 'value': 'OLD', 'domain': 'canvas.ssu.ac.kr', 'path': '/'},
    ])
    fresh = auth.LmsSession('20201234', 'NEW', cookies=[
        {'name': 'xn_api_token', 'value': 'NEW', 'domain': 'canvas.ssu.ac.kr', 'path': '/'},
        {'name': '_normandy_session', 'value': 'NEW', 'domain': 'canvas.ssu.ac.kr', 'path': '/'},
    ])
    preserve_sso(fresh, old)
    assert fresh.sso_token == 'SSO'
    assert {c['name']: c['value'] for c in fresh.cookies} == {
        'sToken': 'SSO', 'smartid': 'GATEWAY', 'xn_api_token': 'NEW', '_normandy_session': 'NEW'}


@pytest.mark.parametrize('learningx,still_expired', [(False, False), (True, False), (True, True)])
async def test_expired_request_automatically_recovers_once(env, token_auth, monkeypatch, learningx, still_expired):
    import httpx
    import respx
    from app.lms.errors import SessionExpired
    api, _ = env
    h = await _signup(api)
    await register(api, h)
    renewals = []
    async def fresh(token, student):
        renewals.append(student)
        return auth.LmsSession(student, 'FRESH')
    monkeypatch.setattr(personal_token, 'session_from_token', fresh)
    path = '/learningx/api/v1/test' if learningx else '/api/v1/test'
    with respx.mock as router:
        first = httpx.Response(400, text='Invalid request.', headers={'content-type': 'text/html'}) if learningx else httpx.Response(401)
        route = router.get(personal_token.BASE + path).mock(side_effect=[first, httpx.Response(401) if still_expired else httpx.Response(200, json=[])])
        async with db_base.sessionmaker()() as db:
            async with lms_client_for(db, 1) as client:
                if still_expired:
                    with pytest.raises(SessionExpired):
                        await client.get_json(path, bearer=learningx)
                else:
                    assert await client.get_json(path, bearer=learningx) == []
        assert len(route.calls) == 2
        if learningx:
            assert route.calls[-1].request.headers['Authorization'] == 'Bearer FRESH'
    assert renewals == ['20201234']


async def test_healthy_use_does_not_rewrite_credentials(env, token_auth):
    """정상 상태에서 쓰기만 하면 저장하지 않는다. 버전이 그대로라 동시에 돌던 긴 작업의 세션 갱신이 살아남는다."""
    api, _ = env
    h = await _signup(api)
    async with db_base.sessionmaker()() as db:
        before = (await db.get(LmsCredential, 1)).credential_version
        async with lms_client_for(db, 1):
            pass
        cred = await db.scalar(select(LmsCredential).where(LmsCredential.user_id == 1)
                               .execution_options(populate_existing=True))
        assert cred.credential_version == before and cred.status == 'ok'


async def test_error_state_is_cleared_after_successful_use(env, token_auth):
    api, _ = env
    h = await _signup(api)
    async with db_base.sessionmaker()() as db:
        await db.execute(update(LmsCredential).where(LmsCredential.user_id == 1)
                         .values(status='error', last_error='boom'))
        await db.commit()
        db.expire_all()
        async with lms_client_for(db, 1):
            pass
        cred = await db.scalar(select(LmsCredential).where(LmsCredential.user_id == 1)
                               .execution_options(populate_existing=True))
        assert cred.status == 'ok' and cred.last_error is None and cred.last_error_at is None
