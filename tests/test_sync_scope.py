"""부분 동기화(자동 재생 과목만)와 주기 작업 대상 선별."""

from datetime import timedelta

import respx
from sqlalchemy import select

from app.db import base as db_base
from app.db.base import utcnow
from app.db.models import CourseRow, LmsCredential, TodoRow, User
from app.services.sync import sync_user
from app.worker import main as worker
from tests.test_api import COURSE_ID, LmsMock, _signup, env  # noqa: F401  (env는 fixture)
from tests.test_worker import Q

OTHER_COURSE = 999


def _todo(course_id, item_id, **kw):
    return TodoRow(user_id=1, lms_course_id=course_id, course_name="c", kind="assignment",
                   lms_item_id=item_id, title=f"t{item_id}", active=True, **kw)


@respx.mock
async def test_auto_sync_only_touches_auto_play_courses(env):
    api, _ = env
    await _signup(api)
    LmsMock().install()
    now = utcnow()
    full_at = now - timedelta(days=3)
    async with db_base.sessionmaker()() as db:
        cred = await db.get(LmsCredential, 1)
        cred.last_synced_at = full_at
        db.add_all([
            CourseRow(user_id=1, lms_course_id=COURSE_ID, term_id=46, name="A", auto_play=True, active=True),
            CourseRow(user_id=1, lms_course_id=OTHER_COURSE, term_id=46, name="B", auto_play=False, active=True),
            _todo(COURSE_ID, 777),      # A에서 사라진 할 일 -> 비활성화
            _todo(OTHER_COURSE, 888),   # B의 할 일 -> 그대로
        ])
        await db.commit()

    async with db_base.sessionmaker()() as db:
        await sync_user(db, 1, now=now, course_ids={COURSE_ID})

    async with db_base.sessionmaker()() as db:
        todos = {t.lms_item_id: t.active for t in (await db.scalars(select(TodoRow))).all()}
        assert todos[777] is False
        assert todos[888] is True
        assert any(item in todos and todos[item] for item in (11, 12, 21))  # A의 새 영상은 들어온다
        courses = {c.lms_course_id: c.active for c in (await db.scalars(select(CourseRow))).all()}
        assert courses == {COURSE_ID: True, OTHER_COURSE: True}
        cred = await db.get(LmsCredential, 1)
        assert cred.last_synced_at.replace(tzinfo=None) == full_at.replace(tzinfo=None)
        gap = cred.next_sync_at.replace(tzinfo=None) - now.replace(tzinfo=None)
        assert timedelta(minutes=104) <= gap <= timedelta(minutes=136)  # 120분 +- 지터 15분


@respx.mock
async def test_full_sync_still_deactivates_everything_missing(env):
    api, _ = env
    await _signup(api)
    LmsMock().install()
    async with db_base.sessionmaker()() as db:
        db.add_all([CourseRow(user_id=1, lms_course_id=OTHER_COURSE, term_id=46, name="B", active=True),
                    _todo(OTHER_COURSE, 888)])
        await db.commit()
    async with db_base.sessionmaker()() as db:
        await sync_user(db, 1)
    async with db_base.sessionmaker()() as db:
        assert (await db.scalar(select(TodoRow).where(TodoRow.lms_item_id == 888))).active is False
        assert (await db.scalar(select(CourseRow).where(CourseRow.lms_course_id == OTHER_COURSE))).active is False


async def _add_user(db, uid, sid, access, auto=True, active=True, due=True):
    db.add(User(id=uid, student_id=sid, consent_at=utcnow(), access_status=access))
    await db.flush()
    db.add(LmsCredential(user_id=uid, student_id=sid, status="ok",
                         next_sync_at=utcnow() + timedelta(minutes=-1 if due else 60)))
    if auto is not None:
        db.add(CourseRow(user_id=uid, lms_course_id=100 + uid, term_id=1, name="c", auto_play=auto, active=active))


async def test_schedule_picks_only_approved_users_with_auto_play(env):
    api, _ = env
    async with db_base.sessionmaker()() as db:
        await _add_user(db, 11, "s11", "approved")                    # 대상
        await _add_user(db, 12, "s12", "pending")                     # 승인 안 됨
        await _add_user(db, 13, "s13", "revoked")                     # 회수됨
        await _add_user(db, 14, "s14", "approved", auto=False)        # 자동 재생 과목 없음
        await _add_user(db, 15, "s15", "approved", active=False)      # 과거 학기 과목뿐
        await _add_user(db, 16, "s16", "approved", due=False)         # 아직 시각 아님
        await _add_user(db, 17, "s17", "approved", auto=None)         # 과목 없음
        await db.commit()
    ctx = {"queue": Q()}
    assert await worker.schedule_syncs(ctx) == 1
    assert ctx["queue"].jobs == [("auto_sync_user_job", (11,), "autosync:11")]


async def test_schedule_includes_admin_regardless_of_stored_status(env, monkeypatch):
    api, _ = env
    monkeypatch.setenv("ADMIN_STUDENT_IDS", '["boss"]')
    from app.config import get_settings
    get_settings.cache_clear()
    async with db_base.sessionmaker()() as db:
        await _add_user(db, 21, "boss", "none")
        await db.commit()
    ctx = {"queue": Q()}
    assert await worker.schedule_syncs(ctx) == 1
    assert ctx["queue"].jobs[0][2] == "autosync:21"
