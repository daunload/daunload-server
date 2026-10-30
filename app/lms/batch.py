"""일괄 재생: 기간 안의 미수강 영상을 묶어 순서대로 실제 재생하고 완료를 확인한다.

순서 규칙
- 과목 안에서는 LMS 화면 순서(주차 → 모듈 안 항목)를 따른다. 선수 조건이 있을 수 있기 때문이다.
- 여러 과목을 묶으면 가장 이른 마감이 있는 과목부터 재생한다.

실패 처리
- 항목마다 재생 + 완료 확인을 최대 2번 시도한다. 그래도 실패하면 failed로 두고 다음 항목으로 간다.
- 같은 과목에서 실패가 난 뒤 다음 항목의 영상을 찾지 못하면 선수 조건으로 잠긴 것으로 보고
  그 과목의 나머지 항목을 locked로 건너뛴다.
- 세션이 만료되면 재로그인하고 같은 항목을 다시 재생한다.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from app.config import Settings, get_settings
from app.lms.errors import PlaybackError, SessionExpired, VideoNotFound
from app.lms.models import Course, Module
from app.lms.todo import VideoTodo, pending_videos

log = logging.getLogger(__name__)

# week: 이번 주 안에 마감되는 영상(마감 범위는 DB 조회 쪽에서 거른다)
Scope = Literal["module", "course", "all", "week"]
ItemStatus = Literal["done", "failed", "skipped", "locked", "cancelled"]

MAX_ATTEMPTS = 2


# ----------------------------------------------------------------------
# 대상 선정과 계획
# ----------------------------------------------------------------------


class ModuleSource(Protocol):
    async def get_modules(self, course_id: int) -> list[Module]: ...


def select_videos(
    videos: list[VideoTodo], scope: Scope, course_id: int | None = None, module_id: int | None = None
) -> list[VideoTodo]:
    if scope == "module":
        if course_id is None or module_id is None:
            raise ValueError("module 범위에는 course_id와 module_id가 필요합니다.")
        return [v for v in videos if v.course_id == course_id and v.module_id == module_id]
    if scope == "course":
        if course_id is None:
            raise ValueError("course 범위에는 course_id가 필요합니다.")
        return [v for v in videos if v.course_id == course_id]
    if scope == "week" and course_id is not None:
        return [v for v in videos if v.course_id == course_id]
    return list(videos)


def order_videos(videos: list[VideoTodo]) -> list[VideoTodo]:
    by_course: dict[int, list[VideoTodo]] = {}
    for v in videos:
        by_course.setdefault(v.course_id, []).append(v)
    courses = sorted(by_course.values(), key=lambda vs: (min(v.deadline for v in vs), vs[0].course_id))
    return [v for vs in courses for v in sorted(vs, key=lambda v: v.order_key)]


@dataclass(frozen=True)
class PlannedItem:
    position: int
    video: VideoTodo
    expected_start: datetime
    expected_end: datetime

    @property
    def at_risk(self) -> bool:
        """예상 종료가 마감보다 늦으면 마감 전에 끝낼 수 없다."""
        return self.expected_end > self.video.deadline


@dataclass(frozen=True)
class BatchPlan:
    items: list[PlannedItem]
    total_seconds: float
    expected_end: datetime | None

    @property
    def warnings(self) -> list[str]:
        return [
            f"'{i.video.title}'은(는) 예상 종료 {i.expected_end:%m/%d %H:%M}가 마감 {i.video.deadline:%m/%d %H:%M}보다 늦습니다."
            for i in self.items
            if i.at_risk
        ]


def build_plan(videos: list[VideoTodo], now: datetime, settings: Settings | None = None) -> BatchPlan:
    settings = settings or get_settings()
    gap = sum(settings.playback_gap_seconds) / 2
    cursor = now
    items: list[PlannedItem] = []
    for i, v in enumerate(order_videos(videos)):
        start = cursor
        end = start + timedelta(seconds=v.duration + settings.playback_extra_seconds)
        items.append(PlannedItem(position=i, video=v, expected_start=start, expected_end=end))
        cursor = end + timedelta(seconds=gap)
    total = (items[-1].expected_end - now).total_seconds() if items else 0.0
    return BatchPlan(items=items, total_seconds=total, expected_end=items[-1].expected_end if items else None)


async def collect_videos(client, courses: list[Course], now: datetime) -> list[VideoTodo]:
    videos: list[VideoTodo] = []
    for course in courses:
        videos.extend(pending_videos(course, await client.get_modules(course.id), now))
    return videos


# ----------------------------------------------------------------------
# 실행
# ----------------------------------------------------------------------


@dataclass
class ItemOutcome:
    position: int
    video: VideoTodo
    status: ItemStatus
    attempts: int = 0
    error: str | None = None


@dataclass
class BatchResult:
    outcomes: list[ItemOutcome] = field(default_factory=list)

    def count(self, status: ItemStatus) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def summary(self) -> str:
        return (
            f"완료 {self.count('done')} · 실패 {self.count('failed')} · 이미 완료 {self.count('skipped')}"
            f" · 잠김 {self.count('locked')} · 취소 {self.count('cancelled')}"
        )


class Player(Protocol):
    async def play(self, session, video: VideoTodo, *, on_progress=None): ...


@dataclass
class BatchHooks:
    on_item_start: Callable[[int, VideoTodo], Awaitable[None]] | None = None
    on_item_done: Callable[[ItemOutcome], Awaitable[None]] | None = None
    on_progress: Callable[[VideoTodo, float, float], Awaitable[None]] | None = None


class BatchRunner:
    def __init__(
        self,
        client,
        player: Player,
        *,
        settings: Settings | None = None,
        hooks: BatchHooks | None = None,
        verify: Callable[..., Awaitable[bool]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        from app.lms.player import verify_completed

        self.client = client
        self.player = player
        self.settings = settings or get_settings()
        self.hooks = hooks or BatchHooks()
        self.verify = verify or (lambda c, v: verify_completed(c, v, self.settings))
        self.sleep = sleep

    async def run(
        self,
        videos: list[VideoTodo],
        *,
        start_position: int = 0,
        cancel: asyncio.Event | None = None,
        end_position: int | None = None,
        failed_courses: set[int] | None = None,
        locked_courses: set[int] | None = None,
    ) -> BatchResult:
        """videos는 이미 order_videos로 정렬된 목록이어야 한다. start_position부터 이어서 재생한다."""
        result = BatchResult()
        failed_courses = set(failed_courses or ())
        locked_courses = set(locked_courses or ())
        first = True

        for position, video in enumerate(videos):
            if end_position is not None and position >= end_position:
                break
            if position < start_position:
                continue
            if cancel and cancel.is_set():
                result.outcomes.append(ItemOutcome(position, video, "cancelled"))
                continue
            if video.course_id in locked_courses:
                outcome = ItemOutcome(position, video, "locked", error="앞 항목 실패로 잠긴 것으로 판단")
                await self._done(result, outcome)
                continue

            current = await self._refresh(video)
            if current is None:
                await self._done(result, ItemOutcome(position, video, "skipped"))
                continue

            if not first:
                await self.sleep(random.uniform(*self.settings.playback_gap_seconds))
            first = False

            if self.hooks.on_item_start:
                await self.hooks.on_item_start(position, current)
            outcome = await self._play_with_retry(position, current, cancel)
            if outcome.status == "failed":
                if video.course_id in failed_courses and outcome.error and outcome.error.startswith("VideoNotFound"):
                    outcome.status = "locked"
                    locked_courses.add(video.course_id)
                failed_courses.add(video.course_id)
            await self._done(result, outcome)
        return result

    async def _done(self, result: BatchResult, outcome: ItemOutcome) -> None:
        result.outcomes.append(outcome)
        if self.hooks.on_item_done:
            await self.hooks.on_item_done(outcome)

    async def _refresh(self, video: VideoTodo) -> VideoTodo | None:
        """재생 직전에 다시 조회해서 이미 완료됐거나 기간이 지난 항목은 건너뛴다."""
        modules = await self._call(self.client.get_modules, video.course_id)
        course = Course(id=video.course_id, name=video.course_name)
        for v in pending_videos(course, modules, datetime.now(UTC)):
            if v.module_item_id == video.module_item_id:
                return v
        return None

    async def _play_with_retry(self, position: int, video: VideoTodo, cancel: asyncio.Event | None) -> ItemOutcome:
        error: str | None = None
        attempts = 0
        relogged = False
        while attempts < MAX_ATTEMPTS:
            if cancel and cancel.is_set():
                return ItemOutcome(position, video, "cancelled", attempts, error)
            attempts += 1
            try:
                if not await self._play_unless_cancelled(video, cancel):
                    return ItemOutcome(position, video, "cancelled", attempts, "재생 중 취소")
                if await self.verify(self.client, video):
                    return ItemOutcome(position, video, "done", attempts)
                error = "재생은 끝났지만 LMS에서 완료로 바뀌지 않았습니다."
            except SessionExpired:
                if relogged:
                    error = "SessionExpired: 재로그인 후에도 세션이 유지되지 않습니다."
                    break
                await self.client.relogin()
                relogged = True
                attempts -= 1  # 세션 만료는 재시도 횟수에 넣지 않는다
                continue
            except VideoNotFound as e:
                error = f"VideoNotFound: {e}"
            except PlaybackError as e:
                error = f"PlaybackError: {e}"
            log.warning("재생 실패(%d/%d) %s: %s", attempts, MAX_ATTEMPTS, video.title, error)
        return ItemOutcome(position, video, "failed", attempts, error)

    async def _play_unless_cancelled(self, video: VideoTodo, cancel: asyncio.Event | None) -> bool:
        """재생이 끝나면 True, 도중에 취소되면 재생을 멈추고(브라우저 컨텍스트 정리) False."""
        play = asyncio.ensure_future(self.player.play(self.client.session, video, on_progress=self.hooks.on_progress))
        if cancel is None:
            await play
            return True
        waiter = asyncio.ensure_future(cancel.wait())
        try:
            await asyncio.wait({play, waiter}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            play.cancel()
            await asyncio.gather(play, return_exceptions=True)
            raise
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        if play.done():
            play.result()  # 재생 중 발생한 예외를 그대로 올린다
            return True
        play.cancel()
        try:
            await play
        except asyncio.CancelledError:
            pass
        return False

    async def _call(self, fn, *args):
        try:
            return await fn(*args)
        except SessionExpired:
            await self.client.relogin()
            return await fn(*args)


# ----------------------------------------------------------------------
# CLI
#   python -m app.lms.batch preview [--course ID] [--module ID]
#   python -m app.lms.batch run     [--course ID] [--module ID]
# ----------------------------------------------------------------------


async def _main(argv: list[str]) -> None:
    import argparse

    from app.lms.client import LmsClient
    from app.lms.player import VideoPlayer, launch_browser

    parser = argparse.ArgumentParser(prog="python -m app.lms.batch")
    parser.add_argument("command", choices=["preview", "run"])
    parser.add_argument("--course", type=int)
    parser.add_argument("--module", type=int)
    args = parser.parse_args(argv)
    scope: Scope = "module" if args.module else "course" if args.course else "all"

    settings = get_settings()
    now = datetime.now(UTC)
    async with await LmsClient.login(settings.lms_id, settings.lms_password) as client:
        term = await client.get_current_term()
        courses = await client.get_courses(term.id)
        if args.course:
            courses = [c for c in courses if c.id == args.course]
        videos = select_videos(await collect_videos(client, courses, now), scope, args.course, args.module)
        plan = build_plan(videos, now, settings)
        for item in plan.items:
            v = item.video
            flag = " ⚠" if item.at_risk else ""
            print(f"{item.position + 1:2}. [{v.course_name}] {v.module_title} / {v.title} "
                  f"({v.duration / 60:.0f}분, 마감 {v.deadline.astimezone():%m/%d %H:%M}){flag}")
        print(f"총 {len(plan.items)}개, 약 {plan.total_seconds / 60:.0f}분")
        for w in plan.warnings:
            print("경고:", w)
        if args.command == "preview" or not plan.items:
            return

        async def on_done(o: ItemOutcome):
            print(f"  → {o.position + 1}. {o.video.title}: {o.status}" + (f" ({o.error})" if o.error else ""))

        async def on_progress(v, t, d):
            print(f"    {t:6.0f}/{d:6.0f}s", end="\r")

        async with launch_browser(settings) as browser:
            runner = BatchRunner(client, VideoPlayer(browser, settings), settings=settings,
                                 hooks=BatchHooks(on_item_done=on_done, on_progress=on_progress))
            result = await runner.run([i.video for i in plan.items])
        print("결과:", result.summary)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main(sys.argv[1:]))
