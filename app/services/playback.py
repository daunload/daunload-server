"""일괄 재생 묶음의 생성(API)과 실행(워커)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.config import Settings, get_settings
from app.db.base import sessionmaker, utcnow
from app.db.models import PlaybackBatch, PlaybackJob, TodoRow, User
from app.lms.batch import BatchHooks, BatchPlan, BatchRunner, ItemOutcome, Scope, build_plan, select_videos
from app.lms.todo import VideoTodo, parse_dt
from app.services.lms_access import lms_client_for
from app.services.notify import notify_user
from app.services.sync import video_from_row
from app.services.week import week_bounds

log = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")


class ActiveBatchExists(Exception):
    def __init__(self, batch_id: int):
        super().__init__(f"이미 진행 중인 일괄 재생이 있습니다 (id={batch_id}).")
        self.batch_id = batch_id


async def pending_videos_from_db(
    db: AsyncSession,
    user_id: int,
    scope: Scope | str,
    course_id: int | None = None,
    module_id: int | None = None,
    now: datetime | None = None,
    course_ids: set[int] | None = None,
) -> list[VideoTodo]:
    now = now or utcnow()
    stmt = select(TodoRow).where(
        TodoRow.user_id == user_id, TodoRow.kind == "video", TodoRow.active, TodoRow.deadline > now
    )
    if course_ids is not None:
        stmt = stmt.where(TodoRow.lms_course_id.in_(course_ids))
    if scope == "week":
        stmt = stmt.where(TodoRow.deadline < week_bounds(now)[1])
    rows = (await db.scalars(stmt)).all()
    videos = [video_from_row(r) for r in rows if r.unlock_at is None or r.unlock_at <= now]
    return select_videos(videos, "all" if scope == "auto" else scope, course_id, module_id)  # type: ignore[arg-type]


async def preview(db: AsyncSession, user_id: int, scope: Scope, course_id: int | None, module_id: int | None) -> BatchPlan:
    now = utcnow()
    videos = await pending_videos_from_db(db, user_id, scope, course_id, module_id, now)
    plan = build_plan(videos, now)
    estimate = (await queue_estimates(db, videos)).get(-1)
    if not estimate:
        return plan
    items = [replace(item, **estimate["job_estimates"][item.position]) for item in plan.items]
    return BatchPlan(items, (items[-1].expected_end - now).total_seconds(), items[-1].expected_end)


async def active_batch(db: AsyncSession, user_id: int) -> PlaybackBatch | None:
    return await db.scalar(
        select(PlaybackBatch).where(PlaybackBatch.user_id == user_id, PlaybackBatch.status.in_(ACTIVE_STATUSES))
    )


async def create_batch(
    db: AsyncSession,
    user_id: int,
    scope: str,
    videos: list[VideoTodo],
    course_id: int | None = None,
    module_id: int | None = None,
) -> PlaybackBatch:
    # 동일 계정의 API·자동 등록을 하나의 트랜잭션으로 직렬화한다.
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    if existing := await active_batch(db, user_id):
        raise ActiveBatchExists(existing.id)
    plan = build_plan(videos, utcnow())
    batch = PlaybackBatch(
        user_id=user_id, scope=scope, lms_course_id=course_id, module_id=module_id,
        status="queued", total=len(plan.items), expected_end=plan.expected_end,
    )
    db.add(batch)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        if existing := await active_batch(db, user_id):
            raise ActiveBatchExists(existing.id) from None
        raise
    for item in plan.items:
        v = item.video
        db.add(PlaybackJob(
            batch_id=batch.id, user_id=user_id, position=item.position, lms_course_id=v.course_id,
            module_item_id=v.module_item_id, title=v.title, duration=v.duration, deadline=v.deadline,
            video=_video_to_json(v), status="queued",
        ))
    await db.commit()
    return batch


def _video_to_json(v: VideoTodo) -> dict:
    data = asdict(v)
    for k in ("due_at", "deadline", "unlock_at"):
        data[k] = data[k].isoformat() if data[k] else None
    return data


def _video_from_json(data: dict) -> VideoTodo:
    data = dict(data)
    for k in ("due_at", "deadline", "unlock_at"):
        data[k] = parse_dt(data[k])
    return VideoTodo(**data)


# ----------------------------------------------------------------------
# 워커에서 실행
# ----------------------------------------------------------------------


async def run_batch(
    batch_id: int, player, settings: Settings | None = None, cancel_poll_seconds: float = 1,
    *, one_item: bool = False,
) -> str:
    """묶음을 실행하고 최종 상태를 반환한다. player는 VideoPlayer(또는 같은 인터페이스)."""
    settings = settings or get_settings()
    sm = sessionmaker()

    async with sm() as db:
        batch = await db.scalar(select(PlaybackBatch).where(PlaybackBatch.id == batch_id).with_for_update())
        if batch is None or batch.status not in ACTIVE_STATUSES:
            return batch.status if batch else "missing"
        jobs = (await db.scalars(
            select(PlaybackJob).where(PlaybackJob.batch_id == batch_id).order_by(PlaybackJob.position)
        )).all()
        # 이어가기: 이미 끝난 항목은 건너뛴다(워커 재시작 대비).
        remaining = [j for j in jobs if j.status in ("queued", "playing")]
        start_position = remaining[0].position if remaining else len(jobs)
        user_id = batch.user_id
        failed_courses = {j.lms_course_id for j in jobs if j.status in ("failed", "locked")}
        locked_courses = {j.lms_course_id for j in jobs if j.status == "locked"}
        try:
            videos = [_video_from_json(j.video) for j in jobs]
        except (KeyError, TypeError, ValueError) as e:
            # 저장된 항목이 깨졌으면 running으로 남겨 두지 말고(재시작마다 재시도됨) 실패로 끝낸다.
            log.exception("일괄 재생 항목 데이터 오류 batch=%s", batch_id)
            batch.status, batch.error, batch.finished_at = "failed", f"재생 항목 데이터 오류: {e!r}", utcnow()
            for j in remaining:
                j.status = "failed"
            await db.commit()
            return "failed"
        batch.status, batch.started_at = "running", batch.started_at or utcnow()
        await db.commit()

    cancel = asyncio.Event()
    watcher = asyncio.create_task(_watch_cancel(batch_id, cancel, cancel_poll_seconds))
    error: str | None = None
    try:
        async with sm() as db, lms_client_for(db, user_id) as client:
            runner = BatchRunner(client, player, settings=settings, hooks=_db_hooks(batch_id))
            await runner.run(
                videos, start_position=start_position, cancel=cancel,
                end_position=start_position + 1 if one_item else None,
                failed_courses=failed_courses, locked_courses=locked_courses,
            )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        log.exception("일괄 재생 실패 batch=%s", batch_id)
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass

    async with sm() as db:
        batch = await db.scalar(select(PlaybackBatch).where(PlaybackBatch.id == batch_id).with_for_update())
        if batch is None:
            return "missing"
        if batch.cancel_requested:
            cancel.set()
        jobs = (await db.scalars(select(PlaybackJob).where(PlaybackJob.batch_id == batch_id))).all()
        if one_item and not error and not cancel.is_set() and any(j.status == "queued" for j in jobs):
            batch.status, batch.current_position, batch.queue_at = "queued", None, utcnow()
            await db.commit()
            return "queued"
        for j in jobs:
            if j.status in ("queued", "playing"):
                j.status = "cancelled" if cancel.is_set() else "failed"
                j.error = j.error or error
        counts = {s: sum(1 for j in jobs if j.status == s) for s in ("done", "failed", "skipped", "locked", "cancelled")}
        batch.done_count, batch.failed_count = counts["done"], counts["failed"] + counts["locked"]
        batch.skipped_count = counts["skipped"]
        batch.error = error
        batch.finished_at = utcnow()
        batch.current_position = None
        if cancel.is_set():
            batch.status = "cancelled"
        elif error or (counts["done"] == 0 and batch.failed_count > 0):
            batch.status = "failed"
        elif batch.failed_count or counts["cancelled"]:
            batch.status = "partial"
        else:
            batch.status = "done"
        await db.commit()
        summary = (
            f"🎬 일괄 재생 {_STATUS_KO[batch.status]}: 완료 {counts['done']} · 실패 {counts['failed']}"
            f" · 잠김 {counts['locked']} · 이미 완료 {counts['skipped']} · 취소 {counts['cancelled']}"
        )
        failed = [j.title for j in jobs if j.status in ("failed", "locked")]
        if failed:
            summary += "\n실패: " + ", ".join(failed[:10])
        if error:
            summary += f"\n오류: {error}"
        await notify_user(db, user_id, summary, key=f"batch:{batch_id}")
        return batch.status


_STATUS_KO = {"done": "완료", "partial": "일부 완료", "failed": "실패", "cancelled": "취소됨"}


def _db_hooks(batch_id: int) -> BatchHooks:
    sm = sessionmaker()

    async def job(db: AsyncSession, position: int) -> PlaybackJob:
        return await db.scalar(
            select(PlaybackJob).where(PlaybackJob.batch_id == batch_id, PlaybackJob.position == position)
        )

    async def on_item_start(position: int, video: VideoTodo) -> None:
        async with sm() as db:
            j = await job(db, position)
            j.status, j.started_at, j.progress_seconds = "playing", utcnow(), 0
            j.progress_updated_at = None
            last_saved.pop((video.course_id, video.module_item_id), None)
            batch = await db.get(PlaybackBatch, batch_id)
            batch.current_position = position
            await db.commit()

    async def on_item_done(outcome: ItemOutcome) -> None:
        async with sm() as db:
            j = await job(db, outcome.position)
            j.status, j.attempts, j.error, j.finished_at = outcome.status, outcome.attempts, outcome.error, utcnow()
            batch = await db.get(PlaybackBatch, batch_id)
            if outcome.status == "done":
                batch.done_count += 1
            elif outcome.status in ("failed", "locked"):
                batch.failed_count += 1
            elif outcome.status == "skipped":
                batch.skipped_count += 1
            if outcome.status in ("done", "skipped"):
                # The worker has confirmed that this item is no longer pending in LMS.
                await db.execute(update(TodoRow).where(
                    TodoRow.user_id == j.user_id, TodoRow.kind == "video",
                    TodoRow.lms_course_id == j.lms_course_id,
                    TodoRow.lms_item_id == j.module_item_id,
                ).values(active=False))
            await db.commit()

    last_saved: dict[tuple[int, int], float] = {}

    async def on_progress(video: VideoTodo, t: float, d: float) -> None:
        # Persist advancing positions at most every ten wall-clock seconds.
        # A stalled player must not appear healthy just because it still reports.
        key = (video.course_id, video.module_item_id)
        now = time.monotonic()
        if now - last_saved.get(key, float("-inf")) < 10:
            return
        async with sm() as db:
            j = await db.scalar(select(PlaybackJob).where(
                PlaybackJob.batch_id == batch_id, PlaybackJob.lms_course_id == video.course_id,
                PlaybackJob.module_item_id == video.module_item_id))
            if j and j.status == "playing" and t > j.progress_seconds:
                j.progress_seconds = t
                j.progress_updated_at = utcnow()
                await db.commit()
                last_saved[key] = now

    return BatchHooks(on_item_start=on_item_start, on_item_done=on_item_done, on_progress=on_progress)


async def _watch_cancel(batch_id: int, cancel: asyncio.Event, poll: float) -> None:
    sm = sessionmaker()
    while not cancel.is_set():
        await asyncio.sleep(poll)
        async with sm() as db:
            batch = await db.get(PlaybackBatch, batch_id)
            if batch is None or batch.cancel_requested:
                cancel.set()


async def cancel_batch(db: AsyncSession, batch_id: int, user_id: int) -> PlaybackBatch | None:
    batch = await db.scalar(select(PlaybackBatch).where(
        PlaybackBatch.id == batch_id, PlaybackBatch.user_id == user_id,
    ).with_for_update().execution_options(populate_existing=True))
    if batch is None:
        return None
    if batch.status in ACTIVE_STATUSES:
        batch.cancel_requested = True
        if batch.status == "queued":
            batch.status, batch.finished_at, batch.current_position = "cancelled", utcnow(), None
            jobs = (await db.scalars(select(PlaybackJob).where(
                PlaybackJob.batch_id == batch_id, PlaybackJob.status.in_(("queued", "playing")),
            ))).all()
            for job in jobs:
                job.status, job.finished_at = "cancelled", utcnow()
    await db.commit()
    return batch


async def queue_estimates(db: AsyncSession, proposed: list[VideoTodo] | None = None) -> dict[int, dict]:
    """현재 대기열의 영상 단위 순환을 시뮬레이션한다. 다른 계정 정보는 반환하지 않는다."""
    from collections import deque

    batches = list((await db.scalars(select(PlaybackBatch).where(
        PlaybackBatch.status.in_(ACTIVE_STATUSES), ~PlaybackBatch.cancel_requested,
    ).order_by(PlaybackBatch.queue_at, PlaybackBatch.id))).all())
    batches.sort(key=lambda b: b.status != "running")  # 재생 중 영상은 먼저 끝낸다.
    jobs = list((await db.scalars(select(PlaybackJob).where(
        PlaybackJob.batch_id.in_([b.id for b in batches]),
        PlaybackJob.status.in_(("queued", "playing")),
    ).order_by(PlaybackJob.position))).all())
    grouped = {b.id: deque() for b in batches}
    for j in jobs:
        grouped[j.batch_id].append((j.position, j.duration, j.deadline,
                                    j.progress_seconds if j.status == "playing" else 0))
    if proposed is not None:
        grouped[-1] = deque((i.position, i.video.duration, i.video.deadline, 0)
                            for i in build_plan(proposed, utcnow()).items)
    order = deque(k for k, v in grouped.items() if v)
    result = {}
    running = bool(batches and batches[0].status == "running")
    for index, key in enumerate(order):
        result[key] = {"queue_position": index if running else index + 1,
                       "estimated_start": None, "estimated_wait_seconds": 0,
                       "expected_end": None, "deadline_at_risk": False, "job_estimates": {}}
    settings = get_settings()
    now = cursor = utcnow()
    overhead = settings.playback_extra_seconds
    gap = sum(settings.playback_gap_seconds) / 2
    while order:
        key = order.popleft()
        position, duration, deadline, progress = grouped[key].popleft()
        start = cursor
        # 이미 마감된 영상은 워커가 건너뛰므로 재생 시간은 잡지 않는다.
        seconds = max(0, duration - progress) + overhead if deadline > start else 0
        end = start + timedelta(seconds=seconds)
        info = result[key]
        if info["estimated_start"] is None:
            info["estimated_start"] = start
            info["estimated_wait_seconds"] = max(0, (start - now).total_seconds())
        info["expected_end"] = end
        info["deadline_at_risk"] |= end > deadline
        info["job_estimates"][position] = {"expected_start": start, "expected_end": end}
        cursor = end + timedelta(seconds=gap)
        if grouped[key]:
            order.append(key)
    return result
