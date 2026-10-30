"""헤드리스 브라우저로 온라인 강의(Commons 영상)를 실제로 재생한다.

- 1배속 고정, 음소거, 끝까지 재생한 뒤 여유 시간을 두고 페이지를 정상 종료한다.
- 진도 API를 직접 호출하지 않는다. 진도는 플레이어가 스스로 보낸다.
- 재생 후 modules API에서 completed가 true로 바뀌었는지 확인한다(verify_completed).

플레이어 DOM 구조는 학교 쪽에서 바뀔 수 있으므로 셀렉터는 이 파일 상단에 모아 둔다.
실제 구조는 `python -m app.lms.player inspect <view_url>`로 확인한다.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Dialog, Frame, Page, async_playwright

from app.config import Settings, get_settings
from app.lms.auth import LmsSession
from app.lms.errors import PlaybackError, SessionExpired, VideoNotFound
from app.lms.todo import VideoTodo

log = logging.getLogger(__name__)

# 재생 시작 버튼 후보 (Commons/LearningX 플레이어와 일반 플레이어)
PLAY_SELECTORS = [
    ".vc-front-screen-play-btn",
    ".vc-pctrl-play-pause-btn",
    ".vjs-big-play-button",
    "button[aria-label*='재생']",
    "button[title*='재생']",
    ".play-btn",
]
# "이어서 보시겠습니까?" 같은 확인 창의 확인 버튼 후보
CONFIRM_SELECTORS = [
    ".confirm-ok-btn",
    ".vc-confirm-ok-btn",
    "button:text-is('예')",
    "button:text-is('확인')",
    "button:has-text('이어')",
    "input[type=button][value='확인']",
]
RESUME_DIALOG_SELECTORS = "[role=dialog], dialog, .modal, .popup, .ui-dialog, .bootbox"
RESUME_PROMPT = re.compile(
    r"이어|(?:이전|마지막|기존).*(?:시청|재생)|(?:시청|재생).*(?:위치|시점)|"
    r"(?:분|초|\d+:\d+).*(?:보시|재생|시청)",
    re.DOTALL,
)

END_GRACE_SECONDS = 15

BROWSER_ARGS = ["--autoplay-policy=no-user-gesture-required", "--mute-audio"]

# 모든 프레임에 미리 주입: 플레이어가 배속을 바꾸려 해도 항상 1배속으로 고정한다.
_FORCE_NORMAL_SPEED_JS = """
(() => {
  const proto = HTMLMediaElement.prototype;
  for (const prop of ['playbackRate', 'defaultPlaybackRate']) {
    const desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set) continue;
    Object.defineProperty(proto, prop, {
      configurable: true,
      get() { return desc.get.call(this); },
      set(_) { desc.set.call(this, 1); },
    });
  }
})();
"""

# 페이지 안에서 가장 긴 <video>의 상태를 읽고, 필요하면 1배속 음소거 재생을 건다.
_VIDEO_JS = """
(action) => {
  // Commons inserts a loaded 1-second preloader before the lecture is opened.
  // Playing it directly bypasses the front-screen button and never starts the lecture.
  const vids = Array.from(document.querySelectorAll('video')).filter(v => {
    const src = v.currentSrc || v.src;
    if (!src) return false;
    const path = new URL(src, document.baseURI).pathname;
    return !path.endsWith('/uniplayer/preloader.mp4');
  });
  if (!vids.length) return null;
  const v = vids.reduce((a, b) => ((b.duration || 0) > (a.duration || 0) ? b : a));
  if (action === 'play') {
    v.muted = true;
    if (v.playbackRate !== 1) v.playbackRate = 1;
    if (v.paused && !v.ended) { const p = v.play(); if (p && p.catch) p.catch(() => {}); }
  }
  return {t: v.currentTime || 0, d: isFinite(v.duration) ? v.duration : 0,
          paused: v.paused, ended: v.ended, rate: v.playbackRate, ready: v.readyState};
}
"""


@dataclass
class VideoState:
    t: float
    d: float
    paused: bool
    ended: bool
    rate: float
    ready: int


@dataclass
class PlaybackResult:
    module_item_id: int
    watched_seconds: float
    position: float
    duration: float
    reloads: int


ProgressCallback = Callable[[VideoTodo, float, float], Awaitable[None]]


@asynccontextmanager
async def launch_browser(settings: Settings | None = None, *, headless: bool | None = None):
    settings = settings or get_settings()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            channel=settings.playback_browser_channel or None,
            headless=settings.playback_headless if headless is None else headless,
            args=BROWSER_ARGS,
        )
        try:
            yield browser
        finally:
            await browser.close()


def playwright_cookies(session: LmsSession) -> list[dict]:
    return [
        {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path") or "/",
            "secure": True,
        }
        for c in session.cookies
    ]


async def new_session_context(browser: Browser, session: LmsSession) -> BrowserContext:
    context = await browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 800})
    await context.add_init_script(_FORCE_NORMAL_SPEED_JS)
    await context.add_cookies(playwright_cookies(session))
    return context


class VideoPlayer:
    def __init__(self, browser: Browser, settings: Settings | None = None, poll_seconds: float = 5.0) -> None:
        self.browser = browser
        self.settings = settings or get_settings()
        self.poll_seconds = poll_seconds

    async def play(
        self,
        session: LmsSession,
        video: VideoTodo,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> PlaybackResult:
        if not video.view_url:
            raise PlaybackError(f"재생 주소가 없습니다: {video.title}")
        context = await new_session_context(self.browser, session)
        try:
            page = await context.new_page()
            page.on("dialog", _accept_dialog)
            return await self._play_on_page(page, video, on_progress)
        finally:
            await context.close()

    async def _play_on_page(self, page: Page, video: VideoTodo, on_progress: ProgressCallback | None) -> PlaybackResult:
        s = self.settings
        started = time.monotonic()
        hard_limit = started + video.duration * 1.5 + 600
        reloads = 0

        await page.goto(video.view_url, wait_until="domcontentloaded")
        _assert_logged_in(page)
        frame = await self._start_video(page)

        last_t, last_advance = -1.0, time.monotonic()
        near_end_since: float | None = None
        state = VideoState(0, 0, True, False, 1, 0)
        while True:
            if time.monotonic() > hard_limit:
                raise PlaybackError(f"재생 시간 초과: {video.title}")
            await asyncio.sleep(self.poll_seconds)
            # The player may create its resume dialog after a video element is found.
            # Confirm before attempting direct media playback on every poll.
            await _confirm_resume(page)
            try:
                state = await _video_state(frame, "play")
            except Exception:  # 프레임이 사라진 경우(플레이어가 iframe을 갈아끼움)
                state = None
            if state is None:
                frame = await self._start_video(page)
                continue

            if on_progress:
                await on_progress(video, state.t, state.d or video.duration)

            # ended 이벤트까지 기다린다. 끝부분에서 ended가 오지 않는 플레이어를 대비해
            # 마지막 1초 안에 들어온 뒤 유예 시간이 지나면 끝난 것으로 본다.
            duration = state.d or video.duration
            if state.ended:
                if video.duration > 0 and state.d > 0 and state.d < video.duration * 0.5:
                    raise PlaybackError(
                        f"강의 길이와 감지한 영상 길이가 다릅니다: {video.title} "
                        f"(예상 {video.duration:.1f}s, 감지 {state.d:.1f}s, 위치 {state.t:.1f}s)"
                    )
                break
            if duration > 0 and state.t >= duration - 1:
                near_end_since = near_end_since or time.monotonic()
                if time.monotonic() - near_end_since > END_GRACE_SECONDS:
                    if video.duration > 0 and state.d > 0 and state.d < video.duration * 0.5:
                        raise PlaybackError(
                            f"강의 길이와 감지한 영상 길이가 다릅니다: {video.title} "
                            f"(예상 {video.duration:.1f}s, 감지 {state.d:.1f}s, 위치 {state.t:.1f}s)"
                        )
                    break
            else:
                near_end_since = None

            if state.t > last_t + 0.5:
                last_t, last_advance = state.t, time.monotonic()
            elif time.monotonic() - last_advance > s.playback_stall_seconds:
                if reloads >= s.playback_max_reloads:
                    raise PlaybackError(f"재생이 계속 멈춥니다: {video.title} ({state.t:.0f}s)")
                reloads += 1
                log.warning("재생 정지 감지, 새로고침 %d회: %s", reloads, video.title)
                await page.reload(wait_until="domcontentloaded")
                frame = await self._start_video(page)
                last_advance = time.monotonic()
            elif state.paused:
                await _click_first(page, PLAY_SELECTORS)

        # 종료 이벤트와 마지막 진도 전송을 위해 잠시 기다린 뒤 페이지를 정상 종료한다.
        await asyncio.sleep(s.playback_extra_seconds)
        log.info(
            "재생 종료: %s (위치 %.1fs / 영상 %.1fs, 예상 %.1fs)",
            video.title, state.t, state.d, video.duration,
        )
        await page.close(run_before_unload=True)
        return PlaybackResult(
            module_item_id=video.module_item_id,
            watched_seconds=time.monotonic() - started,
            position=state.t,
            duration=state.d or video.duration,
            reloads=reloads,
        )

    async def _start_video(self, page: Page, timeout: float = 90) -> Frame:
        """영상이 들어 있는 프레임을 찾아 재생을 시작하고 그 프레임을 반환한다."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await _confirm_resume(page)
            for frame in page.frames:
                try:
                    state = await _video_state(frame, "play")
                except Exception:
                    continue
                if state is not None:
                    if state.paused and not state.ended:
                        await _click_first(page, PLAY_SELECTORS)
                    return frame
            await _click_first(page, PLAY_SELECTORS)
            await asyncio.sleep(2)
        raise VideoNotFound("페이지에서 영상(<video>)을 찾지 못했습니다. 잠긴 항목이거나 플레이어 구조가 바뀌었을 수 있습니다.")


