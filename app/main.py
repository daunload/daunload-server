from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

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


# 브라우저 Whisper: transformers.js·onnxruntime(cdn.jsdelivr.net, wasm 백엔드는 blob: 모듈로 불러온다),
# 모델(Hugging Face Hub와 그 CDN). 글꼴(Pretendard)은 jsdelivr에서 받는다.
# 대시보드 HTML 문자열에 style 속성이 있어 스타일만 인라인을 허용한다. 스크립트는 인라인을 막는다.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net blob: 'wasm-unsafe-eval'",
    "worker-src 'self' blob:",
    "connect-src 'self' https://cdn.jsdelivr.net https://huggingface.co https://*.huggingface.co https://*.hf.co",
    "img-src 'self' data: https:",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "font-src 'self' https://cdn.jsdelivr.net",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
SECURITY_HEADERS = {
    "content-security-policy": CSP,
    "strict-transport-security": "max-age=31536000",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    # 다운로드·전사 링크 토큰이 주소에 들어가므로 Referer로 새지 않게 한다
    "referrer-policy": "no-referrer",
}
DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeaders:
    """모든 응답에 보안 헤더를 붙인다. 순수 ASGI 미들웨어라 스트리밍 응답의 정리 동작을 바꾸지 않는다."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        docs = scope["path"].startswith(DOCS_PATHS)  # Swagger UI는 CDN·인라인 스크립트를 쓴다(개발용)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    if not (docs and k == "content-security-policy") and k not in headers:
                        headers[k] = v
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(lifespan_=lifespan) -> FastAPI:
    docs = get_settings().api_docs
    app = FastAPI(title="SSU LMS 서버", version="0.1.0", lifespan=lifespan_,
                  docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None,
                  openapi_url="/openapi.json" if docs else None)
    app.add_middleware(SecurityHeaders)
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
