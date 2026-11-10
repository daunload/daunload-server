"""브라우저 텍스트 변환 흐름. 실제 Whisper 대신 가짜 Worker로 확장 프로그램 PCM → Worker → 서버 저장 경로를 확인한다."""

import json

import pytest
from playwright.async_api import expect

from tests.test_web import dashboard  # noqa: F401  (fixture)
from tests.web_root import EXTENSION_CLIENT

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


CDN_URL = "https://cdn.commonscdn.com/a.mp4"
DIRECT = {"url": CDN_URL, "file_name": "a.mp4", "receipt": "r"}

# 확장 프로그램을 흉내 낸다. 호출은 window.__ext에 남고, window.__read(n, call)로 audio-read 응답을 바꿀 수 있다.
FAKE_EXT = """
crypto.randomUUID = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');  // http 테스트 주소에는 없다
window.__ext = [];
window.__ping = { version: 1, sessions: true, queue: true, audio: true };
window.__reads = 0;
window.__hang = false;
window.__sine = (sec) => {
  const n = 16000 * sec, pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) pcm[i] = Math.round(Math.sin(2 * Math.PI * 440 * i / 16000) * 16000);
  let s = ""; const b = new Uint8Array(pcm.buffer);
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s);
};
window.__read = async (n) => {
  if (n === 1 && window.__hang) return new Promise(() => {});
  return n < 3 ? { pcm: window.__sine(1), seconds: 1 } : { done: true, duration: 3 };
};
__ssu.ext.request = async (action, data = {}) => {
  window.__ext.push({ action, ...data });
  if (action === "ping") return window.__ping;
  if (action === "audio-open") return { opened: true };
  if (action === "audio-close") return { closed: true };
  if (action === "audio-read") {
    const r = await window.__read(window.__reads++, data);
    if (r?.error) throw new Error(r.error);
    return r;
  }
  throw new Error("unexpected " + action);
};
"""

TRANSCRIPT_OK = {"id": 9, "lms_course_id": 1, "module_item_id": 12, "course_name": "컴퓨터프로그래밍",
                 "title": "2강 재귀", "model": "whisper-small/webgpu", "duration": 3, "updated_at": "2026-10-29T00:00:00Z"}


async def _setup(page, data, *, worker=None, duration=3, fake_ext=True, extra_videos=()):
    await page.add_init_script(worker or FAKE_WORKER)
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [{**_video(12, "2강 재귀"), "duration": duration}, *extra_videos]
    for v in extra_videos:
        data[f"/courses/1/videos/{v['module_item_id']}/direct-download"] = DIRECT
    data["/courses/1/videos/12/direct-download"] = DIRECT
    saved = []

    async def transcript(route):
        saved.append(json.loads(route.request.post_data))
        await route.fulfill(json=TRANSCRIPT_OK)

    await page.route("**/courses/1/videos/*/transcript", transcript)
    await page.goto("http://dashboard.test/#videos")
    await expect(page.locator("#video-list li.video-row").first).to_be_visible()
    if fake_ext:
        await page.evaluate("() => {" + FAKE_EXT + "}")
    return saved


async def test_transcribe_pulls_pcm_from_extension_and_saves(dashboard):
    page, data = dashboard
    forbidden = []
    page.on("request", lambda r: forbidden.append(r.url) if ("/downloads/complete" in r.url or "/audio" in r.url) else None)
    saved = await _setup(page, data)
    await page.get_by_role("button", name="텍스트로 변환", exact=True).click()
    await expect(page.locator("#video-list").get_by_role("button", name="텍스트 보기")).to_be_visible()
    assert abs(await page.evaluate("window.__samples") - 48000) <= 1
    body = saved[0]
    assert body["text"] == "안녕하세요\n강의를 시작합니다"
    assert body["model"] == "whisper-small/webgpu" and body["course_name"] == "컴퓨터프로그래밍"
    assert [s["text"] for s in body["segments"]] == ["안녕하세요", "강의를 시작합니다"]
    ext = await page.evaluate("window.__ext")
    opens = [c for c in ext if c["action"] == "audio-open"]
    assert [o["url"] for o in opens] == [CDN_URL]
    assert ext[-1]["action"] == "audio-close" and ext[-1]["job"] == opens[0]["job"]
    assert forbidden == []


async def test_transcribe_without_extension_shows_install_guide(dashboard):
    page, data = dashboard
    await _setup(page, data, fake_ext=False)
    await page.evaluate("""() => { __ssu.ext.request = async () => {
      throw Object.assign(new Error("아유 하기싫어 확장프로그램에 연결하지 못했습니다."), { code: "EXTENSION_MISSING" }); }; }""")
    await page.get_by_role("button", name="텍스트로 변환", exact=True).click()
    await expect(page.locator("#video-list details")).to_have_attribute("open", "")
    await expect(page.locator("#task-sheet")).to_contain_text("확장프로그램에 연결하지 못했습니다")


