import pytest
import respx
from sqlalchemy import select

from app.db import base as db_base
from app.db.models import DownloadRecord, SavedVideo
from app.lms.media import CONTENT_PHP
from app.security import create_download_token
from tests.test_api import env  # noqa: F401
from tests.test_media import CDN, VIDEO_XML, _with_course


@respx.mock
async def test_direct_download_only_fetches_metadata_and_records_after_completion(env):
    api, _ = env
    headers = await _with_course(api)
    respx.get(CONTENT_PHP).respond(200, text=VIDEO_XML)
    # Any attempted request to the actual MP4 fails the test (no matching respx route).
    result = await api.post('/courses/45446/videos/11/direct-download', headers=headers)
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    data = result.json()
    assert data['url'] == f'{CDN}/lec.mp4'
    assert set(data) == {'url', 'file_name', 'receipt'}
    async with db_base.sessionmaker()() as db:
        assert await db.scalar(select(DownloadRecord)) is None
    assert (await api.get('/me', headers={'Authorization': f"Bearer {data['receipt']}"})).status_code == 401
    assert (await api.get(f"/downloads/{data['receipt']}")).status_code == 401
    assert (await api.post('/downloads/complete', headers=headers, json={'receipt': data['receipt']})).status_code == 204
    async with db_base.sessionmaker()() as db:
        record = await db.scalar(select(DownloadRecord))
        assert record.item_key == 'v11'


@respx.mock
@pytest.mark.parametrize(('path', 'code'), [
    ('/courses/45446/videos/12/direct-download', 404),
    ('/courses/45446/videos/21/direct-download', 409),
    ('/courses/99999/videos/11/direct-download', 404),
    ('/saved-videos/999/direct-download', 404),
])
async def test_direct_download_ownership_and_availability(env, path, code):
    api, _ = env
    headers = await _with_course(api)
    assert (await api.post(path, headers=headers)).status_code == code


@respx.mock
async def test_saved_direct_download_without_network_and_rejects_invalid_receipts(env):
    api, _ = env
    headers = await _with_course(api)
    async with db_base.sessionmaker()() as db:
        db.add(SavedVideo(user_id=1, lms_course_id=45446, course_name='리눅스', module_item_id=11,
                          module_title='1주차', title='1강', content_id='m11', media_url=f'{CDN}/a.mp4', file_name='a.mp4'))
        await db.commit()
    result = await api.post('/saved-videos/1/direct-download', headers=headers)
    assert result.status_code == 200 and result.json()['url'] == f'{CDN}/a.mp4'
    for token in [
        'invalid',
        create_download_token(1, {'kind': 'video'}),
        create_download_token(2, {'kind': 'direct_receipt', 'version': 0}),
        create_download_token(1, {'kind': 'direct_receipt', 'version': 99}),
        create_download_token(1, {'kind': 'direct_receipt', 'version': 0}, minutes=-1),
    ]:
        assert (await api.post('/downloads/complete', headers=headers, json={'receipt': token})).status_code == 401


@respx.mock
async def test_legacy_video_relay_disabled_without_affecting_direct_download(env, monkeypatch):
    from app.config import get_settings
    api, _ = env
    headers = await _with_course(api)
    monkeypatch.setenv('ALLOW_VIDEO_RELAY', 'false')
    get_settings.cache_clear()
    assert (await api.post('/courses/45446/videos/11/download-link', headers=headers)).status_code == 410
    token = create_download_token(1, {'content_id': 'm11', 'file_name': 'a.mp4'})
    assert (await api.get(f'/downloads/{token}')).status_code == 410
    respx.get(CONTENT_PHP).respond(200, text=VIDEO_XML)
    assert (await api.post('/courses/45446/videos/11/direct-download', headers=headers)).status_code == 200
