"""Real MV3 extension smoke test with mocked school responses; no school credentials."""
import asyncio
import os
import re
import ssl
import subprocess
import threading
import json
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from tests.web_root import EXTENSION_CLIENT

ROOT = Path(__file__).resolve().parents[1]
MEDIA = {f'/{name}': (ROOT / 'tests' / 'fixtures' / 'media' / name).read_bytes()
         for name in ['tone-opus.mp4', 'tone-opus-12s.mp4']}
SMALL_RANGE = 16384  # test-only Range size so the 12 s fixture needs many requests
CDN = 'https://download-test.commonscdn.com'
UNSUPPORTED_AUDIO = '이 영상은 텍스트 변환을 지원하지 않습니다.'
pytestmark = pytest.mark.browser


@pytest.fixture
def school_cdn(tmp_path):
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=download-test.commonscdn.com'],
                   check=True, capture_output=True)
    seen = []
    release_downloads = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(('GET', self.path, dict(self.headers)))
            if self.path == '/expired.mp4':
                self.send_response(403)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if self.path in MEDIA:
                data = MEDIA[self.path]
                headers = {k.lower(): v for k, v in self.headers.items()}
                match = re.fullmatch(r'bytes=(\d+)-(\d*)', headers.get('range', ''))
                if not (match and headers.get('referer') == 'https://commons.ssu.ac.kr/'
                        and headers.get('origin') == 'https://commons.ssu.ac.kr'):
                    self.send_response(403)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                start = int(match[1])
                end = min(int(match[2]) if match[2] else len(data) - 1, len(data) - 1)
                body = data[start:end + 1]
                self.send_response(206)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == '/redirect.mp4':
                self.send_response(302)
                self.send_header('Location', '/test.mp4')
                self.end_headers()
                return
            if self.path == '/outside.mp4':
                self.send_response(302)
                self.send_header('Location', 'https://outside.invalid/blocked.mp4')
                self.end_headers()
                return
            valid = (self.headers.get('Referer') == 'https://commons.ssu.ac.kr/'
                     and self.headers.get('Origin') == 'https://commons.ssu.ac.kr')
            data = b'fake-video-bytes' if valid else b'missing required school headers'
            self.send_response(200 if valid else 403)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if self.path.startswith('/hold.mp4'):
                release_downloads.wait(timeout=20)
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass  # Closing the manager intentionally disconnects in-flight downloads.

        def do_OPTIONS(self):
            seen.append(('OPTIONS', self.path, dict(self.headers)))
            self.send_response(405)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, seen, release_downloads
    finally:
        release_downloads.set()
        server.shutdown()
        server.server_close()
        thread.join()


