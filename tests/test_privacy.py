from html.parser import HTMLParser

import httpx

from app.main import create_app


class PageResources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.scripts.append(attrs)
        if tag in ('img', 'iframe', 'script', 'link'):
            self.resources.append(attrs.get('src') or attrs.get('href') or '')


async def test_privacy_page_is_public_and_has_no_remote_tracking():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url='https://test') as api:
        response = await api.get('/privacy.html')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/html')
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['x-frame-options'] == 'DENY'
        assert '개인정보처리방침' in response.text
        assert 'mailto:daun4799@gmail.com' in response.text
        assert 'chrome.storage.session' in response.text
        assert '계정 삭제' in response.text
        assert '암호화한 학교 비밀번호' in response.text
        assert '학교 공식 서비스가 아닙니다' in response.text
        page = PageResources()
        page.feed(response.text)
        assert not page.scripts
        assert all(not resource.startswith(('https://', 'http://', '//')) or resource == 'https://daunload.cloud/privacy.html'
                   for resource in page.resources)
        dashboard = await api.get('/')
        assert 'href="/privacy.html"' in dashboard.text
