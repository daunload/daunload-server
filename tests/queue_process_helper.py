"""Subprocess used only by the opt-in PostgreSQL crash recovery test."""
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.db.base import init_engine
from app.services import playback
from app.worker import playback as worker
import app.lms.player


async def main():
    engine = init_engine(os.environ["QUEUE_TEST_DATABASE_URL"])
    engine.update_execution_options(schema_translate_map={None: os.environ["QUEUE_TEST_SCHEMA"]})

    @asynccontextmanager
    async def client(db, user_id):
        yield SimpleNamespace(session=user_id)

    async def refresh(self, v):
        return v

    async def play(*args, **kwargs):
        print("playing", flush=True)
        if sys.argv[1] == "block":
            await asyncio.Event().wait()

    playback.lms_client_for = client
    playback.BatchRunner._refresh = refresh
    playback.notify_user = AsyncMock()
    app.lms.player.verify_completed = AsyncMock(return_value=True)
    try:
        async with worker.exclusive_worker(engine):
            await worker.recover_interrupted()
            await worker.process_next(AsyncMock(return_value=SimpleNamespace(play=play)))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
