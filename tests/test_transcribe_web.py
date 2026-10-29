"""브라우저 전사 흐름. 실제 Whisper 대신 가짜 Worker로 PCM 스트림 → Worker → 서버 저장 경로를 확인한다."""

import json

import pytest
from playwright.async_api import expect

from tests.test_web import dashboard  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser

# 받은 샘플 수를 세고, end를 받으면 문장 두 개를 돌려주는 가짜 Worker
FAKE_WORKER = """
window.Worker = class {
  constructor() { this.listeners = []; this.samples = 0; }
  addEventListener(type, fn) { if (type === "message") this.listeners.push(fn); }
  emit(data) { setTimeout(() => this.listeners.forEach((fn) => fn({ data })), 0); }
  terminate() {}
  postMessage(m) {
    if (m.type === "start") this.emit({ type: "device", device: "webgpu" });
    if (m.type === "chunk") { this.samples += m.samples.length; window.__samples = this.samples; }
    if (m.type === "end") {
      this.emit({ type: "segment", jobId: m.jobId, start: 0, end: 1, text: "안녕하세요" });
      this.emit({ type: "segment", jobId: m.jobId, start: 1, end: 2, text: "강의를 시작합니다" });
      this.emit({ type: "progress", jobId: m.jobId, seconds: 2 });
      this.emit({ type: "done", jobId: m.jobId });
    }
  }
};
"""


def _video(mid, title, transcript=None):
    return {"module_item_id": mid, "module_id": 1, "module_title": "1주차", "title": title, "content_type": "movie",
            "duration": 2, "size": 1, "completed": False, "available": True, "thumbnail_url": None,
            "downloaded_at": None, "transcript_id": transcript}


async def test_transcribe_streams_pcm_to_worker_and_saves(dashboard):
    page, data = dashboard
    await page.add_init_script(FAKE_WORKER)
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(12, "2강 재귀")]
    data["/courses/1/videos/12/audio-link"] = {"url": "/audio/tok", "file_name": "a.mp4", "expires_in_minutes": 10}
    saved = []

    async def audio(route):
        await route.fulfill(body=b"\x00\x10" * 32001, headers={"X-Audio-Duration": "2", "Content-Type": "application/octet-stream"})

    async def transcript(route):
        saved.append(json.loads(route.request.post_data))
        await route.fulfill(json={"id": 9, "lms_course_id": 1, "module_item_id": 12, "course_name": "컴퓨터프로그래밍",
                                  "title": "2강 재귀", "model": "whisper-small/webgpu", "duration": 2,
                                  "updated_at": "2026-10-29T00:00:00Z"})

    await page.route("**/audio/tok", audio)
    await page.route("**/courses/1/videos/12/transcript", transcript)
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="전사", exact=True).click()
    await expect(page.locator("#video-list").get_by_text("전사됨")).to_be_visible()
    assert await page.evaluate("window.__samples") == 32001  # 홀수 바이트 경계 없이 모든 샘플 전달
    body = saved[0]
    assert body["text"] == "안녕하세요\n강의를 시작합니다"
    assert body["model"] == "whisper-small/webgpu" and body["course_name"] == "컴퓨터프로그래밍"
    assert [s["text"] for s in body["segments"]] == ["안녕하세요", "강의를 시작합니다"]


async def test_transcript_dialog_toggle_and_delete(dashboard):
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(12, "2강 재귀", transcript=9)]
    data["/transcripts/9"] = {"id": 9, "lms_course_id": 1, "module_item_id": 12, "course_name": "컴퓨터프로그래밍",
                              "title": "2강 재귀", "model": "whisper-small/wasm", "duration": 70, "updated_at": "2026-10-29T00:00:00Z",
                              "text": "안녕하세요\n끝", "segments": [{"start": 0, "end": 1, "text": "안녕하세요"},
                                                                  {"start": 65, "end": 70, "text": "끝"}]}
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="전사됨 보기").click()
    text = page.locator("#tx-text")
    await expect(text).to_have_text("안녕하세요\n끝")
    await page.get_by_label("시간 표시").check()
    await expect(text).to_have_text("[00:00] 안녕하세요\n[01:05] 끝")
    await page.get_by_role("button", name="삭제").click()
    await expect(page.locator("#transcript-dialog")).not_to_be_visible()
    await expect(page.get_by_role("button", name="전사됨 보기")).to_have_count(0)


FAILING_WORKER = """
window.Worker = class {
  constructor() { this.listeners = []; }
  addEventListener(type, fn) { if (type === "message") this.listeners.push(fn); }
  terminate() {}
  postMessage(m) {
    if (m.type === "start") setTimeout(() => this.listeners.forEach((fn) => fn({ data: { type: "error", jobId: m.jobId, message: "디코더 오류", fatal: false } })), 0);
  }
};
"""


async def _start_one(page, data, worker, audio_seconds, duration):
    await page.add_init_script(worker)
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(12, "2강 재귀")]
    data["/courses/1/videos/12/audio-link"] = {"url": "/audio/tok", "file_name": "a.mp4", "expires_in_minutes": 10}
    saved = []

    async def audio(route):
        await route.fulfill(body=b"\x00\x10" * (16000 * audio_seconds),
                            headers={"X-Audio-Duration": str(duration), "Content-Type": "application/octet-stream"})

    async def transcript(route):
        saved.append(route.request.post_data)
        await route.fulfill(json={"id": 9})

    await page.route("**/audio/tok", audio)
    await page.route("**/courses/1/videos/12/transcript", transcript)
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="전사", exact=True).click()
    return saved


async def test_failed_job_aborts_audio_stream(dashboard):
    page, data = dashboard
    saved = await _start_one(page, data, FAILING_WORKER, audio_seconds=2, duration=2)
    await expect(page.locator("#transcribe-panel")).to_contain_text("디코더 오류")
    # 실패한 작업의 오디오 요청을 끊어야 서버의 동시 실행 자리가 풀린다
    assert await page.evaluate("tx.finished[0].ctrl.signal.aborted") is True
    assert saved == []


async def test_truncated_audio_is_not_saved(dashboard):
    page, data = dashboard
    saved = await _start_one(page, data, FAKE_WORKER, audio_seconds=2, duration=60)
    await expect(page.locator("#transcribe-panel")).to_contain_text("오디오가 중간에 끊겼습니다")
    assert saved == []
