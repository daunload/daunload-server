"""Dashboard interactions with local assets and mocked APIs; no school account needed."""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright, expect

from tests.web_root import WEB, require_build

pytestmark = pytest.mark.browser


@pytest.fixture
async def dashboard():
    require_build()
    now = datetime.now(UTC)
    data = {
        "/me": {"student_id": "20260001", "access_status": "approved"},
        "/lms/credentials": {"status": "ok", "last_synced_at": now.isoformat(),
                             "personal_token_registered": True, "personal_token_status": "ok"},
        "/courses": [{"lms_course_id": 1, "name": "컴퓨터프로그래밍", "professors": "김교수", "attendance": [], "auto_play": False}],
        "/todos": [
            {"kind": kind, "lms_course_id": 1, "lms_item_id": i, "course_name": "컴퓨터프로그래밍", "title": title,
             "deadline": (now + timedelta(hours=hours)).isoformat(), "duration": 1800 if kind == "video" else None,
             "module_title": "6주차", "url": "https://lms.ssu.ac.kr/courses/1"}
            for i, (kind, title, hours) in enumerate([
                ("assignment", "자료구조 실습 과제", 2), ("video", "함수와 재귀 이해하기", 48), ("quiz", "지난 주차 퀴즈", -1)
            ])
        ],
        "/playback/batches": [], "/notifications/channels": [],
        "/courses/1/videos": [],
        "/assignments": [
            {"assignment_id": i, "kind": "assignment", "lms_course_id": 1, "course_name": "컴퓨터프로그래밍", "title": title, "status": status,
             "deadline": (now + timedelta(hours=hours)).isoformat(), "url": "https://lms.ssu.ac.kr/courses/1/assignments/1"}
            for i, (title, status, hours) in enumerate([
                ("자료구조 실습 과제", "open", 2), ("제출한 실습", "submitted", -24), ("지난 과제", "overdue", -48), ("확인 중인 과제", "unknown", 24)
            ])
        ],
    }
    # 홈: 재생할 강의(전체 미리보기)와 낼 과제(/week)
    video = data["/todos"][1]
    data["/week"] = {"start": now.isoformat(), "end": (now + timedelta(days=7)).isoformat(), "videos": [video], "video_seconds": 1800,
                     "assignments": [{**data["/assignments"][0], "unlock_at": None, "lock_at": None}], "missed": [],
                     "later_assignments": [], "late_assignments": []}
    data["/playback/batches/preview"] = {"items": [{
        "position": 0, "lms_course_id": 1, "course_name": "컴퓨터프로그래밍", "module_id": 6, "module_title": "6주차",
        "module_item_id": video["lms_item_id"], "title": video["title"], "duration": 1800, "deadline": video["deadline"],
        "expected_start": now.isoformat(), "expected_end": (now + timedelta(minutes=30)).isoformat(), "at_risk": False}],
        "total_seconds": 1800, "expected_end": (now + timedelta(minutes=30)).isoformat(), "warnings": []}
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def route(request):
            url = urlparse(request.request.url)
            if url.hostname != "dashboard.test":
                await request.abort()
                return
            path = url.path
            static = WEB / ("index.html" if path == "/" else path.lstrip("/"))
            mime = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}.get(static.suffix)
            if mime and static.is_file():  # 실제 페이지가 불러오는 정적 파일을 모두 내보낸다
                await request.fulfill(body=static.read_text(), content_type=mime)
            elif path in data:
                await request.fulfill(json=data[path])
            else:
                await request.fulfill(status=404, json={"detail": "Not found"})

        await page.route("**/*", route)
        await page.add_init_script('localStorage.setItem("token", "test-token");')
        yield page, data
        await browser.close()
    assert not errors, errors


