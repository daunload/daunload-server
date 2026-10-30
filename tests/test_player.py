"""실제 브라우저(Chrome)로 가짜 플레이어 페이지를 재생해 본다. Chrome이 없으면 건너뛴다."""

from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.lms.auth import LmsSession
from app.lms.errors import PlaybackError
from app.lms.models import Module
from app.lms.player import VideoPlayer, _accept_dialog, _confirm_resume, launch_browser, verify_completed
from app.lms.todo import VideoTodo

PAGE = (Path(__file__).parent / "fixtures" / "player" / "index.html").resolve().as_uri()

SETTINGS = Settings(playback_extra_seconds=0, playback_stall_seconds=10, verify_attempts=2, verify_interval_seconds=0)


def _video(url: str = PAGE, duration: float = 4) -> VideoTodo:
    now = datetime.now(UTC)
    return VideoTodo(
        kind="video", course_id=1, course_name="c", module_id=1, module_title="1주차", module_position=1,
        module_item_id=10, item_position=1, item_id=100, title="테스트 영상", duration=duration,
        view_url=url, due_at=None, deadline=now, unlock_at=None,
    )


@pytest.fixture
async def browser():
    async with AsyncExitStack() as stack:
        try:
            b = await stack.enter_async_context(launch_browser(SETTINGS, headless=True))
        except Exception as e:  # Chrome 미설치 등. 재생 중 오류는 skip하지 않는다.
            pytest.skip(f"브라우저를 띄울 수 없습니다: {e}")
        yield b


@pytest.mark.browser
async def test_plays_video_in_iframe_to_the_end(browser):
    progress = []

    async def on_progress(v, t, d):
        progress.append((t, d))

    session = LmsSession(user_id="1", bearer_token="b", cookies=[])
    result = await VideoPlayer(browser, SETTINGS, poll_seconds=0.5).play(session, _video(), on_progress=on_progress)

    # 끝까지(ended) 재생해야 하고, 페이지가 2배속을 걸어도 1배속으로 고정되어 4초 이상 걸려야 한다.
    assert result.position >= 3.9
    assert result.watched_seconds >= 3.9
    assert progress and progress[-1][1] == pytest.approx(4, abs=0.5)


@pytest.mark.browser
async def test_ignores_commons_preloader_and_clicks_lecture_start(browser):
    context = await browser.new_context()
    try:
        page = await context.new_page()
        clip = Path(__file__).parent / "fixtures" / "player" / "clip.webm"
        await page.route("**/viewer/uniplayer/preloader.mp4", lambda route: route.fulfill(path=str(clip)))
        await page.route("**/lecture.webm", lambda route: route.fulfill(path=str(clip)))
        await page.route("**/index.html", lambda route: route.fulfill(
            content_type="text/html", body="""
            <video id="preloader" hidden preload="auto" src="/viewer/uniplayer/preloader.mp4"></video>
            <button class="vc-front-screen-play-btn" onclick="
                this.remove(); window.lectureStarted = true;
                const v = document.createElement('video');
                v.src = '/lecture.webm'; v.muted = true;
                document.body.appendChild(v); v.play();
            ">Start lecture</button>
            """))
        observations = []

        async def progress(v, t, d):
            observations.append(await page.evaluate("""() => ({
                started: window.lectureStarted,
                preloaderTime: document.querySelector('#preloader').currentTime
            })"""))

        result = await VideoPlayer(browser, SETTINGS, poll_seconds=0.5)._play_on_page(
            page, _video("https://player.test/index.html"), progress,
        )
        assert result.position >= 3.9
        assert observations and all(o["started"] and o["preloaderTime"] == 0 for o in observations)
    finally:
        await context.close()


@pytest.mark.browser
async def test_short_media_does_not_count_as_lecture_end(browser):
    session = LmsSession(user_id="1", bearer_token="b", cookies=[])
    with pytest.raises(PlaybackError, match="강의 길이와 감지한 영상 길이가 다릅니다"):
        await VideoPlayer(browser, SETTINGS, poll_seconds=0.5).play(session, _video(duration=2400))


