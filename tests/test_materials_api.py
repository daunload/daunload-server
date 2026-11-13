import respx

from app.db import base as db_base
from app.db.models import CourseRow
from app.lms.media import CONTENT_PHP
from tests.test_api import _signup, env  # noqa: F401
from tests.test_materials import _modules

C = "https://canvas.ssu.ac.kr"
WIKI = '<a class="instructure_file_link" href="/courses/45446/files/701">a.pdf</a>'


async def _with_course(api):
    h = await _signup(api)
    async with db_base.sessionmaker()() as db:
        db.add(CourseRow(user_id=1, lms_course_id=45446, name="리눅스"))
        await db.commit()
    respx.get(f"{C}/learningx/api/v1/courses/45446/modules").respond(200, json=[m.model_dump() for m in _modules()])
    respx.get(f"{C}/api/v1/courses/45446/pages/week-1").respond(200, json={"title": "1주차 자료", "body": WIKI})
    respx.get(f"{C}/api/v1/courses/45446/pages/week-2").respond(200, json={"title": "2주차 자료", "body": ""})
    return h


@respx.mock
async def test_list_materials_and_ownership(env):
    api, _ = env
    h = await _with_course(api)
    mats = (await api.get("/courses/45446/materials", headers=h)).json()
    assert [(m["key"], m["kind"], m["file_name"]) for m in mats] == [
        ("c12", "commons_file", "1장.pptx"), ("f701", "canvas_file", "a.pdf")]
    assert (await api.get("/courses/99999/materials", headers=h)).status_code == 404


@respx.mock
async def test_material_link_rejects_unknown_key(env):
    api, _ = env
    h = await _with_course(api)
    assert (await api.post("/courses/45446/materials/f999/download-link", headers=h)).status_code == 404
    assert (await api.post("/courses/45446/materials/c11/download-link", headers=h)).status_code == 404  # 영상


@respx.mock
async def test_commons_file_relay(env):
    api, _ = env
    h = await _with_course(api)
    r = await api.post("/courses/45446/materials/c12/download-link", headers=h)
    assert r.status_code == 200 and r.json()["file_name"] == "1장.pptx"
    up = respx.get("https://commons.ssu.ac.kr/index.php").respond(
        200, content=b"PPTX", headers={"Content-Type": "application/vnd.ms-powerpoint", "Content-Length": "4"})
    got = await api.get(r.json()["url"])
    assert got.status_code == 200 and got.content == b"PPTX"
    assert got.headers["content-type"] == "application/vnd.ms-powerpoint"
    assert 'filename="download.pptx"' in got.headers["content-disposition"]
    assert up.calls.last.request.url.params["content_id"] == "f12"
    assert up.calls.last.request.headers["Referer"] == "https://commons.ssu.ac.kr/"


@respx.mock
async def test_canvas_file_relay_checks_file_through_lms(env):
    api, _ = env
    h = await _with_course(api)
    url = (await api.post("/courses/45446/materials/f701/download-link", headers=h)).json()["url"]
    meta = respx.get(f"{C}/api/v1/courses/45446/files/701").respond(
        200, json={"id": 701, "url": f"{C}/files/701/download?download_frd=1&verifier=v"})
    respx.get(f"{C}/files/701/download").respond(200, content=b"%PDF", headers={"Content-Type": "application/pdf"})
    got = await api.get(url)
    assert got.status_code == 200 and got.content == b"%PDF"
    assert meta.called


@respx.mock
async def test_canvas_file_outside_host_rejected(env):
    api, _ = env
    h = await _with_course(api)
    url = (await api.post("/courses/45446/materials/f701/download-link", headers=h)).json()["url"]
    respx.get(f"{C}/api/v1/courses/45446/files/701").respond(200, json={"url": "https://evil.example.com/a.pdf"})
    assert (await api.get(url)).status_code == 422


@respx.mock
async def test_redirect_to_outside_host_rejected(env):
    api, _ = env
    h = await _with_course(api)
    url = (await api.post("/courses/45446/materials/c12/download-link", headers=h)).json()["url"]
    respx.get("https://commons.ssu.ac.kr/index.php").respond(302, headers={"Location": "https://evil.example.com/x"})
    respx.get("https://evil.example.com/x").respond(200, content=b"x")
    assert (await api.get(url)).status_code == 422


@respx.mock
async def test_video_relay_still_works(env):
    api, _ = env
    h = await _with_course(api)
    # _modules()의 11번은 movie(m11)
    url = (await api.post("/courses/45446/videos/11/download-link", headers=h)).json()["url"]
    cdn = "https://ssuin-object.commonscdn.com/media/abc"
    respx.get(CONTENT_PHP).respond(200, text=f"<content><content_playing_info><media_uri>{cdn}/a.mp4</media_uri></content_playing_info></content>")
    respx.get(f"{cdn}/a.mp4").respond(200, content=b"mp4")
    got = await api.get(url)
    assert got.status_code == 200 and got.headers["content-type"] == "video/mp4"


@respx.mock
async def test_relay_asks_upstream_for_uncompressed_bytes(env):
    """압축 응답을 디코딩하지 않고 그대로 넘기면 파일이 깨진다. 업스트림에 압축하지 말라고 요청한다."""
    api, _ = env
    h = await _with_course(api)
    url = (await api.post("/courses/45446/materials/c12/download-link", headers=h)).json()["url"]
    up = respx.get("https://commons.ssu.ac.kr/index.php").respond(200, content=b"plain text")
    got = await api.get(url)
    assert got.content == b"plain text"
    assert up.calls.last.request.headers["Accept-Encoding"] == "identity"


@respx.mock
async def test_material_links_reuse_listed_materials(env):
    """묶음 다운로드: 목록을 본 뒤 링크를 여러 개 만들어도 모듈·위키 페이지를 다시 받지 않는다."""
    api, _ = env
    h = await _with_course(api)
    modules = next(r for r in respx.routes if "/modules" in repr(r.pattern))
    week1 = next(r for r in respx.routes if "week-1" in repr(r.pattern))
    assert (await api.get("/courses/45446/materials", headers=h)).status_code == 200
    assert (modules.call_count, week1.call_count) == (1, 1)
    for key in ("c12", "f701", "c12"):
        assert (await api.post(f"/courses/45446/materials/{key}/download-link", headers=h)).status_code == 200
    assert (modules.call_count, week1.call_count) == (1, 1)
    # 목록에 없던 자료면 LMS에서 다시 확인한다(그래도 없으면 404).
    assert (await api.post("/courses/45446/materials/f999/download-link", headers=h)).status_code == 404
    assert modules.call_count == 2
    # 다른 사용자의 과목은 캐시와 상관없이 막힌다.
    assert (await api.post("/courses/99999/materials/c12/download-link", headers=h)).status_code == 404


def test_listing_cache_expires():
    from app.services.lms_cache import TtlCache

    now = [0.0]
    cache = TtlCache(ttl=10, max_entries=2, clock=lambda: now[0])
    cache.put("a", 1)
    assert cache.get("a") == 1
    now[0] = 10
    assert cache.get("a") is None
    cache.put("a", 1); cache.put("b", 2); cache.put("c", 3)
    assert cache.get("a") is None and cache.get("c") == 3