async def test_old_extension_asks_update_for_transcribe(dashboard):
    page, data = dashboard
    await _setup(page, data)
    await page.evaluate("window.__ping = { version: 1, sessions: true, queue: true }")
    await page.get_by_role("button", name="텍스트로 변환", exact=True).click()
    await expect(page.locator("#task-sheet")).to_contain_text("아유 하기싫어 확장프로그램을 업데이트해 주세요.")


async def test_transcribe_hidden_on_phone(dashboard):
    page, data = dashboard
    await page.add_init_script("""const mm = window.matchMedia.bind(window);
      window.matchMedia = (q) => q.includes("hover: none") ? { matches: true, media: q, addEventListener() {}, removeEventListener() {} } : mm(q);""")
    await page.add_init_script(FAKE_WORKER)
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(12, "2강 재귀"), _video(13, "3강 정렬"), _video(14, "4강 탐색", transcript=9)]
    await page.goto("http://dashboard.test/#videos")
    await expect(page.locator("#video-list li.video-row")).to_have_count(3)
    await expect(page.locator("[data-tx]")).to_have_count(0)
    await expect(page.locator("[data-tx-group]")).to_have_count(0)
    await expect(page.locator("[data-tx-open]")).to_have_count(1)


async def test_cancel_closes_extension_job(dashboard):
    page, data = dashboard
    await _setup(page, data, extra_videos=[{**_video(13, "3강 정렬"), "duration": 3}])
    await page.evaluate("window.__hang = true")
    await page.locator("[data-tx-group]").click()
    await page.wait_for_function("window.__reads === 2")
    first = await page.evaluate("__ssu.tx.current.extJob")
    await page.evaluate("window.__hang = false; window.__reads = 0; __ssu.cancelTranscription()")
    await page.wait_for_function("__ssu.tx.current?.moduleItemId === 13")
    ext = await page.evaluate("window.__ext")
    idx = next(i for i, c in enumerate(ext) if c["action"] == "audio-close" and c["job"] == first)
    assert not [c for c in ext[idx + 1:] if c["action"] == "audio-read" and c["job"] == first]


async def test_refresh_reissues_url(dashboard):
    page, data = dashboard
    await _setup(page, data)
    posts = []
    new_url = "https://cdn.commonscdn.com/b.mp4"

    async def direct(route):
        posts.append(1)
        await route.fulfill(json={**DIRECT, "url": CDN_URL if len(posts) == 1 else new_url})

    await page.route("**/courses/1/videos/12/direct-download", direct)
    await page.evaluate("""const base = window.__read;
      window.__read = (n, call) => n === 0 ? Promise.resolve({ refresh: true }) : base(n - 1, call);""")
    await page.get_by_role("button", name="텍스트로 변환", exact=True).click()
    await expect(page.locator("#video-list").get_by_role("button", name="텍스트 보기")).to_be_visible()
    assert len(posts) == 2
    reads = [c for c in await page.evaluate("window.__ext") if c["action"] == "audio-read"]
    assert "url" not in reads[0] and reads[1]["url"] == new_url


async def test_extension_error_is_shown(dashboard):
    page, data = dashboard
    saved = await _setup(page, data)
    await page.evaluate('window.__read = async () => ({ error: "이 영상에는 소리 트랙이 없습니다." })')
    await page.get_by_role("button", name="텍스트로 변환", exact=True).click()
    await expect(page.locator("#task-sheet")).to_contain_text("이 영상에는 소리 트랙이 없습니다.")
    assert saved == []
    assert (await page.evaluate("window.__ext"))[-1]["action"] == "audio-close"


async def test_short_audio_is_rejected(dashboard):
    page, data = dashboard
    saved = await _setup(page, data, duration=10)
    await page.get_by_role("button", name="텍스트로 변환", exact=True).click()
    await expect(page.locator("#task-sheet")).to_contain_text("오디오가 중간에 끊겼습니다")
    assert saved == []


async def test_read_timeout_fails_job_and_closes(dashboard):
    page, data = dashboard
    await _setup(page, data)
    await page.evaluate("""() => { window.__read = async () => { throw new Error("아유 하기싫어 확장프로그램에 연결하지 못했습니다."); }; }""")
    await page.get_by_role("button", name="텍스트로 변환", exact=True).click()
    await expect(page.locator("#task-sheet")).to_contain_text("연결하지 못했습니다")
    exts = [c["action"] for c in await page.evaluate("window.__ext")]
    assert exts.count("audio-read") == 1 and exts[-1] == "audio-close"


