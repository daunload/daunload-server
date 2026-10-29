"""실제 브라우저(Chrome)로 가짜 플레이어 페이지를 재생해 본다. Chrome이 없으면 건너뛴다."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.lms.auth import LmsSession
from app.lms.models import Module
from app.lms.player import VideoPlayer, launch_browser, verify_completed
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
    try:
        async with launch_browser(SETTINGS, headless=True) as b:
            yield b
    except Exception as e:  # Chrome 미설치 등
        pytest.skip(f"브라우저를 띄울 수 없습니다: {e}")


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
