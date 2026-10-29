import httpx
import pytest
import respx

from app.db import base as db_base
from app.db.models import CourseRow
from app.lms.media import (
    CONTENT_PHP,
    MediaError,
    check_media_host,
    content_disposition,
    course_videos,
    download_to_file,
    extract_media_url,
    safe_file_name,
)
from app.lms.models import Course, Module
from tests.test_api import _signup, env  # noqa: F401  (env는 fixture)

CDN = "https://ssuin-object.commonscdn.com/media/abc"

# --- XML에서 mp4 주소 찾기 (다운로더의 4가지 전략) ----------------------------


def test_strategy1_media_file_template():
    xml = f"""<content><content_playing_info><story_list><story><main_media_list><main_media>lec.mp4</main_media>
      </main_media_list></story></story_list></content_playing_info>
      <service_root><media><media_uri>{CDN}/[MEDIA_FILE]</media_uri></media></service_root></content>"""
    assert extract_media_url(xml) == f"{CDN}/lec.mp4"


def test_strategy2_direct_mp4_in_list():
    xml = f"""<content><content_playing_info><media_uri>{CDN}/[x]</media_uri><media_uri>{CDN}/hd.mp4</media_uri>
      </content_playing_info></content>"""
    assert extract_media_url(xml) == f"{CDN}/hd.mp4"


def test_strategy3_desktop_html5_video1():
    # 실제 video1 응답의 구조(태그만 남김)
    xml = f"""<content><content_type>video1</content_type><content_playing_info><content_uri>{CDN}/web_files</content_uri>
      <main_media><desktop><html5><media_uri>{CDN}/old.mp4</media_uri></html5></desktop></main_media>
      </content_playing_info></content>"""
    assert extract_media_url(xml) == f"{CDN}/old.mp4"


def test_strategy4_content_uri_fallback():
    xml = f"""<content><content_playing_info><content_uri>{CDN}/web_files</content_uri>
      <main_media_list><main_media>a.mp4</main_media></main_media_list></content_playing_info></content>"""
    assert extract_media_url(xml) == f"{CDN}/media_files/a.mp4"


def test_unsupported_document_returns_none():
    xml = "<content><content_type>sharedocs</content_type><content_playing_info><content_uri>x</content_uri></content_playing_info></content>"
    assert extract_media_url(xml) is None
    assert extract_media_url("not xml") is None


@pytest.mark.parametrize("url", ["https://evil.example.com/a.mp4", "http://ssuin-object.commonscdn.com/a.mp4",
                                 "https://commonscdn.com.evil.io/a.mp4"])
def test_media_host_is_restricted(url):
    with pytest.raises(MediaError):
        check_media_host(url)


def test_media_host_allows_school_cdn():
    assert check_media_host(f"{CDN}/a.mp4")
    assert check_media_host("https://commons.ssu.ac.kr/a.mp4")


# --- 목록과 이름 --------------------------------------------------------------


def _modules():
    def item(mid, pos, ctype, cid, completed=False, title=None):
        return {"module_item_id": mid, "position": pos, "completed": completed,
                "content_data": {"item_content_type": "commons", "title": title or f"영상{mid}",
                                 "item_content_data": {"content_type": ctype, "content_id": cid, "duration": 600,
                                                       "total_file_size": 600_000_000}}}
    return [Module.model_validate(m) for m in [
        {"module_id": 2, "title": "2주차", "position": 2, "module_items": [item(21, 1, "movie", "not_open")]},
        {"module_id": 1, "title": "1주차", "position": 1, "module_items": [
            item(12, 2, "pdf", "p1"), item(11, 1, "movie", "abc123", completed=True, title="1강: 소개/개요"),
            item(13, 3, "everlec", 987654)]},
    ]]


def test_course_videos_order_types_and_availability():
    vids = course_videos(Course(id=1, name="리눅스"), _modules())
    assert [v.module_item_id for v in vids] == [11, 13, 21]  # pdf 제외, 화면 순서
    assert vids[0].completed and vids[0].available
    assert vids[1].content_id == "987654"  # 숫자도 문자열로
    assert not vids[2].available
    assert vids[0].file_name == "리눅스 1주차 1강 소개 개요.mp4"


def test_safe_name_and_disposition():
    assert safe_file_name('a/b:c*?"<>|d') == "a b c d"
    cd = content_disposition("리눅스 1강.mp4")
    assert 'filename="lecture.mp4"' in cd and "filename*=UTF-8''%EB%A6%AC" in cd
    assert 'filename="Linux 01.mp4"' in content_disposition("Linux 01.mp4")


