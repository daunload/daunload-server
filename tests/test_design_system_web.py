"""Design-system rules: 44px touch targets, 16/14px text, mobile density, no sideways scroll."""

import pytest
from playwright.async_api import expect

from tests.test_videos_web import FAKE_EXTENSION
from tests.test_web import dashboard  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser

LONG_TITLE = "가" * 80
LONG_WEEK = "주" * 40
VIEWS = ("dashboard", "playback", "videos", "notifications", "account")


def _video(mid, title, week="1주차", transcript=None):
    return {"module_item_id": mid, "module_id": 1, "module_title": week, "title": title, "content_type": "movie",
            "duration": 600, "size": 100_000_000, "completed": False, "available": True,
            "thumbnail_url": None, "downloaded_at": None, "transcript_id": transcript}


def _seed(data, videos):
    data["/saved-videos"] = []
    data["/courses/1/videos"] = videos
    for v in videos:
        data[f"/courses/1/videos/{v['module_item_id']}/direct-download"] = {"url": "https://cdn.commonscdn.com/a.mp4", "file_name": "a.mp4", "receipt": "r"}
    data["/transcripts/9"] = {"id": 9, "lms_course_id": 1, "module_item_id": 13, "course_name": "x", "title": "t", "segments": []}


async def test_mobile_touch_targets_and_type(dashboard):
    page, data = dashboard
    # every row has at most two icon buttons: transcribe or view transcript, and download
    _seed(data, [_video(11, "2강 재귀"), _video(12, "3강 정렬"), _video(13, "4강 탐색", transcript=9)])
    await page.set_viewport_size({"width": 375, "height": 812})
    sizes = []
    for view in VIEWS:
        await page.goto(f"http://dashboard.test/#{view}")
        await expect(page.locator("#view")).not_to_be_empty()
        if view == "videos":
            await expect(page.locator("#video-list li.video-row")).to_have_count(3)
            title, meta = await page.evaluate("""() => {
              const px = (s) => getComputedStyle(document.querySelector(s)).fontSize;
              return [px('.video-row .title'), px('.video-row .meta')];
            }""")
            assert (title, meta) == ("16px", "14px")
        await page.locator("#menu-toggle").click()  # opens the mobile menu so .side buttons are measured
        sizes += await page.evaluate("""[...document.querySelectorAll('.btn,.side button,.cal-nav,summary,.brand,label:has(.checkbox)')]
          .filter(e => e.offsetParent).map(e => e.getBoundingClientRect().height)""")
        await page.locator("#menu-toggle").click()
    assert sizes and min(sizes) >= 44, min(sizes)


async def test_mobile_video_density(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_EXTENSION)  # 설치한 사용자: 안내가 접혀 있다
    _seed(data, [_video(11 + i, f"{i + 1}강 재귀") for i in range(6)])
    await page.set_viewport_size({"width": 375, "height": 812})
    await page.goto("http://dashboard.test/#videos")
    rows = page.locator("#video-list li.video-row")
    await expect(rows).to_have_count(6)
    # the page header, notice and filters sit above the list, so scroll the list to the top first
    await page.evaluate("document.querySelector('#video-list').scrollIntoView()")
    inside = await page.evaluate("""[...document.querySelectorAll('.video-row')].filter(r => {
      const b = r.getBoundingClientRect();
      return b.top >= document.querySelector('.navigation').getBoundingClientRect().bottom && b.bottom <= innerHeight;
    }).length""")
    assert inside >= 4, inside
    tops = await rows.nth(0).locator(".btn").evaluate_all("els => els.map(e => Math.round(e.getBoundingClientRect().top))")
    assert len(tops) == 2 and tops[0] == tops[1], tops


@pytest.mark.parametrize("width", (320, 375, 768, 1024, 1440))
async def test_long_titles_never_scroll_sideways(dashboard, width):
    page, data = dashboard
    _seed(data, [_video(11, LONG_TITLE, week=LONG_WEEK), _video(12, LONG_TITLE, week=LONG_WEEK, transcript=9)])
    data["/todos"][0]["title"] = LONG_TITLE
    data["/todos"][0]["module_title"] = LONG_WEEK
    data["/playback/batches/preview"]["items"][0].update(title=LONG_TITLE, module_title=LONG_WEEK, course_name=LONG_TITLE)
    await page.set_viewport_size({"width": width, "height": 900})
    for view in ("dashboard", "videos", "playback"):
        await page.goto(f"http://dashboard.test/#{view}")
        await expect(page.locator("#view")).not_to_be_empty()
        await page.wait_for_timeout(150)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (view, width)


async def test_mobile_edges_line_up(dashboard):
    page, data = dashboard
    _seed(data, [_video(11, "2강 재귀"), _video(12, "3강 정렬")])
    await page.set_viewport_size({"width": 375, "height": 812})
    await page.goto("http://dashboard.test/#videos")
    await expect(page.locator("#video-list li.video-row")).to_have_count(2)
    lefts = await page.evaluate("""['.topbar .home-link', '#current-view', '[role=tablist]', '.notice-fold', '#video-list']
      .map(s => Math.round(document.querySelector(s).getBoundingClientRect().left))""")
    assert set(lefts) == {12}, lefts


async def test_disclosure_arrow_is_an_icon_that_turns(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_EXTENSION)  # 설치한 사용자: 안내가 접힌 채 시작한다
    _seed(data, [_video(11, "2강 재귀")])
    await page.goto("http://dashboard.test/#videos")
    await expect(page.locator("#video-list .guide:not(.needs-install)")).to_be_visible()
    summary = page.locator("#video-list .guide > summary")
    arrow = lambda prop: summary.evaluate(f"e => getComputedStyle(e, '::after').{prop}")
    assert await arrow("content") == '""' and "svg" in await arrow("maskImage")
    assert await arrow("rotate") == "none"
    await summary.click()
    await page.wait_for_function("getComputedStyle(document.querySelector('.guide > summary'), '::after').rotate === '180deg'")
    await summary.hover()
    assert await summary.evaluate("e => getComputedStyle(e).cursor") == "pointer"
