"""브라우저 테스트가 내보낼 웹 폴더. 기본은 원본(app/web), WEB_ROOT=app/web-dist면 배포용 빌드 결과를 검사한다."""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = Path(os.environ.get("WEB_ROOT") or REPO / "app" / "web").resolve()
BUILT = (WEB / "main.min.js").is_file()  # 빌드 결과에는 스크립트가 main.min.js 하나로 묶여 있다
