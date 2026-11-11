"""arq 워커. 실행: arq app.worker.main.WorkerSettings

작업
- sync_user_job: 동기화 → 마감 알림 → 자동 수강 과목이면 일괄 재생 묶음 생성
- auto_sync_user_job: 자동 재생 과목만 동기화(작업 ID autosync:{uid}). 전체 동기화(sync:{uid})와 ID를 나눠 서로 버려지지 않게 한다
- schedule_syncs (cron): 시각이 된 승인 사용자 중 자동 재생 과목이 있는 사람만 auto_sync_user_job을 넣는다
- deadline_reminders (cron): 마감 24시간·3시간 전 알림
- prune_daily_activity / prune_notification_logs (cron): 오래된 일별 활동·알림 발송 기록 정리
- 재생은 별도 DB 큐 워커(app.worker.playback)가 담당한다
"""

from __future__ import annotations

import logging
from datetime import timedelta

from arq import cron
from arq.connections import RedisSettings
from arq.worker import Retry, func
from sqlalchemy import func as sql_func, select

from app.api.deps import Queue
from app.config import get_settings
from app.db.base import init_engine, sessionmaker, utcnow
from app.db.models import CourseRow, LmsCredential, PlaybackBatch, PlaybackJob, User
from app.lms.errors import InvalidCredentials, LmsError
from app.services import playback
from app.services.access import has_access, user_has_access
from app.services.activity import prune_daily
from app.services.notify import notify_deadlines, notify_user, prune_notification_log
from app.services.sync import sync_user

log = logging.getLogger(__name__)

AUTO_RETRY_COOLDOWN = timedelta(hours=12)
AUTO_MAX_FAILURES = 3  # 자동 묶음에서 이만큼 시도해 실패한 영상은 더 자동으로 넣지 않는다(수동 재생은 된다)
AUTO_GIVE_UP_WINDOW = timedelta(days=7)  # 실패 횟수는 이 기간 안의 것만 센다. 지나면 다시 자동으로 시도한다


# 같은 사용자의 두 동기화가 동시에 돌면 같은 줄을 함께 넣다가 유니크 제약으로 실패한다. 아래로 막는다.
# - 자동 동기화는 락을 잡고 돈다. 전체 동기화가 큐에 있거나 돌고 있으면(arq:job:sync:{uid}) 시작하지 않는다.
# - 전체 동기화는 락이 잡혀 있으면 Retry로 미룬다. 전체 동기화는 버려지지 않고 자동 동기화 뒤에 돈다.
# Redis를 쓰므로 워커 프로세스가 여럿이어도 동작한다.
SYNC_LOCK_TTL = 700  # 작업 제한 시간(600초)보다 길게
FULL_SYNC_RETRY_DELAY = 15
FULL_SYNC_MAX_TRIES = 60  # 15초 x 60 = 15분. 자동 동기화 제한 시간보다 길다


def _sync_lock_key(user_id: int) -> str:
    return f"synclock:{user_id}"


async def sync_user_job(ctx, user_id: int) -> str:
    """전체 동기화. 로그인·`LMS 동기화` 버튼·승인 직후에만 큐에 들어간다(작업 ID sync:{uid})."""
    redis = ctx.get("redis")
    if redis is not None and await redis.exists(_sync_lock_key(user_id)):
        raise Retry(defer=FULL_SYNC_RETRY_DELAY)  # 자동 동기화가 끝난 뒤 다시 돈다
    return await _run_sync(user_id, None)


async def auto_sync_user_job(ctx, user_id: int) -> str:
    """자동 재생 과목만 동기화한다(작업 ID autosync:{uid}). 주기 작업이 넣는다.
    전체 동기화가 있으면 "busy"로 끝낸다. next_sync_at을 그대로 두므로 다음 크론 때 다시 시도한다."""
    redis = ctx.get("redis")
    if redis is None:
        return await _run_sync(user_id, "auto")
    key = _sync_lock_key(user_id)
    if not await redis.set(key, "1", nx=True, ex=SYNC_LOCK_TTL):
        return "busy"
    try:
        if await redis.exists(f"arq:job:sync:{user_id}"):  # 전체 동기화가 큐에 있거나 돌고 있다
            return "busy"
        return await _run_sync(user_id, "auto")
    finally:
        await redis.delete(key)


async def _run_sync(user_id: int, only: str | None) -> str:
    async with sessionmaker()() as db:
        if not await user_has_access(db, user_id):
            return "denied"
        course_ids = None
        if only == "auto":
            course_ids = await _auto_play_course_ids(db, user_id)
            if not course_ids:
                return "skipped"
        try:
            await sync_user(db, user_id, course_ids=course_ids)
        except InvalidCredentials:
            await notify_user(db, user_id, "🔒 학교 로그인이 만료되었습니다. 사이트에서 다시 로그인하거나 계정에서 새 개인 토큰을 등록하면 동기화와 자동 수강이 이어집니다.",
                              key=f"login-invalid:{utcnow():%Y%m%d}")
            return "invalid"
        except LmsError as e:
            log.warning("동기화 실패 user=%s: %s", user_id, e)
            cred = await db.get(LmsCredential, user_id)
            if cred:
                cred.status, cred.last_error, cred.last_error_at = "error", str(e), utcnow()
                cred.next_sync_at = utcnow() + timedelta(minutes=15)
                await db.commit()
            return "error"
        await notify_deadlines(db, user_id)
        try:
            await create_auto_batch(db, user_id)
        except playback.ActiveBatchExists:
            pass  # 수동 요청이 먼저 등록됐다면 기존 묶음을 유지한다.
    return "ok"


