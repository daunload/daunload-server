"""첫 화면 '이번 주 할 일': 최소한의 정보와 버튼 하나 (가짜 API, 실제 Chrome)."""

import json
import re
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
        "/me": {"student_id": "20260001", "access_status": "approved"},
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
    await expect(page.get_by_role("link", name="6주차 퀴즈 LMS에서 제출 (새 탭)")).to_have_attribute("href", LMS)
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
    await page.get_by_role("button", name="이번 주 강의 2개 재생").click()
    dialog = page.locator("#confirm-dialog")
    await expect(dialog).to_contain_text("이번 주 강의 2개를 순서대로 재생할까요?")
    await expect(dialog).to_contain_text("학칙")
    await page.screenshot(path=tmp_path / "confirm.png", full_page=True)
    await dialog.get_by_role("button", name="2개 재생 시작").click()
    await expect(page.locator(".cta-progress")).to_contain_text("강의 재생 중 0/2개")
    await expect(page.locator(".task").first).to_contain_text("듣는 중 30%")
    assert page.calls == [("POST", "/playback/batches", {"scope": "week"})]
    await page.screenshot(path=tmp_path / "playing.png", full_page=True)

    await page.locator("#task-bar").click()
    sheet = page.locator("#task-sheet")
    await sheet.get_by_role("button", name="재생 중지").click()
    await dialog.get_by_role("button", name="재생 중지").click()
    await expect(sheet).to_contain_text("중지하는 중이에요")
    assert page.calls[-1] == ("DELETE", "/playback/batches/7", None)


async def test_cancel_confirmation_starts_nothing(page):
    await page.goto("http://dashboard.test/")
    await page.get_by_role("button", name="이번 주 강의 2개 재생").click()
    await page.locator("#confirm-dialog").get_by_role("button", name="취소").click()
    await expect(page.get_by_role("button", name="이번 주 강의 2개 재생")).to_be_visible()
    assert page.calls == []


async def test_only_assignments_left_button_opens_submission(page):
    page.data["/week"]["videos"] = []
    page.data["/week"]["video_seconds"] = 0
    await page.goto("http://dashboard.test/")
    await expect(page.get_by_role("heading", name="이번 주 할 일이 1개 남았어요")).to_be_visible()
    await expect(page.get_by_role("link", name="6주차 퀴즈 LMS에서 제출", exact=True)).to_have_attribute("href", LMS)


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
    await panel.get_by_role("button", name="전체 강의 2개 재생", exact=True).click()
    await page.locator("#confirm-dialog").get_by_role("button", name="2개 재생 시작").click()
    progress = page.locator(".cta-progress")
    await expect(progress).to_contain_text("강의 재생 중 0/2개")
    assert page.calls == [("POST", "/playback/batches", {"scope": "all"})]
    await expect(panel.locator("#dashboard-play-all")).to_have_count(0)
    await expect(panel).to_contain_text("재생 중")
    await progress.get_by_role("button", name="재생 현황 보기").click()
    await expect(page).to_have_url("http://dashboard.test/#playback")


async def test_dashboard_playable_empty_and_loading_error(page):
    page.data["/playback/batches/preview"].update(items=[], total_seconds=0, expected_end=None)
    await page.goto("http://dashboard.test/")
    panel = page.locator("#dashboard-playback")
    await expect(panel.get_by_role("button", name="전체 강의 0개 재생")).to_be_disabled()
    await expect(panel).to_contain_text("현재 재생 가능한 미수강 강의가 없습니다.")
    await page.route("**/playback/batches/preview?scope=all", lambda r: r.fulfill(status=503, json={"detail": "조회 실패"}))
    await panel.get_by_role("button", name="목록 다시 불러오기").click()
    await expect(panel.get_by_role("alert")).to_contain_text("조회 실패")
    await page.unroute("**/playback/batches/preview?scope=all")
    await panel.get_by_role("button", name="다시 시도").click()
    await expect(panel).to_contain_text("현재 재생 가능한 미수강 강의가 없습니다.")