async def test_extension_bridge_download_and_validation(tmp_path, school_cdn):
    port, seen, release_downloads = school_cdn
    profile = tmp_path / 'profile'
    (profile / 'Default').mkdir(parents=True)
    (profile / 'Default' / 'Preferences').write_text(json.dumps({'download': {
        'default_directory': str(tmp_path / 'downloads'), 'prompt_for_download': False}}))
    extension = ROOT / 'extensions' / 'ssu-downloader'
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(tmp_path / 'profile'), channel='chromium', headless=True,
            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}',
                  '--ignore-certificate-errors', '--no-proxy-server',
                  f'--host-resolver-rules=MAP download-test.commonscdn.com 127.0.0.1:{port}, MAP outside.invalid 127.0.0.1:{port}'],
            downloads_path=str(tmp_path / 'downloads'),
            accept_downloads=True,
        )
        try:
            page = await context.new_page()
            await context.route('https://daunload.cloud/**', lambda route: route.fulfill(
                body='<html><body>dashboard</body></html>', content_type='text/html'))
            await page.goto('https://daunload.cloud/')
            await page.add_script_tag(content=EXTENSION_CLIENT)
            assert await page.evaluate('extensionRequest("ping")') == {'version': 1, 'sessions': True, 'queue': True, 'audio': True}
            # Production bridge must not be injected into localhost or a lookalike domain.
            for origin in ['http://localhost:8000', 'https://daunload.cloud.example.org']:
                await context.route(f'{origin}/**', lambda route: route.fulfill(
                    body='<html><body>untrusted</body></html>', content_type='text/html'))
                untrusted = await context.new_page()
                await untrusted.goto(origin + '/')
                await untrusted.add_script_tag(content=EXTENSION_CLIENT)
                connected = await untrusted.evaluate("""async () => {
                  try { await extensionRequest('ping'); return true; } catch { return false; }
                }""")
                assert connected is False
                # audio-open gets no answer at all (raw message, so the 15 s request timeout is not needed).
                answered = await untrusted.evaluate("""url => new Promise(resolve => {
                  addEventListener('message', e => { if (e.data?.channel === 'ssu-download-response') resolve(true); });
                  postMessage({channel: 'ssu-download-request', id: 'audio-probe', action: 'audio-open',
                               job: crypto.randomUUID(), url}, location.origin);
                  setTimeout(() => resolve(false), 1500);
                })""", f'{CDN}/tone-opus.mp4')
                assert answered is False
                await untrusted.close()

            await page.evaluate('''async () => {
              const first = await Promise.all([extensionRequest('prepare'), extensionRequest('prepare')]);
              if (first[0].session !== first[1].session) throw new Error('Duplicate manager session');
              window.__session = first[0].session;
            }''')
            bad = await page.evaluate('''async () => {
              try { await extensionRequest('start', {session: window.__session, job: crypto.randomUUID(), data: {url: 'https://evil.example/a.mp4', file_name: 'a.mp4'}}); }
              catch (e) { return e.message; }
            }''')
            assert '학교 CDN' in bad
            bad_name = await page.evaluate('''async () => {
              try { await extensionRequest('start', {session: window.__session, job: crypto.randomUUID(), data: {url: 'https://commons.ssu.ac.kr/a.mp4', file_name: '../a.mp4'}}); }
              catch (e) { return e.message; }
            }''')
            assert '파일명' in bad_name
            key = '12345678-1234-1234-1234-123456789012'
            result = await page.evaluate('''job => extensionRequest('start', {session: window.__session, job, data: {
              url: 'https://download-test.commonscdn.com/redirect.mp4', file_name: '테스트.mp4'
            }})''', key)
            assert result == {'started': True}
            for _ in range(50):
                manager = next((p for p in context.pages if '/manager.html' in p.url), None)
                if manager:
                    break
                await asyncio.sleep(.1)
            assert manager is not None
            assert len([p for p in context.pages if '/manager.html' in p.url]) == 1
            await manager.wait_for_load_state()
            window_type = await manager.evaluate("""async () => {
              const { managerTab } = await chrome.storage.session.get('managerTab');
              const tab = await chrome.tabs.get(managerTab);
              return (await chrome.windows.get(tab.windowId)).type;
            }""")
            assert window_type == 'popup'
            assert page.url == 'https://daunload.cloud/'
            assert await manager.evaluate("typeof showDirectoryPicker") == 'function'
            # Use a real browser filesystem in the temporary profile; only the native folder chooser is replaced.
            await manager.evaluate("""() => {
              window.showDirectoryPicker = () => navigator.storage.getDirectory();
            }""")
            await manager.get_by_role('button', name='저장 폴더 선택').click()
            for _ in range(50):
                status = await page.evaluate('job => extensionRequest("status", {job})', key)
                if status['state'] not in ('queued', 'in_progress'):
                    break
                await asyncio.sleep(.1)
            assert status['state'] == 'complete', json.dumps([status, seen], indent=2)
            assert status['bytesReceived'] == len(b'fake-video-bytes')
            assert await manager.evaluate("""async () => {
              const root = await navigator.storage.getDirectory();
              const handle = await root.getFileHandle('테스트.mp4');
              return Array.from(new Uint8Array(await (await handle.getFile()).arrayBuffer()));
            }""") == list(b'fake-video-bytes')
            assert [path for _, path, _ in seen] == ['/redirect.mp4', '/test.mp4']
            assert all({k.lower(): v for k, v in headers.items()}.get('referer') == 'https://commons.ssu.ac.kr/' for _, _, headers in seen)
            # Same request is idempotent, and another tab cannot inspect/cancel this job.
            assert await page.evaluate('''job => extensionRequest('start', {session: window.__session, job, data: {
              url: 'https://download-test.commonscdn.com/redirect.mp4', file_name: '테스트.mp4'
            }})''', key) == {'started': True}
            other = await context.new_page()
            await other.goto('https://daunload.cloud/')
            await other.add_script_tag(content=EXTENSION_CLIENT)
            denied = await other.evaluate('''async job => {
              try { await extensionRequest('cancel', {job}); } catch (e) { return e.message; }
            }''', key)
            assert '이 창에서' in denied
            # Focusing the manager again must reuse its existing popup and folder selection.
            worker = context.service_workers[0]
            await worker.evaluate('exclusive(() => ensureManager(true))')
            assert len([p for p in context.pages if '/manager.html' in p.url]) == 1
            outside = '87654321-1234-1234-1234-123456789012'
            await page.evaluate('''job => extensionRequest('start', {session: window.__session, job, data: {
              url: 'https://download-test.commonscdn.com/outside.mp4', file_name: 'outside.mp4'
            }})''', outside)
            for _ in range(50):
                status = await page.evaluate('job => extensionRequest("status", {job})', outside)
                if status['state'] not in ('queued', 'in_progress'):
                    break
                await asyncio.sleep(.1)
            assert status['state'] == 'interrupted', status
            assert not any(path == '/blocked.mp4' for _, path, _ in seen)

            # Close/reopen clears the visible queue without losing old results needed by the dashboard.
            await worker.evaluate("""async () => {
              const {managerTab} = await chrome.storage.session.get('managerTab');
              await chrome.tabs.remove(managerTab);
            }""")
            await page.evaluate("""async () => {
              const responses = await Promise.all([extensionRequest('prepare'), extensionRequest('prepare')]);
              if (responses[0].session !== responses[1].session) throw new Error('Duplicate popup');
              window.__session = responses[0].session;
            }""")
            old_session = await page.evaluate('window.__session')
            queued = [f'queued-00000000000000{i}' for i in range(1, 5)]
            results = await page.evaluate("""keys => Promise.all(keys.map(job => extensionRequest('start', {
              session: window.__session, job, data: {url:'https://download-test.commonscdn.com/hold.mp4', file_name:'queued.mp4'}
            })))""", queued)
            assert results == [{'started': True}] * 4
            for _ in range(50):
                managers = [p for p in context.pages if '/manager.html' in p.url]
                if len(managers) == 1:
                    break
                await asyncio.sleep(.1)
            assert len(managers) == 1
            current = managers[0]
            await current.wait_for_load_state()
            visible = await current.evaluate("send('manager-list')")
            assert set(visible) == set(queued)
            assert all(job['state'] == 'queued' for job in visible.values())
            assert await current.evaluate('directory') is None
            assert await current.locator('#jobs > li').count() == 4
            await current.evaluate("window.showDirectoryPicker = () => navigator.storage.getDirectory()")
            await current.get_by_role('button', name='저장 폴더 선택').click()
            await current.wait_for_function("""async () => {
              const jobs = Object.values(await send('manager-list'));
              return jobs.filter(j => j.state === 'in_progress').length === 2 && jobs.filter(j => j.state === 'queued').length === 2;
            }""")
            for _ in range(50):
                if len([path for _, path, _ in seen if path.startswith('/hold.mp4')]) == 2:
                    break
                await asyncio.sleep(.1)
            assert len([path for _, path, _ in seen if path.startswith('/hold.mp4')]) == 2

            old_tab = await worker.evaluate("""async () => {
              const {managerTab} = await chrome.storage.session.get('managerTab');
              await chrome.tabs.remove(managerTab);
              return managerTab;
            }""")
            # currentManager detects closure even before the queued onRemoved callback runs.
            statuses = await page.evaluate("keys => Promise.all(keys.map(job => extensionRequest('status', {job})))", queued)
            assert all(item['code'] == 'MANAGER_CLOSED' for item in statuses)
            release_downloads.set()
            late = await page.evaluate("""session => extensionRequest('start', {
              session, job:'late-0000000000000001',
              data:{url:'https://download-test.commonscdn.com/test.mp4', file_name:'late.mp4'}
            })""", old_session)
            assert late['closed'] is True
            assert await worker.evaluate("chrome.storage.session.get('managerTab')") == {'managerTab': None}

            await page.evaluate("async () => { window.__session = (await extensionRequest('prepare')).session; }")
            assert await page.evaluate('window.__session') != old_session
            # A delayed event from the old popup must not close or modify the new session.
            await worker.evaluate('tab => exclusive(() => closeManager(tab))', old_tab)
            result = await page.evaluate("""() => extensionRequest('start', {
              session:window.__session, job:'fresh-000000000000001',
              data:{url:'https://download-test.commonscdn.com/test.mp4',file_name:'fresh.mp4'}
            })""")
            assert result == {'started': True}
            state = await page.evaluate("extensionRequest('status', {job:'fresh-000000000000001'})")
            assert state['state'] == 'queued'

        finally:
            await context.close()


