"""Dashboard interactions with local assets and mocked APIs; no school account needed."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright, expect

WEB = Path(__file__).resolve().parents[1] / "app" / "web"
pytestmark = pytest.mark.browser


@pytest.fixture
async def dashboard():
    now = datetime.now(UTC)
    data = {
        "/me": {"student_id": "20260001"},
        "/lms/credentials": {"status": "ok", "last_synced_at": now.isoformat()},
        "/courses": [{"lms_course_id": 1, "name": "컴퓨터프로그래밍", "professors": "김교수", "attendance": [], "auto_play": False}],
        "/todos": [
            {"kind": kind, "lms_course_id": 1, "lms_item_id": i, "course_name": "컴퓨터프로그래밍", "title": title,
             "deadline": (now + timedelta(hours=hours)).isoformat(), "duration": 1800 if kind == "video" else None,
             "module_title": "6주차", "url": "https://lms.ssu.ac.kr/courses/1"}
            for i, (kind, title, hours) in enumerate([
                ("assignment", "자료구조 실습 과제", 2), ("video", "함수와 재귀 이해하기", 48), ("quiz", "지난 주차 퀴즈", -1)
            ])
        ],
        "/playback/batches": [], "/announcements": [], "/notifications/channels": [],
        "/courses/1/videos": [],
        "/assignments": [
            {"assignment_id": i, "kind": "assignment", "lms_course_id": 1, "course_name": "컴퓨터프로그래밍", "title": title, "status": status,
             "deadline": (now + timedelta(hours=hours)).isoformat(), "url": "https://lms.ssu.ac.kr/courses/1/assignments/1"}
            for i, (title, status, hours) in enumerate([
                ("자료구조 실습 과제", "open", 2), ("제출한 실습", "submitted", -24), ("지난 과제", "overdue", -48), ("확인 중인 과제", "unknown", 24)
            ])
        ],
    }
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


async def test_todo_search_deadlines_and_mobile_layout(dashboard, tmp_path):
    page, _ = dashboard
    await page.goto("http://dashboard.test/#todos")
    await expect(page.locator("#todo-count")).to_have_text("3개")
    await expect(page.get_by_text("마감 지남", exact=True)).to_be_visible()
    await page.get_by_role("searchbox").fill("재귀")
    await expect(page.locator("#todo-count")).to_have_text("1개")
    await expect(page.locator("#todo-results")).to_contain_text("함수와 재귀")
    await page.get_by_role("searchbox").fill("없는 검색어")
    await page.get_by_role("button", name="전체 할 일 보기").click()
    await page.get_by_label("24시간 내 마감만 보기").check()
    await expect(page.locator("#todo-count")).to_have_text("1개")
    await expect(page.locator("#todo-results")).to_contain_text("자료구조")
    await page.get_by_label("24시간 내 마감만 보기").uncheck()
    await page.screenshot(path=tmp_path / "desktop.png", full_page=True)
    for width in (1440, 1024, 768, 390, 375, 320):
        await page.set_viewport_size({"width": width, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.screenshot(path=tmp_path / "mobile.png", full_page=True)
    await page.locator("#menu-toggle").click()
    await page.get_by_role("button", name="계정", exact=True).click()
    await expect(page.get_by_role("heading", name="계정", exact=True)).to_be_in_viewport()
    await expect(page.locator('[data-view="account"]')).to_have_attribute("aria-current", "page")


async def test_boot_failure_can_retry(dashboard):
    page, _ = dashboard
    await page.route("**/me", lambda route: route.fulfill(status=503, json={"detail": "잠시 후 다시 시도하세요."}))
    await page.goto("http://dashboard.test/#todos")
    await expect(page.get_by_role("button", name="다시 시도", exact=True)).to_be_visible()
    await page.unroute("**/me")
    await page.get_by_role("button", name="다시 시도", exact=True).click()
    await expect(page.locator("#todo-count")).to_have_text("3개")


async def test_notification_response_does_not_replace_another_view(dashboard):
    page, _ = dashboard
    requested, release = asyncio.Event(), asyncio.Event()

    async def delayed(route):
        requested.set()
        await release.wait()
        await route.fulfill(json=[])

    await page.route("**/notifications/channels", delayed)
    await page.goto("http://dashboard.test/#todos")
    await page.get_by_role("button", name="알림", exact=True).click()
    await asyncio.wait_for(requested.wait(), timeout=5)
    await page.get_by_role("button", name="과목", exact=True).click()
    async with page.expect_response("**/notifications/channels"):
        release.set()
    await expect(page.get_by_role("heading", name="과목", exact=True)).to_be_visible()
    await expect(page.locator("#channel-form")).to_have_count(0)


async def test_login_password_visibility(dashboard, tmp_path):
    page, _ = dashboard
    await page.goto("http://dashboard.test/#todos")
    await page.get_by_role("button", name="로그아웃", exact=True).click()
    await page.get_by_label("비밀번호", exact=True).fill("example-password")
    await page.get_by_role("button", name="표시", exact=True).click()
    await expect(page.locator("#password")).to_have_attribute("type", "text")
    await page.get_by_role("button", name="숨기기", exact=True).click()
    await expect(page.locator("#password")).to_have_attribute("type", "password")
    await page.screenshot(path=tmp_path / "login.png", full_page=True)


async def test_summary_shortcuts_and_browser_history(dashboard):
    page, _ = dashboard
    await page.goto("http://dashboard.test/#todos")
    await page.get_by_role("button", name="미수강 영상 1개 보기").click()
    await expect(page.locator("#todo-count")).to_have_text("1개")
    await expect(page.locator("#todo-results")).to_contain_text("함수와 재귀")
    await page.get_by_role("button", name="수강 중인 과목 1개 보기").click()
    await expect(page.locator(".overview")).to_be_hidden()
    await expect(page).to_have_url("http://dashboard.test/#courses")
    await page.go_back()
    await expect(page.locator("#todo-search")).to_be_visible()
    await expect(page.locator('[data-f="video"]')).to_have_attribute("aria-pressed", "true")
    await page.get_by_role("button", name="24시간 내 마감 1개 보기").click()
    await expect(page.get_by_label("24시간 내 마감만 보기")).to_be_checked()
    await expect(page.locator("#todo-results")).to_contain_text("자료구조")


async def test_mobile_menu_calendar_and_long_titles(dashboard, tmp_path):
    page, data = dashboard
    title = "긴 강의 제목도 휴대폰에서 끝까지 읽을 수 있어야 합니다 " * 3
    data["/todos"][0]["title"] = title.strip()
    await page.set_viewport_size({"width": 375, "height": 812})
    await page.emulate_media(reduced_motion="reduce")
    await page.goto("http://dashboard.test/#todos")
    await expect(page.locator("#calendar-content")).to_be_hidden()
    await page.get_by_role("button", name="일정 펼치기").click()
    await expect(page.locator("#calendar-content")).to_be_visible()
    assert await page.locator(".chip span").first.evaluate("el => el.scrollWidth <= el.clientWidth && el.scrollHeight <= el.clientHeight")
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await page.locator("#menu-toggle").click()
    await expect(page.get_by_role("button", name="계정", exact=True)).to_be_in_viewport()
    await page.keyboard.press("Escape")
    await expect(page.locator("#menu-toggle")).to_be_focused()
    await expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
    await page.screenshot(path=tmp_path / "mobile-long-titles.png", full_page=True)
    await page.set_viewport_size({"width": 812, "height": 375})
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


async def test_deep_link_and_skip_link_keep_current_page(dashboard):
    page, _ = dashboard
    await page.goto("http://dashboard.test/#courses")
    await expect(page.get_by_role("heading", name="과목", exact=True)).to_be_visible()
    await page.locator(".skip-link").focus()
    await page.keyboard.press("Enter")
    await expect(page.locator("#view")).to_be_focused()
    await expect(page).to_have_url("http://dashboard.test/#courses")
    await expect(page.locator(".overview")).to_be_hidden()
