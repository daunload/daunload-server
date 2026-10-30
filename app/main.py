from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import routes_account, routes_data, routes_media, routes_notifications, routes_playback, routes_transcripts
from app.api.deps import Queue
from app.api import routes_admin
from app.config import get_settings
from app.db.base import init_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    from arq import create_pool
    from arq.connections import RedisSettings

    settings = get_settings()
    if settings.jwt_secret == "change-me" or len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET을 32자 이상의 임의 값으로 설정하세요.")
    if not settings.fernet_key:
        raise RuntimeError("FERNET_KEY를 설정하세요 (Fernet.generate_key()).")
    engine = init_engine()
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.queue = Queue(redis)
    try:
        yield
    finally:
        await redis.aclose()
        await engine.dispose()


def create_app(lifespan_=lifespan) -> FastAPI:
    app = FastAPI(title="SSU LMS 서버", version="0.1.0", lifespan=lifespan_)
    app.include_router(routes_account.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_data.router)
    app.include_router(routes_playback.router)
    app.include_router(routes_notifications.router)
    app.include_router(routes_media.router)
    app.include_router(routes_transcripts.router)

    @app.get("/health", tags=["meta"])
    async def health():
        return {"ok": True}

    # 대시보드(정적 파일). API 라우트가 먼저 등록되어 있으므로 우선권은 API에 있다.
    app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web")
    return app


app = create_app()
