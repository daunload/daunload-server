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
