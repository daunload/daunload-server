"""강의 영상·수업자료 화면 (정적 파일 + 모킹한 API)."""

import pytest
from playwright.async_api import expect

from tests.test_web import dashboard  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser


def _video(mid, title, downloaded=False, transcript=None):
    return {"module_item_id": mid, "module_id": 1, "module_title": "1주차", "title": title, "content_type": "movie",
            "duration": 600, "size": 100_000_000, "completed": False, "available": True,
            "thumbnail_url": "https://commons.ssu.ac.kr/thumb.jpg",
            "downloaded_at": "2026-10-29T00:00:00Z" if downloaded else None, "transcript_id": transcript}


async def test_video_tab_badges_filter_and_materials_tab(dashboard):
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(11, "받은 강의", downloaded=True), _video(12, "안 받은 강의")]
    data["/courses/1/materials"] = [
        {"key": "c21", "kind": "commons_file", "module_id": 1, "module_title": "1주차", "title": "1장",
         "file_name": "1장.pptx", "size": 2_000_000, "downloaded_at": None},
        {"key": "f701", "kind": "canvas_file", "module_id": 1, "module_title": "1주차", "title": "1주차 자료",
         "file_name": "노트.pdf", "size": None, "downloaded_at": "2026-10-29T00:00:00Z"},
    ]
    await page.goto("http://dashboard.test/#videos")
    rows = page.locator("#video-list li.video-row")
    await expect(rows).to_have_count(2)
    await expect(rows.nth(0).locator(".received")).to_be_visible()
    await expect(rows.nth(1).locator(".received")).to_have_count(0)
    await expect(page.get_by_role("button", name="1주차 전체 내려받기 (2)")).to_be_visible()

    await page.get_by_label("안 받은 것만").check()
    await expect(rows).to_have_count(1)
    await expect(rows.nth(0)).to_contain_text("안 받은 강의")

    await page.get_by_role("tab", name="수업자료").click()
    await expect(rows).to_have_count(1)  # 필터가 유지되어 받은 노트.pdf는 숨김
    await expect(rows.nth(0)).to_contain_text("1장.pptx")
    await page.get_by_label("안 받은 것만").uncheck()
    await expect(rows).to_have_count(2)
    await expect(rows.nth(1)).to_contain_text("위키 첨부")


async def test_material_download_marks_row(dashboard):
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = []
    data["/courses/1/materials"] = [{"key": "c21", "kind": "commons_file", "module_id": 1, "module_title": "1주차",
                                     "title": "1장", "file_name": "1장.pptx", "size": 1, "downloaded_at": None}]
    data["/courses/1/materials/c21/download-link"] = {"url": "/downloads/tok", "file_name": "1장.pptx", "expires_in_minutes": 10}
    data["/downloads/tok"] = {}
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("tab", name="수업자료").click()
    await page.get_by_role("button", name="내려받기", exact=True).click()
    await expect(page.locator("#video-list").locator(".received")).to_be_visible()


FAKE_FOLDER = """
window.__saved = {};
window.showDirectoryPicker = async () => ({
  getFileHandle: async (name) => ({
    createWritable: async () => {
      const parts = [];
      return new WritableStream({ write: (c) => parts.push(c), close: () => { window.__saved[name] = parts.length; } });
    },
  }),
});
"""


