"""캘린더: 마감일에 해야 할 일·완료·마감 지남을 표시 (가짜 API, 실제 Chrome)."""

from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright, expect

WEB = Path(__file__).resolve().parents[1] / "app" / "web"
pytestmark = pytest.mark.browser
MIME = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}


def _a(aid, title, when, status, **kw):
    return {"status": status, "kind": kw.get("kind", "assignment"), "lms_course_id": 1, "course_name": "피지컬미디어",
            "assignment_id": aid, "title": title, "group_name": None, "due_at": None,
            "deadline": when.isoformat() if when else None, "lock_at": None, "unlock_at": kw.get("unlock"),
            "submitted_at": kw.get("submitted"), "missing": False, "score": None, "points_possible": 5,
            "url": f"https://canvas.ssu.ac.kr/courses/1/assignments/{aid}"}


@pytest.fixture
async def page():
    now = datetime.now().astimezone()  # 브라우저와 같은 지역 시간
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    later = today + timedelta(days=40)
    data = {
        "/me": {"student_id": "20260001"},
        "/lms/credentials": {"student_id": "20260001", "status": "ok", "last_error": None, "last_login_at": now.isoformat(),
                             "last_synced_at": now.isoformat(), "next_sync_at": None},
        "/courses": [{"lms_course_id": 1, "name": "피지컬미디어", "professors": "교수", "attendance": [], "auto_play": False}],
        "/todos": [{"kind": "video", "lms_course_id": 1, "course_name": "피지컬미디어", "lms_item_id": 9, "title": "버튼과 LED",
                    "deadline": (today + timedelta(hours=23, minutes=50)).isoformat(), "due_at": None, "url": None,
                    "module_id": 6, "module_title": "6주차", "duration": 1800}],
        "/assignments": [
            _a(1, "실습 5 보고서", today + timedelta(hours=23, minutes=30), "open"),
            _a(2, "실습 4 보고서", today + timedelta(hours=12), "submitted", submitted=(today - timedelta(days=2)).isoformat()),
            _a(3, "5주차 퀴즈", today + timedelta(minutes=5), "overdue", kind="quiz"),
            _a(4, "기말 프로젝트", later + timedelta(hours=12), "open"),
            _a(5, "마감 없는 과제", None, "no_due"),
        ],
        "/week": {"start": today.isoformat(), "end": (today + timedelta(days=7)).isoformat(), "videos": [], "video_seconds": 0,
                  "assignments": [], "missed": []},
        "/playback/batches": [], "/announcements": [], "/notifications/channels": [],
    }
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        pg = await browser.new_page(viewport={"width": 1280, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))

        async def route(r):
            url = urlparse(r.request.url)
            if url.hostname != "dashboard.test":
                return await r.abort()
            f = WEB / ("index.html" if url.path == "/" else url.path.lstrip("/"))
            if f.suffix in MIME and f.is_file():
                return await r.fulfill(body=f.read_text(), content_type=MIME[f.suffix])
            if url.path in data:
                return await r.fulfill(json=data[url.path])
            await r.fulfill(status=404, json={"detail": "Not found"})

        await pg.route("**/*", route)
        await pg.add_init_script('localStorage.setItem("token", "t");')
        pg.today, pg.later = today, later
        yield pg
        await browser.close()
    assert not errors, errors


def _cell(page, d):
    return page.locator(f'.cal-day[data-day="{d.year}-{d.month - 1}-{d.day}"]')


async def test_calendar_marks_today_and_lists_items(page, tmp_path):
    await page.goto("http://dashboard.test/#calendar")
    t = page.today
    await expect(page.locator("#cal-title")).to_have_text(f"{t.year}년 {t.month}월")
    today = _cell(page, t)
    await expect(today).to_have_attribute("aria-pressed", "true")
    await expect(today).to_have_attribute("aria-label", f"{t.month}월 {t.day}일, 해야 할 일 2개, 마감 지남 1개, 완료 1개")
    await expect(today.locator(".cal-dots i")).to_have_count(3)

    items = page.locator(".cal-item")
    await expect(items).to_have_count(4)  # 마감 없는 과제는 날짜에 놓지 않는다
    # 해야 할 일 → 마감 지남 → 완료 순
    await expect(items.nth(0)).to_contain_text("실습 5 보고서")
    await expect(items.nth(1)).to_contain_text("버튼과 LED")
    await expect(items.nth(1)).to_contain_text("6주차")
    await expect(items.nth(2)).to_contain_text("마감 지남")
    await expect(items.nth(3)).to_contain_text("완료")
    await expect(items.nth(3)).to_contain_text("제출")
    await page.screenshot(path=tmp_path / "calendar.png", full_page=True)


async def test_select_empty_day_and_move_months(page):
    await page.goto("http://dashboard.test/#calendar")
    t, later = page.today, page.later
    # 같은 달의 다른 날(할 일 없음)
    other = t.replace(day=1) if t.day != 1 else t.replace(day=2)
    await _cell(page, other).click()
    await expect(page.locator(".cal-list")).to_contain_text("이날은 할 일이 없어요")

    # 40일 뒤 과제가 있는 달로 이동
    months = (later.year - t.year) * 12 + later.month - t.month
    for _ in range(months):
        await page.get_by_role("button", name="다음 달").click()
    await expect(page.locator("#cal-title")).to_have_text(f"{later.year}년 {later.month}월")
    await _cell(page, later).click()
    await expect(page.locator(".cal-item")).to_contain_text("기말 프로젝트")

    await page.get_by_role("button", name="오늘", exact=True).click()
    await expect(page.locator("#cal-title")).to_have_text(f"{t.year}년 {t.month}월")
    await expect(_cell(page, t)).to_have_attribute("aria-pressed", "true")


async def test_calendar_from_menu_and_mobile(page, tmp_path):
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto("http://dashboard.test/")
    await page.locator("#menu-toggle").click()
    await page.get_by_role("button", name="캘린더", exact=True).click()
    await expect(page).to_have_url("http://dashboard.test/#calendar")
    await expect(page.locator(".cal-grid")).to_be_visible()
    for width in (390, 320):
        await page.set_viewport_size({"width": width, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
    await page.screenshot(path=tmp_path / "calendar-mobile.png", full_page=True)
