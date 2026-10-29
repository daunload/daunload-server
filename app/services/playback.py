"""일괄 재생 묶음의 생성(API)과 실행(워커)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.base import sessionmaker, utcnow
from app.db.models import PlaybackBatch, PlaybackJob, TodoRow
from app.lms.batch import BatchHooks, BatchPlan, BatchRunner, ItemOutcome, Scope, build_plan, select_videos
from app.lms.errors import LmsError
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
    return build_plan(await pending_videos_from_db(db, user_id, scope, course_id, module_id, now), now)


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
    if existing := await active_batch(db, user_id):
        raise ActiveBatchExists(existing.id)
    plan = build_plan(videos, utcnow())
    batch = PlaybackBatch(
        user_id=user_id, scope=scope, lms_course_id=course_id, module_id=module_id,
        status="queued", total=len(plan.items), expected_end=plan.expected_end,
    )
    db.add(batch)
    await db.flush()
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


async def run_batch(batch_id: int, player, settings: Settings | None = None, cancel_poll_seconds: float = 10) -> str:
    """묶음을 실행하고 최종 상태를 반환한다. player는 VideoPlayer(또는 같은 인터페이스)."""
    settings = settings or get_settings()
    sm = sessionmaker()

    async with sm() as db:
        batch = await db.get(PlaybackBatch, batch_id)
        if batch is None or batch.status not in ACTIVE_STATUSES:
            return batch.status if batch else "missing"
        jobs = (await db.scalars(
            select(PlaybackJob).where(PlaybackJob.batch_id == batch_id).order_by(PlaybackJob.position)
        )).all()
        # 이어가기: 이미 끝난 항목은 건너뛴다(워커 재시작 대비).
        remaining = [j for j in jobs if j.status in ("queued", "playing")]
        start_position = remaining[0].position if remaining else len(jobs)
        user_id = batch.user_id
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
            await runner.run(videos, start_position=start_position, cancel=cancel)
    except LmsError as e:
        error = str(e)
        log.exception("일괄 재생 실패 batch=%s", batch_id)
    finally:
        watcher.cancel()

    async with sm() as db:
        batch = await db.get(PlaybackBatch, batch_id)
        jobs = (await db.scalars(select(PlaybackJob).where(PlaybackJob.batch_id == batch_id))).all()
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
            await db.commit()

    last_saved: dict[int, float] = {}

    async def on_progress(video: VideoTodo, t: float, d: float) -> None:
        # 너무 자주 쓰지 않도록 30초 단위로 저장
        if t - last_saved.get(video.module_item_id, -999) < 30:
            return
        last_saved[video.module_item_id] = t
        async with sm() as db:
            j = await db.scalar(select(PlaybackJob).where(
                PlaybackJob.batch_id == batch_id, PlaybackJob.module_item_id == video.module_item_id))
            if j:
                j.progress_seconds = t
                await db.commit()

    return BatchHooks(on_item_start=on_item_start, on_item_done=on_item_done, on_progress=on_progress)


async def _watch_cancel(batch_id: int, cancel: asyncio.Event, poll: float) -> None:
    sm = sessionmaker()
    while not cancel.is_set():
        await asyncio.sleep(poll)
        async with sm() as db:
            batch = await db.get(PlaybackBatch, batch_id)
            if batch is None or batch.cancel_requested:
                cancel.set()
