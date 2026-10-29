import pytest
import respx

from app.lms.auth import LmsSession
from app.lms.client import LmsClient
from app.lms.materials import (
    check_file_host,
    commons_download_url,
    commons_files,
    course_materials,
    wiki_file_links,
)
from app.lms.media import MediaError
from app.lms.models import Course, Module

C = "https://canvas.ssu.ac.kr"
COURSE = Course(id=45446, name="리눅스")


def _modules():
    def commons(mid, pos, ctype, cid, file_name=None, title=None):
        return {"module_item_id": mid, "position": pos,
                "content_data": {"item_content_type": "commons", "title": title or f"자료{mid}",
                                 "item_content_data": {"content_type": ctype, "content_id": cid,
                                                       "file_name": file_name, "total_file_size": 1000}}}

    def wiki(mid, pos, slug, title):
        return {"module_item_id": mid, "position": pos, "content_type": "wiki_page",
                "content_data": {"title": title, "url": slug}}

    return [Module.model_validate(m) for m in [
        {"module_id": 2, "title": "2주차", "position": 2, "module_items": [
            commons(21, 1, "file", "not_open"), wiki(22, 2, "week-2", "2주차 자료")]},
        {"module_id": 1, "title": "1주차", "position": 1, "module_items": [
            commons(12, 2, "file", "f12", file_name="1장.pptx"), commons(11, 1, "movie", "m11"),
            wiki(13, 3, "week-1", "1주차 자료")]},
    ]]


def test_commons_files_skip_videos_and_unopened():
    mats = commons_files(COURSE, _modules())
    assert [(m.key, m.kind, m.file_name, m.source_id) for m in mats] == [("c12", "commons_file", "1장.pptx", "f12")]
    assert mats[0].module_title == "1주차" and mats[0].size == 1000


def test_wiki_links_from_endpoint_or_href():
    html = f"""<p><a class="instructure_file_link inline_disabled" title="강의노트.pdf"
        data-api-endpoint="{C}/api/v1/courses/45446/files/701" href="{C}/courses/45446/files/701?wrap=1">노트</a>
      <a class="instructure_file_link" href="/courses/45446/files/702/download?verifier=x">실습 &amp; 과제.zip</a></p>"""
    assert wiki_file_links(html, 45446) == [("701", "강의노트.pdf"), ("702", "실습 & 과제.zip")]


def test_wiki_links_skip_other_course_and_external():
    html = """<a class="instructure_file_link" href="/courses/99999/files/1">남의 과목</a>
      <a class="instructure_file_link" href="https://evil.example.com/courses/45446/files/2">외부</a>
      <a href="/courses/45446/files/3">클래스 없음</a>"""
    assert wiki_file_links(html, 45446) == []


@respx.mock
async def test_course_materials_merges_wiki_files_in_week_order():
    respx.get(f"{C}/api/v1/courses/45446/pages/week-1").respond(200, json={
        "title": "1주차 자료", "body": '<a class="instructure_file_link" href="/courses/45446/files/701">a.pdf</a>'})
    respx.get(f"{C}/api/v1/courses/45446/pages/week-2").respond(200, json={
        "title": "2주차 자료",
        "body": '<a class="instructure_file_link" href="/courses/45446/files/701">a.pdf</a>'
                '<a class="instructure_file_link" href="/courses/45446/files/702">b.pdf</a>'})
    async with LmsClient(LmsSession(user_id="1", bearer_token="B", cookies=[]), min_interval=0) as client:
        mats = await course_materials(client, COURSE, _modules())
    assert [(m.key, m.module_title, m.file_name) for m in mats] == [
        ("c12", "1주차", "1장.pptx"), ("f701", "1주차", "a.pdf"), ("f702", "2주차", "b.pdf")]
    assert mats[1].kind == "canvas_file" and mats[1].title == "1주차 자료" and mats[1].source_id == "701"


@respx.mock
async def test_failed_wiki_page_is_skipped():
    respx.get(f"{C}/api/v1/courses/45446/pages/week-1").respond(500)
    respx.get(f"{C}/api/v1/courses/45446/pages/week-2").respond(200, json={
        "body": '<a class="instructure_file_link" href="/courses/45446/files/702">b.pdf</a>'})
    async with LmsClient(LmsSession(user_id="1", bearer_token="B", cookies=[]), min_interval=0) as client:
        mats = await course_materials(client, COURSE, _modules())
    assert [m.key for m in mats] == ["c12", "f702"]
    assert mats[1].title == "2주차 자료"  # 페이지 제목이 없으면 모듈 항목 제목


def test_download_url_and_host_check():
    assert commons_download_url("f12").endswith("act=dispXn_media_content2013DownloadContent&content_id=f12")
    assert check_file_host("https://canvas.ssu.ac.kr/files/1/download")
    for bad in ["http://canvas.ssu.ac.kr/x", "https://evil.example.com/x", "https://ssu.ac.kr.evil.io/x"]:
        with pytest.raises(MediaError):
            check_file_host(bad)
