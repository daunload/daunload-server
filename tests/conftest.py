import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def fixture():
    return load_fixture


@pytest.fixture(autouse=True)
def approve_test_users():
    """테스트에서 만드는 사용자는 승인된 상태로 시작한다(설계 12장). 제품 기본값(none)은 그대로 둔다.

    User(...)를 만들 때(로그인으로 가입할 때 포함) access_status를 따로 주지 않으면 approved를 넣는다.
    승인 전 상황은 tests/test_access_api.py처럼 이 픽스처를 같은 이름으로 덮어 명시적으로 만든다.
    """
    from sqlalchemy import event

    from app.db.models import User

    def default_approved(target, args, kwargs):
        kwargs.setdefault("access_status", "approved")

    event.listen(User, "init", default_approved)
    yield
    event.remove(User, "init", default_approved)


@pytest.fixture(autouse=True)
def fresh_queue_estimates():
    """API의 대기열 예상 캐시는 프로세스 전역이다. 테스트마다 DB가 새로 생기므로 비우고 시작한다."""
    from app.services.playback import queue_estimate_cache

    queue_estimate_cache.invalidate()
    yield
    queue_estimate_cache.invalidate()


@pytest.fixture(autouse=True)
def fresh_lms_listing_cache():
    """다운로드 링크용 LMS 목록 캐시도 프로세스 전역이다. 테스트끼리 섞이지 않게 비운다."""
    from app.services.lms_cache import listing_cache

    listing_cache.clear()
    yield
    listing_cache.clear()
