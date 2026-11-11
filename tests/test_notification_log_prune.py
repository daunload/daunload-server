from datetime import timedelta

from sqlalchemy import select

from app.db import base as db_base
from app.db.base import utcnow
from app.db.models import NotificationLog
from app.services.notify import notify_user, prune_notification_log
from tests.test_api import _signup, env  # noqa: F401  (env는 fixture)


async def test_prune_removes_only_logs_older_than_90_days(env):
    await _signup(env[0])
    now = utcnow()
    async with db_base.sessionmaker()() as db:
        db.add_all([
            NotificationLog(user_id=1, key="old", created_at=now - timedelta(days=91)),
            NotificationLog(user_id=1, key="recent", created_at=now - timedelta(days=89)),
            NotificationLog(user_id=1, key="today", created_at=now),
        ])
        await db.commit()
        assert await prune_notification_log(db) == 1
        keys = set((await db.scalars(select(NotificationLog.key))).all())
    assert keys == {"recent", "today"}


async def test_pruned_key_can_be_sent_again(env, monkeypatch):
    await _signup(env[0])

    async def allowed(db, user_id):
        return True

    monkeypatch.setattr("app.services.access.user_has_access", allowed)
    async with db_base.sessionmaker()() as db:
        db.add(NotificationLog(user_id=1, key="k", created_at=utcnow() - timedelta(days=100)))
        await db.commit()
        assert await notify_user(db, 1, "알림", key="k") is False  # 아직 남아 있으면 중복으로 막힌다
        await prune_notification_log(db)
        assert await notify_user(db, 1, "알림", key="k") is True