# --- 파일로 받기, 이어받기 ----------------------------------------------------


@respx.mock
async def test_download_resumes_from_part_file(tmp_path):
    data = b"0123456789"
    dest = tmp_path / "v.mp4"
    (tmp_path / "v.mp4.part").write_bytes(data[:4])

    def handler(request):
        assert request.headers["Range"] == "bytes=4-"
        assert request.headers["Referer"] == "https://commons.ssu.ac.kr/"
        return httpx.Response(206, content=data[4:], headers={"Content-Range": "bytes 4-9/10"})

    respx.get(f"{CDN}/v.mp4").mock(side_effect=handler)
    seen = []
    async with httpx.AsyncClient() as http:
        await download_to_file(http, f"{CDN}/v.mp4", dest, lambda d, t: seen.append((d, t)))
    assert dest.read_bytes() == data and not (tmp_path / "v.mp4.part").exists()
    assert seen[-1] == (10, 10)


@respx.mock
async def test_download_detects_truncation(tmp_path):
    respx.get(f"{CDN}/v.mp4").respond(200, content=b"abc", headers={"Content-Length": "3"})
    respx.get(f"{CDN}/w.mp4").respond(206, content=b"abc", headers={"Content-Range": "bytes 0-2/10"})
    async with httpx.AsyncClient() as http:
        assert (await download_to_file(http, f"{CDN}/v.mp4", tmp_path / "v.mp4")).read_bytes() == b"abc"
        with pytest.raises(MediaError):
            await download_to_file(http, f"{CDN}/w.mp4", tmp_path / "w.mp4")
    assert (tmp_path / "w.mp4.part").exists()  # 다시 실행하면 이어받을 수 있게 남겨 둔다


# --- API: 본인 과목만, 저장 없이 중계 -------------------------------------------

C = "https://canvas.ssu.ac.kr"
VIDEO_XML = f"<content><content_playing_info><media_uri>{CDN}/[MEDIA_FILE]</media_uri><main_media_list><main_media>lec.mp4</main_media></main_media_list></content_playing_info></content>"


async def _with_course(api):
    h = await _signup(api)
    async with db_base.sessionmaker()() as db:
        db.add(CourseRow(user_id=1, lms_course_id=45446, name="리눅스"))
        await db.commit()
    raw = [m.model_dump() for m in _modules()]
    respx.get(f"{C}/learningx/api/v1/courses/45446/modules").respond(200, json=raw)
    return h


@respx.mock
async def test_list_videos_and_ownership(env):
    api, _ = env
    h = await _with_course(api)
    vids = (await api.get("/courses/45446/videos", headers=h)).json()
    assert [(v["module_item_id"], v["available"]) for v in vids] == [(11, True), (13, True), (21, False)]
    assert (await api.get("/courses/99999/videos", headers=h)).status_code == 404  # 수강 과목 아님


@respx.mock
async def test_link_rules(env):
    api, _ = env
    h = await _with_course(api)
    assert (await api.post("/courses/45446/videos/12/download-link", headers=h)).status_code == 404  # pdf
    assert (await api.post("/courses/45446/videos/21/download-link", headers=h)).status_code == 409  # 미공개
    assert (await api.post("/courses/99999/videos/11/download-link", headers=h)).status_code == 404
    r = await api.post("/courses/45446/videos/11/download-link", headers=h)
    assert r.status_code == 200 and r.json()["url"].startswith("/downloads/")
    # 다운로드 토큰으로 API에 로그인할 수 없다
    token = r.json()["url"].rsplit("/", 1)[1]
    assert (await api.get("/me", headers={"Authorization": f"Bearer {token}"})).status_code == 401


@respx.mock
async def test_download_relays_without_storing(env):
    api, _ = env
    h = await _with_course(api)
    url = (await api.post("/courses/45446/videos/11/download-link", headers=h)).json()["url"]
    php = respx.get(CONTENT_PHP).respond(200, text=VIDEO_XML)
    cdn = respx.get(f"{CDN}/lec.mp4").respond(206, content=b"xyz",
                                              headers={"Content-Range": "bytes 5-7/100", "Content-Length": "3"})
    r = await api.get(url, headers={"Range": "bytes=5-7"})  # Authorization 없이 링크만으로
    assert r.status_code == 206 and r.content == b"xyz"
    assert r.headers["content-range"] == "bytes 5-7/100"
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    assert php.calls.last.request.url.params["content_id"] == "abc123"
    assert cdn.calls.last.request.headers["Range"] == "bytes=5-7"
    assert cdn.calls.last.request.headers["Referer"] == "https://commons.ssu.ac.kr/"