async def test_dashboard_accepted_job_survives_detail_failure(page):
    await page.route("**/playback/batches/7", lambda r: r.fulfill(status=503, json={"detail": "일시 오류"}))
    await page.goto("http://dashboard.test/")
    panel = page.locator("#dashboard-playback")
    await panel.get_by_role("button", name="전체 강의 2개 재생").click()
    await page.locator("#confirm-dialog").get_by_role("button", name="2개 재생 시작").click()
    await expect(page.locator(".cta-progress")).to_contain_text("강의 재생 중 0/2개")
    await expect(page.locator("#task-bar")).to_contain_text("작업 1개 진행 중")
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
    await page.locator("#confirm-dialog").get_by_role("button", name="2개 재생 시작").click()
    await expect(page.locator(".cta-progress")).to_contain_text("강의 재생 중 0/2개")
    assert page.calls == [("POST", "/playback/batches", {"scope": "all"})]


async def test_playback_percent_queue_and_live_update(page):
    batch = page.data["/playback/batches/7"]
    page.data["/playback/batches"] = [batch]
    await page.goto("http://dashboard.test/#playback")
    panel = page.locator("#view")
    hero = panel.locator(".playback-hero")
    await expect(hero).to_contain_text("전체 진행률 15%")
    await expect(hero.get_by_role("heading")).to_have_text("아두이노 소개")
    await expect(hero.locator(".playback-percent")).to_have_text("30%")
    await expect(panel.locator(".steps li").nth(1)).to_contain_text("버튼과 LED")
    await expect(panel.locator(".steps li").nth(1)).to_contain_text("다음 재생")
    batch["jobs"][0].update(status="done", progress_seconds=1800)
    batch["jobs"][1].update(status="playing", progress_seconds=900)
    batch["done_count"] = 1
    await expect(hero).to_contain_text("전체 진행률 75%", timeout=10000)
    await expect(hero.get_by_role("heading")).to_have_text("버튼과 LED")
    await expect(hero.locator(".playback-percent")).to_have_text("50%")
    await expect(panel.locator(".steps .status.done")).to_have_count(1)
    await page.set_viewport_size({"width": 375, "height": 812})
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


async def test_playback_queue_excludes_failed_and_cancelled_jobs(page):
    batch = page.data["/playback/batches/7"]
    batch["jobs"][0].update(status="skipped", duration=0)
    batch["jobs"][1].update(status="failed", duration=0)
    page.data["/playback/batches"] = [batch]
    await page.goto("http://dashboard.test/#playback")
    panel = page.locator("#view")
    hero = panel.locator(".playback-hero")
    await expect(hero).to_contain_text("전체 진행률 0%")
    await expect(hero.get_by_role("heading")).to_have_text("재생을 준비하고 있어요")
    await expect(panel.get_by_text("다음 재생", exact=True)).to_have_count(0)
    batch["jobs"][1].update(status="queued", duration=1800)
    batch["cancel_requested"] = True
    await expect(hero.get_by_role("heading")).to_have_text("예정된 강의는 재생하지 않아요", timeout=10000)
    await expect(hero).to_contain_text("재생 중지 요청됨")
    await expect(panel.get_by_text("다음 재생", exact=True)).to_have_count(0)


async def test_reload_shows_saved_progress_and_stale_activity(page):
    batch = page.data["/playback/batches/7"]
    page.data["/playback/batches"] = [batch]
    job = batch["jobs"][0]
    job["progress_updated_at"] = datetime.now(UTC).isoformat()
    await page.goto("http://dashboard.test/#playback")
    hero = page.locator("#view .playback-hero")
    await expect(hero).to_contain_text("9분 0초 / 30분 0초")
    await expect(hero.locator(".warning")).to_have_count(0)  # 정상일 때는 따로 말하지 않는다
    job.update(progress_seconds=900, progress_updated_at=(datetime.now(UTC) - timedelta(minutes=2)).isoformat())
    await page.reload()
    await expect(hero.locator(".playback-percent")).to_have_text("50%")
    await expect(hero.locator(".warning")).to_contain_text("재생 확인 필요")
    await expect(hero.locator(".warning")).to_contain_text("마지막")
    job.update(progress_updated_at=None)
    await page.reload()
    await expect(hero).to_contain_text("재생 위치를 확인하는 중이에요")


