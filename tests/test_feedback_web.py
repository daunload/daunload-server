"""지속되는 오류, 처리 중 상태, 스켈레톤, 비활성 이유, 휴대폰 안내, 광고 겹침 (가짜 API, 실제 Chrome)."""

import asyncio
import re
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright, expect

from tests.test_tasks_web import _active
from tests.test_videos_web import FAKE_EXTENSION, _video
from tests.test_web import WEB, dashboard  # noqa: F401  (fixture)
from tests.web_root import require_build
from tests.test_week_web import page  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser
NOTICE = "PC 크롬에서 받을 수 있어요"


@pytest.fixture
async def mobile_page():
    require_build()
    data = {
        "/me": {"student_id": "20260001", "access_status": "approved"},
        "/lms/credentials": {"status": "ok", "last_synced_at": "2026-11-05T00:00:00+00:00"},
        "/courses": [{"lms_course_id": 1, "name": "컴퓨터프로그래밍", "professors": "김교수", "attendance": [], "auto_play": False}],
        "/todos": [], "/playback/batches": [], "/notifications/channels": [],
        "/saved-videos": [
            {"id": 5, "lms_course_id": 1, "course_name": "컴퓨터프로그래밍", "module_item_id": 99, "module_id": 1,
             "module_title": "1주차", "title": "만료 영상", "saved": False, "resolve_error": "주소가 만료됐어요",
             "downloaded_at": None, "transcript_id": None, "duration": 60, "size": 1}],
        "/courses/1/videos": [_video(11, "받을 영상"), _video(12, "받을 영상 2")],
        "/courses/1/materials": [{"key": "c21", "kind": "commons_file", "module_id": 1, "module_title": "1주차",
                                  "title": "1장", "file_name": "1장.pptx", "size": 2, "downloaded_at": None},
                                 {"key": "c22", "kind": "commons_file", "module_id": 1, "module_title": "1주차",
                                  "title": "2장", "file_name": "2장.pptx", "size": 2, "downloaded_at": None}],
    }
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg = await context.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))

        async def route(r):
            url = urlparse(r.request.url)
            if url.hostname != "dashboard.test":
                return await r.abort()
            f = WEB / ("index.html" if url.path == "/" else url.path.lstrip("/"))
            mime = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}.get(f.suffix)
            if mime and f.is_file():
                return await r.fulfill(body=f.read_text(), content_type=mime)
            if url.path in data:
                return await r.fulfill(json=data[url.path])
            await r.fulfill(status=404, json={"detail": "Not found"})

        await pg.route("**/*", route)
        await pg.add_init_script('localStorage.setItem("token", "t");')
        yield pg
        await browser.close()
    assert not errors, errors


async def test_error_toast_stays_until_closed_success_expires(page):
    await page.goto("http://dashboard.test/")
    await expect(page.get_by_role("heading", name="할 일이 3개 남았어요")).to_be_visible()
    items = page.locator("#toast > *")
    await page.evaluate("__ssu.toast('연결이 끊겼어요', true); __ssu.toast('저장했어요')")
    await expect(items).to_have_count(2)
    await expect(page.get_by_role("alert").filter(has_text="연결이 끊겼어요")).to_be_visible()
    await page.wait_for_timeout(3500)
    await expect(page.locator("#toast")).not_to_contain_text("저장했어요")  # 성공은 3.2초 뒤 사라진다
    await page.wait_for_timeout(1000)
    await expect(page.locator("#toast")).to_contain_text("연결이 끊겼어요")  # 오류는 남는다
    await page.locator("#toast").get_by_role("button", name="닫기").click()
    await expect(items).to_have_count(0)

    await page.evaluate("__ssu.toast('같은 오류', true); __ssu.toast('같은 오류', true)")
    await expect(items).to_have_count(1)
    await page.evaluate("__ssu.toast('둘째', true); __ssu.toast('셋째', true); __ssu.toast('넷째', true)")
    await expect(items).to_have_count(3)
    await expect(page.locator("#toast")).not_to_contain_text("같은 오류")
    await expect(page.locator("#toast")).to_contain_text("넷째")

    # 넘칠 때는 사라질 알림부터 지우고, 오류는 남긴다
    await page.evaluate("__ssu.clearToasts(); __ssu.toast('오류 하나', true); __ssu.toast('성공 알림'); __ssu.toast('오류 둘', true); __ssu.toast('오류 셋', true)")
    await expect(items).to_have_count(3)
    await expect(page.locator("#toast")).not_to_contain_text("성공 알림")
    await expect(page.locator("#toast")).to_contain_text("오류 하나")
    await expect(page.locator("#toast")).to_contain_text("오류 셋")


