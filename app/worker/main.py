"""arq 워커. 실행: arq app.worker.main.WorkerSettings

작업
- sync_user_job: 동기화 → 새 공지·마감 알림 → 자동 수강 과목이면 일괄 재생 묶음 생성
- schedule_syncs (cron): 동기화 시각이 된 사용자를 큐에 넣는다
- deadline_reminders (cron): 마감 24시간·3시간 전 알림
- 재생은 별도 DB 큐 워커(app.worker.playback)가 담당한다
"""

from __future__ import annotations

import logging
from datetime import timedelta

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func
from sqlalchemy import select

from app.api.deps import Queue
from app.config import get_settings
from app.db.base import init_engine, sessionmaker, utcnow
from app.db.models import CourseRow, LmsCredential, PlaybackJob
from app.lms.errors import InvalidCredentials, LmsError
from app.services import playback
from app.services.notify import notify_announcements, notify_deadlines, notify_user
from app.services.sync import sync_user

log = logging.getLogger(__name__)

AUTO_RETRY_COOLDOWN = timedelta(hours=12)


async def sync_user_job(ctx, user_id: int) -> str:
    async with sessionmaker()() as db:
        try:
            result = await sync_user(db, user_id)
        except InvalidCredentials:
            await notify_user(db, user_id, "🔒 LMS 로그인 실패: 비밀번호가 바뀌었다면 계정을 다시 등록하세요.",
                              key=f"login-invalid:{utcnow():%Y%m%d}")
            return "invalid"
        except LmsError as e:
            log.warning("동기화 실패 user=%s: %s", user_id, e)
            cred = await db.get(LmsCredential, user_id)
            if cred:
                cred.status, cred.last_error = "error", str(e)
                cred.next_sync_at = utcnow() + timedelta(minutes=15)
                await db.commit()
            return "error"
        await notify_announcements(db, user_id, result.new_announcements)
        await notify_deadlines(db, user_id)
        try:
            await create_auto_batch(db, user_id)
        except playback.ActiveBatchExists:
            pass  # 수동 요청이 먼저 등록됐다면 기존 묶음을 유지한다.
    return "ok"


async def create_auto_batch(db, user_id: int) -> PlaybackBatch | None:
    """자동 수강이 켜진 과목의 미수강 영상으로 묶음을 만든다. 이미 진행 중이면 만들지 않는다.
    최근 실패한 항목은 같은 실패를 반복하지 않도록 일정 시간 제외한다."""
    if await playback.active_batch(db, user_id):
        return None
    course_ids = set((await db.scalars(
        select(CourseRow.lms_course_id).where(CourseRow.user_id == user_id, CourseRow.auto_play, CourseRow.active)
    )).all())
    if not course_ids:
        return None
    videos = await playback.pending_videos_from_db(db, user_id, "auto", course_ids=course_ids)
    recent_failures = set((await db.scalars(
        select(PlaybackJob.module_item_id).where(
            PlaybackJob.user_id == user_id,
            PlaybackJob.status.in_(("failed", "locked")),
            PlaybackJob.finished_at >= utcnow() - AUTO_RETRY_COOLDOWN,
        )
    )).all())
    videos = [v for v in videos if v.module_item_id not in recent_failures]
    if not videos:
        return None
    return await playback.create_batch(db, user_id, "auto", videos)


async def schedule_syncs(ctx) -> int:
    async with sessionmaker()() as db:
        user_ids = (await db.scalars(
            select(LmsCredential.user_id).where(
                LmsCredential.status != "invalid", LmsCredential.next_sync_at <= utcnow()
            )
        )).all()
    for uid in user_ids:
        await ctx["queue"].enqueue("sync_user_job", uid, job_id=f"sync:{uid}")
    return len(user_ids)


async def deadline_reminders(ctx) -> int:
    async with sessionmaker()() as db:
        user_ids = (await db.scalars(select(LmsCredential.user_id))).all()
        return sum([await notify_deadlines(db, uid) for uid in user_ids])


async def startup(ctx) -> None:
    logging.basicConfig(level=logging.INFO)
    init_engine()
    ctx["queue"] = Queue(ctx["redis"])


class WorkerSettings:
    functions = [
        func(sync_user_job, timeout=600),
    ]
    cron_jobs = [
        cron(schedule_syncs, minute=set(range(0, 60, 5)), run_at_startup=True),
        cron(deadline_reminders, minute=set(range(2, 60, 10))),
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 20
    keep_result = 0  # 같은 job_id(sync:{user})를 바로 다시 넣을 수 있도록 결과를 남기지 않는다
    job_timeout = 24 * 3600