@asynccontextmanager
async def dashboard(tmp_path, port):
    extension = ROOT / 'extensions' / 'ssu-downloader'
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(tmp_path / 'profile'), channel='chromium', headless=True,
            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}',
                  '--ignore-certificate-errors', '--no-proxy-server',
                  f'--host-resolver-rules=MAP download-test.commonscdn.com 127.0.0.1:{port}'],
        )
        try:
            await context.route('https://daunload.cloud/**', lambda route: route.fulfill(
                body='<html><body>dashboard</body></html>', content_type='text/html'))
            page = await context.new_page()
            await page.goto('https://daunload.cloud/')
            await page.add_script_tag(content=EXTENSION_CLIENT)
            yield context, page
        finally:
            await context.close()


# open -> read until done -> close. Returns sample counts per pcm chunk, the done reply and refresh count.
READ_ALL = """async ({ job, url, refreshUrl, chunkBytes, opened = false }) => {
  if (!opened) await extensionRequest('audio-open', chunkBytes ? { job, url, chunkBytes } : { job, url });
  const chunks = [];
  let refreshes = 0, next = null;
  for (let i = 0; i < 100; i++) {
    const reply = await extensionRequest('audio-read', next ? { job, url: next } : { job });
    next = null;
    if (reply.refresh) { refreshes++; next = refreshUrl; continue; }
    if (reply.done) {
      await extensionRequest('audio-close', { job });
      return { chunks, done: reply, refreshes };
    }
    if (typeof reply.pcm !== 'string' || typeof reply.seconds !== 'number') throw new Error(JSON.stringify(reply));
    const samples = atob(reply.pcm).length / 2;
    if (Math.abs(reply.seconds - samples / 16000) > 1e-6) throw new Error('seconds mismatch');
    chunks.push(samples);
  }
  throw new Error('too many reads');
}"""

