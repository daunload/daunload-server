"""첫 화면 '이번 주 할 일': 최소한의 정보와 버튼 하나 (가짜 API, 실제 Chrome)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright, expect

WEB = Path(__file__).resolve().parents[1] / "app" / "web"
pytestmark = pytest.mark.browser
MIME = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}
LMS = "https://canvas.ssu.ac.kr/courses/1/assignments/3"


def _video(now, i, title, hours):
    return {"kind": "video", "lms_course_id": 1, "course_name": "피지컬미디어", "lms_item_id": i, "title": title,
            "deadline": (now + timedelta(hours=hours)).isoformat(), "due_at": None, "url": None,
            "module_id": 6, "module_title": "6주차", "duration": 1800}


def _assignment(now, aid, title, hours, status="open", kind="quiz"):
    return {"status": status, "kind": kind, "lms_course_id": 1, "course_name": "피지컬미디어", "assignment_id": aid,
            "title": title, "group_name": None, "due_at": None, "deadline": (now + timedelta(hours=hours)).isoformat(),
            "lock_at": None, "unlock_at": None, "submitted_at": None, "missing": False, "score": None,
            "points_possible": 5, "url": LMS}


def _data(now):
    videos = [_video(now, 11, "아두이노 소개", 5), _video(now, 12, "버튼과 LED", 30)]
    plan = [{"position": i, "lms_course_id": 1, "course_name": "피지컬미디어", "module_id": 6, "module_title": "6주차",
             "module_item_id": v["lms_item_id"], "title": v["title"], "duration": 1800, "deadline": v["deadline"],
             "expected_start": now.isoformat(), "expected_end": (now + timedelta(minutes=31 * (i + 1))).isoformat(),
             "at_risk": False} for i, v in enumerate(videos)]
    batch = {"id": 7, "scope": "week", "lms_course_id": None, "module_id": None, "status": "running", "total": 2,
             "done_count": 0, "failed_count": 0, "skipped_count": 0, "current_position": 0, "expected_end": None,
             "cancel_requested": False, "error": None, "created_at": now.isoformat(), "started_at": now.isoformat(),
             "finished_at": None}
    jobs = [{"position": i, "lms_course_id": 1, "module_item_id": v["lms_item_id"], "title": v["title"], "duration": 1800,
             "deadline": v["deadline"], "status": "playing" if i == 0 else "queued", "attempts": 1, "progress_seconds": 540,
             "error": None, "started_at": None, "finished_at": None} for i, v in enumerate(videos)]
    return {
        "/me": {"student_id": "20260001"},
        "/lms/credentials": {"student_id": "20260001", "status": "ok", "last_error": None, "last_login_at": now.isoformat(),
                             "last_synced_at": now.isoformat(), "next_sync_at": None},
        "/courses": [{"lms_course_id": 1, "name": "피지컬미디어", "professors": "교수", "attendance": [], "auto_play": False}],
        "/todos": videos, "/announcements": [], "/notifications/channels": [], "/playback/batches": [],
        "/week": {"start": (now - timedelta(days=2)).isoformat(), "end": (now + timedelta(days=5)).isoformat(),
                  "videos": videos, "video_seconds": 3600, "assignments": [_assignment(now, 3, "6주차 퀴즈", 48)], "missed": []},
        "/playback/batches/preview": {"items": plan, "total_seconds": 3660, "expected_end": plan[-1]["expected_end"], "warnings": []},
        "/playback/batches/7": {**batch, "jobs": jobs},
        "POST /playback/batches": batch,
    }


@pytest.fixture
async def page():
    now = datetime.now(UTC)
    data = _data(now)
    calls = []
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        pg = await browser.new_page(viewport={"width": 1280, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))

        async def route(r):
            url = urlparse(r.request.url)
            if url.hostname != "dashboard.test":
                return await r.abort()
            path, method = url.path, r.request.method
            if method == "POST" and path == "/playback/batches":
                calls.append(("POST", path, json.loads(r.request.post_data)))
                return await r.fulfill(status=201, json=data["POST /playback/batches"])
            if method == "DELETE":
                calls.append(("DELETE", path, None))
                data["/playback/batches/7"]["cancel_requested"] = True
                return await r.fulfill(json=data["/playback/batches/7"])
            f = WEB / ("index.html" if path == "/" else path.lstrip("/"))
            if f.suffix in MIME and f.is_file():
                return await r.fulfill(body=f.read_text(), content_type=MIME[f.suffix])
            if path in data:
                return await r.fulfill(json=data[path])
            await r.fulfill(status=404, json={"detail": "Not found"})

        await pg.route("**/*", route)
        await pg.add_init_script('localStorage.setItem("token", "t");')
        pg.calls, pg.data, pg.now = calls, data, now
        yield pg
        await browser.close()
    assert not errors, errors


async def test_first_screen_shows_only_this_week(page, tmp_path):
    await page.goto("http://dashboard.test/")
    await expect(page.get_by_role("heading", name="이번 주 할 일이 3개 남았어요")).to_be_visible()
    rows = page.locator(".task")
    await expect(rows).to_have_count(3)
    await expect(rows.first).to_contain_text("아두이노 소개")  # 마감순
    await expect(page.get_by_role("link", name="6주차 퀴즈 제출하기 (새 탭)")).to_have_attribute("href", LMS)
    # 첫 화면에는 요약 카드·검색·7일 일정표가 없다
    await expect(page.locator(".overview")).to_be_hidden()
    await expect(page.locator(".rail-wrap")).to_be_hidden()
    await expect(page.locator("#week-panel .btn.primary")).to_have_count(1)
    await page.screenshot(path=tmp_path / "home.png", full_page=True)
    for width in (1280, 768, 390, 320):
        await page.set_viewport_size({"width": width, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
    await page.screenshot(path=tmp_path / "home-mobile.png", full_page=True)


async def test_one_button_plays_this_weeks_lectures(page, tmp_path):
    await page.goto("http://dashboard.test/")
    await page.get_by_role("button", name="강의 2개 한 번에 듣기").click()
    await expect(page.locator(".cta-confirm")).to_contain_text("강의 2개를 순서대로 끝까지 들을게요")
    await expect(page.locator(".cta-confirm")).to_contain_text("학칙")
    await page.screenshot(path=tmp_path / "confirm.png", full_page=True)
    await page.get_by_role("button", name="시작하기").click()
    await expect(page.locator(".cta-progress")).to_contain_text("0/2개 완료")
    await expect(page.locator(".task").first).to_contain_text("듣는 중 30%")
    assert page.calls == [("POST", "/playback/batches", {"scope": "week"})]
    await page.screenshot(path=tmp_path / "playing.png", full_page=True)

    await page.get_by_role("button", name="중지").click()
    await expect(page.locator(".cta-progress")).to_contain_text("중지하는 중이에요")
    assert page.calls[-1] == ("DELETE", "/playback/batches/7", None)


async def test_cancel_confirmation_starts_nothing(page):
    await page.goto("http://dashboard.test/")
    await page.get_by_role("button", name="강의 2개 한 번에 듣기").click()
    await page.get_by_role("button", name="취소").click()
    await expect(page.get_by_role("button", name="강의 2개 한 번에 듣기")).to_be_visible()
    assert page.calls == []


async def test_only_assignments_left_button_opens_submission(page):
    page.data["/week"]["videos"] = []
    page.data["/week"]["video_seconds"] = 0
    await page.goto("http://dashboard.test/")
    await expect(page.get_by_role("heading", name="이번 주 할 일이 1개 남았어요")).to_be_visible()
    await expect(page.get_by_role("link", name="6주차 퀴즈 제출하러 가기")).to_have_attribute("href", LMS)


async def test_all_done(page):
    page.data["/week"].update(videos=[], video_seconds=0, assignments=[])
    await page.goto("http://dashboard.test/")
    await expect(page.get_by_role("heading", name="이번 주 할 일을 모두 끝냈어요")).to_be_visible()
    await expect(page.locator("#week-panel .btn.primary")).to_have_count(0)
    await page.get_by_role("button", name="전체 할 일 보기").click()
    await expect(page).to_have_url("http://dashboard.test/#todos")



async def test_dashboard_all_playable_lectures_start_with_one_click(page, tmp_path):
    # A lecture due next week still belongs to the all-courses playable list.
    plan = page.data["/playback/batches/preview"]
    plan["items"][1]["deadline"] = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    page.data["/week"]["videos"] = page.data["/week"]["videos"][:1]
    await page.goto("http://dashboard.test/")
    panel = page.locator("#dashboard-playback")
    await expect(panel.get_by_role("heading", name="재생 가능한 강의 2개", exact=True)).to_be_visible()
    await expect(panel.locator(".dashboard-lecture-list li")).to_have_count(2)
    await expect(panel).to_contain_text("버튼과 LED")
    for width in (1280, 768, 375, 320):
        await page.set_viewport_size({"width": width, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await page.screenshot(path=tmp_path / "playable-mobile.png", full_page=True)
    await panel.get_by_role("button", name="모두 재생 (2개)", exact=True).click()
    await expect(panel.get_by_role("status")).to_contain_text("0/2개 완료")
    assert page.calls == [("POST", "/playback/batches", {"scope": "all"})]
    await expect(panel.locator("#dashboard-play-all")).to_have_count(0)
    await expect(panel).to_contain_text("재생 중")
    await panel.get_by_role("button", name="재생 현황 보기").click()
    await expect(page).to_have_url("http://dashboard.test/#playback")


async def test_dashboard_playable_empty_and_loading_error(page):
    page.data["/playback/batches/preview"].update(items=[], total_seconds=0, expected_end=None)
    await page.goto("http://dashboard.test/")
    panel = page.locator("#dashboard-playback")
    await expect(panel.get_by_role("button", name="모두 재생 (0개)")).to_be_disabled()
    await expect(panel).to_contain_text("현재 재생 가능한 미수강 강의가 없습니다.")
    await page.route("**/playback/batches/preview?scope=all", lambda r: r.fulfill(status=503, json={"detail": "조회 실패"}))
    await panel.get_by_role("button", name="목록 새로고침").click()
    await expect(panel.get_by_role("alert")).to_contain_text("조회 실패")
    await page.unroute("**/playback/batches/preview?scope=all")
    await panel.get_by_role("button", name="다시 시도").click()
    await expect(panel).to_contain_text("현재 재생 가능한 미수강 강의가 없습니다.")


async def test_dashboard_accepted_job_survives_detail_failure(page):
    await page.route("**/playback/batches/7", lambda r: r.fulfill(status=503, json={"detail": "일시 오류"}))
    await page.goto("http://dashboard.test/")
    panel = page.locator("#dashboard-playback")
    await panel.get_by_role("button", name="모두 재생 (2개)").click()
    await expect(panel.get_by_role("status")).to_contain_text("0/2개 완료")
    await expect(panel.locator("#dashboard-play-all")).to_have_count(0)
    assert page.calls == [("POST", "/playback/batches", {"scope": "all"})]


async def test_dashboard_repeated_click_only_enqueues_once(page):
    await page.goto("http://dashboard.test/")
    await expect(page.locator("#dashboard-play-all")).to_be_enabled()
    await page.evaluate('''() => {
      const button = document.getElementById("dashboard-play-all");
      button.click();
      button.click();
    }''')
    await expect(page.locator(".dashboard-playback-progress")).to_contain_text("0/2개 완료")
    assert page.calls == [("POST", "/playback/batches", {"scope": "all"})]
