class LmsError(Exception):
    """LMS 연동 중 발생한 오류의 기본 클래스."""


class InvalidCredentials(LmsError):
    """학번 또는 비밀번호가 틀렸다."""


class LmsParseError(LmsError):
    """LMS 페이지 구조가 예상과 다르다. 학교 사이트가 바뀌었을 가능성이 크다."""


class SessionExpired(LmsError):
    """세션이 만료되어 재로그인이 필요하다."""


class PlaybackError(LmsError):
    """영상 재생 또는 완료 확인에 실패했다."""


class VideoNotFound(PlaybackError):
    """재생 페이지에서 영상을 찾지 못했다. 선수 조건으로 잠긴 항목일 수 있다."""
