"""강의 영상(mp4) 주소 찾기와 내려받기.

soongsil-lms-downloader(src/main/services/lms.ts, download.ts)의 방식을 옮겼다.
1. 모듈 항목의 item_content_data.content_id
2. commons.ssu.ac.kr content.php → XML
3. XML에서 mp4 주소 추출(콘텐츠 형식마다 구조가 달라 4가지 전략을 차례로 시도)
4. Referer: commons.ssu.ac.kr 헤더로 mp4를 받는다

개인 복습용이다. 본인이 수강하는 과목에서 확인된 항목만 받도록 호출하는 쪽(API, CLI)에서 제한한다.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from app.lms.errors import LmsError
from app.lms.models import Course, Module

CONTENT_PHP = "https://commons.ssu.ac.kr/viewer/ssplayer/uniplayer_support/content.php"
COMMONS_HEADERS = {"Referer": "https://commons.ssu.ac.kr/", "Origin": "https://commons.ssu.ac.kr"}
VIDEO_TYPES = {"movie", "everlec", "video", "mp4"}
# XML이 다른 곳을 가리키더라도 학교 CDN·도메인이 아니면 받지 않는다.
ALLOWED_MEDIA_HOSTS = ("commonscdn.com", "ssu.ac.kr")


class MediaError(LmsError):
    """영상 주소를 찾지 못했거나 내려받을 수 없다."""


# ----------------------------------------------------------------------
# 목록
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CourseVideo:
    course_id: int
    course_name: str
    module_id: int
    module_title: str
    module_position: int
    module_item_id: int
    item_position: int
    title: str
    content_id: str | None  # 아직 열리지 않았으면 None
    content_type: str
    duration: float | None
    size: int | None
    completed: bool

    @property
    def available(self) -> bool:
        return self.content_id is not None

    @property
    def file_name(self) -> str:
        return safe_file_name(f"{self.course_name} {self.module_title} {self.title}") + ".mp4"


def course_videos(course: Course, modules: list[Module]) -> list[CourseVideo]:
    """과목의 영상 항목을 LMS 화면 순서대로 반환한다(완료한 영상 포함)."""
    result: list[CourseVideo] = []
    for module in modules:
        for item in module.module_items:
            data = item.content_data
            icd = data.item_content_data if data else None
            if data is None or icd is None or data.item_content_type != "commons":
                continue
            if (icd.content_type or "") not in VIDEO_TYPES:
                continue
            cid = icd.content_id if icd.content_id and icd.content_id != "not_open" else None
            result.append(
                CourseVideo(
                    course_id=course.id, course_name=course.name,
                    module_id=module.module_id or 0, module_title=module.title or "",
                    module_position=module.position or 0, module_item_id=item.module_item_id or 0,
                    item_position=item.position or 0, title=data.title or item.title or "",
                    content_id=cid, content_type=icd.content_type or "", duration=icd.duration,
                    size=icd.total_file_size, completed=bool(item.completed),
                )
            )
    result.sort(key=lambda v: (v.module_position, v.item_position))
    return result


# ----------------------------------------------------------------------
# 주소 추출
# ----------------------------------------------------------------------


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    node = el.find(path)
    return node.text.strip() if node is not None and node.text and node.text.strip() else None


def _texts(el: ET.Element | None, path: str) -> list[str]:
    if el is None:
        return []
    return [n.text.strip() for n in el.findall(path) if n.text and n.text.strip()]


def extract_media_url(xml: str) -> str | None:
    """content.php XML에서 mp4 주소를 찾는다. 못 찾으면 None."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    content = root if root.tag == "content" else root.find(".//content")
    if content is None:
        return None
    info = content.find("content_playing_info")
    if info is None:
        return None

    file_name = _text(info, "story_list/story/main_media_list/main_media") or _text(info, "main_media_list/main_media")
    media_uris = _texts(content, "service_root/media/media_uri") or _texts(info, "media_uri") or _texts(content, "media_uri")

    # 1. "[MEDIA_FILE]" 자리에 파일명 넣기
    if file_name and media_uris and "[MEDIA_FILE]" in media_uris[0]:
        return media_uris[0].replace("[MEDIA_FILE]", file_name)
    # 2. 이미 완전한 mp4 주소
    for uri in media_uris:
        if ".mp4" in uri and "[" not in uri:
            return uri
    # 3. 구형 콘텐츠(video1 등)의 desktop html5 경로
    desktop = _text(info, "main_media/desktop/html5/media_uri")
    if desktop and ".mp4" in desktop:
        return desktop
    # 4. content_uri(web_files → media_files) + 파일명
    content_uri = _text(info, "content_uri")
    if file_name and content_uri:
        return re.sub(r"web_files$", "media_files", content_uri) + "/" + file_name
    return None