async def _video_state(frame: Frame, action: str) -> VideoState | None:
    data = await frame.evaluate(_VIDEO_JS, action)
    return VideoState(**data) if data else None


async def _accept_dialog(dialog: Dialog) -> None:
    # Preserve the player's suggested resume time in native prompt dialogs.
    await dialog.accept(dialog.default_value if dialog.type == "prompt" else None)


async def _confirm_resume(page: Page) -> bool:
    for frame in page.frames:
        # Preserve known player-specific controls even without dialog semantics.
        candidates = [frame.locator(".confirm-ok-btn, .vc-confirm-ok-btn")]
        dialogs = frame.locator(RESUME_DIALOG_SELECTORS).filter(has_text=RESUME_PROMPT)
        candidates.append(dialogs.locator(", ".join(CONFIRM_SELECTORS)))
        for buttons in candidates:
            try:
                matches = await buttons.all()
            except Exception:  # Player frames may be replaced while a dialog opens.
                continue
            for button in matches:
                try:
                    if await button.is_visible() and await button.is_enabled():
                        await button.click(timeout=2000)
                        log.info("이어보기 확인 버튼을 눌렀습니다.")
                        return True
                except Exception:
                    continue
    return False


async def _click_first(page: Page, selectors: list[str]) -> bool:
    for frame in page.frames:
        for sel in selectors:
            try:
                loc = frame.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=2000)
                    return True
            except Exception:
                continue
    return False