async def test_bulk_download_waits_out_busy_server(dashboard):
    """텍스트 변환이 사용자 자리 하나를 쓰고 있어도 여러 개 받기는 자리가 날 때까지 기다렸다 받는다."""
    page, data = dashboard
    await page.add_init_script(FAKE_FOLDER)
    await page.clock.install()
    data["/saved-videos"] = []
    data["/courses/1/videos"] = []
    material = {"kind": "commons_file", "module_id": 1, "module_title": "1주차", "size": 1, "downloaded_at": None}
    data["/courses/1/materials"] = [{**material, "key": "c21", "title": "자료", "file_name": "자료.pdf"},
                                    {**material, "key": "c22", "title": "자료2", "file_name": "자료2.pdf"}]
    links, busy = [], {"left": 8}

    async def link(route):
        links.append(1)
        name = "자료2.pdf" if "c22" in route.request.url else "자료.pdf"
        await route.fulfill(json={"url": "/downloads/tok", "file_name": name, "expires_in_minutes": 10})

    async def download(route):
        if busy["left"]:
            busy["left"] -= 1
            await route.fulfill(status=429, json={"detail": "동시에 2개까지 받을 수 있습니다."})
        else:
            await route.fulfill(body=b"mp4", content_type="video/mp4")

    await page.route("**/courses/1/materials/*/download-link", link)
    await page.route("**/downloads/tok", download)
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("tab", name="수업자료").click()
    await page.get_by_role("button", name="1주차 전체 내려받기 (2)").click()
    await expect(page.locator("#task-sheet")).to_contain_text("다운로드 0/2개")
    for _ in range(100):  # 완료 토스트(3.2초)가 사라지기 전에 멈추도록 잘게 나눠 진행한다
        if await page.locator("#task-panel").is_hidden():
            break
        await page.clock.run_for(1000)
    await expect(page.locator("#task-panel")).to_be_hidden()
    await expect(page.locator("#toast")).to_contain_text("2개를 모두 받았어요")
    assert await page.evaluate("window.__saved['자료.pdf']") == 1
    assert await page.evaluate("window.__saved['자료2.pdf']") == 1
    assert len(links) == 2  # 기다리는 동안 링크를 새로 발급하지 않는다(이력 횟수가 늘지 않게)


FAKE_EXTENSION = """
crypto.randomUUID = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');
window.__extensionState = 'in_progress';
window.__extensionCalls = [];
window.addEventListener('message', event => {
  const m = event.data;
  if (m?.channel !== 'ssu-download-request') return;
  window.__extensionCalls.push(m.action);
  const result = m.action === 'ping' ? {version: 1, sessions: true, queue: true} : m.action === 'prepare' ? {session: 'test-session'} : m.action === 'start' ? {started: true}
    : m.action === 'cancel' ? {cancelled: true} : {state: window.__extensionState, code: window.__extensionCode, error: window.__extensionError};
  window.postMessage({channel: 'ssu-download-response', id: m.id, result}, location.origin);
});
"""


async def test_direct_video_records_only_after_extension_completion(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_EXTENSION)
    data['/saved-videos'] = []
    data['/courses/1/videos'] = [_video(11, '직접 받기')]
    data['/courses/1/videos/11/direct-download'] = {
        'url': 'https://ssuin-object.commonscdn.com/a.mp4', 'file_name': 'a.mp4', 'receipt': 'receipt'}
    calls = []
    async def complete(route):
        calls.append(route.request.post_data_json)
        await route.fulfill(status=204)
    await page.route('**/downloads/complete', complete)
    await page.goto('http://dashboard.test/#videos')
    await page.get_by_role('button', name='내려받기', exact=True).click()
    await expect(page.get_by_role('button', name='다운로드 중', exact=True)).to_be_disabled()
    assert calls == []
    await expect(page.locator('#video-list').locator('.received')).to_have_count(0)
    await page.evaluate("window.__extensionState = 'complete'")
    await expect(page.locator('#video-list').locator('.received')).to_be_visible()
    assert calls == [{'receipt': 'receipt'}]


async def test_missing_extension_never_falls_back_to_relay(dashboard):
    page, data = dashboard
    await page.add_init_script("crypto.randomUUID = () => 'test-missing-extension'")
    data['/saved-videos'] = []
    data['/courses/1/videos'] = [_video(11, '직접 받기')]
    requested = []
    page.on('request', lambda req: requested.append(req.url))
    await page.goto('http://dashboard.test/#videos')
    await page.get_by_role('button', name='내려받기', exact=True).click()
    await expect(page.locator('#video-list details')).to_contain_text('확장프로그램에 연결하지 못했습니다')
    assert not any('/download-link' in url or '/direct-download' in url or '/downloads/' in url for url in requested)