FIRST_PCM = """async ({ job, url, chunkBytes }) => {
  await extensionRequest('audio-open', chunkBytes ? { job, url, chunkBytes } : { job, url });
  const reply = await extensionRequest('audio-read', { job });
  if (typeof reply.pcm !== 'string') throw new Error(JSON.stringify(reply));
  return atob(reply.pcm).length / 2;
}"""

AUDIO_ERROR = """async ({ action, data }) => {
  try { return JSON.stringify(await extensionRequest(action, data)); } catch (e) { return e.message; }
}"""


def commons_headers(seen):
    requests = [{k.lower(): v for k, v in headers.items()} for _, path, headers in seen if path in MEDIA]
    assert requests
    return all(h.get('referer') == 'https://commons.ssu.ac.kr/' and h.get('origin') == 'https://commons.ssu.ac.kr'
               for h in requests)


async def test_audio_pipeline_through_extension(tmp_path, school_cdn):
    port, seen, _ = school_cdn
    async with dashboard(tmp_path, port) as (_, page):
        assert (await page.evaluate('extensionRequest("ping")'))['audio'] is True
        result = await page.evaluate(READ_ALL, {'job': '11111111-1111-4111-8111-111111111111', 'url': f'{CDN}/tone-opus.mp4'})
        assert abs(sum(result['chunks']) - 48000) <= 1600, result
        assert all(0 < n <= 80000 for n in result['chunks'])
        assert 2.9 <= result['done']['duration'] <= 3.1
        assert commons_headers(seen)
        assert not any(method == 'OPTIONS' for method, _, _ in seen)


