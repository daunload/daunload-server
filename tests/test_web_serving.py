"""대시보드 화면 서빙: Vite 빌드 결과(app/web-dist)를 내보내고, 해시 붙은 파일은 오래 캐시한다."""

import re
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

import app.main as main
from tests.web_root import WEB


@asynccontextmanager
async def no_lifespan(_):
    yield


def client(monkeypatch, web_dir):
    monkeypatch.setattr(main, "WEB_DIR", web_dir)
    return TestClient(main.create_app(no_lifespan))


def test_index_is_revalidated_and_hashed_assets_are_immutable(monkeypatch, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<script type="module" src="/assets/index-abc.js"></script>', encoding="utf-8")
    (tmp_path / "assets" / "index-abc.js").write_text("1", encoding="utf-8")
    (tmp_path / "mark.png").write_bytes(b"png")
    with client(monkeypatch, tmp_path) as c:
        for path in ("/", "/index.html"):
            res = c.get(path)
            assert res.status_code == 200 and res.headers["cache-control"] == "no-cache"
            assert "content-security-policy" in res.headers
            assert 'src="/assets/index-abc.js"' in res.text
        asset = c.get("/assets/index-abc.js")
        assert asset.status_code == 200 and asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert c.get("/assets/missing.js").status_code == 404
        assert "immutable" not in c.get("/mark.png").headers.get("cache-control", "")  # 이름이 고정된 public 파일은 오래 캐시하지 않는다


def test_api_still_runs_without_a_web_build(monkeypatch, tmp_path):
    with client(monkeypatch, tmp_path / "missing") as c:
        res = c.get("/")
        assert res.status_code == 503 and "npm run build" in res.text
        assert c.get("/health").json() == {"ok": True}


built = pytest.mark.skipif(not (WEB / "index.html").is_file(), reason="npm run build가 필요합니다")


@built
def test_build_has_one_entry_module_and_no_inline_script():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert len(scripts) == 1 and 'type="module"' in scripts[0] and 'src="/assets/' in scripts[0]  # CSP가 인라인 스크립트를 막는다
    assert re.findall(r"<script\b[^>]*>\s*</script>", html) == [s + "</script>" for s in scripts]
    assert "?v=" not in html


@built
def test_admin_code_is_a_separate_chunk():
    chunks = {p.name: p.read_text(encoding="utf-8") for p in (WEB / "assets").glob("*.js")}
    admin = [name for name in chunks if name.startswith("admin-")]
    assert len(admin) == 1
    entry = re.search(r'src="/assets/([^"]+)"', (WEB / "index.html").read_text(encoding="utf-8")).group(1)
    assert "재생 묶음 오류" not in chunks[entry] and "재생 묶음 오류" in chunks[admin[0]]  # 관리자 화면 문구는 첫 화면 묶음에 없다
    assert any(name.startswith("whisper.worker-") for name in chunks)


@built
def test_adsense_loader_and_ads_txt_are_served(monkeypatch):
    with client(monkeypatch, WEB) as c:
        # 광고 로더는 첫 화면을 그린 뒤 assets.js가 붙인다. HTML에는 사이트 확인용 계정 태그를 둔다.
        assert '<meta name="google-adsense-account" content="ca-pub-1006974862356513">' in c.get("/").text
        entry = re.search(r'src="(/assets/[^"]+)"', c.get("/").text).group(1)
        assert "pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1006974862356513" in c.get(entry).text
        r = c.get("/ads.txt")
        assert r.status_code == 200
        assert r.text.strip() == "google.com, pub-1006974862356513, DIRECT, f08c47fec0942fa0"
