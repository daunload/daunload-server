from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://lms:lms@localhost:5432/lms"
    redis_url: str = "redis://localhost:6379/0"

    # 학교 로그인 세션 암호화용 Fernet 키 (Fernet.generate_key()로 생성)
    fernet_key: str = ""
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 60 * 24
    admin_student_ids: list[str] = []
    # /docs, /redoc, /openapi.json 공개 여부. 운영에서는 끈다(개발용 .env에서 켠다).
    api_docs: bool = False

    # "이번 주"(월~일)를 계산할 기준 시간대
    timezone: str = "Asia/Seoul"

    # 동기화·재생 정책
    auto_sync_interval_minutes: int = 120  # 자동 재생 과목만 이 주기로 동기화한다
    sync_jitter_minutes: int = 15
    playback_extra_seconds: int = 30
    playback_gap_seconds: tuple[int, int] = (10, 30)
    playback_headless: bool = True
    # Playwright 기본 Chromium은 H.264/AAC를 재생하지 못하므로 설치된 Chrome을 쓴다.
    playback_browser_channel: str = "chrome"
    playback_stall_seconds: int = 90
    playback_max_reloads: int = 3
    verify_attempts: int = 4
    verify_interval_seconds: int = 20
    # 호환용 영상 중계는 기본 차단. 확장프로그램은 이 설정과 무관하게 CDN에서 직접 받는다.
    allow_video_relay: bool = False

    # 개발용: CLI에서 쓰는 본인 계정
    lms_id: str = ""
    lms_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