async def _auto_play_course_ids(db, user_id: int) -> set[int]:
    return set((await db.scalars(
        select(CourseRow.lms_course_id).where(CourseRow.user_id == user_id, CourseRow.auto_play, CourseRow.active)
    )).all())


async def create_auto_batch(db, user_id: int) -> PlaybackBatch | None:
    """자동 수강이 켜진 과목의 미수강 영상으로 묶음을 만든다. 이미 진행 중이면 만들지 않는다.
    최근 실패한 항목은 같은 실패를 반복하지 않도록 일정 시간 제외한다. 자동 묶음에서 AUTO_MAX_FAILURES번
    시도해 실패한 영상은 AUTO_GIVE_UP_WINDOW 동안 제외한다. 마감 없는 영상은 미수강 목록에서 빠지지 않아
    끝없이 재시도되기 때문이다. 기간이 지나면 다시 세므로 일시적인 장애로 실패한 영상도 다시 자동으로 시도한다."""
    if not await user_has_access(db, user_id) or await playback.active_batch(db, user_id):
        return None
    course_ids = await _auto_play_course_ids(db, user_id)
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
    given_up = set((await db.scalars(
        select(PlaybackJob.module_item_id)
        .join(PlaybackBatch, PlaybackBatch.id == PlaybackJob.batch_id)
        .where(
            PlaybackJob.user_id == user_id,
            PlaybackBatch.scope == "auto",
            PlaybackJob.status.in_(("failed", "locked")),
            PlaybackJob.started_at.is_not(None),
            sql_func.coalesce(PlaybackJob.finished_at, PlaybackJob.started_at) >= utcnow() - AUTO_GIVE_UP_WINDOW,
        )
        .group_by(PlaybackJob.module_item_id)
        .having(sql_func.count() >= AUTO_MAX_FAILURES)
    )).all())
    videos = [v for v in videos if v.module_item_id not in recent_failures | given_up]
    if not videos:
        return None
    return await playback.create_batch(db, user_id, "auto", videos)


async def schedule_syncs(ctx) -> int:
    """동기화 시각이 된 사용자 중 승인되었고 현재 학기 자동 재생 과목이 있는 사람만 큐에 넣는다."""
    async with sessionmaker()() as db:
        rows = (await db.execute(
            select(User).join(LmsCredential, LmsCredential.user_id == User.id).where(
                LmsCredential.status != "invalid", LmsCredential.next_sync_at <= utcnow(),
                select(CourseRow.id).where(
                    CourseRow.user_id == User.id, CourseRow.auto_play, CourseRow.active).exists(),
            ).order_by(User.id)
        )).scalars().all()
        user_ids = [u.id for u in rows if has_access(u)]
    for uid in user_ids:
        await ctx["queue"].enqueue("auto_sync_user_job", uid, job_id=f"autosync:{uid}")
    return len(user_ids)


async def deadline_reminders(ctx) -> int:
    async with sessionmaker()() as db:
        users = (await db.scalars(
            select(User).join(LmsCredential, LmsCredential.user_id == User.id).order_by(User.id)
        )).all()
        user_ids = [u.id for u in users if has_access(u)]
        return sum([await notify_deadlines(db, uid) for uid in user_ids])


async def prune_daily_activity(ctx) -> int:
    async with sessionmaker()() as db:
        return await prune_daily(db)


async def prune_notification_logs(ctx) -> int:
    async with sessionmaker()() as db:
        return await prune_notification_log(db)


async def startup(ctx) -> None:
    logging.basicConfig(level=logging.INFO)
    init_engine()
    ctx["queue"] = Queue(ctx["redis"])


class WorkerSettings:
    functions = [
        func(sync_user_job, timeout=600, max_tries=FULL_SYNC_MAX_TRIES),
        func(auto_sync_user_job, timeout=600),
    ]
    cron_jobs = [
        cron(schedule_syncs, minute=set(range(0, 60, 5)), run_at_startup=True),
        cron(deadline_reminders, minute=set(range(2, 60, 10))),
        cron(prune_daily_activity, hour=4, minute=10),
        cron(prune_notification_logs, hour=4, minute=20),
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 20
    keep_result = 0  # 같은 job_id(sync:{user}, autosync:{user})를 바로 다시 넣을 수 있도록 결과를 남기지 않는다
    job_timeout = 24 * 3600
