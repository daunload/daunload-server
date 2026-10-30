from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, Db
from app.api.schemas import BatchDetailOut, BatchIn, BatchOut, JobOut, PlanItemOut, PlanOut
from app.db.models import PlaybackBatch, PlaybackJob
from app.lms.batch import BatchPlan, Scope
from app.services import playback

router = APIRouter(prefix="/playback", tags=["playback"])


def _plan_out(plan: BatchPlan) -> PlanOut:
    return PlanOut(
        items=[
            PlanItemOut(
                position=i.position, lms_course_id=i.video.course_id, course_name=i.video.course_name,
                module_id=i.video.module_id, module_title=i.video.module_title,
                module_item_id=i.video.module_item_id, title=i.video.title, duration=i.video.duration,
                deadline=i.video.deadline, expected_start=i.expected_start, expected_end=i.expected_end,
                at_risk=i.at_risk,
            )
            for i in plan.items
        ],
        total_seconds=plan.total_seconds,
        expected_end=plan.expected_end,
        warnings=plan.warnings,
    )


def _validate_scope(scope: Scope, course_id: int | None, module_id: int | None) -> None:
    if scope in ("module", "course") and course_id is None:
        raise HTTPException(422, f"{scope} 범위에는 course_id가 필요합니다.")
    if scope == "module" and module_id is None:
        raise HTTPException(422, "module 범위에는 module_id가 필요합니다.")


@router.get("/batches/preview", response_model=PlanOut)
async def preview_batch(user: CurrentUser, db: Db, scope: Scope, course_id: int | None = None, module_id: int | None = None):
    _validate_scope(scope, course_id, module_id)
    return _plan_out(await playback.preview(db, user.id, scope, course_id, module_id))


async def _create(db, user_id: int, scope: str, videos, course_id=None, module_id=None) -> BatchOut:
    if not videos:
        raise HTTPException(404, "기간 안에 재생할 미수강 영상이 없습니다.")
    try:
        batch = await playback.create_batch(db, user_id, scope, videos, course_id, module_id)
    except playback.ActiveBatchExists as e:
        raise HTTPException(409, str(e)) from e
    # DB 커밋 자체가 큐 등록이다. Redis 장애와 무관하게 워커가 읽는다.
    return await _batch_out(db, batch)


@router.post("/batches", response_model=BatchOut, status_code=201)
async def create_batch(body: BatchIn, user: CurrentUser, db: Db):
    _validate_scope(body.scope, body.course_id, body.module_id)
    videos = await playback.pending_videos_from_db(db, user.id, body.scope, body.course_id, body.module_id)
    return await _create(db, user.id, body.scope, videos, body.course_id, body.module_id)


@router.post("/items/{module_item_id}", response_model=BatchOut, status_code=201)
async def play_single(module_item_id: int, user: CurrentUser, db: Db):
    """영상 한 개 재생(항목 1개짜리 묶음)."""
    videos = [v for v in await playback.pending_videos_from_db(db, user.id, "all") if v.module_item_id == module_item_id]
    return await _create(db, user.id, "item", videos, videos[0].course_id if videos else None)


@router.get("/batches", response_model=list[BatchOut])
async def list_batches(user: CurrentUser, db: Db, limit: int = Query(default=20, ge=1, le=100)):
    stmt = (
        select(PlaybackBatch).where(PlaybackBatch.user_id == user.id)
        .order_by(PlaybackBatch.id.desc()).limit(limit)
    )
    batches = (await db.scalars(stmt)).all()
    estimates = await playback.queue_estimates(db)
    return [await _batch_out(db, b, estimates) for b in batches]


async def _own_batch(db, user_id: int, batch_id: int) -> PlaybackBatch:
    batch = await db.get(PlaybackBatch, batch_id)
    if batch is None or batch.user_id != user_id:
        raise HTTPException(404, "묶음을 찾을 수 없습니다.")
    return batch


@router.get("/batches/{batch_id}", response_model=BatchDetailOut)
async def get_batch(batch_id: int, user: CurrentUser, db: Db):
    batch = await _own_batch(db, user.id, batch_id)
    jobs = (await db.scalars(
        select(PlaybackJob).where(PlaybackJob.batch_id == batch_id).order_by(PlaybackJob.position)
    )).all()
    estimates = await playback.queue_estimates(db)
    info = estimates.get(batch.id, {})
    return BatchDetailOut.model_validate({
        **(await _batch_out(db, batch, estimates)).model_dump(),
        "jobs": [{**JobOut.model_validate(j).model_dump(),
                  **info.get("job_estimates", {}).get(j.position, {})} for j in jobs],
    })


@router.delete("/batches/{batch_id}", response_model=BatchOut)
async def cancel_batch(batch_id: int, user: CurrentUser, db: Db):
    batch = await playback.cancel_batch(db, batch_id, user.id)
    if batch is None:
        raise HTTPException(404, "묶음을 찾을 수 없습니다.")
    return await _batch_out(db, batch)


async def _batch_out(db, batch, estimates=None) -> BatchOut:
    estimates = await playback.queue_estimates(db) if estimates is None else estimates
    info = {k: v for k, v in estimates.get(batch.id, {}).items() if k != "job_estimates"}
    return BatchOut.model_validate({**BatchOut.model_validate(batch).model_dump(), **info})
