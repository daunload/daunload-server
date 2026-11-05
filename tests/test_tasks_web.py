"""하단 진행 패널: 재생과 텍스트 변환을 한곳에서 보고 멈추며, 실패는 남겨 둔다 (가짜 API, 실제 Chrome)."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from playwright.async_api import expect

from tests.test_transcribe_web import FAILING_WORKER, _start_one
from tests.test_web import dashboard  # noqa: F401  (fixture)
from tests.test_week_web import page  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser
RUNNING_NOTE = "이 탭을 열어 두세요"
HANGING_WORKER = """
window.Worker = class {
  addEventListener() {}
  terminate() {}
  postMessage() {}
};
"""
POLL = 10000  # 폴링 주기(5초)를 넘겨 기다린다


def _active(page):
    batch = page.data["/playback/batches/7"]
    page.data["/playback/batches"] = [batch]
    return batch


def _past(id_, days=1, failed=1, status="partial"):
    return {"id": id_, "scope": "week", "status": status, "total": 2, "done_count": 1, "failed_count": failed,
            "skipped_count": 0, "cancel_requested": False, "error": None,
            "created_at": (datetime.now(UTC) - timedelta(days=days, hours=1)).isoformat(),
            "started_at": None, "finished_at": (datetime.now(UTC) - timedelta(days=days)).isoformat()}


async def _open(page):
    bar = page.locator("#task-bar")
    await bar.click()
    await expect(bar).to_have_attribute("aria-expanded", "true")
    await expect(page.locator("#task-sheet")).to_be_visible()


async def test_bar_hidden_without_tasks(page):
    await page.goto("http://dashboard.test/")
    await expect(page.get_by_role("heading", name="이번 주 할 일이 3개 남았어요")).to_be_visible()
    await expect(page.locator("#task-panel")).to_be_hidden()
    await expect(page.locator("body")).not_to_have_class(re.compile(r"\bhas-task-bar\b"))


async def test_panel_hidden_after_logout(page):
    _active(page)
    await page.goto("http://dashboard.test/")
    await expect(page.locator("#task-panel")).to_be_visible()
    await page.locator("#logout-btn").click()
    await expect(page.locator("#auth")).to_be_visible()
    await expect(page.locator("#task-panel")).to_be_hidden()
    await expect(page.locator("body")).not_to_have_class(re.compile(r"\bhas-task-bar\b"))


async def test_bar_summarizes_and_sheet_controls_playback(page):
    _active(page)
    await page.goto("http://dashboard.test/")
    bar = page.locator("#task-bar")
    await expect(bar).to_contain_text("작업 1개 진행 중")
    await expect(bar).to_have_attribute("aria-expanded", "false")
    await expect(page.locator("#task-sheet")).to_be_hidden()
    await _open(page)
    sheet = page.locator("#task-sheet")
    await expect(sheet).to_contain_text("강의 재생 0/2개")
    await expect(sheet).to_contain_text("창을 닫아도 서버에서 계속돼요")
    await sheet.get_by_role("button", name="재생 중지").click()
    dialog = page.locator("#confirm-dialog")
    await expect(dialog).to_contain_text("재생을 중지할까요?")
    await expect(dialog).to_contain_text("지금 재생 중인 강의가 끝나는 대로 멈춰요.")
    assert page.calls == []
    await dialog.get_by_role("button", name="재생 중지").click()
    await expect(page.get_by_text("지금 듣는 강의가 끝나는 대로 멈출게요")).to_be_visible()
    assert page.calls == [("DELETE", "/playback/batches/7", None)]
    await expect(sheet).to_contain_text("중지하는 중이에요")
    await expect(sheet.get_by_role("button", name="재생 중지")).to_have_count(0)


async def test_cancelling_stop_dialog_sends_nothing_and_returns_focus(page):
    _active(page)
    await page.goto("http://dashboard.test/")
    await _open(page)
    stop = page.locator("#task-sheet").get_by_role("button", name="재생 중지")
    await stop.click()
    await page.locator("#confirm-dialog").get_by_role("button", name="취소").click()
    await expect(stop).to_be_focused()
    assert page.calls == []


async def test_queued_and_auto_batches_are_listed(page):
    batch = _active(page)
    batch.update(status="queued", scope="auto", queue_position=3)
    await page.goto("http://dashboard.test/")
    await _open(page)
    await expect(page.locator("#task-sheet")).to_contain_text("대기 3번째")


async def test_transcribe_failure_stays_until_retry_or_close(dashboard):
    page, data = dashboard
    await _start_one(page, data, FAILING_WORKER, audio_seconds=2, duration=2)
    bar = page.locator("#task-bar")
    await expect(bar).to_contain_text("실패 1개")
    await expect(bar).not_to_contain_text("진행 중")
    await _open(page)
    sheet = page.locator("#task-sheet")
    await expect(sheet).to_contain_text("2강 재귀")
    await expect(sheet).to_contain_text("디코더 오류")
    await expect(sheet).not_to_contain_text(RUNNING_NOTE)

    # 다시 시도: 작업을 다시 넣는다 (가짜 Worker라 다시 실패한다)
    assert await page.evaluate("tx.nextId") == 2
    await sheet.get_by_role("button", name="다시 시도").click()
    await page.wait_for_function("tx.nextId === 3 && tx.finished.length === 1 && !tx.current")
    await expect(bar).to_contain_text("실패 1개")

    # 닫기: 그 항목만 없앤다
    await page.evaluate('''() => {
      tx.finished.push({ key: "1:99", status: "failed", title: "다른 강의", error: "다른 오류" });
      renderTaskPanel();
    }''')
    await expect(bar).to_contain_text("실패 2개")
    row = sheet.locator("li", has_text="2강 재귀")
    await row.get_by_role("button", name="닫기").click()
    await expect(sheet).not_to_contain_text("2강 재귀")
    await expect(sheet).to_contain_text("다른 강의")
    await expect(bar).to_contain_text("실패 1개")
    await sheet.get_by_role("button", name="닫기").click()
    await expect(page.locator("#task-panel")).to_be_hidden()


async def test_running_transcription_row(dashboard):
    page, _ = dashboard
    await page.goto("http://dashboard.test/#videos")
    await page.evaluate('''() => {
      window.__job = { key: "1:1", title: "돌아가는 강의", seconds: 30, duration: 60, ctrl: new AbortController(), reject() {} };
      tx.current = window.__job;
      tx.queue.push({ key: "1:2", title: "다음 강의" });
      tx.modelPercent = 40;
      renderTaskPanel();
    }''')
    bar = page.locator("#task-bar")
    await expect(bar).to_contain_text("작업 1개 진행 중")
    await _open(page)
    sheet = page.locator("#task-sheet")
    await expect(sheet).to_contain_text("돌아가는 강의")
    await expect(sheet).to_contain_text("50%")
    await expect(sheet).to_contain_text("대기 1개")
    await expect(sheet).to_contain_text("Whisper 모델 받는 중 40%")
    await expect(sheet).to_contain_text(RUNNING_NOTE)
    # 변환 중지는 확인 창 없는 일반 버튼
    stop = sheet.get_by_role("button", name="변환 중지")
    await expect(stop).not_to_have_class(re.compile(r"\bdanger\b"))
    await page.evaluate("tx.worker = null")
    await stop.click()
    await expect(page.locator("#confirm-dialog")).not_to_be_visible()
    assert await page.evaluate("window.__job.ctrl.signal.aborted") is True
    await page.evaluate("() => { tx.current = null; tx.queue.length = 0; tx.modelPercent = null; renderTaskPanel(); }")
    await expect(page.locator("#task-panel")).to_be_hidden()


async def test_redraw_while_pressing_does_not_lose_click(dashboard):
    page, _ = dashboard
    await page.goto("http://dashboard.test/#videos")
    await page.evaluate('''() => {
      window.__job = { key: "1:1", title: "돌아가는 강의", seconds: 10, duration: 60, ctrl: new AbortController(), reject() {} };
      tx.current = window.__job;
      renderTaskPanel();
    }''')
    await _open(page)
    sheet = page.locator("#task-sheet")
    await sheet.get_by_role("button", name="변환 중지").hover()
    await page.mouse.down()
    await page.evaluate("() => { window.__job.seconds = 30; renderTaskPanel(); }")  # 누르는 중에 진행률이 바뀐다
    await page.mouse.up()
    assert await page.evaluate("window.__job.ctrl.signal.aborted") is True
    await expect(sheet).to_contain_text("50%")  # 손을 떼면 밀린 내용을 그린다
    await page.evaluate("() => { tx.current = null; renderTaskPanel(); }")


async def test_failed_batch_within_7_days_links_to_reason(page):
    page.data["/playback/batches"] = [_past(21)]
    await page.goto("http://dashboard.test/")
    bar = page.locator("#task-bar")
    await expect(bar).to_contain_text("실패 1개")
    await expect(bar).not_to_contain_text("진행 중")
    await _open(page)
    sheet = page.locator("#task-sheet")
    await sheet.get_by_role("button", name="실패 사유 보기").click()
    await expect(page).to_have_url("http://dashboard.test/#playback")
    await expect(sheet).to_be_hidden()
    await expect(bar).to_have_attribute("aria-expanded", "false")
    await _open(page)
    await sheet.get_by_role("button", name="닫기").click()
    await expect(page.locator("#task-panel")).to_be_hidden()
    assert await page.evaluate('localStorage.getItem("dismissed-playback-failures")') == "[21]"
    await page.reload()
    await expect(page.get_by_role("heading", name="강의 재생")).to_be_visible()
    await expect(page.locator("#task-panel")).to_be_hidden()


async def test_old_or_only_cancelled_batches_are_not_failures(page):
    page.data["/playback/batches"] = [_past(31, days=8), _past(32, failed=0, status="cancelled")]
    await page.goto("http://dashboard.test/")
    await expect(page.get_by_role("heading", name="이번 주 할 일이 3개 남았어요")).to_be_visible()
    await expect(page.locator("#task-panel")).to_be_hidden()


async def test_failed_playback_batches_rule(page):
    await page.goto("http://dashboard.test/")
    result = await page.evaluate('''() => {
      const now = Date.parse("2026-11-05T00:00:00Z");
      const at = (days) => new Date(now - days * 86400000).toISOString();
      const ids = (list, dismissed = []) => failedPlaybackBatches(list, dismissed, now).map((b) => b.id);
      return {
        kept: ids([
          { id: 1, status: "partial", failed_count: 1, finished_at: at(1) },
          { id: 2, status: "failed", failed_count: 0, finished_at: at(6.9) },
        ]),
        dropped: ids([
          { id: 3, status: "running", failed_count: 1, finished_at: at(1) },
          { id: 4, status: "queued", failed_count: 1, finished_at: at(1) },
          { id: 5, status: "partial", failed_count: 1, finished_at: at(7.1) },
          { id: 6, status: "cancelled", failed_count: 0, finished_at: at(1) },
          { id: 7, status: "done", failed_count: 0, finished_at: at(1) },
          { id: 8, status: "partial", failed_count: 1, finished_at: null },
        ]),
        dismissed: ids([{ id: 9, status: "failed", failed_count: 1, finished_at: at(1) }], [9]),
        cancelledWithFailure: ids([{ id: 10, status: "cancelled", failed_count: 1, finished_at: at(1) }]),
      };
    }''')
    assert result == {"kept": [1, 2], "dropped": [], "dismissed": [], "cancelledWithFailure": [10]}


@pytest.mark.parametrize("value", ["{깨짐", "5", "null"])
async def test_corrupt_dismissed_storage_is_ignored(page, value):
    page.data["/playback/batches"] = [_past(21)]
    await page.add_init_script(f'localStorage.setItem("dismissed-playback-failures", {value!r});')
    await page.goto("http://dashboard.test/")
    await expect(page.locator("#task-bar")).to_contain_text("실패 1개")


async def test_poll_keeps_sheet_open_and_focus(page):
    batch = _active(page)
    await page.goto("http://dashboard.test/")
    await _open(page)
    sheet = page.locator("#task-sheet")
    stop = sheet.get_by_role("button", name="재생 중지")
    await stop.focus()
    batch["done_count"] = 1  # 폴링이 줄을 실제로 다시 그리게 한다
    await expect(sheet).to_contain_text("강의 재생 1/2개", timeout=POLL)
    await expect(page.locator("#task-bar")).to_have_attribute("aria-expanded", "true")
    await expect(sheet).to_be_visible()
    await expect(stop).to_be_focused()


async def test_batch_finishing_during_stop_dialog_sends_nothing(page):
    batch = _active(page)
    await page.goto("http://dashboard.test/")
    await _open(page)
    await page.locator("#task-sheet").get_by_role("button", name="재생 중지").click()
    dialog = page.locator("#confirm-dialog")
    await expect(dialog).to_be_visible()
    batch.update(status="done", done_count=2, finished_at=datetime.now(UTC).isoformat())
    await page.wait_for_function("state.active === null", timeout=POLL)
    await dialog.get_by_role("button", name="재생 중지").click()
    await expect(dialog).not_to_be_visible()
    await expect(page.locator("#task-panel")).to_be_hidden()
    assert page.calls == []


async def test_stop_dialog_does_not_stop_a_newer_batch(page):
    _active(page)
    await page.goto("http://dashboard.test/")
    await _open(page)
    await page.locator("#task-sheet").get_by_role("button", name="재생 중지").click()
    dialog = page.locator("#confirm-dialog")
    await expect(dialog).to_be_visible()
    await page.evaluate("state.active = { ...state.active, id: 8 }")
    await dialog.get_by_role("button", name="재생 중지").click()
    await expect(dialog).not_to_be_visible()
    assert page.calls == []


async def test_ten_failures_scroll_inside_sheet(page):
    await page.set_viewport_size({"width": 375, "height": 667})
    page.data["/playback/batches"] = [_past(100 + i) for i in range(10)]
    await page.goto("http://dashboard.test/")
    await expect(page.locator("#task-bar")).to_contain_text("실패 10개")
    await _open(page)
    box = await page.locator("#task-panel").bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 667
    sheet = page.locator("#task-sheet")
    assert await sheet.evaluate("el => el.scrollHeight > el.clientHeight")
    await sheet.get_by_role("button", name="닫기").last.click()
    await expect(page.locator("#task-bar")).to_contain_text("실패 9개")
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


async def test_bar_does_not_cover_last_row(page):
    await page.set_viewport_size({"width": 375, "height": 812})
    _active(page)
    await page.goto("http://dashboard.test/")
    last = page.locator(".dashboard-lecture-list li").last
    await expect(last).to_be_visible()
    await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    await page.wait_for_function("scrollY > 0")
    row = await last.bounding_box()
    bar = await page.locator("#task-bar").bounding_box()
    assert row["y"] + row["height"] <= bar["y"]
    assert bar["x"] == pytest.approx(10, abs=2.5) and bar["x"] + bar["width"] == pytest.approx(365, abs=2.5)


async def test_panel_is_right_corner_on_desktop_and_toast_sits_above(page):
    _active(page)
    await page.goto("http://dashboard.test/")
    panel = await page.locator("#task-panel").bounding_box()
    assert panel["width"] == 320
    assert panel["x"] + panel["width"] > 1250
    assert panel["y"] + panel["height"] <= 900 - 8
    await page.evaluate('toast("알림")')
    await page.wait_for_timeout(300)  # 토스트 전환이 끝난 위치를 잰다
    toast = await page.locator("#toast").bounding_box()
    assert toast["y"] + toast["height"] <= panel["y"]


async def test_long_title_in_sheet_wraps(dashboard):
    page, _ = dashboard
    await page.set_viewport_size({"width": 320, "height": 700})
    await page.goto("http://dashboard.test/")
    title = "가" * 80
    await page.evaluate('''(title) => {
      tx.finished.push({ key: "1:1", status: "failed", title, error: "오류" });
      renderTaskPanel();
    }''', title)
    await _open(page)
    await expect(page.locator("#task-sheet")).to_contain_text(title)
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    sheet = page.locator("#task-sheet")
    assert await sheet.evaluate("el => el.scrollWidth <= el.clientWidth")


async def test_task_strings_are_escaped(dashboard):
    page, _ = dashboard
    await page.goto("http://dashboard.test/")
    await page.evaluate('''() => {
      tx.finished.push({ key: "1:1", status: "failed", title: "<img src=x onerror=window.__xss=1>", error: "<b>오류</b>" });
      renderTaskPanel();
    }''')
    await expect(page.locator("#task-sheet img")).to_have_count(0)
    await expect(page.locator("#task-sheet b")).to_have_count(0)


async def test_keyboard_opens_and_escape_closes_sheet(page):
    _active(page)
    await page.goto("http://dashboard.test/")
    bar = page.locator("#task-bar")
    await bar.focus()
    await page.keyboard.press("Enter")
    await expect(bar).to_have_attribute("aria-expanded", "true")
    await page.keyboard.press("Tab")
    await expect(page.locator("#task-sheet").get_by_role("button", name="재생 중지")).to_be_focused()
    await page.keyboard.press("Escape")
    await expect(bar).to_have_attribute("aria-expanded", "false")
    await expect(bar).to_be_focused()


async def test_first_screen_shows_summary_without_stop(page):
    _active(page)
    await page.goto("http://dashboard.test/")
    week = page.locator("#week-panel")
    await expect(week).to_contain_text("강의 재생 중 0/2개")
    await expect(week.get_by_role("button", name="재생 중지")).to_have_count(0)
    await expect(page.locator("#dashboard-playback .dashboard-playback-progress")).to_have_count(0)
    await week.get_by_role("button", name="재생 현황 보기").click()
    await expect(page).to_have_url("http://dashboard.test/#playback")


async def test_playback_view_keeps_rows_without_stop(page):
    _active(page)
    await page.goto("http://dashboard.test/#playback")
    await expect(page.locator("#view .steps li")).to_have_count(2)
    await expect(page.locator("#cancel-batch")).to_have_count(0)
    await expect(page.locator("#view").get_by_role("button", name="재생 중지")).to_have_count(0)


async def test_logout_clears_bulk_and_transcribe_tasks(dashboard):
    page, data = dashboard
    await _start_one(page, data, HANGING_WORKER, audio_seconds=2, duration=2)
    await expect(page.locator("#task-panel")).to_be_visible()
    await page.wait_for_function("tx.current && tx.current.status === 'running'")
    await page.evaluate("""() => {
      bulk.ctrl = new AbortController();
      state.bulk = { done: 0, failed: 0, total: 2, running: true, errors: [], failedItems: [] };
      tx.queue.push({ key: "1:98", title: "대기", status: "queued" });
      renderTaskPanel();
    }""")
    await page.locator("#logout-btn").click()
    await expect(page.locator("#auth")).to_be_visible()
    assert await page.evaluate("bulk.ctrl.signal.aborted") is True
    await page.wait_for_function("!tx.current")
    assert await page.evaluate("[tx.queue.length, tx.finished.length, state.bulk]") == [0, 0, None]
    await page.evaluate("state.token = 'again'; renderTaskPanel()")
    await expect(page.locator("#task-panel")).to_be_hidden()
