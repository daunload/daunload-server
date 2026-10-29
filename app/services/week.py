"""이번 주(월요일 00:00 ~ 다음 월요일 00:00, 설정한 시간대 기준) 계산."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings


def week_bounds(now: datetime, tz: str | None = None) -> tuple[datetime, datetime]:
    """now가 속한 주의 (시작, 끝)을 UTC로 반환한다. 끝은 포함하지 않는다."""
    zone = ZoneInfo(tz or get_settings().timezone)
    local = now.astimezone(zone)
    start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start.astimezone(UTC), end.astimezone(UTC)
