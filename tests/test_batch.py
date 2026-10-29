import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.lms.batch import BatchHooks, BatchRunner, build_plan, order_videos, select_videos
from app.lms.errors import PlaybackError, SessionExpired, VideoNotFound
from app.lms.models import Module
from app.lms.todo import VideoTodo

NOW = datetime.now(UTC)
SETTINGS = Settings(playback_extra_seconds=30, playback_gap_seconds=(10, 30))


def V(course, module_pos, item_pos, *, deadline_h=48, duration=600, mid=None) -> VideoTodo:
    item_id = mid or course * 1000 + module_pos * 10 + item_pos
    return VideoTodo(
        kind="video", course_id=course, course_name=f"과목{course}", module_id=course * 100 + module_pos,
        module_title=f"{module_pos}주차", module_position=module_pos, module_item_id=item_id, item_position=item_pos,
        item_id=item_id, title=f"c{course}-w{module_pos}-{item_pos}", duration=duration, view_url="u",
        due_at=None, deadline=NOW + timedelta(hours=deadline_h), unlock_at=None,
    )


# --- 선정·정렬·계획 ------------------------------------------------------------


def test_order_within_course_follows_module_order():
    vs = [V(1, 2, 1), V(1, 1, 2), V(1, 1, 1)]
    assert [v.title for v in order_videos(vs)] == ["c1-w1-1", "c1-w1-2", "c1-w2-1"]


def test_order_courses_by_earliest_deadline():
    vs = [V(1, 1, 1, deadline_h=72), V(2, 1, 1, deadline_h=10), V(2, 2, 1, deadline_h=100)]
    assert [v.course_id for v in order_videos(vs)] == [2, 2, 1]


def test_select_scopes():
    vs = [V(1, 1, 1), V(1, 2, 1), V(2, 1, 1)]
    assert len(select_videos(vs, "all")) == 3
    assert [v.course_id for v in select_videos(vs, "course", 1)] == [1, 1]
    assert [v.title for v in select_videos(vs, "module", 1, 102)] == ["c1-w2-1"]
    with pytest.raises(ValueError):
        select_videos(vs, "module", 1)


def test_build_plan_times_and_warnings():
    vs = [V(1, 1, 1, duration=3600, deadline_h=2), V(1, 1, 2, duration=3600, deadline_h=2)]
    plan = build_plan(vs, NOW, SETTINGS)
    first, second = plan.items
    assert first.expected_end == NOW + timedelta(seconds=3630)
    assert second.expected_start == first.expected_end + timedelta(seconds=20)
    assert not first.at_risk and second.at_risk
    assert len(plan.warnings) == 1
    assert plan.total_seconds == pytest.approx(3630 * 2 + 20)


def test_build_plan_empty():
    plan = build_plan([], NOW, SETTINGS)
    assert plan.items == [] and plan.total_seconds == 0 and plan.expected_end is None


# --- 실행 ----------------------------------------------------------------------


class FakeLms:
    """modules API 흉내. completed 집합에 있는 항목은 완료로 응답한다."""

    def __init__(self, videos):
        self.videos = videos
        self.completed: set[int] = set()
        self.session = object()
        self.relogins = 0

    async def get_modules(self, course_id):
        modules = {}
        for v in self.videos:
            if v.course_id != course_id:
                continue
            m = modules.setdefault(v.module_id, {"module_id": v.module_id, "title": v.module_title,
                                                 "position": v.module_position, "module_items": []})
            m["module_items"].append({
                "module_item_id": v.module_item_id, "position": v.item_position,
                "completed": v.module_item_id in self.completed,
                "content_data": {"item_id": v.item_id, "item_content_type": "commons", "title": v.title,
                                 "due_at": v.deadline.isoformat(),
                                 "item_content_data": {"duration": v.duration, "view_url": v.view_url}},
            })
        return [Module.model_validate(m) for m in modules.values()]

    async def relogin(self):
        self.relogins += 1
        self.session = object()


class FakePlayer:
    def __init__(self, lms: FakeLms, behaviors: dict | None = None):
        self.lms = lms
        self.behaviors = behaviors or {}  # module_item_id -> [동작...] (예외 또는 "nocomplete")
        self.played: list[str] = []

    async def play(self, session, video, *, on_progress=None):
        self.played.append(video.title)
        queue = self.behaviors.get(video.module_item_id, [])
        action = queue.pop(0) if queue else "ok"
        if isinstance(action, Exception):
            raise action
        if action == "ok":
            self.lms.completed.add(video.module_item_id)