async def _open_panel(page):
    bar = page.locator("#task-bar")
    if await bar.get_attribute("aria-expanded") != "true":
        await bar.click()
    await expect(page.locator("#task-sheet")).to_be_visible()
    return page.locator("#task-sheet")


async def _bulk_videos(page, data, count=4):
    await page.add_init_script(FAKE_EXTENSION)
    data['/saved-videos'] = []
    data['/courses/1/videos'] = [_video(mid, f'강의 {mid}') for mid in range(11, 11 + count)]
    data['/downloads/complete'] = {}
    for mid in range(11, 11 + count):
        data[f'/courses/1/videos/{mid}/direct-download'] = {
            'url': f'https://ssuin-object.commonscdn.com/{mid}.mp4', 'file_name': f'{mid}.mp4', 'receipt': 'receipt'}
    await page.goto('http://dashboard.test/#videos')


async def test_bulk_stops_on_closed_manager_and_can_restart(dashboard):
    page, data = dashboard
    await _bulk_videos(page, data)
    await page.get_by_role('button', name='1주차 전체 내려받기 (4)').click()
    await page.wait_for_function("window.__extensionCalls.filter(x => x === 'start').length === 4")
    # All four videos must be registered before any download has completed.
    sheet = await _open_panel(page)
    await expect(sheet).to_contain_text('다운로드 0/4개')
    await expect(page.locator('#video-list').locator('.received')).to_have_count(0)
    await page.evaluate("""() => {
      window.__extensionState = 'interrupted';
      window.__extensionCode = 'MANAGER_CLOSED';
      window.__extensionError = '다운로드 창을 닫았습니다. 다시 내려받기를 눌러 주세요.';
    }""")
    await expect(sheet).to_contain_text('다운로드 실패 4개')
    await expect(sheet).to_contain_text('다운로드 창을 닫았습니다')
    assert await page.evaluate("window.__extensionCalls.filter(x => x === 'start').length") == 4
    assert await page.evaluate("window.__extensionCalls.filter(x => x === 'prepare').length") == 1
    assert await page.evaluate('__ssu.bulk.ctrl') is None
    await page.evaluate("""() => {
      window.__extensionState = 'complete';
      window.__extensionCode = undefined;
      window.__extensionCalls = [];
    }""")
    await page.get_by_role('button', name='1주차 전체 내려받기 (4)').click()
    await expect(page.locator('#toast')).to_contain_text('4개를 모두 받았어요')
    await expect(page.locator('#task-panel')).to_be_hidden()
    assert await page.evaluate("window.__extensionCalls.filter(x => x === 'prepare').length") == 1
    assert await page.evaluate("window.__extensionCalls.filter(x => x === 'start').length") == 4


async def test_bulk_progress_note_and_stop_in_panel(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_FOLDER)
    await _bulk_videos(page, data)
    await page.get_by_role('button', name='1주차 전체 내려받기 (4)').click()
    sheet = await _open_panel(page)
    await expect(sheet).to_contain_text('다운로드 0/4개')
    await expect(sheet).to_contain_text('이 탭과 다운로드 창을 열어 두세요')
    await expect(page.locator('#bulk-status')).to_have_count(0)
    await sheet.get_by_role('button', name='다운로드 중지').click()
    await expect(page.locator('#confirm-dialog')).not_to_be_visible()
    await expect(page.locator('#toast')).to_contain_text('0/4개 받고 중지했어요')
    await expect(page.locator('#task-panel')).to_be_hidden()
    assert await page.evaluate('__ssu.bulk.ctrl') is None


async def test_bulk_material_note(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_FOLDER)
    data['/saved-videos'] = []
    data['/courses/1/videos'] = []
    material = {"kind": "commons_file", "module_id": 1, "module_title": "1주차", "title": "자료", "size": 1, "downloaded_at": None}
    data['/courses/1/materials'] = [{**material, "key": "c21", "file_name": "자료.pdf"}, {**material, "key": "c22", "file_name": "자료2.pdf"}]
    data["/courses/1/materials/c21/download-link"] = {"url": "/downloads/tok", "file_name": "자료.pdf", "expires_in_minutes": 10}
    data["/courses/1/materials/c22/download-link"] = {"url": "/downloads/tok", "file_name": "자료2.pdf", "expires_in_minutes": 10}

    async def download(route):  # 응답하지 않고 붙잡아 둔다
        pass

    await page.route("**/downloads/tok", download)
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("tab", name="수업자료").click()
    await page.get_by_role("button", name="1주차 전체 내려받기 (2)").click()
    sheet = await _open_panel(page)
    await expect(sheet).to_contain_text("이 탭을 열어 두세요")
    await expect(sheet).not_to_contain_text("다운로드 창")


