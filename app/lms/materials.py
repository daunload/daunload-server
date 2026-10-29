"""수업자료(영상이 아닌 파일) 목록과 다운로드 주소.

soongsil-lms-downloader v1.7.0(commons 파일)과 위키 첨부 기능을 옮겼다.
- commons 파일: 모듈 항목 하나 = 파일 하나. content.php 없이 다운로드 주소로 바로 받는다.
- 위키 첨부: wiki_page 본문의 instructure_file_link에서 같은 과목의 Canvas 파일만 모은다.

개발용 CLI (`.env`의 LMS_ID/LMS_PASSWORD 사용):
  python -m app.lms.materials list --course <id>
  python -m app.lms.materials get  --course <id> --key <key> [--out 폴더]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlparse

from app.lms.client import LmsClient
from app.lms.errors import InvalidCredentials, LmsError, SessionExpired
from app.lms.media import MediaError, safe_file_name
from app.lms.models import Course, Module

log = logging.getLogger(__name__)

COMMONS_DOWNLOAD = "https://commons.ssu.ac.kr/index.php"
# 리다이렉트 뒤 최종 주소도 이 안이어야 받는다. Canvas가 외부 스토리지로 보내면 실계정 확인 후 추가한다.
ALLOWED_FILE_HOSTS = ("ssu.ac.kr", "commonscdn.com")
_FILE_PATH = re.compile(r"/courses/(\d+)/files/(\d+)")


@dataclass(frozen=True)
class CourseMaterial:
    key: str  # "c{module_item_id}"(commons) | "f{file_id}"(위키 첨부)
    kind: str  # "commons_file" | "canvas_file"
    course_id: int
    module_id: int
    module_title: str
    module_position: int
    item_position: int
    title: str
    file_name: str
    source_id: str  # commons content_id | Canvas file id
    size: int | None


def commons_download_url(content_id: str) -> str:
    return (f"{COMMONS_DOWNLOAD}?module=xn_media_content2013&act=dispXn_media_content2013DownloadContent"
            f"&content_id={quote(content_id, safe='')}")


def check_file_host(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in ALLOWED_FILE_HOSTS):
        raise MediaError(f"허용되지 않은 파일 주소입니다: {host}")
    return url


def _ordered(modules: list[Module]):
    for module in sorted(modules, key=lambda m: m.position or 0):
        for item in sorted(module.module_items, key=lambda i: i.position or 0):
            yield module, item


def commons_files(course: Course, modules: list[Module]) -> list[CourseMaterial]:
    result = []
    for module, item in _ordered(modules):
        data = item.content_data
        icd = data.item_content_data if data else None
        if data is None or icd is None or data.item_content_type != "commons" or icd.content_type != "file":
            continue
        if not icd.content_id or icd.content_id == "not_open":
            continue
        title = data.title or item.title or ""
        result.append(CourseMaterial(
            key=f"c{item.module_item_id}", kind="commons_file", course_id=course.id,
            module_id=module.module_id or 0, module_title=module.title or "", module_position=module.position or 0,
            item_position=item.position or 0, title=title, file_name=safe_file_name(icd.file_name or title),
            source_id=icd.content_id, size=icd.total_file_size,
        ))
    return result


class _FileLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[dict, list[str]]] = []
        self._current: tuple[dict, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and "instructure_file_link" in (a.get("class") or "").split():
            self._current = (a, [])

    def handle_data(self, data):
        if self._current:
            self._current[1].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current:
            self.links.append(self._current)
            self._current = None


def wiki_file_links(html: str, course_id: int) -> list[tuple[str, str]]:
    """위키 본문에서 같은 과목의 Canvas 파일 링크를 (file_id, 이름)으로 뽑는다."""
    parser = _FileLinks()
    parser.feed(html or "")
    parser.close()
    result = []
    for attrs, text in parser.links:
        match = None
        for src in (attrs.get("data-api-endpoint"), attrs.get("href")):
            host = urlparse(src or "").hostname
            if src and host in (None, "canvas.ssu.ac.kr") and (match := _FILE_PATH.search(src)):
                break
            match = None
        if match is None or int(match.group(1)) != course_id:
            continue
        name = (attrs.get("title") or "".join(text)).strip() or "첨부파일"
        result.append((match.group(2), name))
    return result


async def course_materials(client: LmsClient, course: Course, modules: list[Module]) -> list[CourseMaterial]:
    result = commons_files(course, modules)
    seen: set[str] = set()
    for module, item in _ordered(modules):
        slug = item.content_data.url if item.content_data else None
        if item.content_type != "wiki_page" or not slug:
            continue
        try:
            page = await client.get_json(f"/api/v1/courses/{course.id}/pages/{quote(slug, safe='')}")
        except (InvalidCredentials, SessionExpired):
            raise
        except LmsError as e:
            log.warning("위키 페이지를 가져오지 못했습니다 %s/%s: %s", course.id, slug, e)
            continue
        page = page if isinstance(page, dict) else {}
        title = page.get("title") or (item.content_data.title if item.content_data else None) or item.title or ""
        for file_id, name in wiki_file_links(page.get("body") or "", course.id):
            key = f"f{file_id}"
            if key in seen:
                continue
            seen.add(key)
            result.append(CourseMaterial(
                key=key, kind="canvas_file", course_id=course.id, module_id=module.module_id or 0,
                module_title=module.title or "", module_position=module.position or 0,
                item_position=item.position or 0, title=title, file_name=safe_file_name(name),
                source_id=file_id, size=None,
            ))
    result.sort(key=lambda m: (m.module_position, m.item_position))  # 안정 정렬: 본문 순서 유지
    return result


# ----------------------------------------------------------------------
# CLI: 실계정으로 목록·다운로드 주소·리다이렉트 호스트를 확인한다
# ----------------------------------------------------------------------


async def _main(argv: list[str]) -> int:
    import httpx

    from app.config import get_settings
    from app.lms import auth
    from app.lms.media import COMMONS_HEADERS

    p = argparse.ArgumentParser(prog="python -m app.lms.materials")
    p.add_argument("command", choices=["list", "get"])
    p.add_argument("--course", type=int, required=True)
    p.add_argument("--key")
    p.add_argument("--out", type=Path, default=Path("downloads"))
    args = p.parse_args(argv)

    s = get_settings()
    async with await LmsClient.login(s.lms_id, s.lms_password) as client:
        term = await client.get_current_term()
        course = next((c for c in await client.get_courses(term.id) if c.id == args.course), None)
        if course is None:
            print("이번 학기 수강 과목이 아닙니다.")
            return 1
        mats = await course_materials(client, course, await client.get_modules(course.id))
        if args.command == "list":
            for m in mats:
                print(f"[{m.module_title}] {m.title} / {m.file_name}  key={m.key}  {m.kind}  {m.size or '?'}B")
            return 0
        mat = next((m for m in mats if m.key == args.key), None)
        if mat is None:
            print("목록에 없는 key입니다.")
            return 1
        if mat.kind == "canvas_file":
            meta = await client.get_json(f"/api/v1/courses/{course.id}/files/{mat.source_id}")
            url, headers, cookies = meta["url"], {}, client.export_cookies()
        else:
            url, headers, cookies = commons_download_url(mat.source_id), COMMONS_HEADERS, []
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as http:
        auth.import_cookies(http, cookies)
        async with http.stream("GET", url, headers=headers) as resp:
            print("리다이렉트:", " → ".join(str(r.url.host) for r in resp.history) or "(없음)")
            print("최종:", resp.status_code, resp.url.host, resp.headers.get("content-type"))
            check_file_host(str(resp.url))
            dest = args.out / safe_file_name(course.name) / mat.file_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(1 << 20):
                    f.write(chunk)
    print("저장:", dest)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
