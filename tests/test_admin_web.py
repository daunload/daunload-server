"""관리자 화면 뼈대와 현황 탭. tests.test_web의 dashboard 픽스처로 정적 파일과 가짜 API를 쓴다."""

import asyncio
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, quote, urlparse

import pytest
from playwright.async_api import expect

from tests.test_web import dashboard  # noqa: F401  픽스처

pytestmark = pytest.mark.browser

BASE = "http://dashboard.test/"


def overview(days=7, **over):
    today = date.today()
    now = datetime.now(UTC)
    series = [{"day": (today - timedelta(days=days - 1 - i)).isoformat(),
               "playback_done": i * 2, "logins": i + 1, "downloads": i % 3} for i in range(days)]
    data = {
        "days": days, "total_users": 132, "active_users": 48,
        "playback_done": 316, "playback_auto": 40, "playback_seconds": 41 * 3600,
        "downloads": 87, "playback_failed": 12, "failure_rate": 12 / 328,
        "tracking_since": "2026-11-01", "problem_count": 15, "series": series,
        "recent_activity": [{"id": 1, "kind": "playback", "title": "자료구조 3강 <img src=x onerror=alert(1)>",
                             "course_name": "자료구조", "status": "done", "recorded_at": now.isoformat(),
                             "started_at": now.isoformat(), "finished_at": now.isoformat(), "count": 1,
                             "student_id": "20231234"},
                            {"id": 2, "kind": "download", "title": "자료구조 4강", "course_name": "자료구조",
                             "status": "link_issued", "recorded_at": now.isoformat(), "started_at": None,
                             "finished_at": None, "count": 9, "student_id": "20231234"}],
        "recent_problems": [
            {"kind": "playback", "occurred_at": now.isoformat(), "student_id": "20220456", "course_name": "운영체제",
             "title": "7강 스케줄링", "error": "플레이어 로딩 시간 초과", "not_run": 0},
            {"kind": "lms", "occurred_at": None, "student_id": "20231877", "course_name": None,
             "title": "LMS 연결", "error": "비밀번호 변경 추정", "not_run": 0},
            {"kind": "batch", "occurred_at": now.isoformat(), "student_id": "20210092", "course_name": None,
             "title": "all", "error": "중단됨", "not_run": 3},
        ],
    }
    data.update(over)
    return data


def as_admin(data, **over):
    data["/me"]["is_admin"] = True
    data["/admin/overview"] = overview(**over)
    data["/admin/users"] = {"total": 0, "users": []}
    data["/admin/activities"] = {"total": 0, "counts": {"playback": 0, "download": 0, "transcript": 0}, "activities": []}
    data["/admin/problems"] = {"total": 0, "counts": {"playback": 0, "batch": 0, "lms": 0, "resolve": 0}, "problems": []}
    data["/admin/video-urls"] = {"total": 0, "items": []}
    data["/admin/saved-videos"] = {"total": 0, "counts": {"saved": 0, "pending": 0, "failed": 0},
                                   "summary": {"items": 0, "videos": 0, "users": 0, "links": 0}, "items": []}


