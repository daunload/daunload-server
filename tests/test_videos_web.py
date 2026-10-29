"""강의 영상·수업자료 화면 (정적 파일 + 모킹한 API)."""

import pytest
from playwright.async_api import expect

from tests.test_web import dashboard  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser


def _video(mid, title, downloaded=False, transcript=None):
    return {"module_item_id": mid, "module_id": 1, "module_title": "1주차", "title": title, "content_type": "movie",
            "duration": 600, "size": 100_000_000, "completed": False, "available": True,
            "thumbnail_url": "https://commons.ssu.ac.kr/thumb.jpg",
            "downloaded_at": "2026-10-29T00:00:00Z" if downloaded else None, "transcript_id": transcript}


async def test_video_tab_badges_filter_and_materials_tab(dashboard):
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(11, "받은 강의", downloaded=True), _video(12, "안 받은 강의")]
    data["/courses/1/materials"] = [
        {"key": "c21", "kind": "commons_file", "module_id": 1, "module_title": "1주차", "title": "1장",
         "file_name": "1장.pptx", "size": 2_000_000, "downloaded_at": None},
        {"key": "f701", "kind": "canvas_file", "module_id": 1, "module_title": "1주차", "title": "1주차 자료",
         "file_name": "노트.pdf", "size": None, "downloaded_at": "2026-10-29T00:00:00Z"},
    ]
    await page.goto("http://dashboard.test/#videos")
    rows = page.locator("#video-list li.video-row")
    await expect(rows).to_have_count(2)
    await expect(rows.nth(0).get_by_text("✓ 받음")).to_be_visible()
    await expect(rows.nth(1).get_by_text("✓ 받음")).to_have_count(0)
    await expect(page.get_by_role("button", name="전체 받기 (2)")).to_be_visible()

    await page.get_by_label("안 받은 것만").check()
    await expect(rows).to_have_count(1)
    await expect(rows.nth(0)).to_contain_text("안 받은 강의")

    await page.get_by_role("tab", name="수업자료").click()
    await expect(rows).to_have_count(1)  # 필터가 유지되어 받은 노트.pdf는 숨김
    await expect(rows.nth(0)).to_contain_text("1장.pptx")
    await page.get_by_label("안 받은 것만").uncheck()
    await expect(rows).to_have_count(2)
    await expect(rows.nth(1)).to_contain_text("위키 첨부")


async def test_material_download_marks_row(dashboard):
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = []
    data["/courses/1/materials"] = [{"key": "c21", "kind": "commons_file", "module_id": 1, "module_title": "1주차",
                                     "title": "1장", "file_name": "1장.pptx", "size": 1, "downloaded_at": None}]
    data["/courses/1/materials/c21/download-link"] = {"url": "/downloads/tok", "file_name": "1장.pptx", "expires_in_minutes": 10}
    data["/downloads/tok"] = {}
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("tab", name="수업자료").click()
    await page.get_by_role("button", name="내려받기", exact=True).click()
    await expect(page.locator("#video-list").get_by_text("✓ 받음")).to_be_visible()


FAKE_FOLDER = """
window.__saved = {};
window.showDirectoryPicker = async () => ({
  getFileHandle: async (name) => ({
    createWritable: async () => {
      const parts = [];
      return new WritableStream({ write: (c) => parts.push(c), close: () => { window.__saved[name] = parts.length; } });
    },
  }),
});
"""


async def test_bulk_download_waits_out_busy_server(dashboard):
    """전사가 사용자 자리 하나를 쓰고 있어도 여러 개 받기는 자리가 날 때까지 기다렸다 받는다."""
    page, data = dashboard
    await page.add_init_script(FAKE_FOLDER)
    await page.clock.install()
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(11, "긴 강의")]
    links, busy = [], {"left": 8}

    async def link(route):
        links.append(1)
        await route.fulfill(json={"url": "/downloads/tok", "file_name": "긴 강의.mp4", "expires_in_minutes": 10})

    async def download(route):
        if busy["left"]:
            busy["left"] -= 1
            await route.fulfill(status=429, json={"detail": "동시에 2개까지 받을 수 있습니다."})
        else:
            await route.fulfill(body=b"mp4", content_type="video/mp4")

    await page.route("**/courses/1/videos/11/download-link", link)
    await page.route("**/downloads/tok", download)
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="전체 받기 (1)").click()
    status = page.locator("#bulk-status")
    for _ in range(20):
        if "다 받았습니다" in (await status.text_content() or ""):
            break
        await page.clock.run_for(5000)
    await expect(status).to_contain_text("다 받았습니다: 1/1 완료, 실패 0")
    assert await page.evaluate("window.__saved['긴 강의.mp4']") == 1
    assert len(links) == 1  # 기다리는 동안 링크를 새로 발급하지 않는다(이력 횟수가 늘지 않게)