async def test_bulk_failure_can_retry_only_failed_items(dashboard):
    page, data = dashboard
    await _bulk_videos(page, data)
    await page.evaluate("window.__extensionState = 'complete'")
    fail = {"on": True}

    async def link13(route):
        if fail["on"]:
            await route.fulfill(status=502, json={"detail": "링크 <b>오류</b>"})
        else:
            await route.fulfill(json=data['/courses/1/videos/13/direct-download'])

    await page.route('**/courses/1/videos/13/direct-download', link13)
    await page.get_by_role('button', name='1주차 전체 내려받기 (4)').click()
    sheet = await _open_panel(page)
    await expect(sheet).to_contain_text('다운로드 실패 1개')
    await expect(sheet).to_contain_text('링크 <b>오류</b>')
    assert await sheet.locator('b').count() == 0
    await expect(page.locator('#video-list').locator('.received')).to_have_count(3)
    fail["on"] = False
    await page.evaluate("window.__extensionCalls = []")
    await sheet.get_by_role('button', name='다시 시도').click()
    await expect(page.locator('#toast')).to_contain_text('1개를 모두 받았어요')
    await expect(page.locator('#task-panel')).to_be_hidden()
    assert await page.evaluate("window.__extensionCalls.filter(x => x === 'start').length") == 1
    await expect(page.locator('#video-list').locator('.received')).to_have_count(4)


async def test_bulk_success_clears_row_and_toasts(dashboard):
    page, data = dashboard
    await _bulk_videos(page, data)
    await page.evaluate("window.__extensionState = 'complete'")
    await page.get_by_role('button', name='1주차 전체 내려받기 (4)').click()
    await expect(page.locator('#toast')).to_contain_text('4개를 모두 받았어요')
    await expect(page.locator('#task-panel')).to_be_hidden()


async def test_single_material_not_in_panel(dashboard):
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = []
    data["/courses/1/materials"] = [{"key": "c21", "kind": "commons_file", "module_id": 1, "module_title": "1주차",
                                     "title": "1장", "file_name": "1장.pptx", "size": 1, "downloaded_at": None}]
    data["/courses/1/materials/c21/download-link"] = {"url": "/downloads/tok", "file_name": "1장.pptx", "expires_in_minutes": 10}
    data["/downloads/tok"] = {}
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("tab", name="수업자료").click()
    await page.get_by_role("button", name="내려받기", exact=True).click()
    await expect(page.locator("#video-list").locator(".received")).to_be_visible()
    await expect(page.locator("#task-panel")).to_be_hidden()


def _material(title="자료"):
    return {"key": "c21", "kind": "commons_file", "module_id": 1, "module_title": title,
            "title": "1장", "file_name": "1장.pdf", "size": 1, "downloaded_at": None}


async def test_bulk_button_escapes_module_title(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_EXTENSION)
    data['/saved-videos'] = []
    title = '<a href="x">링크</a>'
    data['/courses/1/videos'] = [{**_video(11, '강의'), 'module_title': title}, {**_video(12, '강의 2'), 'module_title': title}]
    await page.goto('http://dashboard.test/#videos')
    await expect(page.locator('#video-list li.video-row')).to_have_count(2)
    assert await page.locator('#video-list .group-actions a').count() == 0
    await expect(page.locator('#video-list [data-bulk]')).to_have_attribute('aria-label', f'{title} 전체 내려받기 (2)')
    await expect(page.locator('#video-list [data-tx-group]')).to_have_attribute('aria-label', f'{title} 전체 텍스트로 변환 (2)')


