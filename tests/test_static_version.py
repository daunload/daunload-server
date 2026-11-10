"""배포 후 브라우저가 예전 JS·CSS와 새 파일을 섞어 쓰지 않게 하는 버전 주소."""

import re
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import asset_version, create_app, versioned_index


@asynccontextmanager
async def no_lifespan(_):
    yield


def client():
    return TestClient(create_app(no_lifespan))


def test_index_links_every_local_script_and_style_with_version():
    version = asset_version()
    with client() as c:
        res = c.get("/")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    assert "content-security-policy" in res.headers
    assert f'href="/style.css?v={version}"' in res.text
    for name in ("assets", "ui", "tasks", "downloads", "transcribe", "app", "access", "week", "admin", "admin-feeds"):
        assert f'src="/{name}.js?v={version}"' in res.text
    # 관리자 스크립트는 처음부터 실행하지 않고 <template>에 두었다가 관리자 화면을 열 때 붙인다
    template = re.search(r'<template id="scripts-admin">(.*?)</template>', res.text, re.S).group(1)
    assert 'src="/admin.js' in template and 'src="/admin-feeds.js' in template
    assert not re.search(r'(?:src|href)="/[\w.-]+\.(?:js|css)"', res.text)
    assert 'src="/mark.png"' in res.text  # 이미지와 외부 주소는 그대로 둔다
    assert 'href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard' in res.text


def test_index_html_path_is_versioned_too():
    with client() as c:
        res = c.get("/index.html")
    assert f"/app.js?v={asset_version()}" in res.text
    assert res.headers["cache-control"] == "no-cache"


def test_versioned_assets_are_served():
    with client() as c:
        assert c.get(f"/app.js?v={asset_version()}").status_code == 200
        assert c.get("/health").json() == {"ok": True}


def test_version_changes_with_any_asset(tmp_path):
    (tmp_path / "index.html").write_text('<script src="/a.js"></script><link href="/b.css">', encoding="utf-8")
    (tmp_path / "a.js").write_text("1", encoding="utf-8")
    (tmp_path / "b.css").write_text("x", encoding="utf-8")
    before = asset_version(tmp_path)
    assert versioned_index(tmp_path) == f'<script src="/a.js?v={before}"></script><link href="/b.css?v={before}">'
    assert asset_version(tmp_path) == before  # 내용이 같으면 그대로다
    (tmp_path / "b.css").write_text("y", encoding="utf-8")
    assert asset_version(tmp_path) != before