async def test_audio_rejects_other_hosts(tmp_path, school_cdn):
    port, seen, _ = school_cdn
    async with dashboard(tmp_path, port) as (_, page):
        for url in ['https://evil.example/a.mp4', 'https://commons.ssu.ac.kr/a.mp4',
                    'http://download-test.commonscdn.com/a.mp4', 'https://download-test.commonscdn.com:8443/a.mp4']:
            message = await page.evaluate(AUDIO_ERROR, {'action': 'audio-open',
                                                         'data': {'job': '22222222-2222-4222-8222-222222222222', 'url': url}})
            assert message == UNSUPPORTED_AUDIO, url
        for chunk in [0, 100, 1.5, '4096', 64 * 1024 * 1024]:
            bad = await page.evaluate(AUDIO_ERROR, {'action': 'audio-open', 'data': {
                'job': '22222222-2222-4222-8222-222222222222', 'url': f'{CDN}/tone-opus.mp4', 'chunkBytes': chunk}})
            assert bad == '올바르지 않은 요청입니다.', chunk
        bad_job = await page.evaluate(AUDIO_ERROR, {'action': 'audio-open', 'data': {'job': '../x', 'url': f'{CDN}/tone-opus.mp4'}})
        assert '작업 번호' in bad_job
        assert seen == []


async def test_audio_refresh_round_trip(tmp_path, school_cdn):
    port, seen, _ = school_cdn
    async with dashboard(tmp_path, port) as (_, page):
        result = await page.evaluate(READ_ALL, {'job': '33333333-3333-4333-8333-333333333333',
                                                'url': f'{CDN}/expired.mp4', 'refreshUrl': f'{CDN}/tone-opus.mp4'})
        assert result['refreshes'] == 1
        assert abs(sum(result['chunks']) - 48000) <= 1600, result
        assert 2.9 <= result['done']['duration'] <= 3.1
        assert ('GET', '/expired.mp4') in [(m, path) for m, path, _ in seen]
        assert commons_headers(seen)


async def test_audio_long_file_reads_five_seconds_at_a_time(tmp_path, school_cdn):
    port, seen, _ = school_cdn
    async with dashboard(tmp_path, port) as (_, page):
        result = await page.evaluate(READ_ALL, {'job': '77777777-7777-4777-8777-777777777777',
                                                'url': f'{CDN}/tone-opus-12s.mp4', 'chunkBytes': SMALL_RANGE})
        duration = result['done']['duration']
        assert 11.9 <= duration <= 12.1
        chunks = result['chunks']
        # 12 s arrives as 5 s + 5 s + ~2 s: decoded pieces larger than the cap are split and carried over.
        assert chunks.count(80000) >= 2 and max(chunks) == 80000, chunks
        assert abs(sum(chunks) - duration * 16000) <= 1600, chunks
        assert len([p for _, p, _ in seen if p == '/tone-opus-12s.mp4']) > 5
        assert commons_headers(seen)


async def test_audio_close_stops_fetching(tmp_path, school_cdn):
    port, seen, _ = school_cdn
    url = f'{CDN}/tone-opus-12s.mp4'
    count_requests = lambda: len([p for _, p, _ in seen if p == '/tone-opus-12s.mp4'])
    job = '44444444-4444-4444-8444-444444444444'
    async with dashboard(tmp_path, port) as (_, page):
        await page.evaluate(READ_ALL, {'job': '88888888-8888-4888-8888-888888888888', 'url': url, 'chunkBytes': SMALL_RANGE})
        full = count_requests()
        seen.clear()
        assert await page.evaluate(FIRST_PCM, {'job': job, 'url': url, 'chunkBytes': SMALL_RANGE}) == 80000
        # Nothing is fetched ahead between audio-read calls.
        after_first = count_requests()
        await asyncio.sleep(.5)
        assert count_requests() == after_first
        assert 0 < after_first < full, (after_first, full)
        assert await page.evaluate('job => extensionRequest("audio-close", {job})', job) == {'closed': True}
        await asyncio.sleep(.5)
        assert count_requests() == after_first
        message = await page.evaluate(AUDIO_ERROR, {'action': 'audio-read', 'data': {'job': job}})
        assert message == '텍스트 변환 작업이 끝났습니다. 다시 시도해 주세요.', message
        assert count_requests() == after_first
        # Unknown jobs close the same way.
        assert await page.evaluate('extensionRequest("audio-close", {job: "55555555-5555-4555-8555-555555555555"})') == {'closed': True}


