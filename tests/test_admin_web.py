"""관리자 화면 뼈대와 현황 탭. tests.test_web의 dashboard 픽스처로 정적 파일과 가짜 API를 쓴다."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse

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
    await expect(page.locator("#view")).to_contain_text("준비 중")
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
    await page.locator("#view nav.admin-tabs").get_by_role("link", name="활동").click()
    async with page.expect_response("**/admin/overview?*"):
        release.set()
    await page.wait_for_timeout(100)
    await expect(page).to_have_url(BASE + "#admin/activity")
    await expect(page.locator("#view")).to_contain_text("준비 중")
    await expect(page.locator("#view .admin-kpi")).to_have_count(0)


async def test_admin_shared_pieces(dashboard):  # noqa: F811
    page, data = dashboard
    as_admin(data)
    await page.goto(BASE + "#admin/users")
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
