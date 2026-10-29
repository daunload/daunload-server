from sqlalchemy import select

from app.db import base as db_base
from app.db.models import DownloadRecord, Transcript, User
from tests.test_api import env  # noqa: F401  (fixture)


async def test_records_and_transcripts_cascade_with_user(env):
    async with db_base.sessionmaker()() as db:
        user = User(student_id="1", password_hash="x", consent_at=db_base.utcnow())
        db.add(user)
        await db.flush()
        db.add(DownloadRecord(user_id=user.id, lms_course_id=1, item_key="v11", kind="video", title="영상"))
        db.add(Transcript(user_id=user.id, lms_course_id=1, module_item_id=11, course_name="리눅스", title="1강",
                          text="안녕하세요", segments=[{"start": 0.0, "end": 1.5, "text": "안녕하세요"}], model="whisper-small/wasm"))
        await db.commit()
        rec = (await db.scalars(select(DownloadRecord))).one()
        assert rec.count == 1 and rec.first_at is not None and rec.last_at is not None
        tr = (await db.scalars(select(Transcript))).one()
        assert tr.segments[0]["text"] == "안녕하세요" and tr.updated_at is not None
        await db.delete(user)
        await db.commit()
        assert (await db.scalars(select(DownloadRecord))).all() == []
        assert (await db.scalars(select(Transcript))).all() == []
