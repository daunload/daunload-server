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