async def test_second_open_tells_the_first_job_why_it_stopped(tmp_path, school_cdn):
    port, _, _ = school_cdn
    first, second = '99999999-9999-4999-8999-999999999991', '99999999-9999-4999-8999-999999999992'
    url = f'{CDN}/tone-opus-12s.mp4'
    async with dashboard(tmp_path, port) as (context, page):
        assert await page.evaluate(FIRST_PCM, {'job': first, 'url': url, 'chunkBytes': SMALL_RANGE}) == 80000
        assert await page.evaluate(FIRST_PCM, {'job': second, 'url': url, 'chunkBytes': SMALL_RANGE}) == 80000
        message = await page.evaluate(AUDIO_ERROR, {'action': 'audio-read', 'data': {'job': first}})
        assert message == '다른 탭에서 텍스트 변환을 시작해 이 변환을 멈췄습니다.', message
        # The running job is unaffected; closing it leaves no offscreen document behind.
        assert await page.evaluate('job => extensionRequest("audio-close", {job})', second) == {'closed': True}
        for _ in range(50):
            contexts = await context.service_workers[0].evaluate(
                "chrome.runtime.getContexts({contextTypes: ['OFFSCREEN_DOCUMENT']}).then(c => c.length)")
            if contexts == 0:
                break
            await asyncio.sleep(.1)
        assert contexts == 0


async def worker_targets(cdp):
    targets = (await cdp.send('Target.getTargets'))['targetInfos']
    return [t['targetId'] for t in targets if t['type'] == 'service_worker' and t['url'].endswith('/worker.js')]


async def worker_eval(cdp, target_id, expression):
    """Evaluate in the extension worker over raw CDP (Playwright keeps a stale Worker after a restart)."""
    reply = asyncio.get_running_loop().create_future()
    cdp.on('Target.receivedMessageFromTarget', lambda e: reply.done() or (
        json.loads(e['message']).get('id') == 1 and reply.set_result(json.loads(e['message']))))
    session = (await cdp.send('Target.attachToTarget', {'targetId': target_id, 'flatten': False}))['sessionId']
    await cdp.send('Target.sendMessageToTarget', {'sessionId': session, 'message': json.dumps({
        'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': expression, 'returnByValue': True}})})
    return (await asyncio.wait_for(reply, 5))['result']['result']['value']


async def test_audio_survives_service_worker_restart(tmp_path, school_cdn):
    port, _, _ = school_cdn
    job = '66666666-6666-4666-8666-666666666666'
    async with dashboard(tmp_path, port) as (context, page):
        first = await page.evaluate(FIRST_PCM, {'job': job, 'url': f'{CDN}/tone-opus-12s.mp4', 'chunkBytes': SMALL_RANGE})
        assert first == 80000
        await context.service_workers[0].evaluate('self.__beforeRestart = true')
        # Terminate the extension service worker (what Puppeteer's WebWorker.close() does).
        cdp = await context.new_cdp_session(page)
        [target] = await worker_targets(cdp)
        assert (await cdp.send('Target.closeTarget', {'targetId': target}))['success']
        for _ in range(50):
            if not await worker_targets(cdp):
                break
            await asyncio.sleep(.1)
        assert await worker_targets(cdp) == []
        result = await page.evaluate(READ_ALL, {'job': job, 'opened': True})
        [target] = await worker_targets(cdp)
        # A fresh worker: the global set before termination is gone.
        assert await worker_eval(cdp, target, 'typeof self.__beforeRestart') == 'undefined'
        # The job resumes mid-stream: more PCM follows the restart, not just done.
        assert len(result['chunks']) >= 2 and result['chunks'][0] == 80000, result
        duration = result['done']['duration']
        assert 11.9 <= duration <= 12.1
        assert abs(first + sum(result['chunks']) - duration * 16000) <= 1600, result
