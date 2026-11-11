"""공통 확인 창: 위험한 동작은 한 곳에서 확인하고, 확인 전에는 서버를 건드리지 않는다."""

import pytest
from playwright.async_api import expect

from tests.test_web import dashboard  # noqa: F401  (fixture)
from tests.test_week_web import page  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser
WARNING = "직접 시청하지 않은 수강은 학칙상 문제가 될 수 있어요."


async def test_play_all_confirms_with_warning_then_starts(page):
    await page.goto("http://dashboard.test/")
    await page.get_by_role("button", name="강의 2개 모두 재생").click()
    dialog = page.locator("#confirm-dialog")
    await expect(dialog).to_be_visible()
    await expect(dialog).to_contain_text("전체 강의 2개를 순서대로 재생할까요?")
    await expect(dialog).to_contain_text("1배속 · 약")
    await expect(dialog).to_contain_text(WARNING)
    assert page.calls == []
    await dialog.get_by_role("button", name="2개 재생 시작").click()
    await expect(page.locator(".cta-progress")).to_contain_text("강의 재생 중 0/2개")
    assert page.calls == [("POST", "/playback/batches", {"scope": "all"})]
    await expect(dialog).not_to_be_visible()


async def test_dialog_focus_escape_and_return(page):
    await page.goto("http://dashboard.test/")
    button = page.get_by_role("button", name="강의 2개 모두 재생")
    await button.click()
    await expect(page.get_by_role("button", name="취소")).to_be_focused()
    await page.keyboard.press("Escape")
    await expect(page.locator("#confirm-dialog")).not_to_be_visible()
    assert page.calls == []
    await expect(button).to_be_focused()


async def test_double_open_shows_one_dialog(page):
    await page.goto("http://dashboard.test/")
    await expect(page.locator("#dashboard-play-all")).to_be_visible()
    await page.evaluate('''() => {
      const b = document.getElementById("dashboard-play-all");
      b.click(); b.click();
    }''')
    dialog = page.locator("#confirm-dialog")
    await expect(dialog).to_be_visible()
    assert await page.locator("dialog[open]").count() == 1
    await dialog.get_by_role("button", name="2개 재생 시작").click()
    await expect(page.locator(".cta-progress")).to_contain_text("강의 재생 중 0/2개")
    assert page.calls == [("POST", "/playback/batches", {"scope": "all"})]


async def test_notification_channel_delete_confirms(page):
    page.data["/notifications/channels"] = [{"id": 5, "kind": "discord", "enabled": True, "created_at": page.now.isoformat()}]
    await page.goto("http://dashboard.test/#notifications")
    await page.get_by_role("button", name="삭제", exact=True).click()
    dialog = page.locator("#confirm-dialog")
    await expect(dialog).to_contain_text("이 알림 채널을 삭제할까요?")
    assert page.calls == []
    await dialog.get_by_role("button", name="알림 채널 삭제").click()
    await expect(dialog).not_to_be_visible()
    await expect(page.get_by_text("삭제했습니다")).to_be_visible()
    assert page.calls == [("DELETE", "/notifications/channels/5", None)]


async def test_account_delete_requires_student_id(page):
    await page.goto("http://dashboard.test/#account")
    await page.get_by_role("button", name="계정 삭제", exact=True).click()
    dialog = page.locator("#confirm-dialog")
    confirm = dialog.get_by_role("button", name="계정 삭제")
    await expect(confirm).to_be_disabled()
    field = dialog.get_by_label("확인을 위해 학번을 입력하세요")
    await field.fill("20260002")
    await expect(confirm).to_be_disabled()
    await field.fill(" 20260001 ")
    await expect(confirm).to_be_enabled()
    assert page.calls == []
    await confirm.click()
    await expect(page.locator("#auth")).to_be_visible()
    await expect(page.locator("#toast")).to_contain_text("계정과 저장된 정보를 모두 지웠습니다")
    assert page.calls == [("DELETE", "/me", None)]


async def test_logout_with_open_dialog_closes_it(page):
    await page.goto("http://dashboard.test/")
    await page.get_by_role("button", name="강의 2개 모두 재생").click()
    await expect(page.locator("#confirm-dialog")).to_be_visible()
    await page.evaluate("__ssu.logout()")
    await expect(page.locator("#confirm-dialog")).not_to_be_visible()
    await expect(page.locator("#auth")).to_be_visible()
    assert page.calls == []


async def test_plan_view_start_shows_warning_without_dialog(page):
    await page.goto("http://dashboard.test/#playback")
    await page.get_by_role("button", name="재생 계획 보기").click()
    await expect(page.locator("#start-batch")).to_have_text("2개 재생 시작")
    await expect(page.locator(".card .alert-error")).to_contain_text(WARNING)
    await page.locator("#start-batch").click()
    await expect(page.locator(".playback-hero")).to_be_visible()
    assert len(page.calls) == 1 and page.calls[0][:2] == ("POST", "/playback/batches")
    assert await page.locator("#confirm-dialog[open]").count() == 0


async def test_transcript_delete_confirms_over_transcript_dialog(dashboard):  # noqa: F811
    page, data = dashboard
    calls = []
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [{
        "module_item_id": 12, "module_id": 1, "module_title": "1주차", "title": "2강 재귀", "content_type": "movie",
        "duration": 2, "size": 1, "completed": False, "available": True, "thumbnail_url": None,
        "downloaded_at": None, "transcript_id": 9}]
    data["/transcripts/9"] = {"id": 9, "lms_course_id": 1, "module_item_id": 12, "course_name": "컴퓨터프로그래밍",
                              "title": "2강 재귀", "model": "whisper-small/wasm", "duration": 70,
                              "updated_at": "2026-10-29T00:00:00Z", "text": "안녕하세요", "segments": []}

    async def delete(route):
        calls.append(route.request.url)
        await route.fulfill(status=204)

    await page.route("**/transcripts/9", lambda r: delete(r) if r.request.method == "DELETE" else r.fallback())
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="텍스트 보기").click()
    transcript = page.locator("#transcript-dialog")
    await expect(transcript).to_be_visible()
    await transcript.get_by_role("button", name="삭제").click()
    dialog = page.locator("#confirm-dialog")
    await expect(dialog).to_contain_text("변환한 텍스트를 삭제할까요?")
    assert calls == []
    await dialog.get_by_role("button", name="취소").click()
    await expect(dialog).not_to_be_visible()
    await expect(transcript).to_be_visible()
    assert calls == []
    await transcript.get_by_role("button", name="삭제").click()
    await dialog.get_by_role("button", name="텍스트 삭제").click()
    await expect(transcript).not_to_be_visible()
    assert len(calls) == 1


async def test_logout_closes_transcript_dialog(dashboard):  # noqa: F811
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [{
        "module_item_id": 12, "module_id": 1, "module_title": "1주차", "title": "2강 재귀", "content_type": "movie",
        "duration": 2, "size": 1, "completed": False, "available": True, "thumbnail_url": None,
        "downloaded_at": None, "transcript_id": 9}]
    data["/transcripts/9"] = {"id": 9, "lms_course_id": 1, "module_item_id": 12, "course_name": "컴퓨터프로그래밍",
                              "title": "2강 재귀", "model": "whisper-small/wasm", "duration": 70,
                              "updated_at": "2026-10-29T00:00:00Z", "text": "안녕하세요", "segments": []}
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="텍스트 보기").click()
    await expect(page.locator("#transcript-dialog")).to_be_visible()
    await page.evaluate("__ssu.logout()")
    await expect(page.locator("#auth")).to_be_visible()
    await expect(page.locator("#transcript-dialog")).not_to_be_visible()
