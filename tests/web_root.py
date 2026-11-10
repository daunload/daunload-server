"""브라우저 테스트가 내보낼 웹 폴더: Vite 빌드 결과(app/web-dist). 테스트 전에 `npm run build`를 돌린다."""

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = Path(os.environ.get("WEB_ROOT") or REPO / "app" / "web-dist").resolve()


def require_build():
    if not (WEB / "index.html").is_file():
        pytest.fail(f"{WEB}가 없습니다. 먼저 `npm run build`를 실행하세요.", pytrace=False)


# 대시보드의 확장 프로그램 창구(app/web/src/extension.js). import가 없는 모듈이라 export만 떼면
# 일반 스크립트로 바로 실행되고, 테스트 문자열에서 extensionRequest(...)를 전역처럼 부를 수 있다.
EXTENSION_CLIENT = re.sub(r"^export ", "", (REPO / "app" / "web" / "src" / "extension.js").read_text(encoding="utf-8"), flags=re.M)