def content_type_of(xml: str) -> str:
    m = re.search(r"<content_type>([^<]+)</content_type>", xml)
    return m.group(1) if m else "알 수 없음"


def check_media_host(url: str) -> str:
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in ALLOWED_MEDIA_HOSTS):
        raise MediaError(f"허용되지 않은 영상 주소입니다: {host}")
    return url


async def resolve_media_url(http: httpx.AsyncClient, content_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", content_id):
        raise MediaError("잘못된 콘텐츠 ID입니다.")
    resp = await http.get(CONTENT_PHP, params={"content_id": content_id, "_": int(time.time() * 1000)}, headers=COMMONS_HEADERS)
    if resp.status_code != 200:
        raise MediaError(f"영상 정보를 가져오지 못했습니다 (HTTP {resp.status_code}).")
    url = extract_media_url(resp.text)
    if not url:
        raise MediaError(f"내려받을 수 없는 콘텐츠 형식입니다 ({content_type_of(resp.text)}).")
    return check_media_host(url)


# ----------------------------------------------------------------------
# 파일로 내려받기 (CLI용). .part 파일로 받다가 끊기면 이어받는다.
# ----------------------------------------------------------------------

Progress = Callable[[int, int | None], Awaitable[None] | None]


async def download_to_file(http: httpx.AsyncClient, url: str, dest: Path, progress: Progress | None = None) -> Path:
    check_media_host(url)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = dict(COMMONS_HEADERS)
    if have:
        headers["Range"] = f"bytes={have}-"
    async with http.stream("GET", url, headers=headers) as resp:
        if resp.status_code == 416:  # 이미 다 받음
            part.rename(dest)
            return dest
        if resp.status_code not in (200, 206):
            raise MediaError(f"영상을 내려받지 못했습니다 (HTTP {resp.status_code}).")
        if resp.status_code == 200:
            have = 0  # 서버가 이어받기를 무시하면 처음부터
        total = _total_size(resp, have)
        with part.open("ab" if have else "wb") as f:
            done = have
            async for chunk in resp.aiter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    r = progress(done, total)
                    if r is not None:
                        await r
    if total is not None and part.stat().st_size != total:
        raise MediaError("내려받은 크기가 맞지 않습니다. 다시 실행하면 이어받습니다.")
    part.rename(dest)
    return dest


def _total_size(resp: httpx.Response, offset: int) -> int | None:
    cr = resp.headers.get("content-range")
    if cr and "/" in cr and not cr.endswith("/*"):
        return int(cr.rsplit("/", 1)[1])
    cl = resp.headers.get("content-length")
    return int(cl) + offset if cl else None


# ----------------------------------------------------------------------
# 이름
# ----------------------------------------------------------------------


def safe_file_name(name: str, limit: int = 150) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:limit].rstrip(" .") or "video")


def content_disposition(file_name: str) -> str:
    # 구형 브라우저용 대체 이름. 한글이 섞이면 잘린 이름 대신 일반 이름을 쓴다(최신 브라우저는 filename*를 쓴다).
    if file_name.isascii():
        ascii_name = file_name.replace('"', "")
    else:
        suffix = Path(file_name).suffix
        ascii_name = "lecture.mp4" if suffix == ".mp4" else f"download{suffix if suffix.isascii() else ''}"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"