def _assert_logged_in(page: Page) -> None:
    if "/login" in page.url:
        raise SessionExpired("재생 페이지가 로그인 화면으로 이동했습니다.")


async def verify_completed(client, video: VideoTodo, settings: Settings | None = None) -> bool:
    """modules API를 다시 조회해 해당 항목이 완료로 바뀌었는지 확인한다(서버 반영 지연을 고려해 여러 번)."""
    settings = settings or get_settings()
    for attempt in range(settings.verify_attempts):
        if attempt:
            await asyncio.sleep(settings.verify_interval_seconds)
        modules = await client.get_modules(video.course_id)
        for module in modules:
            for item in module.module_items:
                if item.module_item_id == video.module_item_id and item.completed:
                    return True
    return False


# ----------------------------------------------------------------------
# CLI
#   python -m app.lms.player inspect <view_url>   플레이어 구조 관찰(브라우저 창이 뜸)
#   python -m app.lms.player play <course_id>      첫 미수강 영상 1개 재생 후 완료 확인
# ----------------------------------------------------------------------


async def _inspect(view_url: str) -> None:
    from app.lms.client import LmsClient

    settings = get_settings()
    async with await LmsClient.login(settings.lms_id, settings.lms_password) as client:
        session = client.session
    async with launch_browser(headless=False) as browser:
        context = await new_session_context(browser, session)
        page = await context.new_page()
        page.on(
            "request",
            lambda r: print(f"[REQ] {r.method} {r.url[:160]}")
            if r.resource_type in ("xhr", "fetch", "beacon", "ping") or r.method == "POST"
            else None,
        )
        await page.goto(view_url)
        print("브라우저 창에서 재생해 보세요. 30초마다 프레임과 영상 상태를 출력합니다. Ctrl+C로 종료.")
        while True:
            await asyncio.sleep(30)
            for frame in page.frames:
                info = await frame.evaluate(
                    """() => ({videos: Array.from(document.querySelectorAll('video')).map(v =>
                         ({src: (v.currentSrc||'').slice(0,100), t: v.currentTime, d: v.duration, cls: v.className})),
                       buttons: Array.from(document.querySelectorAll('button,[role=button],[class*=btn]')).slice(0,30)
                         .map(b => (b.className||'') + ' | ' + (b.innerText||'').trim().slice(0,20))})"""
                )
                print(f"\n[FRAME] {frame.url[:120]}\n  videos={info['videos']}\n  buttons={info['buttons']}")


async def _play_one(course_id: int) -> None:
    from datetime import UTC, datetime

    from app.lms.client import LmsClient
    from app.lms.models import Course
    from app.lms.todo import pending_videos

    settings = get_settings()
    async with await LmsClient.login(settings.lms_id, settings.lms_password) as client:
        modules = await client.get_modules(course_id)
        videos = pending_videos(Course(id=course_id, name=str(course_id)), modules, datetime.now(UTC))
        if not videos:
            print("기간 안의 미수강 영상이 없습니다.")
            return
        video = min(videos, key=lambda v: v.duration)
        print(f"재생: {video.module_title} / {video.title} ({video.duration / 60:.1f}분)")

        async def progress(v, t, d):
            print(f"  {t:7.0f}s / {d:7.0f}s", end="\r")

        async with launch_browser() as browser:
            result = await VideoPlayer(browser).play(client.session, video, on_progress=progress)
        print(f"\n재생 종료: {result.watched_seconds:.0f}초 소요, 새로고침 {result.reloads}회")
        print("완료 확인:", "성공" if await verify_completed(client, video) else "실패")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    cmd, arg = sys.argv[1], sys.argv[2]
    asyncio.run(_inspect(arg) if cmd == "inspect" else _play_one(int(arg)))