@pytest.mark.browser
@pytest.mark.parametrize("dialog_attributes", ['role="dialog"', 'class="modal"'])
async def test_confirms_delayed_resume_in_iframe(browser, dialog_attributes):
    context = await browser.new_context()
    try:
        page = await context.new_page()
        clip = Path(__file__).parent / "fixtures" / "player" / "clip.webm"
        await page.route("**/lecture.webm", lambda route: route.fulfill(path=str(clip)))
        await page.route("**/index.html", lambda route: route.fulfill(
            content_type="text/html", body='<iframe src="/player.html"></iframe>',
        ))
        await page.route("**/player.html", lambda route: route.fulfill(
            content_type="text/html", body=f"""
            <meta charset="utf-8">
            <video muted preload="auto"></video>
            <script>
              window.resumed = false;
              const video = document.querySelector('video');
              // Blob URLs support seeking without a test HTTP Range server.
              fetch('/lecture.webm').then(r => r.blob()).then(blob => {{
                video.src = URL.createObjectURL(blob);
              }});
              let scheduled = false;
              video.addEventListener('play', () => {{
                if (!window.resumed) {{
                  video.pause();
                  if (!scheduled) {{ scheduled = true; setTimeout(showResume, 800); }}
                }}
              }});
              function showResume() {{
                const dialog = document.createElement('div');
                dialog.setAttribute('data-resume', '');
                dialog.innerHTML = '<p>00:01부터 보시겠습니까?</p><button>확인</button>';
                const template = document.createElement('div');
                template.innerHTML = '<div {dialog_attributes}></div>';
                const wrapper = template.firstChild;
                wrapper.appendChild(dialog);
                document.body.appendChild(wrapper);
                dialog.querySelector('button').onclick = () => {{
                  window.resumed = true;
                  video.currentTime = 1.2;
                  wrapper.remove();
                  video.play();
                }};
              }}
            </script>
            """,
        ))
        resumed_positions = []

        async def progress(v, t, d):
            frame = page.frames[1]
            if await frame.evaluate("window.resumed && !document.querySelector('video').seeking"):
                resumed_positions.append(t)

        result = await VideoPlayer(browser, SETTINGS, poll_seconds=0.2)._play_on_page(
            page, _video("https://player.test/index.html"), progress,
        )
        assert resumed_positions and resumed_positions[0] >= 1.2
        assert result.position >= 3.9
        assert result.reloads == 0
    finally:
        await context.close()


@pytest.mark.browser
async def test_resume_confirmation_leaves_unrelated_buttons_alone(browser):
    page = await browser.new_page()
    try:
        await page.set_content("""
            <button onclick="window.clicked = true">확인</button>
            <div role="dialog">삭제하시겠습니까?
              <button onclick="window.clicked = true">확인</button>
            </div>
            <div role="dialog" hidden>10분부터 보시겠습니까?<button>확인</button></div>
        """)
        assert await _confirm_resume(page) is False
        assert await page.evaluate("!!window.clicked") is False
    finally:
        await page.close()


@pytest.mark.browser
async def test_native_resume_prompt_preserves_suggested_position(browser):
    page = await browser.new_page()
    try:
        page.on("dialog", _accept_dialog)
        assert await page.evaluate("prompt('몇 분부터 보시겠습니까?', '12:30')") == "12:30"
        assert await page.evaluate("confirm('이어서 보시겠습니까?')") is True
    finally:
        await page.close()


class _FakeClient:
    def __init__(self, completed_on_call: int):
        self.calls = 0
        self.completed_on_call = completed_on_call

    async def get_modules(self, course_id):
        self.calls += 1
        done = self.calls >= self.completed_on_call
        return [Module.model_validate({"module_id": 1, "module_items": [{"module_item_id": 10, "completed": done}]})]


async def test_verify_completed_retries_until_reflected():
    client = _FakeClient(completed_on_call=2)
    assert await verify_completed(client, _video(), SETTINGS) is True
    assert client.calls == 2


async def test_verify_completed_gives_up():
    client = _FakeClient(completed_on_call=99)
    assert await verify_completed(client, _video(), SETTINGS) is False
    assert client.calls == SETTINGS.verify_attempts