@respx.mock
async def test_download_bad_token_and_unsupported(env):
    api, _ = env
    h = await _with_course(api)
    assert (await api.get("/downloads/not-a-token")).status_code == 401
    url = (await api.post("/courses/45446/videos/11/download-link", headers=h)).json()["url"]
    respx.get(CONTENT_PHP).respond(200, text="<content><content_type>sharedocs</content_type><content_playing_info/></content>")
    r = await api.get(url)
    assert r.status_code == 422 and "sharedocs" in r.json()["detail"]


# --- 시청기간이 끝난 뒤를 위한 주소 저장 ----------------------------------------


def _php(xml_by_id: dict[str, str]):
    return respx.get(CONTENT_PHP).mock(
        side_effect=lambda r: httpx.Response(200, text=xml_by_id[r.url.params["content_id"]]))


async def _save(user_id=1, modules=None, budget=None):
    from app.services.saved_videos import ResolveBudget, save_course_videos
    async with db_base.sessionmaker()() as db, httpx.AsyncClient() as http:
        await save_course_videos(db, http, user_id, Course(id=45446, name="리눅스"), modules or _modules(),
                                 budget or ResolveBudget())
        await db.commit()


@respx.mock
async def test_sync_saves_media_urls_once(env):
    api, _ = env
    h = await _signup(api)
    sharedocs = "<content><content_type>sharedocs</content_type><content_playing_info/></content>"
    php = _php({"abc123": VIDEO_XML, "987654": sharedocs})
    await _save()
    await _save()  # 이미 찾았거나 지원하지 않는 형식이면 다시 묻지 않는다
    assert php.call_count == 2

    saved = (await api.get("/saved-videos", headers=h)).json()
    assert [(v["module_item_id"], v["saved"]) for v in saved] == [(11, True), (13, False)]  # 미공개(21)는 없음
    assert "sharedocs" in saved[1]["resolve_error"]


@respx.mock
async def test_resolve_budget_and_changed_content(env):
    from app.services.saved_videos import ResolveBudget
    api, _ = env
    h = await _signup(api)
    php = _php({"abc123": VIDEO_XML, "987654": VIDEO_XML, "new1": VIDEO_XML.replace("lec.mp4", "new.mp4")})
    await _save(budget=ResolveBudget(left=1))
    assert php.call_count == 1
    await _save()
    assert php.call_count == 2

    # 교수가 영상을 바꾸면 주소를 다시 찾는다
    mods = _modules()
    mods[1].module_items[1].content_data.item_content_data.content_id = "new1"
    await _save(modules=mods)
    saved = {v["module_item_id"]: v for v in (await api.get("/saved-videos", headers=h)).json()}
    assert php.calls.last.request.url.params["content_id"] == "new1" and saved[11]["saved"]


@respx.mock
async def test_saved_download_works_after_course_is_gone(env):
    api, _ = env
    h = await _signup(api)
    _php({"abc123": VIDEO_XML, "987654": VIDEO_XML})
    await _save()
    saved = (await api.get("/saved-videos", headers=h, params={"course_id": 45446})).json()

    # 지난 학기: 수강 과목에도 없고 LMS·content.php도 부르지 않는다
    respx.reset()
    cdn = respx.get(f"{CDN}/lec.mp4").respond(200, content=b"video")
    r = await api.post(f"/saved-videos/{saved[0]['id']}/download-link", headers=h)
    assert r.status_code == 200
    got = await api.get(r.json()["url"])
    assert got.status_code == 200 and got.content == b"video"
    assert cdn.calls.last.request.headers["Referer"] == "https://commons.ssu.ac.kr/"

    cdn.respond(404)
    assert (await api.get(r.json()["url"])).status_code == 410  # 학교가 영상을 지웠다

    other = await _signup(api, "20209999")
    assert (await api.post(f"/saved-videos/{saved[0]['id']}/download-link", headers=other)).status_code == 404
    assert (await api.get("/saved-videos", headers=other)).json() == []


@respx.mock
async def test_saved_link_requires_resolved_url(env):
    api, _ = env
    h = await _signup(api)
    respx.get(CONTENT_PHP).mock(side_effect=httpx.ConnectError("down"))
    await _save()
    saved = (await api.get("/saved-videos", headers=h)).json()
    assert not saved[0]["saved"] and saved[0]["resolve_error"] is None  # 일시 오류는 다음 동기화에서 다시
    assert (await api.post(f"/saved-videos/{saved[0]['id']}/download-link", headers=h)).status_code == 409