async def test_external_buttons_show_new_tab_icon(page):
    await page.goto("http://dashboard.test/")
    submit = page.get_by_role("link", name="6주차 퀴즈 LMS에서 제출 (새 탭)")
    await expect(submit.locator('svg[data-icon="external"][aria-hidden="true"]')).to_have_count(1)
    lms = page.locator('a[href="https://lms.ssu.ac.kr"]')
    await expect(lms.locator('svg[data-icon="external"][aria-hidden="true"]')).to_have_count(1)


async def test_dashboard_has_one_primary_button(page):
    await page.goto("http://dashboard.test/")
    play_all = page.locator("#dashboard-play-all")
    await expect(play_all).to_have_text("전체 강의 2개 재생")
    await expect(play_all).not_to_have_class(re.compile(r"\bprimary\b"))
    await expect(page.locator(".btn.primary:visible")).to_have_count(1)


async def test_playback_view_says_each_fact_once(page):
    batch = page.data["/playback/batches/7"]
    page.data["/playback/batches"] = [batch]
    await page.goto("http://dashboard.test/#playback")
    panel = page.locator("#view")
    hero = panel.locator(".playback-hero")
    await expect(hero.get_by_role("heading", name="아두이노 소개")).to_be_visible()
    await expect(panel.locator("progress")).to_have_count(2)  # 지금 강의와 전체, 두 개뿐이다
    await expect(panel.get_by_text("아두이노 소개", exact=True)).to_have_count(2)  # 지금 강의 제목과 목록 한 줄
    await expect(hero).not_to_contain_text("0 실패")
    await expect(panel.locator(".steps .progress, .steps progress")).to_have_count(0)


async def test_playback_view_with_one_lecture_has_no_duplicate_list(page):
    batch = page.data["/playback/batches/7"]
    batch["jobs"] = batch["jobs"][:1]
    batch["total"] = 1
    page.data["/playback/batches"] = [batch]
    await page.goto("http://dashboard.test/#playback")
    panel = page.locator("#view")
    await expect(panel.locator(".playback-hero")).to_contain_text("아두이노 소개")
    await expect(panel.locator(".steps")).to_have_count(0)
    await expect(panel.locator("progress")).to_have_count(1)


async def test_playback_guide_is_folded_and_stays_open_across_polls(page):
    batch = page.data["/playback/batches/7"]
    page.data["/playback/batches"] = [batch]
    await page.goto("http://dashboard.test/#playback")
    panel = page.locator("#view")
    guide = panel.locator(".playback-help")
    await expect(guide.get_by_text("번갈아")).to_be_hidden()
    await guide.locator("summary").click()
    await expect(guide.get_by_text("번갈아")).to_be_visible()
    batch["jobs"][0]["progress_seconds"] = 1200
    await expect(panel.locator(".playback-hero")).to_contain_text("66%", timeout=10000)
    await expect(panel.locator(".playback-help").get_by_text("번갈아")).to_be_visible()


async def test_playback_history_hides_zero_counts(page):
    batch = page.data["/playback/batches/7"]
    page.data["/playback/batches"] = [{**batch, "id": 8, "status": "failed", "scope": "all", "total": 1, "done_count": 0,
                                       "failed_count": 1, "skipped_count": 0, "error": None, "created_at": "2026-10-30T11:58:00Z"}]
    page.data.pop("/playback/batches/7", None)
    await page.goto("http://dashboard.test/#playback")
    row = page.locator("#view .playback-history li")
    await expect(row).to_contain_text("실패 1")
    await expect(row).not_to_contain_text("완료 0")