async def _one_video(dashboard):
    page, data = dashboard
    await page.add_init_script("crypto.randomUUID = () => 'test-missing-extension'")  # http 테스트 주소에는 없다
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(11, "직접 받기")]
    return page, data


async def _two_videos(dashboard):
    page, data = await _one_video(dashboard)
    data["/courses/1/videos"].append(_video(12, "직접 받기 2"))
    return page, data


async def test_missing_extension_opens_guide(dashboard):
    page, _ = await _one_video(dashboard)
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="내려받기", exact=True).click()
    guide = page.locator("#video-list details")
    await expect(guide).to_have_attribute("open", "")
    await expect(guide).to_contain_text("확장프로그램에 연결하지 못했습니다")
    await expect(page.locator("#toast > *")).to_have_count(0)


async def test_missing_extension_toasts_when_guide_is_not_on_the_page(dashboard):
    page, _ = await _one_video(dashboard)
    await page.goto("http://dashboard.test/#videos")
    await expect(page.get_by_role("button", name="내려받기", exact=True)).to_be_visible()
    # 받은 것만 남은 목록: 안내 상자가 그려지지 않으므로 오류를 토스트로 알려야 한다
    await page.evaluate("""() => {
      __ssu.state.notDownloadedOnly = true;
      __ssu.state.videoList.items.forEach((v) => { v.downloaded_at = new Date().toISOString(); });
      __ssu.renderVideoList();
      __ssu.reportDownloadError(Object.assign(new Error("확장프로그램에 연결하지 못했습니다."), { code: "EXTENSION_MISSING" }));
    }""")
    await expect(page.locator("#video-list details")).to_have_count(0)
    await expect(page.locator("#toast")).to_contain_text("확장프로그램에 연결하지 못했습니다")


async def test_missing_extension_on_bulk_opens_guide(dashboard):
    page, _ = await _two_videos(dashboard)
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="1주차 전체 내려받기 (2)").click()
    await expect(page.locator("#video-list details")).to_have_attribute("open", "")
    await expect(page.locator("#video-list details")).to_contain_text("확장프로그램에 연결하지 못했습니다")
    await expect(page.locator("#toast > *")).to_have_count(0)


async def test_bulk_does_not_rerender_after_leaving_videos(dashboard):
    page, data = await _two_videos(dashboard)
    await page.add_init_script(FAKE_EXTENSION)
    data["/courses/1/videos/11/direct-download"] = {"url": "https://x.test/a.mp4", "file_name": "a.mp4", "receipt": "r"}
    data["/courses/1/videos/12/direct-download"] = {"url": "https://x.test/b.mp4", "file_name": "b.mp4", "receipt": "r"}
    await page.route("**/downloads/complete", lambda r: r.fulfill(status=204))
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="1주차 전체 내려받기 (2)").click()
    await page.wait_for_function("window.__extensionCalls.filter(x => x === 'start').length === 2")
    await page.evaluate("location.hash = '#courses'")
    courses = page.get_by_role("heading", name="과목", exact=True)
    await expect(courses).to_be_visible()
    await page.evaluate("window.__extensionState = 'complete'")
    await expect(page.locator("#toast")).to_contain_text("2개를 모두 받았어요")
    await expect(courses).to_be_visible()  # 영상 목록이 다른 화면을 덮어쓰지 않는다


async def test_outdated_extension_uses_toast(dashboard):
    page, _ = await _one_video(dashboard)
    await page.add_init_script(FAKE_EXTENSION.replace("version: 1", "version: 2"))
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="내려받기", exact=True).click()
    await expect(page.get_by_role("alert").filter(has_text="업데이트")).to_be_visible()
    await expect(page.locator("#video-list details")).not_to_have_attribute("open", "")


async def test_disabled_reason_visible_without_hover(dashboard):
    page, data = dashboard
    saved = {"lms_course_id": 1, "course_name": "컴퓨터프로그래밍", "module_id": 1, "module_title": "1주차", "saved": False,
             "downloaded_at": None, "transcript_id": None, "duration": 60, "size": 1}
    data["/saved-videos"] = [
        {**saved, "id": 5, "module_item_id": 98, "title": "만료 영상", "resolve_error": "주소가 만료됐어요"},
        {**saved, "id": 6, "module_item_id": 99, "title": "사유 없는 영상", "resolve_error": None},
        {**saved, "id": 7, "lms_course_id": 9, "course_name": "지난 과목", "module_item_id": 100, "title": "옛 영상",
         "resolve_error": "삭제된 영상이에요"},
    ]
    data["/courses/1/videos"] = []
    await page.goto("http://dashboard.test/#videos")
    rows = page.locator("#video-list li.video-row")
    await expect(rows.filter(has_text="만료 영상")).to_contain_text("주소가 만료됐어요")
    await expect(rows.filter(has_text="사유 없는 영상")).to_contain_text("지금은 내려받을 수 없어요")
    await page.select_option("#video-course", "9")
    await expect(page.get_by_role("tab", name="수업자료")).to_be_disabled()
    await expect(page.locator(".tabs-reason")).to_have_text("지난 과목은 수업자료를 볼 수 없어요")