async def test_materials_bulk_without_folder_support_shows_visible_reason(dashboard):
    page, data = dashboard
    await page.add_init_script("delete window.showDirectoryPicker")
    data['/saved-videos'] = []
    data['/courses/1/videos'] = []
    data['/courses/1/materials'] = [_material(), {**_material(), 'key': 'c22', 'file_name': '2장.pdf'}]
    await page.goto('http://dashboard.test/#videos')
    await page.get_by_role('tab', name='수업자료').click()
    group = page.locator('#video-list .group-actions')
    await expect(group.get_by_role('button')).to_be_disabled()
    await expect(group.locator('.reason')).to_be_visible()
    await expect(group.locator('.reason')).to_contain_text('데스크톱 Chrome')


async def test_panel_clears_when_bulk_ends_while_list_is_reloading(dashboard):
    page, data = dashboard
    await _bulk_videos(page, data, count=2)
    await page.get_by_role('button', name='1주차 전체 내려받기 (2)').click()
    await page.wait_for_function("window.__extensionCalls.filter(x => x === 'start').length === 2")
    await expect(page.locator('#task-panel')).to_be_visible()
    await page.evaluate("__ssu.state.videoList = null")  # 과목을 바꿔 목록을 다시 불러오는 중
    await page.evaluate("window.__extensionState = 'complete'")
    await expect(page.locator('#toast')).to_contain_text('2개를 모두 받았어요')
    await expect(page.locator('#task-panel')).to_be_hidden()


async def _two_weeks(page, data, week1=1, week2=2):
    await page.add_init_script(FAKE_EXTENSION)
    data['/saved-videos'] = []
    first = [{**_video(100 + i, f'1주 강의 {i}'), 'module_id': 1, 'module_title': '1주차'} for i in range(week1)]
    second = [{**_video(200 + i, f'2주 강의 {i}'), 'module_id': 2, 'module_title': '2주차'} for i in range(week2)]
    data['/courses/1/videos'] = first + second
    await page.goto('http://dashboard.test/#videos')
    await expect(page.locator('#video-list li.video-row')).to_have_count(week1 + week2)


async def test_group_actions_only_appear_when_they_add_something(dashboard):
    page, data = dashboard
    await _two_weeks(page, data)
    groups = page.locator('#video-list .group-label')
    await expect(groups.nth(0).get_by_role('button')).to_have_count(0)  # 1개뿐이면 줄 버튼과 같은 동작이다
    await expect(groups.nth(1).get_by_role('button', name='2주차 전체 내려받기 (2)')).to_have_text('전체 2')
    await expect(groups.nth(1).get_by_role('button', name='2주차 전체 텍스트로 변환 (2)')).to_have_text('전체 2')


async def test_group_transcribe_hides_when_one_video_is_left(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_EXTENSION)
    data['/saved-videos'] = []
    data['/courses/1/videos'] = [_video(11, '변환 끝', transcript=5), _video(12, '남은 강의')]
    await page.goto('http://dashboard.test/#videos')
    await expect(page.get_by_role('button', name='1주차 전체 내려받기 (2)')).to_be_visible()
    await expect(page.locator('#video-list [data-tx-group]')).to_have_count(0)


async def test_group_bulk_starts_its_own_week_after_a_hidden_one(dashboard):
    page, data = dashboard
    data['/downloads/complete'] = {}
    for mid in (200, 201):
        data[f'/courses/1/videos/{mid}/direct-download'] = {
            'url': f'https://ssuin-object.commonscdn.com/{mid}.mp4', 'file_name': f'{mid}.mp4', 'receipt': 'receipt'}
    await _two_weeks(page, data)
    await page.get_by_role('button', name='2주차 전체 내려받기 (2)').click()
    await page.wait_for_function("window.__extensionCalls.filter(x => x === 'start').length === 2")
    sheet = await _open_panel(page)
    await expect(sheet).to_contain_text('다운로드 0/2개')


