"""배포용 웹 빌드(scripts/build-web.mjs): 스크립트를 순서대로 묶고, 관리자 묶음은 나중에 받게 둔다."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.main import asset_version, versioned_index

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (REPO / "node_modules" / "esbuild").is_dir(), reason="npm install이 필요합니다"
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("web-dist")
    subprocess.run(["node", "scripts/build-web.mjs", str(out)], cwd=REPO, check=True, capture_output=True)
    return out


def test_scripts_are_bundled_in_order(built):
    html = (built / "index.html").read_text(encoding="utf-8")
    outside = re.sub(r"<template.*?</template>", "", html, flags=re.S)
    assert re.findall(r'<script src="(/[^"]+)"', outside) == ["/main.min.js"]
    assert re.search(r'<template id="scripts-admin"><script src="/admin.min.js"></script></template>', html)
    main = (built / "main.min.js").read_text(encoding="utf-8")
    # 전역 이름을 공유하는 일반 스크립트라 최상위 이름이 그대로 남아야 한다(관리자 묶음이 esc·state를 쓴다)
    positions = [main.index(f"function {name}(") for name in ("loadScripts", "esc", "boot", "renderWeek")]
    assert positions == sorted(positions)
    assert "function viewAdmin(" not in main
    assert "function viewAdmin(" in (built / "admin.min.js").read_text(encoding="utf-8")


def test_sources_are_replaced_and_assets_are_kept(built):
    names = {p.name for p in built.iterdir()}
    assert not names & {"app.js", "ui.js", "admin.js", "assets.js"}
    assert {"style.css", "privacy.html", "whisper.worker.js", "whisper-window.mjs", "patrick-blank.webp", "ads.txt"} <= names
    assert len((built / "style.css").read_bytes()) < len((REPO / "app" / "web" / "style.css").read_bytes())


def test_built_index_is_versioned(built):
    html = versioned_index(built)
    version = asset_version(built)
    assert f'src="/main.min.js?v={version}"' in html
    assert f'src="/admin.min.js?v={version}"' in html
    assert f'href="/style.css?v={version}"' in html
