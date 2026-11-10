"""승인 전 사용자 화면: 상태별 문구, 요청 보내기, 열어 둔 탭에서 권한이 회수될 때의 전환."""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright, expect

WEB = Path(__file__).resolve().parents[1] / "app" / "web"
MIME = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}
pytestmark = pytest.mark.browser

# 시각에 따라 결과가 달라지지 않도록 고정된 먼 미래와 먼 과거를 쓴다.
FUTURE = "2099-11-07T06:20:00+00:00"
PAST = "2000-11-07T06:20:00+00:00"
REQUESTED = "2026-11-06T06:20:00+00:00"
NORMAL = {
    "/lms/credentials": {"status": "ok", "last_synced_at": REQUESTED},
    "/courses": [], "/todos": [], "/playback/batches?limit=10": [], "/playback/batches": [],
    "/announcements": [], "/notifications/channels": [],
}


def me_of(status="none", requested=None, retry=None):
    return {"student_id": "20260001", "access_status": status, "access_requested_at": requested, "access_retry_at": retry}


@pytest.fixture
async def stage():
    """pg.me: 지금 /me가 돌려줄 값. pg.handlers: 경로별 (상태, 본문) 덮어쓰기. pg.calls: 받은 요청 전부."""
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        pg = await browser.new_page(viewport={"width": 1440, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.me, pg.handlers, pg.calls = me_of(), {}, []

        async def route(r):
            url = urlparse(r.request.url)
            if url.hostname != "dashboard.test":
                return await r.abort()
            path, method = url.path, r.request.method
            key = path + (f"?{url.query}" if url.query else "")
            static = WEB / ("index.html" if path == "/" else path.lstrip("/"))
            if static.suffix in MIME and static.is_file():
                return await r.fulfill(body=static.read_text(), content_type=MIME[static.suffix])
            if "." not in path:  # 이미지 같은 정적 파일은 API 호출이 아니다
                pg.calls.append((method, key))
            if (method, path) in pg.handlers:
                status, body = pg.handlers[(method, path)]
                return await r.fulfill(status=status, json=body)
            if path == "/me" and method == "GET":
                return await r.fulfill(json=pg.me)
            if method == "GET" and key in NORMAL:
                return await r.fulfill(json=NORMAL[key])
            await r.fulfill(status=404, json={"detail": "Not found"})

        await pg.route("**/*", route)
        await pg.add_init_script('localStorage.setItem("token", "t");')
        yield pg
        await browser.close()
    assert not errors, errors


def only_open_apis(page):
    return all(path in ("/me", "/access/request") for _, path in page.calls)


@pytest.mark.parametrize("me, title, text, request_label", [
    (me_of("none"), "아직 권한이 없어요", "관리자가 승인하면 강의 재생, 내려받기, 할 일을 쓸 수 있어요.", "권한 요청 보내기"),
    (me_of("revoked"), "권한이 회수됐어요", "관리자가 사용 권한을 거뒀어요. 다시 쓰려면 요청을 보내 주세요.", "권한 요청 보내기"),
    (me_of("rejected", retry=FUTURE), "요청이 거절됐어요", "부터 다시 요청할 수 있어요.", "다시 요청하기"),
])
async def test_access_screen_states(stage, me, title, text, request_label):
    stage.me = me
    await stage.goto("http://dashboard.test/")
    await expect(stage.get_by_role("heading", name=title)).to_be_visible()
    await expect(stage.locator("#access-message")).to_contain_text(text)
    await expect(stage.get_by_role("button", name=request_label, exact=True)).to_be_visible()
    await expect(stage.locator("#app")).to_be_hidden()
    await expect(stage.locator("#main-menu")).to_be_hidden()
    assert only_open_apis(stage), stage.calls


async def test_pending_screen_has_no_request_button(stage):
    stage.me = me_of("pending", requested=REQUESTED)
    await stage.goto("http://dashboard.test/")
    await expect(stage.get_by_role("heading", name="승인을 기다리는 중")).to_be_visible()
    await expect(stage.locator("#access-message")).to_contain_text("요청을 보냈어요. 승인되면 다시 접속했을 때 바로 쓸 수 있어요.")
    await expect(stage.locator("#access-tag")).to_have_text("대기 중")
    await expect(stage.locator("#access-request")).to_be_hidden()


async def test_request_button_sends_and_shows_pending(stage):
    stage.me = me_of("none")
    stage.handlers[("POST", "/access/request")] = (200, {"access_status": "pending", "access_requested_at": REQUESTED, "access_retry_at": None})
    await stage.goto("http://dashboard.test/")
    await stage.get_by_role("button", name="권한 요청 보내기").click()
    await expect(stage.get_by_role("heading", name="승인을 기다리는 중")).to_be_visible()
    await expect(stage.locator("#access-tag")).to_be_visible()
    assert ("POST", "/access/request") in stage.calls


async def test_rejected_retry_button_disabled_until_retry_at(stage):
    stage.me = me_of("rejected", retry=FUTURE)
    await stage.goto("http://dashboard.test/")
    await expect(stage.get_by_role("button", name="다시 요청하기")).to_be_disabled()
    await expect(stage.locator("#access-message")).to_have_text((await stage.evaluate(
        "fmtDate(new Date('2099-11-07T06:20:00+00:00'))")) + "부터 다시 요청할 수 있어요.")
    stage.me = me_of("rejected", retry=PAST)
    await stage.reload()
    await expect(stage.get_by_role("button", name="다시 요청하기")).to_be_enabled()
    await expect(stage.locator("#access-message")).to_have_text("지금 다시 요청할 수 있어요.")


async def test_request_429_shows_rejected_state(stage):
    stage.me = me_of("revoked")
    stage.handlers[("POST", "/access/request")] = (429, {"detail": {"message": "아직 요청할 수 없어요.", "access_retry_at": FUTURE}})
    await stage.goto("http://dashboard.test/")
    stage.me = me_of("rejected", retry=FUTURE)  # 서버가 다시 알려 주는 값
    await stage.get_by_role("button", name="권한 요청 보내기").click()
    await expect(stage.get_by_role("heading", name="요청이 거절됐어요")).to_be_visible()
    await expect(stage.get_by_role("button", name="다시 요청하기")).to_be_disabled()
    await expect(stage.locator("#access-error")).to_have_text("")


async def test_access_required_mid_session_switches_screen(stage):
    stage.me = me_of("approved")
    await stage.goto("http://dashboard.test/#courses")
    await expect(stage.locator("#app")).to_be_visible()
    # 관리자가 권한을 회수했다: /me는 열려 있고 나머지는 403
    stage.me = me_of("revoked")
    stage.handlers[("GET", "/notifications/channels")] = (403, {"detail": {"code": "ACCESS_REQUIRED", "message": "관리자 승인이 필요합니다."}})
    await stage.get_by_role("button", name="알림", exact=True).click()
    await expect(stage.get_by_role("heading", name="권한이 회수됐어요")).to_be_visible()
    await expect(stage.locator("#app")).to_be_hidden()
    await expect(stage.locator("#toast")).not_to_contain_text("관리자 승인이 필요합니다")


@pytest.mark.parametrize("failure", ["network", "5xx"])
async def test_me_failure_during_switch_still_shows_access_screen(stage, failure):
    stage.me = me_of("approved")
    await stage.goto("http://dashboard.test/#courses")
    await expect(stage.locator("#app")).to_be_visible()
    stage.handlers[("GET", "/notifications/channels")] = (403, {"detail": {"code": "ACCESS_REQUIRED", "message": "관리자 승인이 필요합니다."}})
    if failure == "5xx":
        stage.handlers[("GET", "/me")] = (503, {"detail": "잠시 후 다시 시도하세요."})
    else:
        await stage.route("**/me", lambda r: r.abort())
    await stage.get_by_role("button", name="알림", exact=True).click()
    await expect(stage.get_by_role("heading", name="권한이 회수됐어요")).to_be_visible()
    await expect(stage.get_by_role("button", name="권한 요청 보내기")).to_be_visible()
    await expect(stage.get_by_role("button", name="상태 다시 확인")).to_be_visible()


async def test_revoked_user_button_in_open_tab_runs_nothing(stage):
    stage.me = me_of("approved")
    await stage.goto("http://dashboard.test/#courses")
    await expect(stage.locator("#app")).to_be_visible()
    stage.me = me_of("revoked")
    stage.handlers[("POST", "/sync")] = (403, {"detail": {"code": "ACCESS_REQUIRED", "message": "관리자 승인이 필요합니다."}})
    await stage.get_by_role("button", name="LMS 동기화").click()
    await expect(stage.get_by_role("heading", name="권한이 회수됐어요")).to_be_visible()
    await expect(stage.locator("#app")).to_be_hidden()
    assert ("POST", "/sync") in stage.calls
    assert stage.calls.count(("POST", "/sync")) == 1


async def test_access_screen_has_logout_delete_and_recheck(stage):
    stage.me = me_of("pending", requested=REQUESTED)
    stage.handlers[("DELETE", "/me")] = (204, None)
    await stage.goto("http://dashboard.test/")
    # 상태 다시 확인: 아직 대기 중이면 그대로, 승인되면 앱이 뜬다
    await stage.get_by_role("button", name="상태 다시 확인").click()
    await expect(stage.get_by_role("heading", name="승인을 기다리는 중")).to_be_visible()
    stage.me = me_of("approved")
    await stage.get_by_role("button", name="상태 다시 확인").click()
    await expect(stage.locator("#app")).to_be_visible()
    await expect(stage.locator("#access")).to_be_hidden()
    assert ("GET", "/todos") in stage.calls

    # 계정 삭제: 기존 확인 창을 거치고, 확인 전에는 서버를 부르지 않는다
    stage.me = me_of("none")
    await stage.reload()
    await stage.get_by_role("button", name="계정 삭제", exact=True).click()
    dialog = stage.locator("#confirm-dialog")
    await expect(dialog).to_contain_text("계정을 삭제할까요?")
    assert ("DELETE", "/me") not in stage.calls
    await dialog.get_by_label("확인을 위해 학번을 입력하세요").fill("20260001")
    await dialog.get_by_role("button", name="계정 삭제").click()
    await expect(stage.locator("#auth")).to_be_visible()
    assert ("DELETE", "/me") in stage.calls

    # 로그아웃
    stage.me = me_of("none")
    await stage.evaluate('localStorage.setItem("token", "t")')
    await stage.reload()
    async with stage.expect_request(lambda r: r.url.endswith("/auth/logout") and r.method == "POST") as req:
        await stage.get_by_role("button", name="로그아웃", exact=True).click()
    assert (await req.value).headers["authorization"] == "Bearer t"  # 서버에서도 토큰을 폐기한다
    await expect(stage.locator("#auth")).to_be_visible()
    await expect(stage.locator("#access")).to_be_hidden()


async def test_renewed_token_header_replaces_stored_token(stage):
    stage.me = me_of("pending", requested=REQUESTED)

    async def renewing_me(r):
        await r.fulfill(json=stage.me, headers={"X-Access-Token": "renewed"})
    await stage.route("http://dashboard.test/me", renewing_me)
    await stage.goto("http://dashboard.test/")
    await expect(stage.get_by_role("heading", name="승인을 기다리는 중")).to_be_visible()
    assert await stage.evaluate('localStorage.getItem("token")') == "renewed"
    async with stage.expect_request("http://dashboard.test/me") as req:
        await stage.get_by_role("button", name="상태 다시 확인").click()
    assert (await req.value).headers["authorization"] == "Bearer renewed"


@pytest.mark.parametrize("me", [me_of("none"), me_of("pending", requested=REQUESTED), me_of("rejected", retry=FUTURE)])
async def test_access_screen_no_sideways_scroll(stage, me, tmp_path):
    stage.me = me
    await stage.goto("http://dashboard.test/")
    await expect(stage.locator("#access")).to_be_visible()
    for width in (375, 768, 1024, 1440):
        await stage.set_viewport_size({"width": width, "height": 800})
        assert await stage.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        for button in await stage.locator("#access button:visible").all():
            assert (await button.bounding_box())["height"] >= 44
        if me["access_status"] == "none":
            await stage.screenshot(path=tmp_path / f"access-{width}.png")


REQUIRED = (403, {"detail": {"code": "ACCESS_REQUIRED", "message": "관리자 승인이 필요합니다."}})
FAKE_FOLDER = """
window.__saved = {};
window.showDirectoryPicker = async () => ({ getFileHandle: async (name) => ({
  createWritable: async () => new WritableStream({ close: () => { window.__saved[name] = 1; } }) }) });
"""
COURSE = {"lms_course_id": 1, "name": "컴퓨터프로그래밍", "professors": "김교수", "attendance": [], "auto_play": False}


async def test_boot_waits_for_every_script_before_reading_me(stage):
    """/me가 뒤에 오는 스크립트보다 먼저 답해도 showAccessScreen이 있어야 한다."""
    stage.me = me_of("none")

    async def slow(r):
        await asyncio.sleep(0.4)
        await r.fulfill(body=(WEB / "access.js").read_text(), content_type="application/javascript")

    await stage.route("**/access.js", slow)
    await stage.goto("http://dashboard.test/")
    await expect(stage.get_by_role("heading", name="아직 권한이 없어요")).to_be_visible()
    await expect(stage.locator("#startup-message")).not_to_contain_text("불러오지 못했습니다")


async def test_bulk_download_signed_link_403_switches_to_access_screen(stage):
    stage.me = me_of("approved")
    await stage.add_init_script(FAKE_FOLDER)
    material = {"kind": "commons_file", "module_id": 1, "module_title": "1주차", "size": 1, "downloaded_at": None}
    stage.handlers[("GET", "/courses")] = (200, [COURSE])
    stage.handlers[("GET", "/saved-videos")] = (200, [])
    stage.handlers[("GET", "/courses/1/videos")] = (200, [])
    stage.handlers[("GET", "/courses/1/materials")] = (200, [
        {**material, "key": "c21", "title": "자료", "file_name": "자료.pdf"},
        {**material, "key": "c22", "title": "자료2", "file_name": "자료2.pdf"}])
    stage.handlers[("POST", "/courses/1/materials/c21/download-link")] = (200, {"url": "/downloads/tok", "file_name": "자료.pdf", "expires_in_minutes": 10})
    stage.handlers[("POST", "/courses/1/materials/c22/download-link")] = (200, {"url": "/downloads/tok", "file_name": "자료2.pdf", "expires_in_minutes": 10})
    stage.handlers[("GET", "/downloads/tok")] = REQUIRED
    await stage.goto("http://dashboard.test/#videos")
    await stage.get_by_role("tab", name="수업자료").click()
    stage.me = me_of("revoked")
    await stage.get_by_role("button", name="1주차 전체 내려받기 (2)").click()
    await expect(stage.get_by_role("heading", name="권한이 회수됐어요")).to_be_visible()
    await expect(stage.locator("#toast")).not_to_contain_text("요청 실패")
    await expect(stage.locator("#toast")).not_to_contain_text("관리자 승인이 필요합니다")


async def test_transcribe_direct_download_403_switches_to_access_screen(stage):
    stage.me = me_of("approved")
    video = {"module_item_id": 12, "module_id": 1, "module_title": "1주차", "title": "2강 재귀", "content_type": "movie",
             "duration": 2, "size": 1, "completed": False, "available": True, "thumbnail_url": None,
             "downloaded_at": None, "transcript_id": None}
    stage.handlers[("GET", "/courses")] = (200, [COURSE])
    stage.handlers[("GET", "/saved-videos")] = (200, [])
    stage.handlers[("GET", "/courses/1/videos")] = (200, [video])
    stage.handlers[("POST", "/courses/1/videos/12/direct-download")] = REQUIRED
    await stage.goto("http://dashboard.test/#videos")
    await stage.evaluate("""window.extensionRequest = async (action) => action === "ping"
      ? { version: 1, sessions: true, queue: true, audio: true } : { opened: true, closed: true };""")
    stage.me = me_of("revoked")
    await stage.get_by_role("button", name="텍스트로 변환", exact=True).click()
    await expect(stage.get_by_role("heading", name="권한이 회수됐어요")).to_be_visible()
    await expect(stage.locator("#toast")).not_to_contain_text("오디오를 받지 못했습니다")


async def test_access_screen_delete_without_known_student_id_still_types_confirmation(stage):
    """state.me가 비어 있어 학번을 모르면 /me로 학번을 알아낸 뒤 입력 확인을 받는다."""
    mes = iter([me_of("approved"), None])  # 첫 /me는 통과, 전환 중 /me는 실패, 그 뒤는 정상
    seen = []

    async def me_route(r):
        n = len(seen)
        seen.append(1)
        if n == 1:
            return await r.fulfill(status=503, json={"detail": "잠시 후 다시 시도하세요."})
        return await r.fulfill(json=me_of("approved") if n == 0 else me_of("none"))

    await stage.route("**/me", me_route)
    stage.handlers[("GET", "/todos")] = REQUIRED
    stage.handlers[("DELETE", "/me")] = (204, None)
    await stage.goto("http://dashboard.test/")
    await expect(stage.get_by_role("heading", name="권한이 회수됐어요")).to_be_visible()
    await stage.get_by_role("button", name="계정 삭제", exact=True).click()
    dialog = stage.locator("#confirm-dialog")
    await expect(dialog.get_by_label("확인을 위해 학번을 입력하세요")).to_be_visible()
    await expect(dialog.get_by_role("button", name="계정 삭제")).to_be_disabled()
    assert ("DELETE", "/me") not in stage.calls


async def test_access_screen_links_to_privacy_policy(stage):
    stage.me = me_of("none")
    await stage.goto("http://dashboard.test/")
    link = stage.locator("#access").get_by_role("link", name="개인정보처리방침")
    await expect(link).to_have_attribute("href", "/privacy.html")
    assert (await link.bounding_box())["height"] >= 44