async def test_row_buttons_keep_weight_only_on_work_left_to_do(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_EXTENSION)
    data['/saved-videos'] = []
    data['/courses/1/videos'] = [_video(11, '받은 강의', downloaded=True), _video(12, '안 받은 강의')]
    await page.goto('http://dashboard.test/#videos')
    rows = page.locator('#video-list li.video-row')
    await expect(rows).to_have_count(2)
    await expect(rows.nth(0).get_by_role('button', name='내려받기', exact=True)).to_have_class('btn btn-icon btn-ghost')
    await expect(rows.nth(1).get_by_role('button', name='내려받기', exact=True)).to_have_class('btn btn-icon btn-soft')
    await expect(rows.nth(1).get_by_role('button', name='텍스트로 변환', exact=True)).to_have_class('btn btn-icon btn-ghost')
    for button in await page.locator('#video-list .video-row .btn').all():
        assert (await button.bounding_box())['height'] >= 44


async def test_rows_use_icons_and_explain_them_once(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_EXTENSION)
    data['/saved-videos'] = []
    data['/courses/1/videos'] = [_video(11, '받은 강의', downloaded=True), {**_video(12, '안 받은 강의'), 'completed': True}]
    await page.set_viewport_size({'width': 375, 'height': 812})
    await page.goto('http://dashboard.test/#videos')
    rows = page.locator('#video-list li.video-row')
    await expect(rows).to_have_count(2)
    await expect(rows.nth(0).locator('[data-icon="download-done"]')).to_be_visible()
    await expect(rows.nth(1).locator('[data-icon="download"]')).to_be_visible()
    await expect(rows.nth(1).get_by_role('img', name='수강 완료')).to_be_visible()
    for row in await rows.all():
        for button in await row.locator('.btn').all():
            box = await button.bounding_box()
            assert box['width'] >= 44 and box['height'] >= 44
            label_box = await button.locator('.sr-only').bounding_box()
            assert label_box['width'] <= 1  # 이름은 보조기기용이고 눈에는 아이콘만 보인다
    legend = page.locator('.icon-legend')
    for label in ('내려받기', '받음', '텍스트로 변환', '수강 완료', '미수강'):
        await expect(legend.get_by_text(label, exact=True)).to_be_visible()
    await expect(legend).not_to_contain_text('아직 열리지 않음')  # 목록에 없는 아이콘은 설명하지 않는다


STORE_URL = "https://chromewebstore.google.com/detail/ocnolcfmmkkphhkdnmblcdmakaciinda"


async def test_guide_leads_to_the_web_store_when_extension_is_missing(dashboard):
    page, data = dashboard
    data['/saved-videos'] = []
    data['/courses/1/videos'] = [_video(12, '안 받은 강의')]
    await page.goto('http://dashboard.test/#videos')
    guide = page.locator('#video-list .guide')
    await expect(guide).to_have_attribute('open', '')
    store = guide.get_by_role('link', name='Chrome 웹 스토어 열기')
    await expect(store).to_have_attribute('href', STORE_URL)
    await expect(store).to_have_attribute('target', '_blank')
    await expect(store).to_have_attribute('rel', 'noopener')
    await expect(guide).not_to_contain_text('ZIP')
    await expect(guide).not_to_contain_text('개발자 모드')
    await guide.locator('summary').click()  # 접으면 목록을 다시 그려도 다시 펼치지 않는다
    await page.wait_for_function('__ssu.state.guideDismissed === true')  # toggle 이벤트는 다음 작업에서 온다
    await page.evaluate('__ssu.renderVideoList()')
    await expect(guide).not_to_have_attribute('open', '')


async def test_guide_stays_folded_when_extension_answers(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_EXTENSION)
    data['/saved-videos'] = []
    data['/courses/1/videos'] = [_video(12, '안 받은 강의')]
    await page.goto('http://dashboard.test/#videos')
    guide = page.locator('#video-list .guide')
    await expect(guide).not_to_have_attribute('open', '')
    await expect(guide.locator('summary')).to_contain_text('크롬 확장프로그램 안내')
    await guide.locator('summary').click()
    await expect(guide.get_by_role('link', name='Chrome 웹 스토어 열기')).to_be_visible()
