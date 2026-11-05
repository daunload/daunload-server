from datetime import UTC, date, datetime, timedelta

import respx
from sqlalchemy import select

from app.db import base as db_base
from app.db.base import utcnow
from app.db.models import LmsCredential, SavedVideo, UserDailyActivity
from app.services.activity import bump_daily, local_day, prune_daily
from tests.test_api import _signup, env  # noqa: F401  (env는 fixture)
from tests.test_media import VIDEO_XML, _modules, _php, _save


async def _rows():
    async with db_base.sessionmaker()() as db:
        return (await db.scalars(select(UserDailyActivity).order_by(UserDailyActivity.day))).all()


def test_local_day_uses_korean_midnight():
    assert local_day(datetime(2026, 11, 4, 15, 30, tzinfo=UTC)) == date(2026, 11, 5)
    assert local_day(datetime(2026, 11, 4, 14, 59, tzinfo=UTC)) == date(2026, 11, 4)


async def test_bump_daily_adds_to_the_same_row(env):
    await _signup(env[0])
    async with db_base.sessionmaker()() as db:
        await bump_daily(db, 1, logins=1)
        await bump_daily(db, 1, logins=1, downloads=1)
        await db.commit()
    rows = [r for r in await _rows() if r.day == local_day()]
    assert [(r.logins, r.downloads) for r in rows] == [(3, 1)]  # 가입 로그인 1회 포함


async def test_login_bumps_daily_logins(env):
    api, _ = env
    await _signup(api)
    rows = await _rows()
    assert [(r.user_id, r.day, r.logins, r.downloads) for r in rows] == [(1, local_day(), 1, 0)]


async def test_record_download_bumps_daily_downloads(env):
    from app.services.downloads import record_download
    await _signup(env[0])
    async with db_base.sessionmaker()() as db:
        await record_download(db, 1, 45446, "v11", "video", "영상")
        await record_download(db, 1, 45446, "v11", "video", "영상")
    assert (await _rows())[0].downloads == 2


async def test_prune_keeps_exactly_90_days(env):
    await _signup(env[0])
    today = local_day()
    async with db_base.sessionmaker()() as db:
        db.add(UserDailyActivity(user_id=1, day=today - timedelta(days=90), logins=1, downloads=0))
        db.add(UserDailyActivity(user_id=1, day=today - timedelta(days=91), logins=1, downloads=0))
        await db.commit()
        assert await prune_daily(db) == 1
    days = {r.day for r in await _rows()}
    assert today - timedelta(days=90) in days and today - timedelta(days=91) not in days


async def test_account_delete_removes_daily_rows(env):
    api, _ = env
    h = await _signup(api)
    assert len(await _rows()) == 1
    assert (await api.delete("/me", headers=h)).status_code == 204
    assert await _rows() == []


async def test_lms_error_time_set_and_cleared(env):
    from app.services import lms_access
    api, _ = env
    await _signup(api)
    async with db_base.sessionmaker()() as db:
        cred = await db.get(LmsCredential, 1)
        await lms_access._mark(db, cred, "error", "boom")
        assert cred.last_error_at is not None and cred.last_error == "boom"
        await lms_access.save_credentials(db, 1, "20201234", "right")
        assert cred.last_error is None and cred.last_error_at is None

        await lms_access._mark(db, cred, "error", "boom")
        async with lms_access.lms_client_for(db, 1):
            pass
        assert cred.last_error is None and cred.last_error_at is None


@respx.mock
async def test_resolve_error_time_set_and_cleared(env):
    api, _ = env
    await _signup(api)
    sharedocs = "<content><content_type>sharedocs</content_type><content_playing_info/></content>"
    _php({"abc123": VIDEO_XML, "987654": sharedocs, "fixed": VIDEO_XML})
    await _save()

    async def row(item_id):
        async with db_base.sessionmaker()() as db:
            return await db.scalar(select(SavedVideo).where(SavedVideo.module_item_id == item_id))

    assert (await row(13)).resolve_failed_at is not None
    ok = await row(11)
    assert ok.resolved_at is not None and ok.resolve_failed_at is None

    mods = _modules()
    mods[1].module_items[2].content_data.item_content_data.content_id = "fixed"  # 교수가 영상을 바꿈
    await _save(modules=mods)
    fixed = await row(13)
    assert fixed.resolved_at is not None and fixed.resolve_failed_at is None and fixed.resolve_error is None
