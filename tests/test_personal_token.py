import logging

import httpx
import pytest
import respx

from app.lms import personal_token as pt
from app.lms.errors import LmsError

B = pt.BASE
TOKEN = 'private-canary-token'


def setup_routes(router=respx):
    profiles = iter([
        httpx.Response(200, json={'id': 7, 'login_id': '20201234'}),
        httpx.Response(200, json={'id': 7, 'login_id': '20201234'}),
    ])
    router.get(B + '/api/v1/users/self/profile').mock(side_effect=lambda r: next(profiles))
    router.get(B + '/login/session_token').respond(200, json={'session_url': B + '/?session_token=one-time-secret'})
    router.get(B + '/').respond(200, headers={'Set-Cookie': '_normandy_session=secret-cookie; Path=/'})
    router.get(B + '/api/v1/courses').respond(200, json=[{'id': 3, 'enrollment_term_id': 9}])
    router.get(B + '/api/v1/courses/3/tabs').respond(200, json=[
        {'id': 'context_external_tool_71', 'label': '주차학습', 'html_url': '/courses/3/external_tools/71'}])
    page = router.get(B + '/courses/3/external_tools/71').respond(200, text='''
        <form action="/ignored"><input name="unrelated" value="no"></form>
        <form method="POST" action="/learningx/lti/modulebuilder">
        <input name="oauth_signature" value="signed&amp;secret"><input name="user_id" value="7"></form>''')
    launch = router.post(B + '/learningx/lti/modulebuilder').respond(
        200, headers={'Set-Cookie': 'xn_api_token=learningx-secret; Path=/'}, text='<html>modules</html>')
    router.get(B + '/learningx/api/v1/users/20201234/terms').respond(200, json={'enrollment_terms': [{'id': 9}]})
    router.get(B + '/learningx/api/v1/learn_activities/courses').respond(200, json=[{'id': 3}])
    router.get(B + '/learningx/api/v1/learn_activities/learnstatus').respond(200, json={'learnstatuses': []})
    router.get(B + '/learningx/api/v1/courses/3/modules').respond(200, json=[])
    return page, launch


@respx.mock
async def test_clean_token_session_and_signed_launch(caplog):
    caplog.set_level(logging.DEBUG)
    _, launch = setup_routes()
    session = await pt.session_from_token(TOKEN, '20201234')
    assert session.user_id == '20201234'
    assert session.bearer_token == 'learningx-secret'
    assert session.sso_token == ''
    assert {c['name'] for c in session.cookies} >= {'xn_api_token', '_normandy_session'}
    assert dict(httpx.QueryParams(launch.calls[0].request.content.decode())) == {'oauth_signature': 'signed&secret', 'user_id': '7'}
    for call in respx.calls:
        req = call.request
        if req.url.path.startswith('/learningx/api/'):
            assert req.headers['Authorization'] == 'Bearer learningx-secret'
            assert not req.headers.get('cookie')
        elif req.url.path not in ('/api/v1/users/self/profile', '/login/session_token'):
            assert 'Authorization' not in req.headers
    for secret in (TOKEN, 'one-time-secret', 'secret-cookie', 'learningx-secret', 'signed&secret'):
        assert secret not in caplog.text


@respx.mock
@pytest.mark.parametrize('status,profile,error', [
    (401, {}, pt.PersonalTokenInvalid),
    (200, {'id': 7, 'login_id': 'another'}, pt.TokenIdentityMismatch),
    (403, {}, LmsError), (503, {}, LmsError),
])
async def test_profile_rejection(status, profile, error):
    respx.get(B + '/api/v1/users/self/profile').respond(status, json=profile)
    with pytest.raises(error):
        await pt.session_from_token(TOKEN, '20201234')
    assert len(respx.calls) == 1


@pytest.mark.parametrize('failure', ['external_session', 'external_redirect', 'missing_form', 'no_courses', 'missing_token', 'bad_api'])
@respx.mock(assert_all_called=False)
async def test_incomplete_auth_never_returns_session(failure, respx_mock):
    page, launch = setup_routes(respx_mock)
    if failure == 'external_session':
        respx_mock.get(B + '/login/session_token').respond(200, json={'session_url': 'https://evil.example/?secret=1'})
    elif failure == 'external_redirect':
        respx_mock.get(B + '/').respond(302, headers={'Location': 'https://evil.example/'})
    elif failure == 'missing_form':
        page.respond(200, text='<html>No forms</html>')
    elif failure == 'no_courses':
        respx_mock.get(B + '/api/v1/courses').respond(200, json=[])
    elif failure == 'missing_token':
        launch.respond(200, text='No token')
    else:
        respx_mock.get(B + '/learningx/api/v1/courses/3/modules').respond(200, json={'unexpected': True})
    with pytest.raises(LmsError):
        await pt.session_from_token(TOKEN, '20201234')
    assert all(c.request.url.host == 'canvas.ssu.ac.kr' for c in respx_mock.calls)


@respx.mock
async def test_network_error_does_not_expose_url():
    respx.get(B + '/api/v1/users/self/profile').mock(side_effect=httpx.ConnectError(TOKEN))
    with pytest.raises(LmsError) as exc:
        await pt.session_from_token(TOKEN, '20201234')
    assert TOKEN not in str(exc.value)