async def test_admin_tabs_routes_and_back(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    await page.goto(BASE + "#admin")
    await expect(page).to_have_url(BASE + "#admin/overview")
    tabs = page.locator("#view nav.admin-tabs")
    await expect(tabs.get_by_role("link", name="현황")).to_have_attribute("aria-current", "page")
    await expect(page.locator("#view .admin-kpi")).to_have_count(4)
    await tabs.get_by_role("link", name="사용자").click()
    await expect(page).to_have_url(BASE + "#admin/users")
    await expect(tabs.get_by_role("link", name="사용자")).to_have_attribute("aria-current", "page")
    await expect(tabs.get_by_role("link", name="사용자")).to_be_focused()
    await expect(page.locator("#view")).to_contain_text("조건에 맞는 사용자가 없습니다")
    await expect(page.locator("#view .admin-kpi")).to_have_count(0)
    await page.go_back()
    await expect(page).to_have_url(BASE + "#admin/overview")
    await expect(tabs.get_by_role("link", name="현황")).to_have_attribute("aria-current", "page")
    await expect(page.locator("#view .admin-kpi")).to_have_count(4)
    await expect(page.locator(".workspace")).to_have_class("workspace admin-workspace")
    await page.get_by_role("button", name="강의 재생", exact=True).click()
    await expect(page).to_have_url(BASE + "#playback")
    await expect(page.locator(".workspace")).not_to_have_class("admin-workspace")


async def test_admin_deep_link_survives_reload(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    await page.goto(BASE + "#admin/problems")
    tabs = page.locator("#view nav.admin-tabs")
    await expect(tabs.get_by_role("link", name="문제")).to_have_attribute("aria-current", "page")
    await page.reload()
    await expect(page).to_have_url(BASE + "#admin/problems")
    await expect(tabs.get_by_role("link", name="문제")).to_have_attribute("aria-current", "page")
    await expect(page.locator('[data-view="admin"]')).to_have_attribute("aria-current", "page")
    await page.goto(BASE + "#admin/nothing")
    await expect(page).to_have_url(BASE + "#admin/overview")


async def test_admin_non_admin_sends_no_requests(dashboard):  # noqa: F811
    page, data = dashboard
    data["/admin/overview"] = overview()
    requested = []
    page.on("request", lambda r: requested.append(r.url) if urlparse(r.url).path.startswith("/admin/") else None)
    await page.goto(BASE + "#admin/overview")
    await expect(page.locator("#view")).to_contain_text("관리자 권한이 필요합니다.")
    await expect(page.locator("#admin-menu")).to_be_hidden()
    await page.wait_for_load_state("networkidle")
    assert requested == []


async def test_overview_cards_chart_and_problems(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    await page.goto(BASE + "#admin/overview")
    view = page.locator("#view")
    kpis = view.locator(".admin-kpi")
    await expect(kpis).to_have_count(4)
    await expect(kpis.nth(0)).to_contain_text("활동 사용자")
    await expect(kpis.nth(0)).to_contain_text("48")
    await expect(kpis.nth(0)).to_contain_text("전체 132명")
    await expect(kpis.nth(1)).to_contain_text("재생한 강의")
    await expect(kpis.nth(1)).to_contain_text("316")
    await expect(kpis.nth(1)).to_contain_text("41시간")
    await expect(kpis.nth(2)).to_contain_text("다운로드 기록")
    await expect(kpis.nth(2)).to_contain_text("완료 보고 또는 링크 발급 횟수")
    await expect(kpis.nth(3)).to_contain_text("재생 실패")
    await expect(kpis.nth(3)).to_contain_text("실패율 3.7%")
    await expect(view.locator("nav.admin-tabs").get_by_role("link", name="문제 15건")).to_be_visible()

    chart = view.locator(".admin-chart")
    bars = chart.locator(".admin-bar")
    await expect(bars).to_have_count(7)
    await expect(chart.get_by_role("button", name="재생 완료")).to_have_attribute("aria-pressed", "true")
    today = date.today()
    assert await bars.last.get_attribute("title") == f"{today.month}/{today.day} 재생 완료 12건"
    await chart.get_by_role("button", name="로그인").click()
    await expect(chart.get_by_role("button", name="로그인")).to_have_attribute("aria-pressed", "true")
    await expect(chart.get_by_role("button", name="재생 완료")).to_have_attribute("aria-pressed", "false")
    assert (await bars.last.get_attribute("title")).endswith("로그인 7건")
    await chart.get_by_role("button", name="다운로드").click()
    assert (await bars.last.get_attribute("title")).endswith("다운로드 0건")
    await expect(chart).to_contain_text("로그인·다운로드 추이는 2026-11-01부터 집계")
    table = chart.locator("details table")
    await expect(table).to_be_hidden()
    await chart.get_by_text("표로 보기").click()
    await expect(table).to_be_visible()
    await expect(table.locator("tbody tr")).to_have_count(7)
    await expect(table.locator("thead")).to_contain_text("재생 완료")

    await expect(view.locator(".admin-feed")).to_contain_text("20231234")
    await expect(view.locator(".admin-feed")).to_contain_text("<img src=x onerror=alert(1)>")
    await expect(view.locator(".admin-feed")).to_contain_text("다운로드 누적 9회")  # 기간 합계가 아니라 누적
    problems = view.locator(".admin-problems")
    await expect(problems.locator("tbody tr")).to_have_count(3)
    await expect(problems).to_contain_text("플레이어 로딩 시간 초과")
    await expect(problems).to_contain_text("시각 기록 없음")
    await expect(problems).to_contain_text("미실행 3개")
    await expect(problems).to_contain_text("전체 재생 묶음")
    await expect(problems).not_to_contain_text("(all)")
    await expect(view.locator("img")).to_have_count(0)

    async with page.expect_request(lambda r: "/admin/overview?" in r.url and "days=30" in r.url):
        await view.get_by_label("기간").select_option("30")
    await problems.get_by_role("link", name="문제 15건 모두 보기").click()
    await expect(page).to_have_url(BASE + "#admin/problems")


async def test_overview_empty_shows_dash(dashboard):  # noqa: F811
    page, data = dashboard
    zero = [{"day": (date.today() - timedelta(days=6 - i)).isoformat(), "playback_done": 0, "logins": 0, "downloads": 0}
            for i in range(7)]
    as_admin(data, active_users=0, total_users=1, playback_done=0, playback_auto=0, playback_seconds=0,
             downloads=0, playback_failed=0, failure_rate=None, tracking_since=None, problem_count=0,
             series=zero, recent_activity=[], recent_problems=[])
    await page.goto(BASE + "#admin/overview")
    view = page.locator("#view")
    await expect(view.locator(".admin-kpi").nth(3)).to_contain_text("실패율 -")
    await expect(view.locator(".admin-bar")).to_have_count(7)
    await expect(view.locator(".admin-chart")).not_to_contain_text("부터 집계")
    await expect(view.locator(".admin-feed")).to_contain_text("활동이 없습니다")
    await expect(view.locator(".admin-problems")).to_contain_text("확인할 문제가 없습니다")


async def test_overview_error_and_retry(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    await page.route("**/admin/overview?*", lambda route: route.fulfill(status=503, json={"detail": "잠시 후 다시 시도하세요."}))
    await page.goto(BASE + "#admin/overview")
    view = page.locator("#view")
    await expect(view.get_by_role("alert")).to_contain_text("잠시 후 다시 시도하세요.")
    await page.unroute("**/admin/overview?*")
    await view.get_by_role("button", name="다시 시도").click()
    await expect(view.locator(".admin-kpi")).to_have_count(4)


async def test_stale_response_does_not_overwrite(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    requested, release = asyncio.Event(), asyncio.Event()

    async def delayed(route):
        requested.set()
        await release.wait()
        await route.fulfill(json=data["/admin/overview"])

    await page.route("**/admin/overview?*", delayed)
    await page.goto(BASE + "#admin/overview")
    await asyncio.wait_for(requested.wait(), timeout=5)
    await expect(page.locator("#view [aria-busy='true']")).to_have_count(1)
    tabs = page.locator("#view nav.admin-tabs")
    await tabs.get_by_role("link", name="활동").click()
    await expect(tabs.get_by_role("link", name="활동")).to_have_attribute("aria-current", "page")
    async with page.expect_event("requestfinished", lambda r: "/admin/overview?" in r.url):
        release.set()
    await page.evaluate("() => new Promise((r) => requestAnimationFrame(() => setTimeout(() => setTimeout(r))))")
    await expect(page).to_have_url(BASE + "#admin/activity")
    await expect(page.locator("#view")).to_contain_text("기간 안에 활동이 없습니다")
    await expect(page.locator("#view .admin-kpi")).to_have_count(0)
    # 늦은 응답이 살아 있는 탭 이름(문제 N건)을 바꾸지 않는다
    await expect(tabs.locator('[data-admin-tab="problems"]')).to_have_text("문제")


async def test_stale_period_response_does_not_overwrite_newer(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    week = overview(days=7, active_users=48, problem_count=15)
    month = overview(days=30, active_users=99, problem_count=21)
    held, release = asyncio.Event(), asyncio.Event()

    async def by_period(route):
        if "days=7" in route.request.url:
            held.set()
            await release.wait()
            await route.fulfill(json=week)
        else:
            await route.fulfill(json=month)

    await page.route("**/admin/overview?*", by_period)
    await page.goto(BASE + "#admin/overview")
    await asyncio.wait_for(held.wait(), timeout=5)
    view = page.locator("#view")
    async with page.expect_event("requestfinished", lambda r: "days=30" in r.url):
        await view.get_by_label("기간").select_option("30")
    kpis = view.locator(".admin-kpi")
    await expect(kpis.nth(0)).to_contain_text("99")
    await expect(view.locator(".admin-bar")).to_have_count(30)
    async with page.expect_event("requestfinished", lambda r: "days=7" in r.url):
        release.set()
    await page.evaluate("() => new Promise((r) => requestAnimationFrame(() => setTimeout(() => setTimeout(r))))")
    await expect(kpis.nth(0)).to_contain_text("99")
    await expect(kpis.nth(0)).not_to_contain_text("48")
    await expect(view.locator(".admin-bar")).to_have_count(30)
    await expect(view.locator("nav.admin-tabs [data-admin-tab='problems']")).to_have_text("문제 21건")
    await expect(view.get_by_label("기간")).to_have_value("30")


async def test_admin_shared_pieces(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    await page.goto(BASE + "#admin/activity")
    await expect(page.locator("#view")).to_contain_text("기간 안에 활동이 없습니다")
    result = await page.evaluate("""async () => {
        const { Table, Pager, ErrorBox, h, render } = await __ssu.loadAdmin();
        const host = document.createElement("div");
        document.querySelector("#view").append(host);
        render(h(Table, { columns: [{ label: "학번", render: (r) => r.id }], rows: [{ id: "<b>1</b>" }] }), host);
        const cell = host.querySelector("td");
        const pages = [];
        const pagerHost = document.createElement("div");
        host.append(pagerHost);
        render(h(Pager, { total: 45, offset: 20, limit: 20, onPage: (o) => pages.push(o) }), pagerHost);
        const pager = pagerHost.querySelector("nav");
        pager.querySelectorAll("button")[1].click();
        pager.querySelectorAll("button")[0].click();
        const state = document.createElement("div");
        let retried = 0;
        render(h(ErrorBox, { message: "<i>x</i>", retry: () => retried++ }), state);
        state.querySelector("button").click();
        return { label: cell.dataset.label, html: cell.firstElementChild.innerHTML, text: pager.textContent, pages, retried,
                 alert: state.querySelector("[role=alert]").textContent };
    }""")
    assert result["label"] == "학번"
    assert result["html"] == "&lt;b&gt;1&lt;/b&gt;"
    assert "21–40 / 45" in result["text"]
    assert result["pages"] == [40, 0]
    assert result["retried"] == 1
    assert "<i>x</i>" in result["alert"]


async def test_overview_no_sideways_scroll(dashboard, tmp_path):  # noqa: F811
    page, data = dashboard
    long = "아주 긴 강의 제목이 표를 밀어내지 않아야 합니다 " * 4
    as_admin(data, days=30)
    data["/admin/overview"]["recent_problems"][0]["title"] = long
    data["/admin/overview"]["recent_activity"][0]["title"] = long
    for width in (375, 768, 1024, 1440):
        await page.set_viewport_size({"width": width, "height": 900})
        await page.goto(BASE + "#admin/overview")
        await expect(page.locator("#view .admin-kpi")).to_have_count(4)
        await page.locator("#view .admin-chart summary").click()
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        await page.screenshot(path=tmp_path / f"overview-{width}.png", full_page=True)


async def test_video_urls_tab(dashboard, tmp_path):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    now = datetime.now(UTC).isoformat()
    url = "https://ssuin-object.commonscdn.com/" + "contents/very/long/path/" * 6 + "lecture.mp4"
    data["/admin/video-urls"] = {"total": 1, "items": [{
        "content_id": "abc123", "media_url": url, "lms_course_id": 1, "module_item_id": 10,
        "course_name": "자료구조", "title": "3강 <img src=x onerror=alert(1)>", "attempts": 3,
        "first_student_id": "20231234", "last_student_id": None, "first_seen_at": now, "last_seen_at": now}]}
    await page.goto(BASE + "#admin/overview")
    tabs = page.locator("#view nav.admin-tabs")
    await tabs.get_by_role("link", name="영상 주소").click()
    await expect(page).to_have_url(BASE + "#admin/videos")
    await expect(tabs.get_by_role("link", name="영상 주소")).to_have_attribute("aria-current", "page")
    view = page.locator("#view")
    await expect(view.locator(".admin-video-urls tbody tr")).to_have_count(1)
    await expect(view.locator(".admin-video-urls")).to_contain_text("3강 <img src=x onerror=alert(1)>")
    await expect(view.locator(".admin-video-urls")).to_contain_text(url)
    await expect(view.locator(".admin-video-urls")).to_contain_text("3회")
    await expect(view.locator(".admin-video-urls")).to_contain_text("마지막 삭제됨")
    await expect(view.locator(".admin-video-urls").get_by_role("link", name="20231234")).to_be_visible()
    await expect(view.locator("#admin-reload")).to_be_visible()  # 기간 조건은 없다
    await expect(view.locator("#admin-days")).to_have_count(0)

    async with page.expect_request(lambda r: "/admin/video-urls" in r.url and parse_qs(urlparse(r.url).query).get("q") == ["자료"]):
        await view.get_by_role("searchbox", name="제목·과목 검색").fill("자료")
    for width in (375, 768, 1024, 1440):
        await page.set_viewport_size({"width": width, "height": 900})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        await page.screenshot(path=tmp_path / f"video-urls-{width}.png", full_page=True)

    data["/admin/video-urls"] = {"total": 0, "items": []}
    await page.goto(BASE + "#admin/overview")
    await page.goto(BASE + "#admin/videos")
    await expect(view).to_contain_text("조건에 맞는 영상 주소가 없습니다")  # 검색어는 탭 조건으로 남는다


async def test_saved_videos_tab(dashboard, tmp_path):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    now = datetime.now(UTC).isoformat()
    url = "https://ssuin-object.commonscdn.com/" + "contents/very/long/path/" * 6 + "lecture.mp4"
    summary = {"items": 300, "videos": 298, "users": 8, "links": 1080}
    data["/admin/saved-videos/courses"] = {"summary": summary, "courses": [
        {"lms_course_id": 7, "course_name": "운영체제 <i>x</i>", "items": 40, "saved": 38, "pending": 0, "failed": 2,
         "users": 3, "last_at": now},
        {"lms_course_id": 1, "course_name": "자료구조", "items": 3, "saved": 1, "pending": 1, "failed": 1, "users": 8, "last_at": now}]}

    def item(n, week, students, **kw):
        return {"id": n, "students": students, "holders": len(students), "lms_course_id": 1, "module_item_id": n,
                "course_name": "자료구조", "module_title": f"{week}주차", "title": f"{n}강 <b>x</b>", "content_id": f"c{n}",
                "media_url": None, "file_name": "a.mp4", "duration": 1800, "size": 2_100_000_000, "resolve_error": None,
                "resolved_at": None, "resolve_failed_at": None, "created_at": now, **kw}
    data["/admin/saved-videos"] = {
        "total": 3, "counts": {"saved": 1, "pending": 1, "failed": 1}, "summary": {**summary, "items": 3, "videos": 3},
        "items": [item(1, 1, ["20231234", "20220456"], media_url=url, resolved_at=now, holders=12),
                  item(2, 1, ["20220456"], resolve_error="내려받을 수 없는 콘텐츠 형식입니다 (everlec)."),
                  item(3, 2, [])]}
    await page.goto(BASE + "#admin/overview")
    tabs = page.locator("#view nav.admin-tabs")
    await tabs.get_by_role("link", name="저장 영상").click()
    await expect(page).to_have_url(BASE + "#admin/saved")
    view = page.locator("#view")
    courses = view.locator(".admin-saved-courses")
    await expect(courses.locator("tbody tr")).to_have_count(2)
    await expect(view).to_contain_text("영상 300개 · 사용자 8명 · 사용자 연결 1,080개")
    await expect(courses).to_contain_text("운영체제 <i>x</i>")
    await expect(courses).to_contain_text("확보 실패 2")
    await expect(courses.locator("tbody tr").first).not_to_contain_text("확보 전")  # 0건 상태는 숨긴다
    for width in (375, 768, 1024, 1440):
        await page.set_viewport_size({"width": width, "height": 900})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("courses", width)
        await page.screenshot(path=tmp_path / f"saved-courses-{width}.png", full_page=True)
    await page.set_viewport_size({"width": 1280, "height": 900})

    # 줄 아무 곳이나 눌러도 그 과목으로 간다
    async with page.expect_request(lambda r: "/admin/saved-videos?" in r.url and parse_qs(urlparse(r.url).query).get("lms_course_id") == ["1"]):
        await courses.locator("tbody tr").nth(1).locator("td").nth(1).click()
    await expect(page).to_have_url(BASE + "#admin/saved/1")
    await expect(tabs.get_by_role("link", name="저장 영상")).to_have_attribute("aria-current", "page")
    await expect(view.get_by_role("heading", name=re.compile("^자료구조"))).to_be_visible()
    weeks = view.locator(".admin-saved-week")
    await expect(weeks).to_have_count(2)
    await expect(weeks.nth(0).get_by_role("heading")).to_have_text("1주차 2개")
    await expect(weeks.nth(1).get_by_role("heading")).to_have_text("2주차 1개")
    table = weeks.nth(0)
    await expect(table).to_contain_text("1강 <b>x</b>")
    await expect(table).to_contain_text("12명")
    await expect(table).to_contain_text("20231234, 20220456 외 10명")
    await expect(table).to_contain_text(url)
    await expect(table).to_contain_text("everlec")
    await expect(table).to_contain_text("2.1GB")
    await expect(weeks.nth(1)).to_contain_text("연결 없음")

    async with page.expect_request(lambda r: "/admin/saved-videos?" in r.url and parse_qs(urlparse(r.url).query).get("status") == ["failed"]):
        await view.locator("[data-kind='failed']").click()
    await expect(view.locator("[data-kind='failed']")).to_have_attribute("aria-pressed", "true")
    for width in (375, 768, 1024, 1440):
        await page.set_viewport_size({"width": width, "height": 900})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("course", width)
        await page.screenshot(path=tmp_path / f"saved-course-{width}.png", full_page=True)

    await view.get_by_role("link", name="과목 목록").click()
    await expect(page).to_have_url(BASE + "#admin/saved")
    async with page.expect_request(lambda r: "/admin/saved-videos/courses" in r.url and parse_qs(urlparse(r.url).query).get("student_id") == ["2023"]):
        await view.get_by_role("searchbox", name="학번").fill("2023")
    await page.goto(BASE + "#admin/saved/abc")
    await expect(view).to_contain_text("과목을 찾을 수 없습니다")


# ---------------- 사용자 목록과 상세 (Task 7) ----------------

SID = "20220456"
XSS = "<img src=x onerror=alert(1)>"


def admin_user(student_id, **over):
    now = datetime.now(UTC).isoformat()
    row = {"student_id": student_id, "created_at": "2026-03-04T01:00:00+00:00", "last_login_at": now, "login_count": 41,
           "lms_status": "ok", "course_count": 6, "playback_done": 58, "playback_failed": 3, "download_count": 21,
           "transcript_count": 4, "channels": ["discord"], "access_status": "approved", "is_admin": False}
    row.update(over)
    return row


def page_of(items, total=None):
    return {"total": len(items) if total is None else total, "items": items}


def seed_user(data, sid=SID, title=f"7강 스케줄링 {XSS}"):
    """한 사용자의 목록 줄과 상세 탭 응답을 모두 채운다. 경로는 픽스처처럼 쿼리 없이 키로 쓴다."""
    now = datetime.now(UTC)
    iso = now.isoformat()
    base = "/admin/users/" + quote(sid, safe="")
    data["/admin/users"] = {"total": 3, "users": [
        admin_user(sid),
        admin_user("20231877", lms_status="invalid", playback_failed=0, channels=[], last_login_at=None),
        admin_user("20231234", lms_status=None, channels=["telegram", "discord"]),
    ]}
    data[base + "/summary"] = {
        "student_id": sid, "is_admin": False, "consent_at": "2026-03-04T01:00:00+00:00",
        "created_at": "2026-03-04T01:00:00+00:00", "last_login_at": iso, "login_count": 41,
        "lms": {"status": "invalid", "last_error": f"학교 비밀번호가 바뀌었습니다 {XSS}", "last_error_at": iso,
                "last_login_at": iso, "last_synced_at": iso, "next_sync_at": None},
        "counts": {"courses": 6, "todos": 9, "playback_done": 58, "playback_failed": 3, "downloads": 21,
                   "saved_videos": 40, "transcripts": 4, "channels": 1},
    }
    data[base + "/activities"] = {"total": 45, "counts": {"playback": 30, "download": 10, "transcript": 5}, "activities": [
        {"id": 1, "kind": "playback", "title": title, "course_name": "운영체제", "status": "failed",
         "recorded_at": iso, "started_at": iso, "finished_at": iso, "count": 1},
        {"id": 2, "kind": "download", "title": "6강 프로세스", "course_name": None, "status": "done",
         "recorded_at": iso, "started_at": None, "finished_at": None, "count": 2},
    ]}
    data[base + "/courses"] = page_of([
        {"lms_course_id": 11, "name": f"운영체제 {XSS}", "professors": "김교수", "active": True, "auto_play": True,
         "attendance": [{"week": 1, "statuses": ["attendance", "absent"]}, {"week": 2, "statuses": ["late"]}]},
        {"lms_course_id": 12, "name": "자료구조", "professors": None, "active": False, "auto_play": False, "attendance": None},
    ], total=25)
    data[base + "/todos"] = page_of([
        {"kind": "video", "title": f"8강 가상 메모리 {XSS}", "course_name": "운영체제", "deadline": None, "due_at": None, "active": True},
        {"kind": "assignment", "title": "과제 2", "course_name": "자료구조", "deadline": iso, "due_at": iso, "active": False},
    ], total=30)
    data[base + "/assignments"] = page_of([
        {"kind": "assignment", "title": "과제 1", "course_name": "자료구조", "submission_state": "submitted",
         "submitted_at": iso, "missing": False, "due_at": iso, "deadline": iso},
        {"kind": "quiz", "title": "퀴즈 1", "course_name": "운영체제", "submission_state": "unsubmitted",
         "submitted_at": None, "missing": True, "due_at": None, "deadline": None},
    ])
    data[base + "/playback"] = page_of([{
        "id": 7, "scope": "module", "status": "partial", "lms_course_id": 11, "total": 3, "done_count": 1,
        "failed_count": 2, "skipped_count": 0, "created_at": iso, "started_at": iso, "finished_at": iso,
        "error": f"중단됨 {XSS}", "jobs": [
            {"id": 71, "title": title, "duration": 2880, "status": "failed", "attempts": 3, "progress_seconds": 600,
             "deadline": None, "started_at": iso, "finished_at": iso, "error": f"플레이어 로딩 시간 초과 {XSS}"},
            {"id": 72, "title": "8강 가상 메모리", "duration": 2700, "status": "failed", "attempts": 0,
             "progress_seconds": 0, "deadline": None, "started_at": None, "finished_at": None, "error": "앞 영상 실패로 중단"},
            {"id": 73, "title": "6강 프로세스", "duration": 2700, "status": "done", "attempts": 1,
             "progress_seconds": 2700, "deadline": iso, "started_at": iso, "finished_at": iso, "error": None},
        ]}, {
        "id": 6, "scope": "week", "status": "done", "lms_course_id": None, "total": 1, "done_count": 1,
        "failed_count": 0, "skipped_count": 0, "created_at": iso, "started_at": None, "finished_at": None,
        "error": None, "jobs": []}], total=12)
    data[base + "/downloads"] = page_of([
        {"title": f"6강 프로세스 {XSS}", "kind": "video", "count": 3, "first_at": iso, "last_at": iso}], total=21)
    data[base + "/saved-videos"] = page_of([
        {"title": "6강 프로세스", "course_name": "운영체제", "module_title": "6주차", "file_name": "os-06.mp4",
         "duration": 2700, "size": 350_000_000, "has_media_url": True, "resolve_error": None,
         "resolved_at": iso, "resolve_failed_at": None},
        {"title": "7강 스케줄링", "course_name": "운영체제", "module_title": "7주차", "file_name": "os-07.mp4",
         "duration": None, "size": None, "has_media_url": False, "resolve_error": f"권한 없음 {XSS}",
         "resolved_at": None, "resolve_failed_at": iso},
    ], total=40)
    data[base + "/transcripts"] = page_of([
        {"title": "6강 프로세스", "course_name": "운영체제", "model": "whisper-small", "duration": 2700,
         "created_at": iso, "updated_at": iso}], total=4)
    data[base + "/notifications"] = {**page_of([{"key": "deadline:42:24h", "created_at": iso}], total=22),
                                     "channels": [{"kind": "discord", "enabled": True, "created_at": iso}]}
    return base


def as_users_admin(data, sid=SID, **kw):
    as_admin(data)
    return seed_user(data, sid, **kw)


async def test_users_table_search_filters_sort(dashboard):  # noqa: F811
    page, data = dashboard
    as_users_admin(data)
    data["/admin/users"]["total"] = 60
    await page.goto(BASE + "#admin/users")
    view = page.locator("#view")
    rows = view.locator(".admin-users tbody tr")
    await expect(rows).to_have_count(3)
    await expect(view.locator("#admin-user-count")).to_have_text("60명")
    await expect(rows.nth(0)).to_contain_text("58 / 3")
    await expect(rows.nth(0)).to_contain_text("Discord")
    await expect(rows.nth(0)).to_contain_text("정상")
    await expect(rows.nth(1)).to_contain_text("다시 로그인 필요")
    await expect(rows.nth(1)).to_contain_text("없음")
    await expect(rows.nth(2)).to_contain_text("연결 안 함")
    await expect(rows.nth(2)).to_contain_text("Telegram, Discord")
    await expect(view.locator(".admin-users thead")).to_contain_text("재생 (완료/실패)")

    def query(r):
        return parse_qs(urlparse(r.url).query) if urlparse(r.url).path == "/admin/users" else None

    async with page.expect_request(lambda r: (query(r) or {}).get("offset") == ["25"]) as req:
        await view.get_by_role("button", name="다음").click()
    assert query(await req.value)["limit"] == ["25"]
    async with page.expect_request(lambda r: (query(r) or {}).get("q") == ["2022"]) as req:
        await view.get_by_label("학번 검색").fill("2022")
    assert query(await req.value)["offset"] == ["0"]  # 조건이 바뀌면 첫 페이지로
    async with page.expect_request(lambda r: (query(r) or {}).get("lms_status") == ["invalid"]):
        await view.get_by_label("LMS 상태").select_option("invalid")
    async with page.expect_request(lambda r: (query(r) or {}).get("has_problem") == ["true"]):
        await view.get_by_label("문제 있음만").check()
    # activity 값은 마지막 로그인 순이다. 이름이 실제 기준을 말한다.
    await expect(view.get_by_label("정렬").locator('option[value="activity"]')).to_have_text("최근 로그인순")
    async with page.expect_request(lambda r: (query(r) or {}).get("sort") == ["student_id"]) as req:
        await view.get_by_label("정렬").select_option("student_id")
    last = query(await req.value)
    assert last["q"] == ["2022"] and last["lms_status"] == ["invalid"] and last["has_problem"] == ["true"]
    async with page.expect_request(lambda r: query(r) is not None and "has_problem" not in query(r)):
        await view.get_by_label("문제 있음만").uncheck()
    # 상세에 다녀와도 조건이 남는다
    await rows.nth(0).get_by_role("link", name=SID).click()
    await view.get_by_role("link", name="사용자 목록").click()
    await expect(view.get_by_label("학번 검색")).to_have_value("2022")
    await expect(view.get_by_label("정렬")).to_have_value("student_id")


async def test_row_opens_detail_by_click_and_keyboard(dashboard):  # noqa: F811
    page, data = dashboard
    as_users_admin(data)
    await page.goto(BASE + "#admin/users")
    view = page.locator("#view")
    await view.locator(".admin-users").get_by_role("link", name=SID).click()
    await expect(page).to_have_url(BASE + f"#admin/users/{SID}")
    await expect(view.get_by_role("heading", name=SID)).to_be_visible()
    tabs = view.locator("nav.admin-tabs")
    await expect(tabs.get_by_role("link", name="사용자")).to_have_attribute("aria-current", "page")
    await view.get_by_role("link", name="사용자 목록").click()
    await expect(page).to_have_url(BASE + "#admin/users")
    link = view.locator(".admin-users").get_by_role("link", name="20231877")
    await link.focus()
    await page.keyboard.press("Enter")
    await expect(page).to_have_url(BASE + "#admin/users/20231877")
    await page.go_back()
    await expect(page).to_have_url(BASE + "#admin/users")
    # 줄의 다른 칸을 눌러도 학번 링크로 간다
    await view.locator(".admin-users tbody tr").nth(0).locator("td").nth(3).click()
    await expect(page).to_have_url(BASE + f"#admin/users/{SID}")


async def test_detail_header_and_counts(dashboard):  # noqa: F811
    page, data = dashboard
    as_users_admin(data)
    await page.goto(BASE + f"#admin/users/{SID}")
    head = page.locator("#view .admin-user-head")
    await expect(head.get_by_role("heading", name=SID)).to_be_visible()
    await expect(head).to_contain_text("가입 2026/03/04")
    await expect(head).to_contain_text("로그인 41회")
    await expect(head).to_contain_text("이용 동의 2026/03/04")
    await expect(head).to_contain_text("일반 사용자")
    await expect(head).to_contain_text("LMS 다시 로그인 필요")
    await expect(head).to_contain_text("학교 비밀번호가 바뀌었습니다")
    sums = head.locator(".admin-kpi")
    await expect(sums).to_have_count(5)
    for i, (label, value) in enumerate([("수강 과목", "6"), ("남은 할 일", "9"), ("재생 완료", "58"),
                                        ("재생 실패", "3"), ("다운로드", "21")]):
        await expect(sums.nth(i)).to_contain_text(label)
        await expect(sums.nth(i).locator("strong")).to_have_text(value)
    await expect(sums.nth(3)).to_have_class(re.compile(r"\bbad\b"))
    data["/admin/users/20231877/summary"] = {**data[f"/admin/users/{SID}/summary"], "student_id": "20231877",
                                             "is_admin": True, "lms": None}
    await page.goto(BASE + "#admin/users/20231877/lms")
    await expect(head).to_contain_text("관리자")
    await expect(head).to_contain_text("LMS 연결 안 함")
    await expect(page.locator("#view .admin-user-tab")).to_contain_text("LMS 계정을 연결하지 않았습니다")


TABS = [
    # (탭 이름, 주소 조각, API 꼬리, 보여야 할 글, 다음 페이지가 있는지)
    ("전체 타임라인", "timeline", "activities", ["다운로드 누적 2회", "운영체제 · 7강 스케줄링", "재생 실패"], True),
    ("과목·출석", "courses", "courses", ["김교수", "현재 학기", "지난 학기", "켜짐", "출석 기록 없음"], True),
    ("할 일·과제", "todos", "todos", ["마감 없음", "목록에서 사라짐(완료 또는 마감)", "제출함", "마감 지나 미제출"], True),
    ("재생", "playback", "playback", ["주차", "이번 주", "일부 실패", "미실행", "앞 영상 실패로 중단"], False),
    ("다운로드", "downloads", "downloads", ["6강 프로세스", "영상", "3회"], True),
    ("저장 영상", "saved", "saved-videos", ["os-06.mp4", "주소 있음", "주소 없음", "권한 없음", "350MB"], True),
    ("전사", "transcripts", "transcripts", ["whisper-small", "45분"], False),
    ("알림", "notifications", "notifications", ["Discord", "사용 중", "deadline:42:24h"], True),
    ("LMS", "lms", None, ["다시 로그인 필요", "학교 비밀번호가 바뀌었습니다", "예정 없음"], False),
]


async def test_every_detail_tab_loads_and_paginates(dashboard):  # noqa: F811
    page, data = dashboard
    base = as_users_admin(data)
    await page.goto(BASE + f"#admin/users/{SID}")
    view = page.locator("#view")
    sub = view.locator("nav.admin-subtabs")
    box = view.locator(".admin-user-tab")
    await expect(sub.get_by_role("link", name="전체 타임라인")).to_have_attribute("aria-current", "page")
    for label, key, tail, texts, pages in TABS:
        await sub.get_by_role("link", name=label, exact=True).click()
        await expect(page).to_have_url(BASE + f"#admin/users/{SID}/{key}")
        await expect(sub.get_by_role("link", name=label, exact=True)).to_have_attribute("aria-current", "page")
        await expect(sub.get_by_role("link", name=label, exact=True)).to_be_focused()
        for text in texts:
            await expect(box).to_contain_text(text)
        if pages:
            path = f"{base}/{tail}"
            async with page.expect_request(lambda r: urlparse(r.url).path == path
                                           and parse_qs(urlparse(r.url).query).get("offset") == ["20"]):
                await box.get_by_role("button", name="다음").first.click()
            await expect(box.get_by_role("button", name="이전").first).to_be_enabled()
    # 할 일과 과제는 페이지를 따로 넘긴다
    await sub.get_by_role("link", name="할 일·과제").click()
    await expect(box.locator(".admin-pager")).to_have_count(1)
    await expect(box.locator("h3")).to_have_text(["할 일", "과제"])
    # 새로 고쳐도 같은 탭
    await page.reload()
    await expect(sub.get_by_role("link", name="할 일·과제")).to_have_attribute("aria-current", "page")
    await page.goto(BASE + f"#admin/users/{SID}/nothing")
    await expect(page).to_have_url(BASE + f"#admin/users/{SID}")
    await expect(sub.get_by_role("link", name="전체 타임라인")).to_have_attribute("aria-current", "page")


async def test_playback_tab_filters_and_not_run(dashboard):  # noqa: F811
    page, data = dashboard
    base = as_users_admin(data)
    await page.goto(BASE + f"#admin/users/{SID}/playback")
    box = page.locator("#view .admin-user-tab")
    batch = box.locator(".admin-batch").first
    await expect(batch).to_contain_text("주차 재생")
    await expect(batch).to_contain_text("운영체제")  # 묶음의 과목 이름(과목 목록에서)
    await expect(batch).to_contain_text("완료 1 · 실패 2 · 건너뜀 0 / 전체 3")
    jobs = batch.locator("tbody tr")
    await expect(jobs).to_have_count(3)
    await expect(jobs.nth(0).locator(".admin-tag")).to_have_text("재생 실패")
    await expect(jobs.nth(1).locator(".admin-tag")).to_have_text("미실행")
    await expect(jobs.nth(2).locator(".admin-tag")).to_have_text("재생 완료")
    await expect(jobs.nth(0)).to_contain_text("3회")
    await expect(jobs.nth(0)).to_contain_text("10분 / 48분")
    await expect(box.locator(".admin-batch").nth(1)).to_contain_text("이번 주 재생")
    status = box.get_by_label("상태")
    options = await status.locator("option").all_text_contents()
    assert options == ["전체", "재생 실패", "재생 잠김", "재생 완료", "재생 대기", "재생 중", "이미 완료", "재생 취소"]
    assert await status.locator("option").evaluate_all("o => o.map((x) => x.value)") == [
        "", "failed", "locked", "done", "queued", "playing", "skipped", "cancelled"]

    def query(r):
        return parse_qs(urlparse(r.url).query) if urlparse(r.url).path == f"{base}/playback" else None

    async with page.expect_request(lambda r: (query(r) or {}).get("status") == ["failed"]):
        await status.select_option("failed")
    async with page.expect_request(lambda r: (query(r) or {}).get("lms_course_id") == ["11"]):
        await box.get_by_label("과목").select_option("11")
    async with page.expect_request(lambda r: (query(r) or {}).get("days") == ["30"]) as req:
        await box.get_by_label("기간").select_option("30")
    q = query(await req.value)
    assert q["status"] == ["failed"] and q["lms_course_id"] == ["11"] and q["offset"] == ["0"]
    async with page.expect_request(lambda r: query(r) is not None and "status" not in query(r)):
        await status.select_option("")
    await expect(box.get_by_label("상태")).to_have_value("")


async def test_detail_escapes_server_strings(dashboard):  # noqa: F811
    page, data = dashboard
    as_users_admin(data)
    await page.goto(BASE + f"#admin/users/{SID}")
    view = page.locator("#view")
    sub = view.locator("nav.admin-subtabs")
    await expect(view.locator(".admin-user-head")).to_contain_text(XSS)
    for label, key, _tail, texts, _pages in TABS:
        await sub.get_by_role("link", name=label, exact=True).click()
        await expect(view.locator(".admin-user-tab")).to_contain_text(texts[0])
        await expect(view.locator("img")).to_have_count(0)
    await sub.get_by_role("link", name="재생", exact=True).click()
    await expect(view.locator(".admin-user-tab")).to_contain_text(f"플레이어 로딩 시간 초과 {XSS}")
    await expect(view.locator(".admin-user-tab")).to_contain_text(f"중단됨 {XSS}")
    await expect(view.locator("img")).to_have_count(0)


async def test_unknown_student_shows_notice(dashboard):  # noqa: F811
    page, data = dashboard
    as_users_admin(data)
    await page.goto(BASE + "#admin/users/99999999")
    view = page.locator("#view")
    await expect(view.get_by_role("alert")).to_contain_text("사용자를 찾을 수 없습니다")
    await expect(view.locator("nav.admin-subtabs")).to_have_count(0)
    await view.get_by_role("link", name="사용자 목록으로").click()
    await expect(page).to_have_url(BASE + "#admin/users")
    await expect(view.locator(".admin-users tbody tr")).to_have_count(3)


async def test_student_id_with_special_chars_routes(dashboard):  # noqa: F811
    page, data = dashboard
    odd = "a/b#1"
    as_users_admin(data, sid=odd)
    requested = []
    page.on("request", lambda r: requested.append(urlparse(r.url).path) if urlparse(r.url).path.startswith("/admin/users/") else None)
    await page.goto(BASE + "#admin/users")
    view = page.locator("#view")
    await view.locator(".admin-users").get_by_role("link", name=odd).click()
    await expect(page).to_have_url(BASE + "#admin/users/a%2Fb%231")
    await expect(view.get_by_role("heading", name=odd)).to_be_visible()
    await expect(view.locator(".admin-user-tab")).to_contain_text("운영체제 · 7강 스케줄링")
    assert "/admin/users/a%2Fb%231/summary" in requested
    assert "/admin/users/a%2Fb%231/activities" in requested
    await view.locator("nav.admin-subtabs").get_by_role("link", name="저장 영상").click()
    await expect(page).to_have_url(BASE + "#admin/users/a%2Fb%231/saved")
    await expect(view.locator(".admin-user-tab")).to_contain_text("os-06.mp4")
    await page.reload()
    await expect(view.get_by_role("heading", name=odd)).to_be_visible()


async def test_detail_tables_fit_mobile(dashboard, tmp_path):  # noqa: F811
    page, data = dashboard
    long = "아주 긴 강의 제목이 표를 밀어내지 않아야 합니다 " * 4
    as_users_admin(data, title=long)
    for width in (375, 768, 1024, 1440):
        await page.set_viewport_size({"width": width, "height": 900})
        await page.goto(BASE + "#admin/users")
        await expect(page.locator("#view .admin-users tbody tr")).to_have_count(3)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("list", width)
    await page.set_viewport_size({"width": 375, "height": 900})
    cell = page.locator("#view .admin-users tbody tr").first.locator("td").nth(2)
    assert await cell.get_attribute("data-label") == "최근 로그인"
    assert await page.locator("#view .admin-users thead").is_hidden()
    for _label, key, _tail, texts, _pages in TABS:
        await page.goto(BASE + f"#admin/users/{SID}/{key}")
        await expect(page.locator("#view .admin-user-tab")).to_contain_text(texts[0])
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), key
        links = await page.locator("#view nav.admin-subtabs a").evaluate_all(
            "els => els.map((e) => e.getBoundingClientRect().height)")
        assert min(links) >= 44
    await page.screenshot(path=tmp_path / "users-detail-375.png", full_page=True)


# ---------------- 활동과 문제 (Task 8) ----------------

def seed_feeds(data):
    """전체 활동과 문제 응답. 경로는 픽스처처럼 쿼리 없이 키로 쓴다."""
    iso = datetime.now(UTC).isoformat()
    data["/admin/activities"] = {"total": 45, "counts": {"playback": 30, "download": 10, "transcript": 5}, "activities": [
        {"id": 1, "kind": "playback", "title": f"7강 스케줄링 {XSS}", "course_name": "운영체제", "status": "failed",
         "recorded_at": iso, "started_at": iso, "finished_at": iso, "count": 1, "student_id": SID},
        {"id": 2, "kind": "download", "title": "6강 프로세스", "course_name": None, "status": "done",
         "recorded_at": iso, "started_at": None, "finished_at": None, "count": 2, "student_id": "20231234"},
        {"id": 3, "kind": "transcript", "title": "5강 스레드", "course_name": "운영체제", "status": "done",
         "recorded_at": iso, "started_at": None, "finished_at": None, "count": 1, "student_id": "20231877"},
    ]}
    data["/admin/problems"] = {"total": 45, "counts": {"playback": 30, "batch": 5, "lms": 4, "resolve": 6}, "problems": [
        {"kind": "playback", "occurred_at": iso, "student_id": SID, "course_name": "운영체제",
         "title": "7강 스케줄링", "error": f"플레이어 로딩 시간 초과 {XSS}", "not_run": 0},
        {"kind": "batch", "occurred_at": iso, "student_id": "20210092", "course_name": None,
         "title": "all", "error": "중단됨", "not_run": 3},
        {"kind": "lms", "occurred_at": None, "student_id": "20231877", "course_name": None,
         "title": "LMS 연결", "error": "비밀번호 변경 추정", "not_run": 0},
        {"kind": "resolve", "occurred_at": iso, "student_id": SID, "course_name": "자료구조",
         "title": "3강 스택", "error": None, "not_run": 0},
    ]}
    data[f"/admin/users/{SID}/courses"] = page_of([
        {"lms_course_id": 11, "name": "운영체제", "professors": None, "active": True, "auto_play": False, "attendance": None},
        {"lms_course_id": 12, "name": "자료구조", "professors": None, "active": True, "auto_play": False, "attendance": None},
    ])


def feed_query(path):
    def query(r):
        return parse_qs(urlparse(r.url).query) if urlparse(r.url).path == path else None
    return query


async def test_activity_tab_filters_and_links_student(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    seed_feeds(data)
    query = feed_query("/admin/activities")
    async with page.expect_request(lambda r: query(r) is not None) as req:
        await page.goto(BASE + "#admin/activity")
    first = query(await req.value)
    assert first["days"] == ["7"] and first["offset"] == ["0"] and first["limit"] == ["20"] and "kind" not in first
    view = page.locator("#view")
    rows = view.locator(".admin-activities tbody tr")
    await expect(rows).to_have_count(3)
    await expect(rows.nth(0)).to_contain_text("재생 실패")
    await expect(rows.nth(0)).to_contain_text(f"운영체제 · 7강 스케줄링 {XSS}")
    await expect(rows.nth(1)).to_contain_text("다운로드 누적 2회")
    await expect(rows.nth(2)).to_contain_text("전사 저장")
    await expect(view.locator("img")).to_have_count(0)
    await expect(view.get_by_role("button", name="전체 45")).to_have_attribute("aria-pressed", "true")

    async with page.expect_request(lambda r: (query(r) or {}).get("kind") == ["playback"]):
        await view.get_by_role("button", name="재생 30").click()
    await expect(view.get_by_role("button", name="재생 30")).to_have_attribute("aria-pressed", "true")
    async with page.expect_request(lambda r: (query(r) or {}).get("student_id") == [SID]) as req:
        await view.get_by_label("학번").fill(SID)
    assert query(await req.value)["kind"] == ["playback"]
    async with page.expect_request(lambda r: (query(r) or {}).get("days") == ["30"]) as req:
        await view.get_by_label("기간").select_option("30")
    assert query(await req.value)["student_id"] == [SID]
    async with page.expect_request(lambda r: (query(r) or {}).get("offset") == ["20"]):
        await view.get_by_role("button", name="다음").click()
    # 학번을 고치자마자 다른 조건을 눌러도 고친 학번으로 요청한다
    await view.get_by_label("학번").fill("2023")
    async with page.expect_request(lambda r: query(r) is not None and "kind" not in query(r)) as req:
        await view.get_by_role("button", name="전체 45").click()
    assert query(await req.value)["student_id"] == ["2023"]

    await rows.nth(0).get_by_role("link", name=SID).click()
    await expect(page).to_have_url(BASE + f"#admin/users/{SID}")
    await page.go_back()
    await expect(page).to_have_url(BASE + "#admin/activity")
    await expect(view.get_by_label("학번")).to_have_value("2023")  # 조건이 남는다


async def test_problems_tab_filters_and_shows_error(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    seed_feeds(data)
    query = feed_query("/admin/problems")
    async with page.expect_request(lambda r: query(r) is not None) as req:
        await page.goto(BASE + "#admin/problems")
    first = query(await req.value)
    assert first["days"] == ["7"] and first["offset"] == ["0"] and "kind" not in first and "lms_course_id" not in first
    view = page.locator("#view")
    table = view.locator(".admin-problem-list")
    rows = table.locator("tbody tr")
    await expect(rows).to_have_count(4)
    assert await table.locator("thead th").all_text_contents() == ["시각", "사용자", "과목 · 영상", "종류", "오류 내용"]
    await expect(rows.nth(0)).to_contain_text("운영체제 · 7강 스케줄링")
    await expect(rows.nth(0)).to_contain_text("재생 실패")
    await expect(rows.nth(0)).to_contain_text(f"플레이어 로딩 시간 초과 {XSS}")
    await expect(rows.nth(1)).to_contain_text("전체 재생 묶음")
    await expect(rows.nth(1)).to_contain_text("재생 묶음 오류")
    await expect(rows.nth(1)).to_contain_text("미실행 3개")
    await expect(rows.nth(2)).to_contain_text("LMS 문제")
    await expect(rows.nth(3)).to_contain_text("영상 주소 확보 실패")
    await expect(rows.nth(3)).to_contain_text("오류 내용 없음")
    await expect(rows.nth(0)).not_to_contain_text("미실행")
    await expect(view.locator("img")).to_have_count(0)
    await expect(view.get_by_role("button", name="LMS 문제 4")).to_be_visible()

    async with page.expect_request(lambda r: (query(r) or {}).get("kind") == ["lms"]):
        await view.get_by_role("button", name="LMS 문제 4").click()
    course = view.get_by_label("과목")
    await expect(course).to_be_disabled()  # 과목은 학번을 고른 뒤에 고른다
    async with page.expect_request(lambda r: (query(r) or {}).get("student_id") == [SID]):
        await view.get_by_label("학번").fill(SID)
    await expect(course).to_be_enabled()
    assert await course.locator("option").all_text_contents() == ["전체", "운영체제", "자료구조"]
    async with page.expect_request(lambda r: (query(r) or {}).get("lms_course_id") == ["11"]) as req:
        await course.select_option("11")
    q = query(await req.value)
    assert q["student_id"] == [SID] and q["kind"] == ["lms"] and q["offset"] == ["0"]
    async with page.expect_request(lambda r: (query(r) or {}).get("days") == ["90"]) as req:
        await view.get_by_label("기간").select_option("90")
    assert query(await req.value)["lms_course_id"] == ["11"]
    # 학번을 바꾸면 그 사람의 과목이 아니므로 과목 조건을 지운다
    async with page.expect_request(lambda r: query(r) is not None and query(r).get("student_id") == ["2023"]) as req:
        await view.get_by_label("학번").fill("2023")
    assert "lms_course_id" not in query(await req.value)
    await expect(course).to_have_value("")
    await rows.nth(2).get_by_role("link", name="20231877").click()
    await expect(page).to_have_url(BASE + "#admin/users/20231877")


async def test_problem_without_time_says_no_record(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    seed_feeds(data)
    await page.goto(BASE + "#admin/problems")
    rows = page.locator("#view .admin-problem-list tbody tr")
    await expect(rows).to_have_count(4)
    await expect(rows.nth(2).locator("td").first).to_have_text("시각 기록 없음")
    await expect(rows.nth(0).locator("td").first.locator("time")).to_have_count(1)


async def test_problem_tab_count_label(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data, problem_count=15, playback_failed=12)
    seed_feeds(data)
    filtered = {**data["/admin/problems"], "total": 2, "counts": {"playback": 2, "batch": 0, "lms": 0, "resolve": 0}}

    async def by_filter(route):
        q = parse_qs(urlparse(route.request.url).query)
        await route.fulfill(json=filtered if "student_id" in q else data["/admin/problems"])

    await page.route("**/admin/problems?*", by_filter)
    await page.goto(BASE + "#admin/problems")
    tab = page.locator("#view nav.admin-tabs [data-admin-tab='problems']")
    await expect(tab).to_have_text("문제 45건")  # 네 종류의 합
    await expect(page.locator("#view .admin-problem-list tbody tr")).to_have_count(4)
    # 조건을 건 결과는 탭 숫자를 바꾸지 않는다
    async with page.expect_event("requestfinished", lambda r: "student_id=" in r.url):
        await page.locator("#view").get_by_label("학번").fill(SID)
    await expect(page.locator("#admin-problems-title .admin-count")).to_have_text("2건")
    await expect(tab).to_have_text("문제 45건")
    await page.locator("#view").get_by_label("학번").fill("")
    await expect(page.locator("#admin-problems-title .admin-count")).to_have_text("45건")
    # 종류 버튼은 서버가 종류별 건수를 그대로 주므로 탭 숫자가 그대로다
    async with page.expect_event("requestfinished", lambda r: "kind=playback" in r.url):
        await page.locator("#view").get_by_role("button", name="재생 실패 30").click()
    await expect(tab).to_have_text("문제 45건")
    # 현황의 재생 실패 카드는 문제 건수와 따로 센다
    await page.locator("#view nav.admin-tabs").get_by_role("link", name="현황").click()
    await expect(page.locator("#view .admin-kpi").nth(3)).to_contain_text("12")
    await expect(tab).to_have_text("문제 15건")


async def test_feed_tabs_fit_every_width(dashboard):  # noqa: F811
    page, data = dashboard
    long = "아주 긴 강의 제목이 표를 밀어내지 않아야 합니다 " * 4
    as_admin(data)
    seed_feeds(data)
    data["/admin/activities"]["activities"][0]["title"] = long
    data["/admin/problems"]["problems"][0]["error"] = long
    for width in (375, 768, 1024, 1440):
        await page.set_viewport_size({"width": width, "height": 900})
        for tab, table in (("activity", ".admin-activities"), ("problems", ".admin-problem-list")):
            await page.goto(BASE + f"#admin/{tab}")
            await expect(page.locator(f"#view {table} tbody tr").first).to_be_visible()
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (tab, width)
            heights = await page.locator("#view .admin-seg button, #view .admin-filters input, #view .admin-filters select").evaluate_all(
                "els => els.map((e) => e.getBoundingClientRect().height)")
            assert min(heights) >= 44, (tab, width)


# ---------- 권한 관리 ----------

def request_row(sid, previous="none", rejects=0, at="2026-11-06T06:20:00+00:00"):
    return {"student_id": sid, "requested_at": at, "previous_status": previous, "reject_count": rejects}


def with_requests(data, *rows):
    data["/admin/access-requests"] = {"total": len(rows), "items": list(rows)}
    data["/admin/overview"]["pending_count"] = len(rows)


async def decisions(page, data, *, status=200, gate=None, sync_queued=True):
    """POST /admin/users/{id}/access를 가로채 기록한다. 성공하면 대기 목록에서 그 줄을 뺀다."""
    calls = []

    async def handle(route):
        req = route.request
        sid = urlparse(req.url).path.split("/")[3]
        action = req.post_data_json["action"]
        calls.append((sid, action))
        if gate:
            await gate.wait()
        if status != 200:
            await route.fulfill(status=status, json={"detail": "이미 처리된 요청입니다."})
            return
        pending = data.get("/admin/access-requests")
        if pending:
            pending["items"] = [r for r in pending["items"] if r["student_id"] != sid]
            pending["total"] = len(pending["items"])
        await route.fulfill(json={"student_id": sid, "access_status": {"approve": "approved", "reject": "rejected", "revoke": "revoked"}[action],
                                  "access_decided_at": "2026-11-06T07:00:00+00:00", "access_decided_by": "20200001", "sync_queued": sync_queued})
    await page.route("**/admin/users/*/access", handle)
    return calls


async def test_approval_without_queued_sync_tells_admin_neutrally(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    with_requests(data, request_row("20231877"))
    await decisions(page, data, sync_queued=False)
    await page.goto(BASE + "#admin/overview")
    box = page.locator("#admin-body .admin-access")
    await box.get_by_role("button", name="승인").click()
    await expect(page.locator("#toast")).to_contain_text("20231877을 승인했습니다")
    await expect(page.locator("#toast")).to_contain_text("동기화는 대기열에 넣지 못했어요")
    await expect(page.locator("#toast .bad")).to_have_count(0)


async def test_overview_shows_access_requests(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    with_requests(data, request_row("20231877"), request_row("20220456", "revoked"), request_row("20241001", "rejected", 1))
    await page.goto(BASE + "#admin/overview")
    body = page.locator("#admin-body")
    box = body.locator(".admin-access")
    await expect(box.get_by_role("heading", name="권한 요청 3건")).to_be_visible()
    rows = box.locator("tbody tr")
    await expect(rows).to_have_count(3)
    await expect(rows.nth(0)).to_contain_text("20231877")
    await expect(rows.nth(0)).to_contain_text("처음 요청")
    await expect(rows.nth(1)).to_contain_text("회수됨")
    await expect(rows.nth(2)).to_contain_text("거절됨 1회")
    await expect(rows.nth(0).get_by_role("button", name="승인")).to_be_visible()
    await expect(rows.nth(0).get_by_role("button", name="거절")).to_be_visible()
    # 지표 위에 있다
    assert await box.evaluate("""(el) => el.parentElement === el.closest('#admin-body').firstElementChild
        && el.parentElement.nextElementSibling.classList.contains('admin-kpis')""")
    # 요청이 없으면 표도 요청도 없다
    requested = []
    page.on("request", lambda r: requested.append(r.url) if urlparse(r.url).path == "/admin/access-requests" else None)
    with_requests(data)
    await page.get_by_role("button", name="다시 불러오기").click()
    await expect(body.locator(".admin-kpi")).to_have_count(4)
    await expect(body.locator(".admin-access")).to_have_count(0)
    assert requested == []


async def test_approve_and_reject_from_overview(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    with_requests(data, request_row("20231877"), request_row("20220456", "revoked"))
    gate = asyncio.Event()
    calls = await decisions(page, data, gate=gate)
    await page.goto(BASE + "#admin/overview")
    box = page.locator("#admin-body .admin-access")
    first = box.locator("tbody tr").nth(0)
    await first.get_by_role("button", name="승인").click()
    # 처리 중에는 그 줄의 버튼이 모두 잠긴다
    await expect(first.locator("button")).to_have_count(2)
    for b in await first.locator("button").all():
        await expect(b).to_be_disabled()
    await expect(box.locator("tbody tr").nth(1).get_by_role("button", name="승인")).to_be_enabled()
    gate.set()
    await expect(box.locator("tbody tr")).to_have_count(1)
    await expect(page.locator("#toast")).to_contain_text("20231877을 승인했습니다")
    assert calls == [("20231877", "approve")]
    await box.get_by_role("button", name="거절").click()
    await expect(page.locator("#toast")).to_contain_text("20220456의 요청을 거절했습니다")
    assert calls[-1] == ("20220456", "reject")
    await expect(box).to_have_count(0)  # 다 처리하면 표가 사라진다


async def test_users_tab_access_column_filter_and_buttons(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    data["/admin/users"] = {"total": 6, "users": [
        admin_user("20200001", access_status="approved", is_admin=True),
        admin_user("20231234", access_status="approved"),
        admin_user("20231877", access_status="pending"),
        admin_user("20209999", access_status="none"),
        admin_user("20208888", access_status="rejected"),
        admin_user("20207777", access_status="revoked"),
    ]}
    await page.goto(BASE + "#admin/users")
    view = page.locator("#view")
    rows = view.locator(".admin-users tbody tr")
    await expect(rows).to_have_count(6)
    await expect(view.locator(".admin-users thead")).to_contain_text("권한")
    for i, label in enumerate(["승인됨", "승인됨", "대기 중", "요청 안 함", "거절됨", "회수됨"]):
        await expect(rows.nth(i).locator('td[data-label="권한"]')).to_have_text(label)

    def names(i):
        return rows.nth(i).locator(".admin-acts button")
    await expect(names(0)).to_have_count(0)  # 관리자 줄
    await expect(rows.nth(0)).to_contain_text("관리자 계정")
    await expect(names(1)).to_have_text(["권한 회수"])
    await expect(names(2)).to_have_text(["승인", "거절"])
    await expect(names(3)).to_have_text(["바로 승인"])
    await expect(names(4)).to_have_text(["바로 승인"])
    await expect(names(5)).to_have_text(["바로 승인"])
    # 버튼을 눌러도 상세로 가지 않는다
    calls = await decisions(page, data)
    await names(3).click()
    await expect(page.locator("#toast")).to_contain_text("20209999을 승인했습니다")
    assert calls == [("20209999", "approve")] and page.url.endswith("#admin/users")

    group = view.get_by_role("group", name="권한")
    await expect(group.get_by_role("button")).to_have_text(["전체", "승인됨", "대기 중", "거절됨", "회수됨", "요청 안 함"])
    await expect(group.get_by_role("button", name="전체")).to_have_attribute("aria-pressed", "true")

    def access(r):
        q = parse_qs(urlparse(r.url).query) if urlparse(r.url).path == "/admin/users" else {}
        return q.get("access")
    async with page.expect_request(lambda r: access(r) == ["pending"]):
        await group.get_by_role("button", name="대기 중").click()
    await expect(group.get_by_role("button", name="대기 중")).to_have_attribute("aria-pressed", "true")
    async with page.expect_request(lambda r: access(r) == ["none"]):
        await group.get_by_role("button", name="요청 안 함").click()
    async with page.expect_request(lambda r: urlparse(r.url).path == "/admin/users" and access(r) is None):
        await group.get_by_role("button", name="전체").click()


async def test_revoke_needs_confirmation(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    data["/admin/users"] = {"total": 1, "users": [admin_user("20231234", access_status="approved")]}
    calls = await decisions(page, data)
    await page.goto(BASE + "#admin/users")
    row = page.locator(".admin-users tbody tr")
    await row.get_by_role("button", name="권한 회수").click()
    dialog = page.locator("#confirm-dialog")
    await expect(dialog).to_contain_text("20231234")
    await expect(dialog).to_contain_text("재생")
    await dialog.get_by_role("button", name="취소").click()
    await expect(dialog).to_be_hidden()
    assert calls == []
    await row.get_by_role("button", name="권한 회수").click()
    await dialog.get_by_role("button", name="권한 회수").click()
    await expect(page.locator("#toast")).to_contain_text("20231234의 권한을 회수했습니다")
    assert calls == [("20231234", "revoke")]


async def test_decision_conflict_shows_neutral_toast_and_reloads(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    with_requests(data, request_row("20231877"))
    await decisions(page, data, status=409)
    loads = []
    page.on("request", lambda r: loads.append(urlparse(r.url).path) if r.method == "GET" else None)
    await page.goto(BASE + "#admin/overview")
    box = page.locator("#admin-body .admin-access")
    await expect(box.locator("tbody tr")).to_have_count(1)
    before = loads.count("/admin/access-requests")
    data["/admin/access-requests"] = {"total": 0, "items": []}  # 다른 관리자가 먼저 처리했다
    await box.get_by_role("button", name="승인").click()
    await expect(page.locator("#toast")).to_contain_text("이미 처리된 요청이라 최신 상태로 다시 불러왔어요")
    await expect(page.locator("#toast .bad")).to_have_count(0)  # 409는 오류가 아니다
    await expect(box).to_have_count(0)
    assert loads.count("/admin/access-requests") == before + 1
    # 사용자 탭도 다시 불러온다
    data["/admin/users"] = {"total": 1, "users": [admin_user("20231234", access_status="approved")]}
    await page.goto(BASE + "#admin/users")
    await expect(page.locator(".admin-users tbody tr")).to_have_count(1)
    before = loads.count("/admin/users")
    data["/admin/users"] = {"total": 1, "users": [admin_user("20231234", access_status="revoked")]}
    await page.locator(".admin-users").get_by_role("button", name="권한 회수").click()
    await page.locator("#confirm-dialog").get_by_role("button", name="권한 회수").click()
    await expect(page.locator(".admin-users tbody tr")).to_contain_text("회수됨")
    assert loads.count("/admin/users") == before + 1


async def test_detail_header_shows_access_and_actions(dashboard):  # noqa: F811
    page, data = dashboard
    as_users_admin(data)
    data[f"/admin/users/{SID}/summary"]["access_status"] = "pending"
    calls = await decisions(page, data)
    await page.goto(BASE + f"#admin/users/{SID}")
    head = page.locator("#view .admin-user-head")
    await expect(head).to_contain_text("대기 중")
    await expect(head.locator(".admin-acts button")).to_have_text(["승인", "거절"])
    data[f"/admin/users/{SID}/summary"]["access_status"] = "approved"
    await head.get_by_role("button", name="승인", exact=True).click()
    assert calls == [(SID, "approve")]
    await expect(head.locator(".admin-who .admin-tag").nth(1)).to_have_text("승인됨")
    await expect(head.locator(".admin-acts button")).to_have_text(["권한 회수"])
    # 관리자 상세는 버튼이 없다
    data["/admin/users/20231877/summary"] = {**data[f"/admin/users/{SID}/summary"], "student_id": "20231877",
                                             "is_admin": True, "access_status": "approved"}
    await page.goto(BASE + "#admin/users/20231877")
    await expect(head).to_contain_text("관리자")
    await expect(head.locator(".admin-acts")).to_have_count(0)