async def test_phone_disables_download_with_one_notice(mobile_page):
    page = mobile_page
    await page.goto("http://dashboard.test/#videos")
    await expect(page.locator("#video-list li.video-row").first).to_be_visible()
    await expect(page.get_by_text(NOTICE)).to_have_count(1)
    await expect(page.get_by_role("button", name="내려받기", exact=True).first).to_be_disabled()
    await expect(page.locator("#video-list [data-bulk]").first).to_be_disabled()
    await expect(page.locator("#video-list details")).to_have_count(0)
    await expect(page.locator("#video-list").get_by_text("주소가 만료됐어요")).to_have_count(0)
    await expect(page.locator("#video-list").get_by_text("지금은 내려받을 수 없어요")).to_have_count(0)
    await page.get_by_role("tab", name="수업자료").click()
    await expect(page.locator("#video-list [data-bulk]").first).to_be_disabled()
    await expect(page.locator("#video-list [data-link]").first).to_be_enabled()
    await expect(page.get_by_text(NOTICE)).to_have_count(1)


async def test_async_buttons_show_pending(dashboard):
    page, data = dashboard
    data["/notifications/channels"] = [{"id": 1, "kind": "discord", "created_at": "2026-11-01T00:00:00Z"}]
    data["/notifications/channels/1/test"] = {}

    async def slow(route):
        if route.request.method == "POST":
            await asyncio.sleep(1)
        await route.fallback()

    await page.route("**/notifications/channels**", slow)
    await page.goto("http://dashboard.test/#notifications")
    await page.locator("input[name=webhook_url]").fill("https://discord.com/api/webhooks/1/a")
    add = page.locator("#channel-form button")
    await add.click()
    await expect(add).to_be_disabled()
    await expect(add).to_contain_text("처리 중")
    await expect(add).not_to_be_disabled()
    test = page.locator("[data-test]")
    await test.click()
    await expect(test).to_be_disabled()
    await expect(test).to_contain_text("처리 중")
    await expect(test).to_be_enabled()
    await expect(test).to_have_text("테스트 메시지 보내기")


async def test_lists_show_skeleton_while_loading(dashboard):
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(11, "느린 강의")]

    async def slow(route):
        await asyncio.sleep(1)
        await route.fallback()

    await page.route("**/courses/1/videos", slow)
    await page.goto("http://dashboard.test/#videos")
    await expect(page.locator("#video-list[aria-busy=true] .skeleton").first).to_be_visible()
    await expect(page.locator("#video-list li.video-row")).to_have_count(1)
    await expect(page.locator("#video-list .skeleton")).to_have_count(0)
    await expect(page.locator("#video-list")).not_to_have_attribute("aria-busy", "true")


async def test_bar_rises_above_anchor_ad(page):  # 375x812
    await page.set_viewport_size({"width": 375, "height": 812})
    _active(page)
    await page.goto("http://dashboard.test/")
    await expect(page.locator("#task-bar")).to_be_visible()
    probe = "(() => { const b = document.querySelector('#task-bar').getBoundingClientRect().bottom;" \
            " const ad = document.querySelector('ins'); return [b, ad ? ad.getBoundingClientRect().top : null]; })()"
    base = (await page.evaluate(probe))[0]
    await page.evaluate("""() => {
      const ins = document.createElement('ins');
      ins.className = 'adsbygoogle';
      ins.style.cssText = 'position:fixed;left:0;right:0;bottom:0;height:60px;display:block';
      document.body.append(ins);
      setTimeout(() => ins.setAttribute('data-anchor-status', 'displayed'), 50);
    }""")
    await page.wait_for_function("document.querySelector('#task-bar').getBoundingClientRect().bottom <= document.querySelector('ins').getBoundingClientRect().top")
    await page.evaluate("document.querySelector('ins').remove()")
    await page.wait_for_function(f"Math.abs(document.querySelector('#task-bar').getBoundingClientRect().bottom - {base}) < 1")
    assert re.fullmatch(r"0(px)?", await page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--ad-inset').trim()"))