async def test_home_and_mobile_layout(dashboard, tmp_path):
    page, _ = dashboard
    await page.goto("http://dashboard.test/")
    await expect(page.get_by_role("heading", name="할 일이 2개 남았어요")).to_be_visible()
    await expect(page.get_by_role("button", name="강의 1개 모두 재생")).to_be_visible()
    await page.screenshot(path=tmp_path / "desktop.png", full_page=True)
    for width in (1440, 1024, 768, 390, 375, 320):
        await page.set_viewport_size({"width": width, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.screenshot(path=tmp_path / "mobile.png", full_page=True)
    await page.locator("#menu-toggle").click()
    await page.get_by_role("button", name="설정", exact=True).click()
    await expect(page.get_by_role("heading", name="설정", exact=True)).to_be_in_viewport()
    await expect(page.locator('[data-view="settings"]')).to_have_attribute("aria-current", "page")


async def test_boot_failure_can_retry(dashboard):
    page, _ = dashboard
    await page.route("**/me", lambda route: route.fulfill(status=503, json={"detail": "잠시 후 다시 시도하세요."}))
    await page.goto("http://dashboard.test/")
    await expect(page.get_by_role("button", name="다시 시도", exact=True)).to_be_visible()
    await page.unroute("**/me")
    await page.get_by_role("button", name="다시 시도", exact=True).click()
    await expect(page.get_by_role("heading", name="할 일이 2개 남았어요")).to_be_visible()


async def test_notification_response_does_not_replace_another_view(dashboard):
    page, _ = dashboard
    requested, release = asyncio.Event(), asyncio.Event()

    async def delayed(route):
        requested.set()
        await release.wait()
        await route.fulfill(json=[])

    await page.route("**/notifications/channels", delayed)
    await page.goto("http://dashboard.test/")
    await page.get_by_role("button", name="설정", exact=True).click()
    await asyncio.wait_for(requested.wait(), timeout=5)
    await page.get_by_role("button", name="강의 재생", exact=True).click()
    async with page.expect_response("**/notifications/channels"):
        release.set()
    await expect(page.get_by_role("heading", name="강의 재생", exact=True)).to_be_visible()
    await expect(page.locator("#channel-form")).to_have_count(0)


async def test_login_password_visibility(dashboard, tmp_path):
    page, _ = dashboard
    await page.goto("http://dashboard.test/")
    await page.get_by_role("button", name="로그아웃", exact=True).click()
    await page.get_by_label("비밀번호", exact=True).fill("example-password")
    await page.get_by_role("button", name="표시", exact=True).click()
    await expect(page.locator("#password")).to_have_attribute("type", "text")
    await page.get_by_role("button", name="숨기기", exact=True).click()
    await expect(page.locator("#password")).to_have_attribute("type", "password")
    await page.screenshot(path=tmp_path / "login.png", full_page=True)


async def test_removed_pages_open_home_and_history_works(dashboard):
    page, _ = dashboard
    for old in ("todos", "assignments", "calendar", "announcements"):  # 예전 즐겨찾기·알림 링크도 깨지지 않는다
        await page.goto(f"http://dashboard.test/#{old}")
        await expect(page.get_by_role("heading", name="할 일이 2개 남았어요")).to_be_visible()
        await expect(page).to_have_url("http://dashboard.test/#dashboard")
    await expect(page.locator('.side [data-view="todos"], .side [data-view="assignments"], .side [data-view="calendar"], .side [data-view="announcements"]')).to_have_count(0)
    for old, new, heading in (("courses", "playback", "강의 재생"), ("notifications", "settings", "설정"), ("account", "settings", "설정")):
        await page.goto(f"http://dashboard.test/#{old}")  # 합친 화면의 예전 주소는 새 화면으로 간다
        await expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        await expect(page).to_have_url(f"http://dashboard.test/#{new}")
    await expect(page.locator('.side [data-view="courses"], .side [data-view="notifications"], .side [data-view="account"]')).to_have_count(0)
    await page.goto("http://dashboard.test/")
    await page.get_by_role("button", name="강의 재생", exact=True).click()
    await expect(page).to_have_url("http://dashboard.test/#playback")
    await page.go_back()
    await expect(page.get_by_role("heading", name="할 일이 2개 남았어요")).to_be_visible()


async def test_mobile_menu_and_long_titles(dashboard, tmp_path):
    page, data = dashboard
    title = "긴 강의 제목도 휴대폰에서 화면 밖으로 넘치면 안 됩니다 " * 3
    data["/playback/batches/preview"]["items"][0]["title"] = title.strip()
    await page.set_viewport_size({"width": 375, "height": 812})
    await page.emulate_media(reduced_motion="reduce")
    await page.goto("http://dashboard.test/")
    await expect(page.locator(".task-title").first).to_be_visible()
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await page.locator("#menu-toggle").click()
    await expect(page.get_by_role("button", name="설정", exact=True)).to_be_in_viewport()
    await page.keyboard.press("Escape")
    await expect(page.locator("#menu-toggle")).to_be_focused()
    await expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
    await page.screenshot(path=tmp_path / "mobile-long-titles.png", full_page=True)
    await page.set_viewport_size({"width": 812, "height": 375})
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


async def test_deep_link_and_skip_link_keep_current_page(dashboard):
    page, _ = dashboard
    await page.goto("http://dashboard.test/#playback")
    await expect(page.get_by_role("heading", name="강의 재생", exact=True)).to_be_visible()
    await page.locator(".skip-link").focus()
    await page.keyboard.press("Enter")
    await expect(page.locator("#view")).to_be_focused()
    await expect(page).to_have_url("http://dashboard.test/#playback")


async def test_playback_queue_shows_wait_and_deadline_risk(dashboard):
    page, data = dashboard
    now = datetime.now(UTC)
    batch = {
        "id": 5, "status": "queued", "scope": "all", "total": 1,
        "done_count": 0, "failed_count": 0, "skipped_count": 0,
        "cancel_requested": False, "queue_position": 3, "estimated_wait_seconds": 3600,
        "estimated_start": (now + timedelta(hours=1)).isoformat(),
        "expected_end": (now + timedelta(hours=2)).isoformat(), "deadline_at_risk": True,
        "jobs": [{"position": 0, "module_item_id": 1, "title": "대기 중 강의", "duration": 1800,
                  "status": "queued", "progress_seconds": 0, "attempts": 0, "error": None,
                  "deadline": (now + timedelta(minutes=30)).isoformat(),
                  "expected_end": (now + timedelta(hours=2)).isoformat()}],
    }
    data["/playback/batches"] = [batch]
    data["/playback/batches/5"] = batch
    await page.goto("http://dashboard.test/#playback")
    await expect(page.get_by_text("다음 영상 대기 3번째", exact=False)).to_be_visible()
    await expect(page.get_by_text("대기 시간 때문에 일부 강의가 마감 전에 끝나지 않을 수 있어요")).to_be_visible()
    await expect(page.locator("#task-bar")).to_contain_text("작업 1개 진행 중")
    await page.locator("#task-bar").click()
    await expect(page.locator("#task-sheet").get_by_role("button", name="재생 중지", exact=True)).to_be_visible()
    await page.set_viewport_size({"width": 375, "height": 844})
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


async def test_admin_shell_mobile_and_access(dashboard, tmp_path):
    page, data = dashboard
    data["/me"]["is_admin"] = True
    data["/admin/overview"] = {
        "days": 7, "total_users": 1, "active_users": 1, "playback_done": 0, "playback_auto": 0,
        "playback_seconds": 0, "downloads": 0, "playback_failed": 0, "failure_rate": None,
        "tracking_since": None, "problem_count": 0, "recent_activity": [], "recent_problems": [],
        "series": [{"day": (datetime.now(UTC).date() - timedelta(days=6 - i)).isoformat(),
                    "playback_done": 0, "logins": 0, "downloads": 0} for i in range(7)],
    }
    await page.goto("http://dashboard.test/")
    await page.locator("#admin-menu").click()
    await expect(page).to_have_url("http://dashboard.test/#admin/overview")
    view = page.locator("#view")
    await expect(view.get_by_role("heading", name="운영 현황", exact=True)).to_be_visible()
    await expect(view.locator(".admin-kpi")).to_have_count(4)
    await page.screenshot(path=tmp_path / "admin-desktop.png", full_page=True)
    await page.emulate_media(reduced_motion="reduce")
    for tab in ("users", "activity", "problems", "videos", "saved", "overview"):
        await page.goto(f"http://dashboard.test/#admin/{tab}")
        await expect(view.locator(".admin-tabs [aria-current='page']")).to_have_attribute("href", f"#admin/{tab}")
        for width, height in ((375, 812), (768, 1024), (1024, 768), (1440, 900), (812, 375)):
            await page.set_viewport_size({"width": width, "height": height})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (tab, width)
    await page.set_viewport_size({"width": 375, "height": 812})
    await page.screenshot(path=tmp_path / "admin-mobile.png", full_page=True)
    data["/me"]["is_admin"] = False
    admin_requests = []
    page.on("request", lambda r: admin_requests.append(r.url) if urlparse(r.url).path.startswith("/admin/") else None)
    await page.reload()
    await expect(view.get_by_text("관리자 권한이 필요합니다.", exact=True)).to_be_visible()
    await expect(page.locator("#admin-menu")).to_be_hidden()
    await page.wait_for_load_state("networkidle")
    assert admin_requests == []


async def test_lms_links_only_allow_https(dashboard):
    page, data = dashboard
    data["/week"]["assignments"][0]["url"] = "javascript:alert(3)"
    data["/week"]["later_assignments"] = [{**data["/week"]["assignments"][0], "assignment_id": 9, "title": "다음 과제",
                                           "url": "data:text/html,<script>alert(4)</script>"}]
    await page.goto("http://dashboard.test/")
    await expect(page.locator(".task")).to_have_count(3)
    hrefs = await page.eval_on_selector_all("a[href]", "els => els.map((a) => a.getAttribute('href'))")
    assert not [h for h in hrefs if h.lower().startswith(("javascript:", "data:"))], hrefs
    await expect(page.get_by_role("link", name="LMS에서 제출")).to_have_count(0)  # 안전하지 않은 주소에는 제출 버튼을 만들지 않는다


async def test_playback_history_reveals_item_error_and_retries_loading(dashboard):
    page, data = dashboard
    batch = {"id": 12, "status": "failed", "scope": "all", "total": 1,
             "done_count": 0, "failed_count": 1, "skipped_count": 0,
             "created_at": datetime.now(UTC).isoformat(), "error": None}
    data["/playback/batches"] = [batch]
    await page.goto("http://dashboard.test/#playback")
    details = page.locator(".playback-failure")
    await details.locator("summary").click()
    await expect(details.get_by_role("alert")).to_contain_text("Not found")
    data["/playback/batches/12"] = {**batch, "jobs": [
        {"title": "실패 영상 <script>", "status": "failed", "attempts": 2,
         "progress_seconds": 1, "duration": 1800,
         "error": "재생은 끝났지만 LMS에서 완료로 바뀌지 않았습니다."}]}
    await details.get_by_role("button", name="다시 시도").click()
    await expect(details).to_contain_text("실패 영상 <script>")
    await expect(details).to_contain_text("재생은 끝났지만 LMS에서 완료로 바뀌지 않았습니다.")
    await expect(details).to_contain_text("시도 2회")
    await expect(details.locator("script")).to_have_count(0)
    await page.set_viewport_size({"width": 375, "height": 844})
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


async def test_stopped_batch_hides_done_and_offers_replay(dashboard):
    page, data = dashboard
    now = datetime.now(UTC)
    job = lambda pos, item, title, status: {  # noqa: E731
        "position": pos, "lms_course_id": 1, "module_item_id": item, "title": title, "duration": 1800,
        "status": status, "progress_seconds": 0, "attempts": 0, "error": None, "deadline": None}
    stopped = {"id": 21, "status": "cancelled", "scope": "all", "total": 3, "done_count": 1, "failed_count": 0,
               "skipped_count": 0, "cancel_requested": True, "created_at": now.isoformat(), "error": None}
    data["/playback/batches"] = [stopped]
    # 1번 영상(함수와 재귀 이해하기)만 아직 /todos에 남아 있다. 완료된 영상과 LMS에서 이미 완료된 영상은 숨긴다.
    data["/playback/batches/21"] = {**stopped, "jobs": [
        job(0, 1, "함수와 재귀 이해하기", "cancelled"), job(1, 7, "이미 들은 강의", "done"), job(2, 8, "LMS에서 완료된 강의", "cancelled")]}
    # 이미 모두 완료된 예전 중지 묶음은 지난 재생에서 빠진다.
    old = {**stopped, "id": 20, "total": 1, "created_at": (now - timedelta(days=1)).isoformat()}
    data["/playback/batches"].append(old)
    data["/playback/batches/20"] = {**old, "jobs": [job(0, 9, "예전 강의", "done")]}
    await page.goto("http://dashboard.test/#playback")
    history = page.locator(".playback-history")
    await expect(history.get_by_text("함수와 재귀 이해하기")).to_be_visible()
    await expect(history.get_by_text("이미 들은 강의")).to_have_count(0)
    await expect(history.get_by_text("LMS에서 완료된 강의")).to_have_count(0)
    await expect(history.locator("[data-batch-id='20']")).to_have_count(0)

    replay = {"id": 22, "status": "queued", "scope": "item", "total": 1, "done_count": 0, "failed_count": 0,
              "skipped_count": 0, "cancel_requested": False, "queue_position": None, "estimated_wait_seconds": None,
              "created_at": now.isoformat(), "error": None, "jobs": [job(0, 1, "함수와 재귀 이해하기", "queued")]}
    data["/playback/items/1"] = replay
    data["/playback/batches/22"] = replay
    async with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/playback/items/1")):
        await history.get_by_role("button", name="함수와 재귀 이해하기 재생 요청").click()
    await expect(page.locator(".playback-hero")).to_contain_text("함수와 재귀 이해하기")


async def test_privacy_notice_shows_once(dashboard):
    page, _ = dashboard
    await page.goto("http://dashboard.test/")
    notice = page.locator("#privacy-notice")
    await expect(notice).to_contain_text("개인정보처리방침")
    await expect(notice.get_by_role("link")).to_have_attribute("href", "/privacy.html")
    await notice.get_by_role("button", name="닫기").click()
    await expect(notice).to_be_hidden()
    await page.reload()
    await expect(page.locator("#view")).not_to_be_empty()
    await expect(notice).to_be_hidden()


async def test_privacy_notice_shows_again_after_access_change(dashboard):
    page, _ = dashboard
    await page.goto("http://dashboard.test/")
    await page.evaluate("localStorage.setItem('privacy-notice-2026-11', '1')")  # 예전 안내를 닫은 사용자
    await page.reload()
    notice = page.locator("#privacy-notice")
    await expect(notice).to_be_visible()
    await expect(notice).to_contain_text("관리자가 승인한 사용자만")
    await notice.get_by_role("button", name="닫기").click()
    await page.reload()
    await expect(page.locator("#view")).not_to_be_empty()
    await expect(notice).to_be_hidden()
    assert await page.evaluate("localStorage.getItem('privacy-notice-2026-11-access')") == "1"


async def test_personal_token_registration_error_and_removal(dashboard):
    page, data = dashboard
    data["/lms/credentials"].update(personal_token_registered=False, personal_token_status="none")
    received = []
    success = False

    async def token_route(route):
        nonlocal success
        method = route.request.method
        if method == 'PUT':
            received.append(route.request.post_data_json)
            if not success:
                await route.fulfill(status=400, json={'detail': '다른 계정의 토큰입니다.'})
                return
            data['/lms/credentials'].update(personal_token_registered=True, personal_token_status='ok')
        else:
            data['/lms/credentials'].update(personal_token_registered=False, personal_token_status='none')
        await route.fulfill(json=data['/lms/credentials'])

    await page.route('**/lms/access-token', token_route)
    await page.goto('http://dashboard.test/#settings')
    field = page.get_by_role('textbox', name='개인 액세스 토큰', exact=True)
    await field.fill('private-ui-secret')
    await page.get_by_role('button', name='토큰 등록', exact=True).click()
    await expect(page.locator('#personal-token-error')).to_have_text('다른 계정의 토큰입니다.')
    await expect(field).to_have_value('')
    await expect(page.locator('#personal-token-error')).to_be_focused()
    assert received == [{'access_token': 'private-ui-secret'}]
    assert not await page.evaluate("JSON.stringify(localStorage).includes('private-ui-secret')")
    success = True
    await field.fill('private-ui-secret')
    await page.get_by_role('button', name='토큰 등록', exact=True).click()
    await expect(page.get_by_role('button', name='토큰 교체', exact=True)).to_be_visible()
    await expect(field).to_have_value('')
    for width in (1440, 390, 320):
        await page.set_viewport_size({'width': width, 'height': 844})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.get_by_role('button', name='저장된 토큰 제거', exact=True).click()
    await expect(page.get_by_role('button', name='토큰 등록', exact=True)).to_be_visible()


async def test_relogin_notice_when_no_personal_token(dashboard):
    page, data = dashboard
    await page.goto("http://dashboard.test/")
    await expect(page.locator("#view")).not_to_be_empty()
    notice = page.locator("#relogin-notice")
    await expect(notice).to_be_hidden()  # 토큰이 있으면 안내하지 않는다
    data["/lms/credentials"].update(personal_token_registered=False, personal_token_status="none")
    await page.reload()
    await expect(notice).to_be_visible()
    await expect(notice).to_contain_text("한 번 더 로그인")
    await notice.get_by_role("button", name="닫기").click()
    await expect(notice).to_be_hidden()
    await page.reload()
    await expect(notice).to_be_visible()  # 닫기는 그 탭에서만. 토큰이 생길 때까지 다시 보인다
    data["/lms/credentials"].update(personal_token_registered=True, personal_token_status="invalid")
    await page.reload()
    await expect(notice).to_be_visible()
    await notice.get_by_role("button", name="다시 로그인", exact=True).click()
    await expect(page.locator("#auth")).to_be_visible()
    await expect(notice).to_be_hidden()