async def _verify(client, video):
    return video.module_item_id in client.completed


async def _nosleep(_):
    return None


def _runner(lms, player, **kw):
    return BatchRunner(lms, player, settings=SETTINGS, verify=_verify, sleep=_nosleep, **kw)


async def test_runs_all_in_order_and_reports():
    vs = order_videos([V(1, 1, 1), V(1, 1, 2), V(2, 1, 1)])
    lms = FakeLms(vs)
    player = FakePlayer(lms)
    done = []
    result = await _runner(lms, player, hooks=BatchHooks(on_item_done=lambda o: _append(done, o))).run(vs)
    assert player.played == [v.title for v in vs]
    assert [o.status for o in result.outcomes] == ["done"] * 3
    assert len(done) == 3
    assert result.summary.startswith("완료 3")


async def _append(lst, item):
    lst.append(item)


async def test_already_completed_items_are_skipped():
    vs = [V(1, 1, 1), V(1, 1, 2)]
    lms = FakeLms(vs)
    lms.completed.add(vs[0].module_item_id)
    result = await _runner(lms, FakePlayer(lms)).run(vs)
    assert [o.status for o in result.outcomes] == ["skipped", "done"]


async def test_retry_once_then_success():
    vs = [V(1, 1, 1)]
    lms = FakeLms(vs)
    player = FakePlayer(lms, {vs[0].module_item_id: ["nocomplete"]})
    result = await _runner(lms, player).run(vs)
    assert result.outcomes[0].status == "done" and result.outcomes[0].attempts == 2


async def test_failure_moves_on_to_next_item():
    vs = [V(1, 1, 1), V(2, 1, 1)]
    lms = FakeLms(vs)
    player = FakePlayer(lms, {vs[0].module_item_id: [PlaybackError("x"), PlaybackError("x")]})
    result = await _runner(lms, player).run(vs)
    assert [o.status for o in result.outcomes] == ["failed", "done"]


async def test_locked_after_failure_halts_rest_of_course():
    vs = [V(1, 1, 1), V(1, 1, 2), V(1, 2, 1), V(2, 1, 1)]
    lms = FakeLms(vs)
    player = FakePlayer(lms, {
        vs[0].module_item_id: [PlaybackError("멈춤"), PlaybackError("멈춤")],
        vs[1].module_item_id: [VideoNotFound("잠김"), VideoNotFound("잠김")],
    })
    result = await _runner(lms, player).run(vs)
    assert [o.status for o in result.outcomes] == ["failed", "locked", "locked", "done"]
    assert vs[2].title not in player.played


async def test_session_expired_relogins_and_replays_same_item():
    vs = [V(1, 1, 1)]
    lms = FakeLms(vs)
    player = FakePlayer(lms, {vs[0].module_item_id: [SessionExpired("x")]})
    result = await _runner(lms, player).run(vs)
    assert lms.relogins == 1
    assert result.outcomes[0].status == "done" and result.outcomes[0].attempts == 1


async def test_cancel_marks_remaining_cancelled():
    vs = [V(1, 1, 1), V(1, 1, 2), V(1, 1, 3)]
    lms = FakeLms(vs)
    cancel = asyncio.Event()

    async def on_done(o):
        cancel.set()

    result = await _runner(lms, FakePlayer(lms), hooks=BatchHooks(on_item_done=on_done)).run(vs, cancel=cancel)
    assert [o.status for o in result.outcomes] == ["done", "cancelled", "cancelled"]


async def test_cancel_during_playback_stops_current_item():
    vs = [V(1, 1, 1), V(1, 1, 2)]
    lms = FakeLms(vs)
    cancel = asyncio.Event()
    stopped = asyncio.Event()

    class SlowPlayer(FakePlayer):
        async def play(self, session, video, *, on_progress=None):
            self.played.append(video.title)
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                stopped.set()
                raise

    async def on_start(position, video):
        asyncio.get_running_loop().call_later(0.05, cancel.set)

    result = await _runner(lms, SlowPlayer(lms), hooks=BatchHooks(on_item_start=on_start)).run(vs, cancel=cancel)
    assert stopped.is_set()
    assert [o.status for o in result.outcomes] == ["cancelled", "cancelled"]


async def test_resume_from_position():
    vs = [V(1, 1, 1), V(1, 1, 2), V(1, 1, 3)]
    lms = FakeLms(vs)
    player = FakePlayer(lms)
    result = await _runner(lms, player).run(vs, start_position=1)
    assert player.played == [vs[1].title, vs[2].title]
    assert [o.position for o in result.outcomes] == [1, 2]