async def test_transcript_dialog_toggle_and_delete(dashboard):
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(12, "2강 재귀", transcript=9)]
    data["/transcripts/9"] = {"id": 9, "lms_course_id": 1, "module_item_id": 12, "course_name": "컴퓨터프로그래밍",
                              "title": "2강 재귀", "model": "whisper-small/wasm", "duration": 70, "updated_at": "2026-10-29T00:00:00Z",
                              "text": "안녕하세요\n끝", "segments": [{"start": 0, "end": 1, "text": "안녕하세요"},
                                                                  {"start": 65, "end": 70, "text": "끝"}]}
    await page.goto("http://dashboard.test/#videos")
    await page.get_by_role("button", name="텍스트 보기").click()
    text = page.locator("#tx-text")
    await expect(text).to_have_text("안녕하세요\n끝")
    await page.get_by_label("시간 표시").check()
    await expect(text).to_have_text("[00:00] 안녕하세요\n[01:05] 끝")
    await page.locator("#transcript-dialog").get_by_role("button", name="삭제").click()
    await page.locator("#confirm-dialog").get_by_role("button", name="텍스트 삭제").click()
    await expect(page.locator("#transcript-dialog")).not_to_be_visible()
    await expect(page.get_by_role("button", name="텍스트 보기")).to_have_count(0)


async def test_redo_lives_in_the_transcript_dialog(dashboard):
    page, data = dashboard
    data["/saved-videos"] = []
    data["/courses/1/videos"] = [_video(12, "2강 재귀", transcript=9)]
    data["/transcripts/9"] = {"id": 9, "lms_course_id": 1, "module_item_id": 12, "course_name": "컴퓨터프로그래밍",
                              "title": "2강 재귀", "model": "whisper-small/wasm", "duration": 70, "updated_at": "2026-10-29T00:00:00Z",
                              "text": "끝", "segments": []}
    await page.goto("http://dashboard.test/#videos")
    row = page.locator("#video-list li.video-row")
    await expect(row.get_by_role("button", name="텍스트 보기")).to_be_visible()
    await expect(row.get_by_role("button", name="다시 변환")).to_have_count(0)
    await expect(row.get_by_role("button")).to_have_count(2)
    await row.get_by_role("button", name="텍스트 보기").click()
    await page.locator("#transcript-dialog").get_by_role("button", name="다시 변환").click()
    await expect(page.locator("#transcript-dialog")).not_to_be_visible()
    # 다시 변환은 이 강의(과목 1, 항목 12) 하나만 변환 대기열에 넣는다
    await page.wait_for_function("""() => { const t = __ssu.tx;
      return [t.current, ...t.queue, ...t.finished].filter(Boolean).map((j) => j.key).join() === "1:12"; }""")


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


async def test_failed_job_closes_extension_job(dashboard):
    page, data = dashboard
    await _setup(page, data, worker=FAILING_WORKER)
    await page.get_by_role("button", name="텍스트로 변환", exact=True).click()
    await expect(page.locator("#task-sheet")).to_contain_text("디코더 오류")
    assert (await page.evaluate("window.__ext"))[-1]["action"] == "audio-close"


async def _start_one(page, data, worker, audio_seconds=3, duration=3):
    """다른 테스트 파일이 쓰는 도우미: 변환 하나를 시작한다(가짜 확장 프로그램은 항상 3초를 준다)."""
    saved = await _setup(page, data, worker=worker, duration=duration)
    await page.get_by_role("button", name="텍스트로 변환", exact=True).click()
    return saved


async def test_audio_requests_wait_60s_and_blame_the_school_server(dashboard):
    page, _ = dashboard
    await page.add_script_tag(content=EXTENSION_CLIENT)
    await page.clock.install()
    # No extension answers here, so every request ends by timeout.
    await page.evaluate("""() => {
      window.__out = {};
      window.postMessage = () => {};  // the page origin is opaque here; nobody answers anyway
      if (!crypto.randomUUID) crypto.randomUUID = () => Math.random().toString(16).slice(2);  // test page is plain http
      for (const action of ["audio-read", "ping", "status"]) {
        extensionRequest(action).then(() => { window.__out[action] = "ok"; },
          (e) => { window.__out[action] = { message: e.message, code: e.code }; });
      }
    }""")
    await page.clock.run_for(2000)
    out = await page.evaluate("window.__out")
    assert out["ping"]["code"] == "EXTENSION_MISSING"
    assert "audio-read" not in out and "status" not in out
    await page.clock.run_for(13500)  # 15.5 s: other actions time out, audio does not
    out = await page.evaluate("window.__out")
    assert "연결하지 못했습니다" in out["status"]["message"] and "audio-read" not in out
    await page.clock.run_for(44000)  # 59.5 s
    assert "audio-read" not in await page.evaluate("window.__out")
    await page.clock.run_for(1000)
    out = await page.evaluate("window.__out")
    assert out["audio-read"] == {"message": "학교 서버 응답이 늦어 텍스트 변환을 멈췄습니다. 다시 시도해 주세요.", "code": None}
