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
                             "student_id": "20231234"}],
        "recent_problems": [
            {"kind": "playback", "occurred_at": now.isoformat(), "student_id": "20220456", "course_name": "운영체제",
             "title": "7강 스케줄링", "error": "플레이어 로딩 시간 초과", "not_run": 0},
            {"kind": "lms", "occurred_at": None, "student_id": "20231877", "course_name": None,
             "title": "LMS 연결", "error": "비밀번호 변경 추정", "not_run": 0},
            {"kind": "batch", "occurred_at": now.isoformat(), "student_id": "20210092", "course_name": None,
             "title": "재생 묶음 (all)", "error": "중단됨", "not_run": 3},
        ],
    }
    data.update(over)
    return data


def as_admin(data, **over):
    data["/me"]["is_admin"] = True
    data["/admin/overview"] = overview(**over)
    data["/admin/users"] = {"total": 0, "users": []}


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
    await page.get_by_role("button", name="과목", exact=True).click()
    await expect(page).to_have_url(BASE + "#courses")
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
    problems = view.locator(".admin-problems")
    await expect(problems.locator("tbody tr")).to_have_count(3)
    await expect(problems).to_contain_text("플레이어 로딩 시간 초과")
    await expect(problems).to_contain_text("시각 기록 없음")
    await expect(problems).to_contain_text("미실행 3개")
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
    await expect(page.locator("#view")).to_contain_text("준비 중")
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
    await expect(page.locator("#view")).to_contain_text("준비 중")
    result = await page.evaluate("""() => {
        const host = document.createElement("div");
        document.querySelector("#view").append(host);
        host.innerHTML = adminTable([{ label: "학번", render: (r) => esc(r.id) }], [{ id: "<b>1</b>" }]);
        const cell = host.querySelector("td");
        const pages = [];
        const pager = adminPager(45, 20, 20, (o) => pages.push(o));
        host.append(pager);
        pager.querySelectorAll("button")[1].click();
        pager.querySelectorAll("button")[0].click();
        const state = document.createElement("div");
        let retried = 0;
        adminState(state, "error", { message: "<i>x</i>", retry: () => retried++ });
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


# ---------------- 사용자 목록과 상세 (Task 7) ----------------

SID = "20220456"
XSS = "<img src=x onerror=alert(1)>"


def admin_user(student_id, **over):
    now = datetime.now(UTC).isoformat()
    row = {"student_id": student_id, "created_at": "2026-03-04T01:00:00+00:00", "last_login_at": now, "login_count": 41,
           "lms_status": "ok", "course_count": 6, "playback_done": 58, "playback_failed": 3, "download_count": 21,
           "transcript_count": 4, "channels": ["discord"]}
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
    await expect(rows.nth(1)).to_contain_text("비밀번호가 바뀜")
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
    await expect(head).to_contain_text("LMS 비밀번호가 바뀜")
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
    ("전체 타임라인", "timeline", "activities", ["다운로드 2회", "운영체제 · 7강 스케줄링", "재생 실패"], True),
    ("과목·출석", "courses", "courses", ["김교수", "현재 학기", "지난 학기", "켜짐", "출석 기록 없음"], True),
    ("할 일·과제", "todos", "todos", ["마감 없음", "목록에서 사라짐(완료 또는 마감)", "제출함", "마감 지나 미제출"], True),
    ("재생", "playback", "playback", ["주차", "이번 주", "일부 실패", "미실행", "앞 영상 실패로 중단"], False),
    ("다운로드", "downloads", "downloads", ["6강 프로세스", "영상", "3회"], True),
    ("저장 영상", "saved", "saved-videos", ["os-06.mp4", "주소 있음", "주소 없음", "권한 없음", "350MB"], True),
    ("전사", "transcripts", "transcripts", ["whisper-small", "45분"], False),
    ("알림", "notifications", "notifications", ["Discord", "사용 중", "deadline:42:24h"], True),
    ("LMS", "lms", None, ["비밀번호가 바뀜", "학교 비밀번호가 바뀌었습니다", "예정 없음"], False),
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
    cell = page.locator("#view .admin-users tbody tr").first.locator("td").nth(1)
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
