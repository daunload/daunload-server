# 수업자료 · 다운로드 이력 · 썸네일 · 브라우저 Whisper 전사 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수업자료(commons 파일 + 위키 첨부) 다운로드, 다운로드 이력, 영상 썸네일, 브라우저 로컬 Whisper 전사(결과는 서버 저장)를 추가한다.

**Architecture:** LMS 연동은 `app/lms/`(DB 없음), DB와 엮는 계층은 `app/services/`, HTTP는 `app/api/`라는 기존 경계를 따른다. 파일은 서버에 저장하지 않고 지금의 서명 링크 중계(`/downloads/{token}`)를 kind별로 넓힌다. 전사는 서버가 ffmpeg로 16kHz PCM을 스트리밍하고, 브라우저 Web Worker(transformers.js)가 전사해 결과만 서버에 PUT한다.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, httpx, respx, pytest-asyncio, ffmpeg, 정적 JS(빌드 없음), transformers.js 4.3.1(jsdelivr), Whisper small(onnx-community).

**Spec:** `docs/superpowers/specs/2026-10-29-materials-and-transcripts-design.md`

## Global Constraints

- 파일·오디오는 서버에 저장하지 않는다. 중계·스트리밍만 한다.
- 본인 과목(`CourseRow`)에서 LMS 목록으로 확인된 항목만 링크를 발급한다(임의 content_id/file_id 거부).
- 다운로드 링크 토큰은 10분(`DOWNLOAD_LINK_MINUTES`), 사용자당 동시 중계 2개(`MAX_DOWNLOADS_PER_USER`), 서버 전체 ffmpeg 4개(`MAX_AUDIO_STREAMS`).
- 업스트림 최종 주소는 `https`이고 허용 호스트(`ssu.ac.kr`, `commonscdn.com`)여야 한다.
- 전사 저장 본문 최대 2MB, segments 최대 20,000개.
- Whisper 모델 `onnx-community/whisper-small`, `language: "korean"`, `task: "transcribe"`, 30초 창 / 5초 겹침, 브라우저 앞서 읽기 60초.
- 외부 스크립트는 `cdn.jsdelivr.net`에서만 불러온다. 사용자에게 보이는 문구는 한국어 존댓말(기존 화면과 같은 말투)로 쓴다.
- 코드 주석·docstring은 기존처럼 한국어로 짧게 쓴다.

## Review Focus

1. 위키 본문 링크가 다른 과목 파일이나 외부 주소를 가리킬 때 → 목록에서 빠지고 링크 발급도 404여야 한다. (Task 2 `test_wiki_links_skip_other_course_and_external`)
2. Canvas가 파일 주소를 허용 목록 밖 호스트로 줄 때 → 받지 않고 422. (Task 3 `test_canvas_file_outside_host_rejected`)
3. 오디오 토큰을 `/downloads/`에, 다운로드 토큰을 `/audio/`에 쓸 때 → 401. (Task 5 `test_tokens_are_not_interchangeable`)
4. 클라이언트가 오디오 스트림 도중 끊을 때 → ffmpeg 프로세스가 종료되어야 한다. (Task 5 `test_pcm_stream_kills_ffmpeg_on_close`)
5. 지난 학기 과목(저장된 영상만 있음)의 전사 저장 → 허용되어야 하고, 남의 과목은 404. (Task 6 `test_save_for_saved_video_of_past_course`)

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `app/lms/models.py` (수정) | `ContentData.url`, `ItemContentData.file_name` 필드 |
| `app/lms/materials.py` (신규) | 수업자료 목록 추출, 다운로드 주소, 허용 호스트, 개발용 CLI |
| `app/lms/media.py` (수정) | `CourseVideo.thumbnail_url` |
| `app/lms/client.py` (수정) | `export_cookies()` |
| `app/lms/audio.py` (신규) | ffmpeg → 16kHz s16le PCM 비동기 스트림 |
| `app/db/models.py` (수정) | `DownloadRecord`, `Transcript` |
| `migrations/versions/d1e2f3a4b5c6_downloads_and_transcripts.py` (신규) | 두 테이블 |
| `app/services/downloads.py` (신규) | 이력 기록·조회 |
| `app/services/transcripts.py` (신규) | 과목별 전사 ID 조회 |
| `app/api/routes_media.py` (수정) | 영상·자료 링크, kind별 중계, 오디오 링크·스트림, 응답 필드 |
| `app/api/routes_transcripts.py` (신규) | 전사 저장·조회·삭제 |
| `app/main.py`, `app/config.py`, `Dockerfile`, `.env.example` (수정) | 라우터 등록, `max_audio_streams`, ffmpeg 설치 |
| `app/web/downloads.js` (신규) | 폴더에 여러 개 받기 큐 |
| `app/web/transcribe.js` (신규) | 전사 큐·진행 패널·결과 대화상자 |
| `app/web/whisper.worker.js` (신규) | transformers.js Whisper Worker |
| `app/web/app.js`, `index.html`, `style.css` (수정) | 영상/수업자료 탭, 배지, 썸네일, 버튼 |
| `README.md` (수정) | 기능·API·한계 |

---

### Task 0: 기준 커밋

이 저장소에는 아직 커밋이 없다. 이후 태스크의 커밋이 의미 있도록 지금 코드를 먼저 커밋한다.

- [ ] **Step 1: `.env`가 무시되는지 확인**

Run: `git check-ignore .env && git status --short | head -20`
Expected: `.env` 출력, 목록에 `.env`가 없음

- [ ] **Step 2: 기준 커밋과 작업 브랜치**

```bash
git add -A
git commit -m "chore: initial import of SSU LMS server"
git branch -M main
git switch -c feat/materials-and-transcripts
```

---

### Task 1: DB 모델과 마이그레이션

**Files:**
- Modify: `app/db/models.py` (파일 끝에 추가)
- Create: `migrations/versions/d1e2f3a4b5c6_downloads_and_transcripts.py`
- Test: `tests/test_db_models.py`

**Interfaces:**
- Produces: `DownloadRecord(user_id, lms_course_id, item_key, kind, title, count, first_at, last_at)`, `Transcript(user_id, lms_course_id, module_item_id, course_name, title, text, segments: list[dict], model, duration, created_at, updated_at)`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_db_models.py`:

```python
from sqlalchemy import select

from app.db import base as db_base
from app.db.models import DownloadRecord, Transcript, User
from tests.test_api import env  # noqa: F401  (fixture)


async def test_records_and_transcripts_cascade_with_user(env):
    async with db_base.sessionmaker()() as db:
        user = User(student_id="1", password_hash="x", consent_at=db_base.utcnow())
        db.add(user)
        await db.flush()
        db.add(DownloadRecord(user_id=user.id, lms_course_id=1, item_key="v11", kind="video", title="영상"))
        db.add(Transcript(user_id=user.id, lms_course_id=1, module_item_id=11, course_name="리눅스", title="1강",
                          text="안녕하세요", segments=[{"start": 0.0, "end": 1.5, "text": "안녕하세요"}], model="whisper-small/wasm"))
        await db.commit()
        rec = (await db.scalars(select(DownloadRecord))).one()
        assert rec.count == 1 and rec.first_at is not None and rec.last_at is not None
        tr = (await db.scalars(select(Transcript))).one()
        assert tr.segments[0]["text"] == "안녕하세요" and tr.updated_at is not None
        await db.delete(user)
        await db.commit()
        assert (await db.scalars(select(DownloadRecord))).all() == []
        assert (await db.scalars(select(Transcript))).all() == []
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_db_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'DownloadRecord'`

- [ ] **Step 3: 모델 추가** (`app/db/models.py` 끝)

```python
class DownloadRecord(Base):
    """다운로드 링크를 발급한 기록. 브라우저에서 저장을 마쳤는지는 서버가 알 수 없다."""

    __tablename__ = "download_records"
    __table_args__ = (UniqueConstraint("user_id", "lms_course_id", "item_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lms_course_id: Mapped[int] = mapped_column(Integer)
    item_key: Mapped[str] = mapped_column(String(64))  # 영상 "v{module_item_id}", 자료 CourseMaterial.key
    kind: Mapped[str] = mapped_column(String(16))  # video / commons_file / canvas_file
    title: Mapped[str] = mapped_column(String(500))
    count: Mapped[int] = mapped_column(Integer, default=1)
    first_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_at: Mapped[datetime] = mapped_column(default=utcnow)


class Transcript(Base):
    """브라우저 Whisper로 만든 강의 전사 결과. 다시 전사하면 덮어쓴다."""

    __tablename__ = "transcripts"
    __table_args__ = (UniqueConstraint("user_id", "lms_course_id", "module_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lms_course_id: Mapped[int] = mapped_column(Integer)
    module_item_id: Mapped[int] = mapped_column(Integer)
    course_name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # [{start, end, text}]
    model: Mapped[str] = mapped_column(String(100))
    duration: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
```

- [ ] **Step 4: 마이그레이션 작성**

`migrations/versions/d1e2f3a4b5c6_downloads_and_transcripts.py`:

```python
"""download records and transcripts

Revision ID: d1e2f3a4b5c6
Revises: c3d5e7f9a1b2
Create Date: 2026-10-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db.base


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c3d5e7f9a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('download_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('lms_course_id', sa.Integer(), nullable=False),
    sa.Column('item_key', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('first_at', app.db.base.UtcDateTime(timezone=True), nullable=False),
    sa.Column('last_at', app.db.base.UtcDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'lms_course_id', 'item_key')
    )
    op.create_index(op.f('ix_download_records_user_id'), 'download_records', ['user_id'], unique=False)
    op.create_table('transcripts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('lms_course_id', sa.Integer(), nullable=False),
    sa.Column('module_item_id', sa.Integer(), nullable=False),
    sa.Column('course_name', sa.String(length=255), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('segments', sa.JSON(), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('duration', sa.Float(), nullable=True),
    sa.Column('created_at', app.db.base.UtcDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.db.base.UtcDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'lms_course_id', 'module_item_id')
    )
    op.create_index(op.f('ix_transcripts_user_id'), 'transcripts', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_transcripts_user_id'), table_name='transcripts')
    op.drop_table('transcripts')
    op.drop_index(op.f('ix_download_records_user_id'), table_name='download_records')
    op.drop_table('download_records')
```

- [ ] **Step 5: 테스트와 마이그레이션 확인**

Run: `uv run pytest tests/test_db_models.py -v`
Expected: PASS

Run: `rm -f /tmp/mig.db && DATABASE_URL=sqlite+aiosqlite:////tmp/mig.db uv run alembic upgrade head && DATABASE_URL=sqlite+aiosqlite:////tmp/mig.db uv run alembic downgrade c3d5e7f9a1b2 && rm /tmp/mig.db`
Expected: 오류 없이 끝남

- [ ] **Step 6: 커밋**

```bash
git add app/db/models.py migrations/versions/d1e2f3a4b5c6_downloads_and_transcripts.py tests/test_db_models.py
git commit -m "feat: add download_records and transcripts tables"
```

---

### Task 2: 수업자료 목록 추출 (`app/lms/materials.py`)

**Files:**
- Modify: `app/lms/models.py` (`ItemContentData`, `ContentData`)
- Create: `app/lms/materials.py`
- Test: `tests/test_materials.py`

**Interfaces:**
- Consumes: `LmsClient.get_json(path)`, `safe_file_name()` (`app/lms/media.py`), `MediaError`
- Produces:
  - `CourseMaterial` dataclass (`key, kind, course_id, module_id, module_title, module_position, item_position, title, file_name, source_id, size`)
  - `commons_files(course: Course, modules: list[Module]) -> list[CourseMaterial]`
  - `wiki_file_links(html: str, course_id: int) -> list[tuple[str, str]]` — `(file_id, 이름)`
  - `async course_materials(client: LmsClient, course: Course, modules: list[Module]) -> list[CourseMaterial]`
  - `commons_download_url(content_id: str) -> str`, `check_file_host(url: str) -> str` (실패 시 `MediaError`)
  - `ALLOWED_FILE_HOSTS`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_materials.py`:

```python
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
      <a class="instructure_file_link" href="https://evil.example.com/x.pdf">외부</a>
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
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_materials.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.lms.materials'`

- [ ] **Step 3: 모델 필드 추가** (`app/lms/models.py`)

`ItemContentData`의 `thumbnail_url` 아래에 추가:

```python
    file_name: str | None = None  # content_type이 "file"인 commons 자료의 원래 파일 이름
```

`ContentData`의 `title` 위에 추가:

```python
    url: str | None = None  # wiki_page 항목의 페이지 slug
```

- [ ] **Step 4: `app/lms/materials.py` 작성**

```python
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

from app.lms.auth import CANVAS_BASE_URL
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
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_materials.py tests/test_media.py -v`
Expected: PASS (기존 media 테스트도 그대로 통과)

- [ ] **Step 6: 개발용 CLI 추가** (`app/lms/materials.py` 끝)

```python
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
```

`export_cookies()`는 Task 3에서 만든다. CLI는 Task 3 이후에 실행한다.

- [ ] **Step 7: 커밋**

```bash
git add app/lms/models.py app/lms/materials.py tests/test_materials.py
git commit -m "feat: extract course materials from commons files and wiki pages"
```

---

### Task 3: 자료 목록·링크 API와 kind별 중계

**Files:**
- Modify: `app/lms/client.py` (`export_cookies`)
- Modify: `app/api/routes_media.py`
- Test: `tests/test_materials_api.py`

**Interfaces:**
- Consumes: Task 2의 `course_materials`, `commons_download_url`, `check_file_host`, `CourseMaterial`
- Produces:
  - `LmsClient.export_cookies() -> list[dict[str, str]]` (`auth.import_cookies` 입력 형식)
  - `GET /courses/{id}/materials -> list[MaterialOut]`, `POST /courses/{id}/materials/{key}/download-link -> LinkOut`
  - 토큰 데이터 `kind`: `video`(기본) / `commons_file` / `canvas_file`. canvas_file은 `course_id`, `file_id`, `file_name`
  - `MaterialOut(key, kind, module_id, module_title, title, file_name, size)` (Task 4에서 `downloaded_at` 추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_materials_api.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_materials_api.py -v`
Expected: FAIL — `/courses/45446/materials` 404

- [ ] **Step 3: `export_cookies` 추가** (`app/lms/client.py`, `relogin` 위)

```python
    def export_cookies(self) -> list[dict[str, str]]:
        """지금 세션 쿠키를 auth.import_cookies 형식으로 내보낸다(다른 클라이언트로 파일을 중계할 때)."""
        return [{"name": c.name, "value": c.value or "", "domain": c.domain, "path": c.path}
                for c in self._http.cookies.jar]
```

- [ ] **Step 4: `routes_media.py` 수정**

import 블록을 다음으로 바꾼다:

```python
from collections import defaultdict

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.api.deps import CurrentUser, Db
from app.db.base import sessionmaker
from app.db.models import CourseRow, SavedVideo
from app.lms import auth
from app.lms.errors import InvalidCredentials, LmsError
from app.lms.materials import CourseMaterial, check_file_host, commons_download_url, course_materials
from app.lms.media import (
    COMMONS_HEADERS,
    CourseVideo,
    MediaError,
    check_media_host,
    content_disposition,
    course_videos,
    resolve_media_url,
)
from app.lms.models import Course
from app.security import DOWNLOAD_LINK_MINUTES, create_download_token, decode_download_token
from app.services.lms_access import NoCredentials, lms_client_for
```

`SavedVideoOut` 아래에 응답 모델 추가:

```python
class MaterialOut(BaseModel):
    key: str
    kind: str
    module_id: int
    module_title: str
    title: str
    file_name: str
    size: int | None
```

기존 `_videos`를 다음 두 함수로 바꾼다(과목 확인과 LMS 오류 처리를 공유):

```python
async def _course_row(db, user_id: int, course_id: int) -> CourseRow:
    row = await db.scalar(select(CourseRow).where(CourseRow.user_id == user_id, CourseRow.lms_course_id == course_id))
    if row is None:
        raise HTTPException(404, "수강 중인 과목이 아닙니다.")
    return row


async def _videos(db, user_id: int, course_id: int) -> list[CourseVideo]:
    row = await _course_row(db, user_id, course_id)
    try:
        async with lms_client_for(db, user_id) as client:
            modules = await client.get_modules(course_id)
    except (NoCredentials, InvalidCredentials) as e:
        raise HTTPException(400, str(e)) from e
    except LmsError as e:
        raise HTTPException(502, f"LMS 조회 실패: {e}") from e
    return course_videos(Course(id=course_id, name=row.name), modules)


async def _materials(db, user_id: int, course_id: int) -> list[CourseMaterial]:
    row = await _course_row(db, user_id, course_id)
    course = Course(id=course_id, name=row.name)
    try:
        async with lms_client_for(db, user_id) as client:
            return await course_materials(client, course, await client.get_modules(course_id))
    except (NoCredentials, InvalidCredentials) as e:
        raise HTTPException(400, str(e)) from e
    except LmsError as e:
        raise HTTPException(502, f"LMS 조회 실패: {e}") from e
```

`create_link` 아래(저장된 영상 라우트 위)에 자료 라우트 추가:

```python
@router.get("/courses/{course_id}/materials", response_model=list[MaterialOut])
async def list_materials(course_id: int, user: CurrentUser, db: Db):
    """과목의 수업자료(commons 파일 + 위키 첨부, LMS에서 실시간 조회)."""
    return [MaterialOut(**{k: getattr(m, k) for k in MaterialOut.model_fields}) for m in await _materials(db, user.id, course_id)]


@router.post("/courses/{course_id}/materials/{key}/download-link", response_model=LinkOut)
async def create_material_link(course_id: int, key: str, user: CurrentUser, db: Db, request: Request):
    mat = next((m for m in await _materials(db, user.id, course_id) if m.key == key), None)
    if mat is None:
        raise HTTPException(404, "이 과목에서 해당 자료를 찾을 수 없습니다.")
    if mat.kind == "canvas_file":
        data = {"kind": mat.kind, "course_id": course_id, "file_id": mat.source_id, "file_name": mat.file_name}
    else:
        data = {"kind": mat.kind, "content_id": mat.source_id, "file_name": mat.file_name}
    token = create_download_token(user.id, data)
    return LinkOut(url=request.app.url_path_for("download_video", token=token), file_name=mat.file_name)
```

기존 `download_video` 함수 전체를 다음으로 바꾼다:

```python
RELAY_KINDS = {"video", "commons_file", "canvas_file"}


def _relay_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0))


async def _canvas_file(user_id: int, course_id: int, file_id: str) -> tuple[str, list[dict]]:
    """LMS에서 파일 정보를 다시 확인하고(본인 세션), 서명된 다운로드 주소와 세션 쿠키를 돌려준다."""
    async with sessionmaker()() as db:
        async with lms_client_for(db, user_id) as client:
            meta = await client.get_json(f"/api/v1/courses/{course_id}/files/{file_id}")
            cookies = client.export_cookies()
    url = meta.get("url") if isinstance(meta, dict) else None
    if not url:
        raise MediaError("LMS가 파일 주소를 주지 않았습니다.")
    return url, cookies


async def _open_upstream(user_id: int, data: dict, range_header: str | None) -> tuple[httpx.AsyncClient, httpx.Response, bool]:
    """토큰 데이터로 업스트림을 연다. 반환: (클라이언트, 스트리밍 응답, 저장된 영상 주소인지)."""
    kind = data.get("kind", "video")
    http = _relay_client()
    try:
        saved = False
        headers = dict(COMMONS_HEADERS)
        if kind == "canvas_file":
            url, cookies = await _canvas_file(user_id, data["course_id"], data["file_id"])
            auth.import_cookies(http, cookies)
            url, headers = check_file_host(url), {}
        elif kind == "commons_file":
            url = commons_download_url(data["content_id"])
        elif saved_url := data.get("media_url"):
            url, saved = check_media_host(saved_url), True
        else:
            url = await resolve_media_url(http, data["content_id"])
        if range_header:  # 브라우저 이어받기
            headers["Range"] = range_header
        upstream = await http.send(http.build_request("GET", url, headers=headers), stream=True)
        try:
            (check_media_host if kind == "video" else check_file_host)(str(upstream.url))  # 리다이렉트 뒤 최종 주소
        except MediaError:
            await upstream.aclose()
            raise
        return http, upstream, saved
    except BaseException:
        await http.aclose()
        raise


@router.get("/downloads/{token}", name="download_video")
async def download_video(token: str, request: Request):
    decoded = decode_download_token(token)
    if decoded is None or decoded[1].get("kind", "video") not in RELAY_KINDS:
        raise HTTPException(401, "다운로드 링크가 만료되었거나 올바르지 않습니다. 목록에서 다시 받으세요.")
    user_id, data = decoded
    if _active[user_id] >= MAX_DOWNLOADS_PER_USER:
        raise HTTPException(429, f"동시에 {MAX_DOWNLOADS_PER_USER}개까지 받을 수 있습니다. 진행 중인 다운로드가 끝난 뒤 다시 시도하세요.")

    try:
        http, upstream, saved = await _open_upstream(user_id, data, request.headers.get("range"))
    except MediaError as e:
        raise HTTPException(422, str(e)) from e
    except (NoCredentials, InvalidCredentials) as e:
        raise HTTPException(400, str(e)) from e
    except LmsError as e:
        raise HTTPException(502, f"LMS 조회 실패: {e}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"학교 서버에 연결하지 못했습니다: {e}") from e
    if upstream.status_code not in (200, 206):
        await upstream.aclose()
        await http.aclose()
        if saved and upstream.status_code in (403, 404, 410):
            raise HTTPException(410, "학교 서버가 저장된 주소의 영상을 더 이상 제공하지 않습니다.")
        raise HTTPException(502, f"학교 서버가 파일을 주지 않았습니다 (HTTP {upstream.status_code}).")

    _active[user_id] += 1

    async def body():
        async for chunk in upstream.aiter_raw(1 << 16):
            yield chunk

    async def cleanup():
        _active[user_id] -= 1
        await upstream.aclose()
        await http.aclose()

    out_headers = {k: upstream.headers[k] for k in ("content-length", "content-range", "accept-ranges") if k in upstream.headers}
    out_headers["Content-Disposition"] = content_disposition(data["file_name"])
    out_headers["Cache-Control"] = "no-store"
    media_type = "video/mp4" if data.get("kind", "video") == "video" else upstream.headers.get("content-type", "application/octet-stream")
    return StreamingResponse(body(), status_code=upstream.status_code, media_type=media_type,
                             headers=out_headers, background=BackgroundTask(cleanup))
```

`content_disposition()`의 ASCII 대체 이름은 `lecture.mp4`로 고정돼 있다. 자료에도 쓰이도록 `app/lms/media.py`를 고친다:

```python
def content_disposition(file_name: str) -> str:
    # 구형 브라우저용 대체 이름. 한글이 섞이면 잘린 이름 대신 일반 이름을 쓴다(최신 브라우저는 filename*를 쓴다).
    if file_name.isascii():
        ascii_name = file_name.replace('"', "")
    else:
        suffix = Path(file_name).suffix
        ascii_name = "lecture.mp4" if suffix == ".mp4" else f"download{suffix if suffix.isascii() else ''}"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_materials_api.py tests/test_media.py -v`
Expected: PASS (기존 `test_safe_name_and_disposition`의 `lecture.mp4` 기대값도 유지)

- [ ] **Step 6: 실계정 확인 (사람이 실행)**

Run: `uv run python -m app.lms.materials list --course <과목ID>` 다음 `get --course <과목ID> --key <c… 하나>`, `get --key <f… 하나>`
Expected: 목록이 LMS 화면과 같고, `최종:` 줄의 호스트가 `ALLOWED_FILE_HOSTS` 안이다. 밖이면 `check_file_host`에서 멈춘다. 그 호스트를 `ALLOWED_FILE_HOSTS`에 추가하고 이 단계를 다시 실행한다. commons 파일이 302로 로그인 페이지에 가면 commons 쿠키가 필요하다는 뜻이다. 이 경우 진행을 멈추고 보고한다(설계 변경 필요).

- [ ] **Step 7: 커밋**

```bash
git add app/lms/client.py app/lms/media.py app/api/routes_media.py tests/test_materials_api.py
git commit -m "feat: list and relay course materials by kind"
```

---

### Task 4: 다운로드 이력과 썸네일

**Files:**
- Create: `app/services/downloads.py`
- Modify: `app/lms/media.py` (`CourseVideo.thumbnail_url`)
- Modify: `app/api/routes_media.py` (기록, 응답 필드)
- Test: `tests/test_downloads.py`

**Interfaces:**
- Consumes: Task 1 `DownloadRecord`, Task 3 라우트
- Produces:
  - `video_key(module_item_id: int) -> str` (`"v{id}"`)
  - `async record_download(db, user_id: int, course_id: int, item_key: str, kind: str, title: str) -> None`
  - `async downloaded_map(db, user_id: int, course_id: int | None = None) -> dict[tuple[int, str], datetime]`
  - `VideoOut.thumbnail_url`, `VideoOut.downloaded_at`, `SavedVideoOut.downloaded_at`, `MaterialOut.downloaded_at`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_downloads.py`:

```python
import respx
from sqlalchemy import select

from app.db import base as db_base
from app.db.models import DownloadRecord, SavedVideo
from app.lms.media import course_videos
from app.lms.models import Course, Module
from tests.test_api import env  # noqa: F401
from tests.test_materials_api import _with_course


@respx.mock
async def test_link_issue_records_history_and_marks_lists(env):
    api, _ = env
    h = await _with_course(api)
    vids = (await api.get("/courses/45446/videos", headers=h)).json()
    assert all(v["downloaded_at"] is None for v in vids)

    await api.post("/courses/45446/videos/11/download-link", headers=h)
    await api.post("/courses/45446/videos/11/download-link", headers=h)
    await api.post("/courses/45446/materials/f701/download-link", headers=h)

    async with db_base.sessionmaker()() as db:
        rows = {r.item_key: r for r in (await db.scalars(select(DownloadRecord))).all()}
    assert rows["v11"].count == 2 and rows["v11"].kind == "video"
    assert rows["f701"].kind == "canvas_file" and rows["f701"].title == "a.pdf"

    vids = {v["module_item_id"]: v for v in (await api.get("/courses/45446/videos", headers=h)).json()}
    assert vids[11]["downloaded_at"] is not None
    mats = {m["key"]: m for m in (await api.get("/courses/45446/materials", headers=h)).json()}
    assert mats["f701"]["downloaded_at"] is not None and mats["c12"]["downloaded_at"] is None


@respx.mock
async def test_saved_video_link_records_same_key(env):
    api, _ = env
    h = await _with_course(api)
    async with db_base.sessionmaker()() as db:
        db.add(SavedVideo(user_id=1, lms_course_id=45446, course_name="리눅스", module_item_id=11, module_title="1주차",
                          title="1강", content_id="m11", media_url="https://ssuin-object.commonscdn.com/a.mp4",
                          file_name="a.mp4"))
        await db.commit()
    saved = (await api.get("/saved-videos", headers=h)).json()[0]
    assert saved["downloaded_at"] is None
    await api.post(f"/saved-videos/{saved['id']}/download-link", headers=h)
    assert (await api.get("/saved-videos", headers=h)).json()[0]["downloaded_at"] is not None


def test_thumbnail_only_https():
    def mod(url):
        return Module.model_validate({"module_id": 1, "title": "1주차", "position": 1, "module_items": [
            {"module_item_id": 1, "position": 1, "content_data": {"item_content_type": "commons", "title": "v",
             "item_content_data": {"content_type": "movie", "content_id": "x", "thumbnail_url": url}}}]})
    assert course_videos(Course(id=1, name="c"), [mod("https://commons.ssu.ac.kr/t.jpg")])[0].thumbnail_url == "https://commons.ssu.ac.kr/t.jpg"
    assert course_videos(Course(id=1, name="c"), [mod("http://x/t.jpg")])[0].thumbnail_url is None
    assert course_videos(Course(id=1, name="c"), [mod(None)])[0].thumbnail_url is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_downloads.py -v`
Expected: FAIL — `KeyError: 'downloaded_at'`, `AttributeError: ... thumbnail_url`

- [ ] **Step 3: `app/services/downloads.py` 작성**

```python
"""다운로드 이력. 링크를 발급할 때 기록한다(브라우저 저장 완료는 알 수 없다)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import DownloadRecord


def video_key(module_item_id: int) -> str:
    return f"v{module_item_id}"


async def record_download(db: AsyncSession, user_id: int, course_id: int, item_key: str, kind: str, title: str) -> None:
    row = await db.scalar(select(DownloadRecord).where(
        DownloadRecord.user_id == user_id, DownloadRecord.lms_course_id == course_id, DownloadRecord.item_key == item_key))
    now = utcnow()
    if row is None:
        db.add(DownloadRecord(user_id=user_id, lms_course_id=course_id, item_key=item_key, kind=kind, title=title,
                              count=1, first_at=now, last_at=now))
    else:
        row.count, row.last_at, row.title = row.count + 1, now, title
    await db.commit()


async def downloaded_map(db: AsyncSession, user_id: int, course_id: int | None = None) -> dict[tuple[int, str], datetime]:
    stmt = select(DownloadRecord.lms_course_id, DownloadRecord.item_key, DownloadRecord.last_at).where(
        DownloadRecord.user_id == user_id)
    if course_id is not None:
        stmt = stmt.where(DownloadRecord.lms_course_id == course_id)
    return {(c, k): t for c, k, t in (await db.execute(stmt)).all()}
```

- [ ] **Step 4: 썸네일 필드** (`app/lms/media.py`)

`CourseVideo`의 `completed: bool` 아래에 `thumbnail_url: str | None = None`을 추가한다. `course_videos()`의 `CourseVideo(...)` 인자 끝에 다음을 추가한다:

```python
                    thumbnail_url=icd.thumbnail_url if (icd.thumbnail_url or "").startswith("https://") else None,
```

- [ ] **Step 5: 라우트 연결** (`app/api/routes_media.py`)

import에 추가:

```python
from datetime import datetime

from app.services.downloads import downloaded_map, record_download, video_key
```

응답 모델 필드 추가:
- `VideoOut`: `thumbnail_url: str | None`, `downloaded_at: datetime | None = None`
- `SavedVideoOut`: `downloaded_at: datetime | None = None`
- `MaterialOut`: `downloaded_at: datetime | None = None`

`list_videos`, `list_saved_videos`, `list_materials`를 다음으로 바꾼다:

```python
_VIDEO_FIELDS = ("module_item_id", "module_id", "module_title", "title", "content_type", "duration", "size",
                 "completed", "available", "thumbnail_url")


@router.get("/courses/{course_id}/videos", response_model=list[VideoOut])
async def list_videos(course_id: int, user: CurrentUser, db: Db):
    """과목의 강의 영상 목록(완료한 영상 포함, LMS에서 실시간 조회)."""
    videos = await _videos(db, user.id, course_id)
    done = await downloaded_map(db, user.id, course_id)
    return [VideoOut(**{k: getattr(v, k) for k in _VIDEO_FIELDS},
                     downloaded_at=done.get((course_id, video_key(v.module_item_id)))) for v in videos]


@router.get("/saved-videos", response_model=list[SavedVideoOut])
async def list_saved_videos(user: CurrentUser, db: Db, course_id: int | None = None):
    """동기화 때 저장해 둔 강의 영상(지난 학기·시청기간이 끝난 영상 포함)."""
    stmt = select(SavedVideo).where(SavedVideo.user_id == user.id)
    if course_id is not None:
        stmt = stmt.where(SavedVideo.lms_course_id == course_id)
    stmt = stmt.order_by(SavedVideo.course_name, SavedVideo.lms_course_id, SavedVideo.module_position, SavedVideo.item_position)
    done = await downloaded_map(db, user.id, course_id)
    skip = {"saved", "downloaded_at"}
    return [
        SavedVideoOut(**{k: getattr(r, k) for k in SavedVideoOut.model_fields if k not in skip}, saved=r.media_url is not None,
                      downloaded_at=done.get((r.lms_course_id, video_key(r.module_item_id))))
        for r in (await db.scalars(stmt)).all()
    ]


@router.get("/courses/{course_id}/materials", response_model=list[MaterialOut])
async def list_materials(course_id: int, user: CurrentUser, db: Db):
    """과목의 수업자료(commons 파일 + 위키 첨부, LMS에서 실시간 조회)."""
    mats = await _materials(db, user.id, course_id)
    done = await downloaded_map(db, user.id, course_id)
    return [MaterialOut(**{k: getattr(m, k) for k in MaterialOut.model_fields if k != "downloaded_at"},
                        downloaded_at=done.get((course_id, m.key))) for m in mats]
```

링크 발급 세 곳에서 토큰을 만든 뒤 기록한다:
- `create_link`: `await record_download(db, user.id, course_id, video_key(module_item_id), "video", video.title)`
- `create_saved_link`: `await record_download(db, user.id, row.lms_course_id, video_key(row.module_item_id), "video", row.title)`
- `create_material_link`: `await record_download(db, user.id, course_id, mat.key, mat.kind, mat.file_name)`

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_downloads.py tests/test_materials_api.py tests/test_media.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/services/downloads.py app/lms/media.py app/api/routes_media.py tests/test_downloads.py
git commit -m "feat: record download history and expose thumbnails"
```

---

### Task 5: 오디오 스트림 (ffmpeg → 16kHz PCM)

**Files:**
- Create: `app/lms/audio.py`
- Modify: `app/api/routes_media.py`, `app/config.py`, `.env.example`, `Dockerfile`, `pyproject.toml` (marker)
- Test: `tests/test_audio.py`

**Interfaces:**
- Produces:
  - `SAMPLE_RATE = 16000`, `ffmpeg_path() -> str | None`, `ffmpeg_args(ffmpeg, source, headers) -> list[str]`
  - `async pcm_stream(source: str, headers: dict | None = None, chunk: int = 1 << 16) -> AsyncIterator[bytes]`
  - `POST /courses/{cid}/videos/{mid}/audio-link`, `POST /saved-videos/{id}/audio-link` → `LinkOut(url="/audio/{token}")`
  - `GET /audio/{token}` (name `stream_audio`), 헤더 `X-Audio-Sample-Rate`, `X-Audio-Duration`
  - `Settings.max_audio_streams: int = 4`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_audio.py`:

```python
import asyncio
import shutil
import subprocess

import pytest
import respx

from app.api import routes_media
from app.lms import audio
from app.lms.media import CONTENT_PHP
from tests.test_api import env  # noqa: F401
from tests.test_materials_api import _with_course

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")


def _sine(path, seconds):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "aac", str(path)], check=True)
    return str(path)


def test_ffmpeg_args_pass_headers_and_pcm_format():
    args = audio.ffmpeg_args("ffmpeg", "https://x/a.mp4", {"Referer": "https://commons.ssu.ac.kr/"})
    assert args[args.index("-headers") + 1] == "Referer: https://commons.ssu.ac.kr/\r\n"
    assert args[-10:] == ["-i", "https://x/a.mp4", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"]


@pytest.mark.ffmpeg
@needs_ffmpeg
async def test_pcm_stream_outputs_16k_mono_s16le(tmp_path):
    src = _sine(tmp_path / "a.m4a", 2)
    data = b"".join([c async for c in audio.pcm_stream(src)])
    assert abs(len(data) - 2 * 16000 * 2) < 3200  # 2초 × 16kHz × 2바이트 (인코더 지연 허용)


@pytest.mark.ffmpeg
@needs_ffmpeg
async def test_pcm_stream_kills_ffmpeg_on_close(tmp_path, monkeypatch):
    src = _sine(tmp_path / "long.m4a", 120)
    procs = []
    real = asyncio.create_subprocess_exec

    async def spy(*a, **k):
        p = await real(*a, **k)
        procs.append(p)
        return p

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", spy)
    gen = audio.pcm_stream(src, chunk=1024)
    assert await gen.__anext__()
    await gen.aclose()
    assert procs[0].returncode is not None


def _fake_stream(monkeypatch, seen):
    async def fake(source, headers=None, chunk=1 << 16):
        seen.append((source, headers))
        yield b"\x01\x00" * 8

    monkeypatch.setattr(routes_media, "pcm_stream", fake)
    monkeypatch.setattr(routes_media, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")


CDN = "https://ssuin-object.commonscdn.com/media/abc"
XML = f"<content><content_playing_info><media_uri>{CDN}/a.mp4</media_uri></content_playing_info></content>"


@respx.mock
async def test_audio_link_and_stream(env, monkeypatch):
    api, _ = env
    h = await _with_course(api)
    seen = []
    _fake_stream(monkeypatch, seen)
    respx.get(CONTENT_PHP).respond(200, text=XML)
    r = await api.post("/courses/45446/videos/11/audio-link", headers=h)
    assert r.status_code == 200 and r.json()["url"].startswith("/audio/")
    got = await api.get(r.json()["url"])
    assert got.status_code == 200 and got.content == b"\x01\x00" * 8
    assert got.headers["x-audio-sample-rate"] == "16000"
    assert seen == [(f"{CDN}/a.mp4", routes_media.COMMONS_HEADERS)]
    assert routes_media._active[1] == 0  # 끝나면 동시 실행 수를 돌려놓는다


@respx.mock
async def test_audio_link_rules(env, monkeypatch):
    api, _ = env
    h = await _with_course(api)
    _fake_stream(monkeypatch, [])
    assert (await api.post("/courses/45446/videos/12/audio-link", headers=h)).status_code == 404  # 자료(영상 아님)
    assert (await api.post("/courses/99999/videos/11/audio-link", headers=h)).status_code == 404


@respx.mock
async def test_tokens_are_not_interchangeable(env, monkeypatch):
    api, _ = env
    h = await _with_course(api)
    _fake_stream(monkeypatch, [])
    audio_url = (await api.post("/courses/45446/videos/11/audio-link", headers=h)).json()["url"]
    dl_url = (await api.post("/courses/45446/videos/11/download-link", headers=h)).json()["url"]
    assert (await api.get("/downloads/" + audio_url.rsplit("/", 1)[1])).status_code == 401
    assert (await api.get("/audio/" + dl_url.rsplit("/", 1)[1])).status_code == 401


@respx.mock
async def test_audio_without_ffmpeg_is_503(env, monkeypatch):
    api, _ = env
    h = await _with_course(api)
    monkeypatch.setattr(routes_media, "ffmpeg_path", lambda: None)
    url = (await api.post("/courses/45446/videos/11/audio-link", headers=h)).json()["url"]
    assert (await api.get(url)).status_code == 503
```

`pyproject.toml`의 `markers`에 `"ffmpeg: 실제 ffmpeg를 실행하는 테스트"`를 추가한다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_audio.py -v`
Expected: FAIL — `ImportError: cannot import name 'audio'`

- [ ] **Step 3: `app/lms/audio.py` 작성**

```python
"""강의 영상에서 오디오만 뽑아 16kHz 모노 PCM(s16le)으로 흘려보낸다. 브라우저 Whisper 전사용.

파일로 저장하지 않는다. ffmpeg가 원본 주소를 직접 읽고, 표준 출력을 그대로 응답으로 넘긴다.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffmpeg_args(ffmpeg: str, source: str, headers: dict[str, str] | None = None) -> list[str]:
    args = [ffmpeg, "-nostdin", "-loglevel", "error"]
    if headers:
        args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    return args + ["-i", source, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"]


async def pcm_stream(source: str, headers: dict[str, str] | None = None, chunk: int = 1 << 16) -> AsyncIterator[bytes]:
    """ffmpeg를 띄워 PCM을 chunk 단위로 내보낸다. 소비자가 중간에 그만두면(aclose) 프로세스를 끝낸다."""
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_args(ffmpeg_path() or "ffmpeg", source, headers),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        while data := await proc.stdout.read(chunk):
            yield data
        await proc.wait()
        if proc.returncode:
            err = (await proc.stderr.read())[-500:].decode(errors="replace")
            log.warning("ffmpeg 실패(%s): %s", proc.returncode, err)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                proc.kill()
                await proc.wait()
```

- [ ] **Step 4: 설정** (`app/config.py`, "동기화·재생 정책" 블록 끝)

```python
    # 전사용 오디오 스트림(ffmpeg) 서버 전체 동시 실행 수
    max_audio_streams: int = 4
```

`.env.example`의 재생 설정 아래에 추가:

```
# 전사용 오디오 변환(ffmpeg) 동시 실행 수
MAX_AUDIO_STREAMS=4
```

- [ ] **Step 5: 라우트** (`app/api/routes_media.py`)

import에 추가:

```python
from app.config import get_settings
from app.lms.audio import SAMPLE_RATE, ffmpeg_path, pcm_stream
```

`_active` 아래에 `_audio_streams = 0`을 추가한다. `create_link`에서 영상을 찾는 부분을 함수로 빼고 오디오 링크를 추가한다:

```python
async def _open_video(db, user_id: int, course_id: int, module_item_id: int) -> CourseVideo:
    video = next((v for v in await _videos(db, user_id, course_id) if v.module_item_id == module_item_id), None)
    if video is None:
        raise HTTPException(404, "이 과목에서 해당 영상을 찾을 수 없습니다.")
    if not video.available:
        raise HTTPException(409, "아직 열리지 않은 영상입니다.")
    return video


@router.post("/courses/{course_id}/videos/{module_item_id}/download-link", response_model=LinkOut)
async def create_link(course_id: int, module_item_id: int, user: CurrentUser, db: Db, request: Request):
    video = await _open_video(db, user.id, course_id, module_item_id)
    token = create_download_token(user.id, {"content_id": video.content_id, "file_name": video.file_name})
    await record_download(db, user.id, course_id, video_key(module_item_id), "video", video.title)
    return LinkOut(url=request.app.url_path_for("download_video", token=token), file_name=video.file_name)


@router.post("/courses/{course_id}/videos/{module_item_id}/audio-link", response_model=LinkOut)
async def create_audio_link(course_id: int, module_item_id: int, user: CurrentUser, db: Db, request: Request):
    """전사용 오디오(16kHz 모노 PCM) 스트림 링크. 다운로드 이력에는 남기지 않는다."""
    video = await _open_video(db, user.id, course_id, module_item_id)
    token = create_download_token(user.id, {"kind": "audio", "content_id": video.content_id,
                                            "duration": video.duration, "file_name": video.file_name})
    return LinkOut(url=request.app.url_path_for("stream_audio", token=token), file_name=video.file_name)
```

저장된 영상 오디오 링크(`create_saved_link` 아래):

```python
@router.post("/saved-videos/{saved_id}/audio-link", response_model=LinkOut)
async def create_saved_audio_link(saved_id: int, user: CurrentUser, db: Db, request: Request):
    row = await db.get(SavedVideo, saved_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "저장된 영상이 아닙니다.")
    if row.media_url is None:
        raise HTTPException(409, row.resolve_error or "아직 영상 주소를 저장하지 못했습니다. 다음 동기화 뒤에 다시 시도하세요.")
    token = create_download_token(user.id, {"kind": "audio", "media_url": row.media_url,
                                            "duration": row.duration, "file_name": row.file_name})
    return LinkOut(url=request.app.url_path_for("stream_audio", token=token), file_name=row.file_name)
```

스트림 라우트(`download_video` 아래):

```python
@router.get("/audio/{token}", name="stream_audio")
async def stream_audio(token: str):
    global _audio_streams
    decoded = decode_download_token(token)
    if decoded is None or decoded[1].get("kind") != "audio":
        raise HTTPException(401, "전사 링크가 만료되었거나 올바르지 않습니다. 다시 시도하세요.")
    user_id, data = decoded
    if ffmpeg_path() is None:
        raise HTTPException(503, "서버에 ffmpeg가 설치되어 있지 않습니다.")
    if _active[user_id] >= MAX_DOWNLOADS_PER_USER:
        raise HTTPException(429, f"동시에 {MAX_DOWNLOADS_PER_USER}개까지 받을 수 있습니다. 진행 중인 작업이 끝난 뒤 다시 시도하세요.")
    if _audio_streams >= get_settings().max_audio_streams:
        raise HTTPException(429, "서버가 바쁩니다. 잠시 뒤 다시 시도하세요.")
    try:
        if saved_url := data.get("media_url"):
            url = check_media_host(saved_url)
        else:
            async with _relay_client() as http:
                url = await resolve_media_url(http, data["content_id"])
    except MediaError as e:
        raise HTTPException(422, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"학교 서버에 연결하지 못했습니다: {e}") from e

    _active[user_id] += 1
    _audio_streams += 1

    async def cleanup():
        global _audio_streams
        _active[user_id] -= 1
        _audio_streams -= 1

    headers = {"X-Audio-Sample-Rate": str(SAMPLE_RATE), "Cache-Control": "no-store"}
    if data.get("duration"):
        headers["X-Audio-Duration"] = str(data["duration"])
    return StreamingResponse(pcm_stream(url, COMMONS_HEADERS), media_type="application/octet-stream",
                             headers=headers, background=BackgroundTask(cleanup))
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_audio.py tests/test_media.py tests/test_downloads.py -v`
Expected: PASS (ffmpeg가 없으면 `ffmpeg` 마커 테스트 2개는 SKIP)

- [ ] **Step 7: Dockerfile에 ffmpeg 추가**

`RUN playwright install --with-deps chrome` 다음 줄에 추가:

```dockerfile
# 전사용 오디오 추출(16kHz PCM)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 8: 커밋**

```bash
git add app/lms/audio.py app/api/routes_media.py app/config.py .env.example Dockerfile pyproject.toml tests/test_audio.py
git commit -m "feat: stream 16kHz PCM audio for browser transcription"
```

---

### Task 6: 전사 결과 저장 API

**Files:**
- Create: `app/services/transcripts.py`, `app/api/routes_transcripts.py`
- Modify: `app/main.py`, `app/api/routes_media.py` (`transcript_id` 필드)
- Test: `tests/test_transcripts.py`

**Interfaces:**
- Consumes: Task 1 `Transcript`
- Produces:
  - `async transcript_map(db, user_id: int, course_id: int | None = None) -> dict[tuple[int, int], int]`
  - `PUT /courses/{cid}/videos/{mid}/transcript` (본문 `TranscriptIn`) → `TranscriptSummary`
  - `GET /transcripts?course_id=` → `list[TranscriptSummary]`, `GET /transcripts/{id}` → `TranscriptOut`, `DELETE /transcripts/{id}` → 204
  - `VideoOut.transcript_id`, `SavedVideoOut.transcript_id`
  - `TranscriptIn = {course_name, title, text, segments: [{start, end, text}], model, duration}`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_transcripts.py`:

```python
import respx

from app.db import base as db_base
from app.db.models import SavedVideo
from tests.test_api import _signup, env  # noqa: F401
from tests.test_materials_api import _with_course

BODY = {"course_name": "리눅스", "title": "1강", "text": "안녕하세요\n반갑습니다",
        "segments": [{"start": 0.0, "end": 1.2, "text": "안녕하세요"}, {"start": 1.2, "end": None, "text": "반갑습니다"}],
        "model": "whisper-small/webgpu", "duration": 2.5}


@respx.mock
async def test_save_list_get_delete(env):
    api, _ = env
    h = await _with_course(api)
    r = await api.put("/courses/45446/videos/11/transcript", headers=h, json=BODY)
    assert r.status_code == 200
    tid = r.json()["id"]
    again = await api.put("/courses/45446/videos/11/transcript", headers=h, json={**BODY, "text": "고침"})
    assert again.json()["id"] == tid  # 덮어쓰기

    assert [t["id"] for t in (await api.get("/transcripts?course_id=45446", headers=h)).json()] == [tid]
    full = (await api.get(f"/transcripts/{tid}", headers=h)).json()
    assert full["text"] == "고침" and full["segments"][1]["end"] is None

    vids = {v["module_item_id"]: v for v in (await api.get("/courses/45446/videos", headers=h)).json()}
    assert vids[11]["transcript_id"] == tid

    assert (await api.delete(f"/transcripts/{tid}", headers=h)).status_code == 204
    assert (await api.get(f"/transcripts/{tid}", headers=h)).status_code == 404


@respx.mock
async def test_other_users_cannot_see_or_write(env):
    api, _ = env
    h = await _with_course(api)
    tid = (await api.put("/courses/45446/videos/11/transcript", headers=h, json=BODY)).json()["id"]
    other = await _signup(api, "20209999")
    assert (await api.get(f"/transcripts/{tid}", headers=other)).status_code == 404
    assert (await api.delete(f"/transcripts/{tid}", headers=other)).status_code == 404
    assert (await api.put("/courses/45446/videos/11/transcript", headers=other, json=BODY)).status_code == 404


@respx.mock
async def test_save_for_saved_video_of_past_course(env):
    api, _ = env
    h = await _with_course(api)
    async with db_base.sessionmaker()() as db:
        db.add(SavedVideo(user_id=1, lms_course_id=11111, course_name="지난 과목", module_item_id=5, module_title="1주차",
                          title="옛 강의", content_id="x", media_url="https://ssuin-object.commonscdn.com/a.mp4", file_name="a.mp4"))
        await db.commit()
    assert (await api.put("/courses/11111/videos/5/transcript", headers=h, json=BODY)).status_code == 200
    assert (await api.put("/courses/11111/videos/6/transcript", headers=h, json=BODY)).status_code == 404
    saved = (await api.get("/saved-videos", headers=h)).json()
    assert saved[0]["transcript_id"] is not None


@respx.mock
async def test_size_limits(env):
    api, _ = env
    h = await _with_course(api)
    big = {**BODY, "text": "가" * (2 * 1024 * 1024)}
    assert (await api.put("/courses/45446/videos/11/transcript", headers=h, json=big)).status_code == 413
    many = {**BODY, "segments": [{"start": 0, "end": 0, "text": ""}] * 20_001}
    assert (await api.put("/courses/45446/videos/11/transcript", headers=h, json=many)).status_code == 422
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_transcripts.py -v`
Expected: FAIL — PUT 404/405

- [ ] **Step 3: `app/services/transcripts.py` 작성**

```python
"""전사 결과 조회 보조."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Transcript


async def transcript_map(db: AsyncSession, user_id: int, course_id: int | None = None) -> dict[tuple[int, int], int]:
    """(과목 ID, module_item_id) → 전사 ID"""
    stmt = select(Transcript.lms_course_id, Transcript.module_item_id, Transcript.id).where(Transcript.user_id == user_id)
    if course_id is not None:
        stmt = stmt.where(Transcript.lms_course_id == course_id)
    return {(c, m): i for c, m, i in (await db.execute(stmt)).all()}
```

- [ ] **Step 4: `app/api/routes_transcripts.py` 작성**

```python
"""브라우저 Whisper 전사 결과 저장·조회. 전사는 브라우저에서 하고 서버는 결과만 보관한다."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.deps import CurrentUser, Db
from app.db.base import utcnow
from app.db.models import CourseRow, SavedVideo, Transcript

router = APIRouter(tags=["transcripts"])

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_SEGMENTS = 20_000


class Segment(BaseModel):
    start: float
    end: float | None = None
    text: str


class TranscriptIn(BaseModel):
    course_name: str = Field(max_length=255)
    title: str = Field(max_length=500)
    text: str
    segments: list[Segment] = Field(default_factory=list, max_length=MAX_SEGMENTS)
    model: str = Field(max_length=100)
    duration: float | None = None


class TranscriptSummary(BaseModel):
    id: int
    lms_course_id: int
    module_item_id: int
    course_name: str
    title: str
    model: str
    duration: float | None
    updated_at: datetime


class TranscriptOut(TranscriptSummary):
    text: str
    segments: list[Segment]


def _summary(t: Transcript) -> TranscriptSummary:
    return TranscriptSummary(**{k: getattr(t, k) for k in TranscriptSummary.model_fields})


async def _can_write(db, user_id: int, course_id: int, module_item_id: int) -> bool:
    """수강 중인 과목이거나, 저장해 둔 영상(지난 학기 포함)이면 쓸 수 있다."""
    if await db.scalar(select(CourseRow.id).where(CourseRow.user_id == user_id, CourseRow.lms_course_id == course_id)):
        return True
    return bool(await db.scalar(select(SavedVideo.id).where(
        SavedVideo.user_id == user_id, SavedVideo.lms_course_id == course_id, SavedVideo.module_item_id == module_item_id)))


@router.put("/courses/{course_id}/videos/{module_item_id}/transcript", response_model=TranscriptSummary)
async def save_transcript(course_id: int, module_item_id: int, request: Request, user: CurrentUser, db: Db):
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "전사 결과가 너무 큽니다 (최대 2MB).")
    try:
        data = TranscriptIn.model_validate_json(body)
    except ValidationError as e:
        raise RequestValidationError(e.errors(include_url=False)) from e
    if not await _can_write(db, user.id, course_id, module_item_id):
        raise HTTPException(404, "수강 중인 과목의 영상이 아닙니다.")
    row = await db.scalar(select(Transcript).where(
        Transcript.user_id == user.id, Transcript.lms_course_id == course_id, Transcript.module_item_id == module_item_id))
    if row is None:
        row = Transcript(user_id=user.id, lms_course_id=course_id, module_item_id=module_item_id)
        db.add(row)
    row.course_name, row.title, row.text, row.model, row.duration = (
        data.course_name, data.title, data.text, data.model, data.duration)
    row.segments = [s.model_dump() for s in data.segments]
    row.updated_at = utcnow()
    await db.commit()
    await db.refresh(row)
    return _summary(row)


@router.get("/transcripts", response_model=list[TranscriptSummary])
async def list_transcripts(user: CurrentUser, db: Db, course_id: int | None = None):
    stmt = select(Transcript).where(Transcript.user_id == user.id)
    if course_id is not None:
        stmt = stmt.where(Transcript.lms_course_id == course_id)
    stmt = stmt.order_by(Transcript.updated_at.desc())
    return [_summary(t) for t in (await db.scalars(stmt)).all()]


async def _own(db, user_id: int, transcript_id: int) -> Transcript:
    row = await db.get(Transcript, transcript_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "전사 결과를 찾을 수 없습니다.")
    return row


@router.get("/transcripts/{transcript_id}", response_model=TranscriptOut)
async def get_transcript(transcript_id: int, user: CurrentUser, db: Db):
    row = await _own(db, user.id, transcript_id)
    return TranscriptOut(**_summary(row).model_dump(), text=row.text, segments=row.segments)


@router.delete("/transcripts/{transcript_id}", status_code=204)
async def delete_transcript(transcript_id: int, user: CurrentUser, db: Db):
    await db.delete(await _own(db, user.id, transcript_id))
    await db.commit()
    return Response(status_code=204)
```

- [ ] **Step 5: 라우터 등록과 목록 필드**

`app/main.py`: import에 `routes_transcripts`를 추가하고 `app.include_router(routes_transcripts.router)`를 `routes_media` 다음에 둔다.

`app/api/routes_media.py`:
- import `from app.services.transcripts import transcript_map`
- `VideoOut`, `SavedVideoOut`에 `transcript_id: int | None = None`
- `list_videos`: `tmap = await transcript_map(db, user.id, course_id)`를 구하고 `transcript_id=tmap.get((course_id, v.module_item_id))`를 넘긴다
- `list_saved_videos`: `tmap = await transcript_map(db, user.id, course_id)`를 구하고 `skip`에 `"transcript_id"`를 넣은 뒤 `transcript_id=tmap.get((r.lms_course_id, r.module_item_id))`를 넘긴다

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_transcripts.py tests/test_downloads.py tests/test_media.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/services/transcripts.py app/api/routes_transcripts.py app/main.py app/api/routes_media.py tests/test_transcripts.py
git commit -m "feat: store browser transcripts per lecture video"
```

---

### Task 7: 웹 — 영상/수업자료 탭, 배지, 썸네일, 폴더에 여러 개 받기

**Files:**
- Create: `app/web/downloads.js`
- Modify: `app/web/app.js` (`state`, `viewVideos`, `renderVideoList`, `downloadVideo`), `app/web/index.html`, `app/web/style.css`

**Interfaces:**
- Consumes: `GET /courses/{id}/materials`, `POST …/download-link`, 응답의 `downloaded_at`, `thumbnail_url`, `transcript_id`
- Produces (전역 함수, 다른 스크립트에서 사용):
  - `bulkDownloadSupported(): boolean`
  - `startBulkDownload(items: {linkPath: string, label: string}[], onUpdate: ({done, failed, total, running}) => void): Promise<void>`
  - `stopBulkDownload(): void`
  - `renderVideoList()`는 Task 8에서 전사 버튼을 붙일 때 그대로 다시 호출한다

- [ ] **Step 1: `app/web/downloads.js` 작성**

```js
"use strict";

/* ---------------- 여러 개 받기 ----------------
 * <a download>는 끝났는지 알 수 없어 동시 수를 지킬 수 없다. 사용자가 고른 폴더에
 * fetch 응답을 스트리밍으로 써서 하나씩 끝을 확인한다(메모리에 올리지 않는다).
 * 서버 제한과 같게 동시 2개, 429면 3초 뒤 다시 시도(최대 5번). */

const BULK_CONCURRENCY = 2;
const bulk = { ctrl: null };

function bulkDownloadSupported() {
  return typeof window.showDirectoryPicker === "function";
}

function stopBulkDownload() {
  bulk.ctrl?.abort();
}

async function startBulkDownload(items, onUpdate) {
  if (bulk.ctrl) throw new Error("이미 여러 개 받기를 진행하고 있습니다.");
  const dir = await window.showDirectoryPicker({ mode: "readwrite" });
  bulk.ctrl = new AbortController();
  const signal = bulk.ctrl.signal;
  const queue = [...items];
  const stat = { done: 0, failed: 0, total: items.length, running: true, errors: [] };
  onUpdate({ ...stat });
  const worker = async () => {
    while (queue.length && !signal.aborted) {
      const item = queue.shift();
      try {
        await saveToFolder(dir, item, signal);
        stat.done++;
        item.onDone?.();
      } catch (err) {
        if (signal.aborted) break;
        stat.failed++;
        stat.errors.push(`${item.label}: ${err.message}`);
      }
      onUpdate({ ...stat });
    }
  };
  try {
    await Promise.all(Array.from({ length: BULK_CONCURRENCY }, worker));
  } finally {
    bulk.ctrl = null;
    stat.running = false;
    onUpdate({ ...stat, stopped: signal.aborted });
  }
}

async function saveToFolder(dir, item, signal) {
  for (let attempt = 0; ; attempt++) {
    const link = await api(item.linkPath, { method: "POST" });  // 링크는 10분짜리라 받기 직전에 발급한다
    const res = await fetch(link.url, { signal });
    if (res.status === 429 && attempt < 5) {
      await new Promise((r) => setTimeout(r, 3000));
      continue;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error(typeof data?.detail === "string" ? data.detail : `요청 실패 (${res.status})`);
    }
    const handle = await dir.getFileHandle(link.file_name, { create: true });
    await res.body.pipeTo(await handle.createWritable(), { signal });
    return;
  }
}
```

- [ ] **Step 2: `index.html`에 스크립트 추가**

`<script src="/app.js"></script>` 앞에 `<script src="/downloads.js"></script>`를 넣는다.

- [ ] **Step 3: `app.js` 상태 추가**

`state` 초기값의 `videoCourse: null, videoList: null, savedVideos: null,` 줄을 다음으로 바꾼다:

```js
  videoCourse: null, videoList: null, savedVideos: null,
  videoTab: "videos", materialList: null, notDownloadedOnly: false, bulk: null,
```

- [ ] **Step 4: `viewVideos` 수정**

`viewVideos`에서 `$("#view").innerHTML = \`…\``부터 함수 끝까지를 다음으로 바꾼다(과목 선택·지난 과목 계산 부분은 그대로 둔다):

```js
  const isPast = !courses.some((c) => c.lms_course_id === state.videoCourse);
  if (isPast) state.videoTab = "videos";  // 지난 과목은 수업자료를 LMS에서 볼 수 없다
  $("#view").innerHTML = `
    <div class="view-head"><h1>강의 영상</h1>
      <label>과목<select id="video-course">${options}</select></label></div>
    <div class="tabs" role="tablist">
      <button role="tab" data-tab="videos" aria-selected="${state.videoTab === "videos"}">영상</button>
      <button role="tab" data-tab="materials" aria-selected="${state.videoTab === "materials"}" ${isPast ? "disabled" : ""}>수업자료</button>
      <label class="check inline"><input type="checkbox" id="not-downloaded" ${state.notDownloadedOnly ? "checked" : ""}><span>안 받은 것만</span></label>
    </div>
    <p class="notice-inline">본인이 수강하는 강의와 자료를 개인 복습용으로 받는 기능입니다. 강의 영상과 자료는 교수와 학교의 저작물이므로 다른 사람에게 공유하거나 올리지 마세요. 파일은 서버에 저장하지 않고 학교에서 받는 대로 바로 전달합니다. 동기화 때 영상 주소를 저장해 두므로 시청기간이 끝난 영상도 받을 수 있지만, 학교 서버가 주소를 막거나 영상을 지우면 받을 수 없습니다.</p>
    <p class="bulk-status" id="bulk-status" role="status"></p>
    <div class="panel" id="video-list"><div class="empty"><p>LMS에서 목록을 불러오는 중</p></div></div>`;
  $("#video-course").addEventListener("change", (e) => {
    state.videoCourse = Number(e.target.value); state.videoList = null; state.materialList = null; viewVideos();
  });
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { state.videoTab = b.dataset.tab; viewVideos(); }));
  $("#not-downloaded").addEventListener("change", (e) => { state.notDownloadedOnly = e.target.checked; renderCurrentTab(); });
  renderBulkStatus();

  const courseId = state.videoCourse;
  if (isPast) {  // 지난 과목: 저장된 영상만
    state.videoList = { courseId, items: [] };
    renderVideoList();
    return;
  }
  const key = state.videoTab === "videos" ? "videoList" : "materialList";
  const path = state.videoTab === "videos" ? `/courses/${courseId}/videos` : `/courses/${courseId}/materials`;
  if (!state[key] || state[key].courseId !== courseId) {
    try {
      state[key] = { courseId, items: await api(path) };
    } catch (err) {
      if (state.view === "videos" && state.videoCourse === courseId) viewError(err.message, viewVideos);
      return;
    }
  }
  if (state.view !== "videos" || state.videoCourse !== courseId) return;
  renderCurrentTab();
}

function renderCurrentTab() {
  if (state.videoTab === "materials") renderMaterialList(); else renderVideoList();
}

function renderBulkStatus() {
  const box = $("#bulk-status");
  if (!box) return;
  const b = state.bulk;
  if (!b) { box.innerHTML = ""; return; }
  const text = b.running ? `여러 개 받는 중: ${b.done + b.failed}/${b.total} (실패 ${b.failed})`
    : b.stopped ? `중지했습니다: ${b.done}/${b.total} 완료` : `다 받았습니다: ${b.done}/${b.total} 완료, 실패 ${b.failed}`;
  box.innerHTML = `${esc(text)} ${b.running ? '<button class="btn small" id="bulk-stop">중지</button>' : ""}
    ${b.errors?.length ? `<details><summary>실패 항목</summary><ul>${b.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></details>` : ""}`;
  $("#bulk-stop")?.addEventListener("click", stopBulkDownload);
}

function bulkButton(items) {
  const label = items.length ? `전체 받기 (${items.length})` : "전체 받기";
  return bulkDownloadSupported()
    ? `<button class="btn small" data-bulk ${items.length && !state.bulk?.running ? "" : "disabled"}>${label}</button>`
    : `<button class="btn small" disabled title="폴더에 바로 저장하는 기능은 데스크톱 Chrome·Edge에서만 지원합니다.">${label}</button>`;
}

function wireBulk(box, groups) {
  box.querySelectorAll("[data-bulk]").forEach((b, i) => b.addEventListener("click", async () => {
    try {
      await startBulkDownload(groups[i], (stat) => { state.bulk = stat; renderBulkStatus(); if (!stat.running) renderCurrentTab(); });
    } catch (err) {
      if (err.name !== "AbortError") toast(err.message, true);  // 폴더 선택 취소는 조용히
    }
  }));
}

function groupBy(items, key, title) {
  const groups = new Map();
  for (const v of items) {
    if (!groups.has(v[key])) groups.set(v[key], { title: v[title], items: [] });
    groups.get(v[key]).items.push(v);
  }
  return [...groups.values()];
}

const doneBadge = (at) => at ? `<span class="badge done" title="${esc(fmtDate(new Date(at)))}에 받음">✓ 받음</span>` : "";
```

- [ ] **Step 5: `renderVideoList` 교체**

기존 `renderVideoList` 전체를 다음으로 바꾼다:

```js
function renderVideoList() {
  const courseId = state.videoList.courseId;
  const course = state.courses.find((c) => c.lms_course_id === courseId);
  const saved = new Map(state.savedVideos.filter((v) => v.lms_course_id === courseId).map((v) => [v.module_item_id, v]));
  // LMS 목록을 기준으로 하고, LMS에서 사라진 저장 영상을 뒤에 붙인다
  let items = state.videoList.items.map((v) => ({ ...v, savedRow: saved.get(v.module_item_id) }));
  const live = new Set(items.map((v) => v.module_item_id));
  for (const s of saved.values()) {
    if (!live.has(s.module_item_id)) items.push({ ...s, module_id: `saved-${s.module_title}`, available: false, completed: false, savedRow: s });
  }
  for (const v of items) {
    v.courseName = course?.name || v.savedRow?.course_name || "";
    v.downloaded_at = v.downloaded_at || v.savedRow?.downloaded_at || null;
    v.transcript_id = v.transcript_id ?? v.savedRow?.transcript_id ?? null;
    v.linkPath = v.available ? `/courses/${courseId}/videos/${v.module_item_id}/download-link`
      : v.savedRow?.saved ? `/saved-videos/${v.savedRow.id}/download-link` : null;
    v.audioPath = v.available ? `/courses/${courseId}/videos/${v.module_item_id}/audio-link`
      : v.savedRow?.saved ? `/saved-videos/${v.savedRow.id}/audio-link` : null;
  }
  if (state.notDownloadedOnly) items = items.filter((v) => !v.downloaded_at);
  const box = $("#video-list");
  if (!items.length) {
    box.innerHTML = `<div class="empty"><p>${state.notDownloadedOnly ? "아직 받지 않은 영상이 없습니다." : "이 과목에는 내려받을 수 있는 강의 영상이 없습니다."}</p></div>`;
    return;
  }
  const groups = groupBy(items, "module_id", "module_title");
  const status = (v) => v.available ? (v.completed ? "수강 완료" : "미수강")
    : v.savedRow ? "시청기간 종료" : "아직 열리지 않음";
  const thumb = (v) => v.thumbnail_url
    ? `<img class="thumb" src="${esc(v.thumbnail_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : "";
  const button = (v) => v.linkPath
    ? `<button class="btn small" data-link="${esc(v.linkPath)}">내려받기</button>`
    : `<button class="btn small" disabled title="${esc(v.savedRow?.resolve_error || "")}">내려받기</button>`;
  const bulkGroups = groups.map((g) => g.items.filter((v) => v.linkPath).map((v) => ({
    linkPath: v.linkPath, label: v.title, onDone: () => { v.downloaded_at = new Date().toISOString(); } })));
  box.innerHTML = groups.map((g, i) => `<div class="group-label row">${esc(g.title)}
      <span class="group-actions">${bulkButton(bulkGroups[i])}${typeof transcribeGroupButton === "function" ? transcribeGroupButton(g.items) : ""}</span></div>
    <ul class="list">${g.items.map((v) => `
    <li class="video-row">
      ${thumb(v)}
      <span class="video-info"><span class="title">${esc(v.title)}</span><br>
        <span class="meta row"><span>${v.duration ? fmtDuration(v.duration) : ""}</span><span>${fmtSize(v.size)}</span>
          <span>${status(v)}</span>${v.savedRow?.saved ? "<span>주소 저장됨</span>" : ""}${doneBadge(v.downloaded_at)}</span></span>
      <span class="row-actions">${typeof transcribeButton === "function" ? transcribeButton(v, courseId) : ""}${button(v)}</span>
    </li>`).join("")}</ul>`).join("");
  box.querySelectorAll("[data-link]").forEach((b) => b.addEventListener("click", async () => {
    if (await downloadVideo(b, b.dataset.link)) {
      const v = items.find((x) => x.linkPath === b.dataset.link);
      if (v) { v.downloaded_at = new Date().toISOString(); markDownloaded(v); }
      renderVideoList();
    }
  }));
  wireBulk(box, bulkGroups);
  if (typeof wireTranscribe === "function") wireTranscribe(box, items, courseId);
}

function markDownloaded(v) {
  // 다시 그려도 표시가 남도록 원본 목록에도 반영한다
  const src = state.videoList.items.find((x) => x.module_item_id === v.module_item_id);
  if (src) src.downloaded_at = v.downloaded_at;
  if (v.savedRow) v.savedRow.downloaded_at = v.downloaded_at;
}

function renderMaterialList() {
  const courseId = state.materialList.courseId;
  let items = state.materialList.items.map((m) => ({ ...m, linkPath: `/courses/${courseId}/materials/${encodeURIComponent(m.key)}/download-link` }));
  if (state.notDownloadedOnly) items = items.filter((m) => !m.downloaded_at);
  const box = $("#video-list");
  if (!items.length) {
    box.innerHTML = `<div class="empty"><p>${state.notDownloadedOnly ? "아직 받지 않은 자료가 없습니다." : "이 과목에는 받을 수 있는 수업자료가 없습니다."}</p></div>`;
    return;
  }
  const groups = groupBy(items, "module_id", "module_title");
  const bulkGroups = groups.map((g) => g.items.map((m) => ({
    linkPath: m.linkPath, label: m.file_name,
    onDone: () => { const src = state.materialList.items.find((x) => x.key === m.key); if (src) src.downloaded_at = new Date().toISOString(); } })));
  box.innerHTML = groups.map((g, i) => `<div class="group-label row">${esc(g.title)}<span class="group-actions">${bulkButton(bulkGroups[i])}</span></div>
    <ul class="list">${g.items.map((m) => `
    <li class="video-row">
      <span class="video-info"><span class="title">${esc(m.file_name)}</span><br>
        <span class="meta row"><span>${esc(m.title)}</span><span>${fmtSize(m.size)}</span>
          <span>${m.kind === "canvas_file" ? "위키 첨부" : "자료"}</span>${doneBadge(m.downloaded_at)}</span></span>
      <button class="btn small" data-link="${esc(m.linkPath)}">내려받기</button>
    </li>`).join("")}</ul>`).join("");
  box.querySelectorAll("[data-link]").forEach((b) => b.addEventListener("click", async () => {
    if (await downloadVideo(b, b.dataset.link)) {
      const m = items.find((x) => x.linkPath === b.dataset.link);
      const src = state.materialList.items.find((x) => x.key === m?.key);
      if (src) src.downloaded_at = new Date().toISOString();
      renderMaterialList();
    }
  }));
  wireBulk(box, bulkGroups);
}
```

- [ ] **Step 6: `downloadVideo`가 성공 여부를 돌려주도록 수정**

```js
async function downloadVideo(btn, linkPath) {
  btn.disabled = true;
  btn.textContent = "준비 중";
  try {
    const link = await api(linkPath, { method: "POST" });
    const a = document.createElement("a");
    a.href = link.url;
    a.download = link.file_name;
    document.body.append(a);
    a.click();
    a.remove();
    toast(`${link.file_name} 내려받기를 시작했습니다. 브라우저 다운로드 목록에서 진행 상황을 볼 수 있습니다.`);
    return true;
  } catch (err) {
    toast(err.message, true);
    return false;
  } finally {
    btn.disabled = false;
    btn.textContent = "내려받기";
  }
}
```

- [ ] **Step 7: 스타일 추가** (`app/web/style.css`, `.group-label` 규칙 다음)

```css
.tabs { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.tabs [role="tab"] { border: 0; background: var(--fill); color: var(--ink-2); font: inherit; font-weight: 600; padding: 7px 14px; border-radius: var(--r-chip); cursor: pointer; }
.tabs [role="tab"][aria-selected="true"] { background: var(--blue); color: #fff; }
.tabs [role="tab"]:disabled { opacity: .45; cursor: not-allowed; }
.tabs .check.inline { margin-left: auto; font-size: 13px; color: var(--ink-2); }
.group-label.row { display: flex; align-items: center; gap: 8px; }
.group-actions { margin-left: auto; display: flex; gap: 6px; }
.row-actions { margin-left: auto; display: flex; gap: 6px; flex-shrink: 0; }
.thumb { width: 96px; aspect-ratio: 16 / 9; object-fit: cover; border-radius: var(--r-chip); background: var(--fill); flex-shrink: 0; }
.badge { font-size: 12px; font-weight: 600; padding: 1px 6px; border-radius: 6px; }
.badge.done { color: var(--green); background: var(--green-soft); }
.badge.transcribed { color: var(--blue); background: var(--blue-soft); }
.bulk-status:empty { display: none; }
.bulk-status { font-size: 13px; color: var(--ink-2); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
@media (max-width: 720px) { .thumb { display: none; } .list > li.video-row { flex-wrap: wrap; } .row-actions { margin-left: 0; } }
```

- [ ] **Step 8: 수동 확인**

Run: `docker compose up -d` (API) 또는 `uv run uvicorn app.main:app --reload`로 띄우고 브라우저에서 `/#videos`를 연다.
Expected:
- 영상 탭에 썸네일(실패하면 숨김), 받은 항목에 "✓ 받음"이 보인다.
- 수업자료 탭에 주차별 자료가 나오고, 내려받기를 하면 배지가 붙는다.
- "안 받은 것만"이 두 탭 모두에 적용된다.
- Chrome에서 "전체 받기"를 누르면 폴더 선택 후 2개씩 저장되고 진행 문구가 바뀐다. 중지가 동작한다.
- Firefox에서는 버튼이 비활성화되고 이유가 툴팁으로 보인다.
- 브라우저 콘솔에 오류가 없다.

- [ ] **Step 9: 커밋**

```bash
git add app/web/downloads.js app/web/app.js app/web/index.html app/web/style.css
git commit -m "feat(web): materials tab, download badges, thumbnails and folder bulk download"
```

---

### Task 8: 웹 — 브라우저 Whisper 전사

**Files:**
- Create: `app/web/whisper.worker.js`, `app/web/transcribe.js`
- Modify: `app/web/index.html`, `app/web/style.css`

**Interfaces:**
- Consumes: `POST …/audio-link` → `/audio/{token}`(16kHz s16le, `X-Audio-Duration`), `PUT /courses/{cid}/videos/{mid}/transcript`, `GET/DELETE /transcripts/{id}`. Task 7의 `renderVideoList`, `items[].audioPath`, `items[].courseName`, `items[].transcript_id`
- Produces (전역, Task 7의 `renderVideoList`가 있으면 호출):
  - `transcribeButton(v, courseId): string`, `transcribeGroupButton(items): string`, `wireTranscribe(box, items, courseId): void`
  - Worker 프로토콜(in) `start{jobId,duration}` / `chunk{jobId,samples}` / `end{jobId}` / `cancel{jobId}`
  - Worker 프로토콜(out) `device{device}` / `model-progress{percent}` / `progress{jobId,seconds}` / `segment{jobId,start,end,text}` / `done{jobId}` / `error{jobId,message,fatal}`

- [ ] **Step 1: `app/web/whisper.worker.js` 작성**

```js
// 브라우저에서 Whisper를 실행하는 모듈 Worker.
// 모델은 처음 실행할 때 Hugging Face Hub에서 내려받아 브라우저 캐시에 남는다.
import { pipeline } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.1";

const MODEL = "onnx-community/whisper-small";
const RATE = 16000;
const WINDOW = 30 * RATE;   // Whisper 한 번 입력 길이
const OVERLAP = 5 * RATE;   // 창 경계에서 말이 잘리지 않도록 겹친다

let pipePromise = null;
const jobs = new Map();

const post = (m) => self.postMessage(m);

async function hasWebGPU() {
  try { return Boolean(navigator.gpu && await navigator.gpu.requestAdapter()); } catch { return false; }
}

function loadPipeline() {
  if (pipePromise) return pipePromise;
  const progress_callback = (p) => {
    if (p.status === "progress" && p.total) post({ type: "model-progress", percent: Math.round((p.loaded / p.total) * 100) });
  };
  pipePromise = (async () => {
    if (await hasWebGPU()) {
      try {
        const pipe = await pipeline("automatic-speech-recognition", MODEL, {
          device: "webgpu", dtype: { encoder_model: "fp16", decoder_model_merged: "q4" }, progress_callback });
        post({ type: "device", device: "webgpu" });
        return pipe;
      } catch (err) {
        console.warn("WebGPU로 Whisper를 띄우지 못해 CPU로 전환합니다.", err);
      }
    }
    const pipe = await pipeline("automatic-speech-recognition", MODEL, { device: "wasm", dtype: "q8", progress_callback });
    post({ type: "device", device: "wasm" });
    return pipe;
  })();
  pipePromise.catch(() => { pipePromise = null; });  // 다음 작업에서 다시 시도할 수 있게
  return pipePromise;
}

function takeSamples(job, n) {
  const out = new Float32Array(n);
  let o = 0;
  for (const c of job.chunks) {
    const k = Math.min(c.length, n - o);
    out.set(c.subarray(0, k), o);
    o += k;
    if (o >= n) break;
  }
  return out;
}

function dropSamples(job, n) {
  job.length -= n;
  while (n > 0) {
    const c = job.chunks[0];
    if (c.length <= n) { n -= c.length; job.chunks.shift(); } else { job.chunks[0] = c.subarray(n); n = 0; }
  }
}

async function drain(id, job) {
  while (!job.cancelled && (job.length >= WINDOW || (job.ended && job.length > 0))) {
    const take = Math.min(WINDOW, job.length);
    const audio = takeSamples(job, take);
    const transcriber = await loadPipeline();
    const out = await transcriber(audio, { language: "korean", task: "transcribe", return_timestamps: true });
    if (job.cancelled) return;
    const base = job.base / RATE;
    for (const c of out.chunks || []) {
      const text = (c.text || "").trim();
      const start = base + (c.timestamp?.[0] ?? 0);
      const end = base + (c.timestamp?.[1] ?? take / RATE);
      if (!text || start < job.lastEnd - 0.5) continue;  // 겹친 구간에서 이미 낸 문장
      post({ type: "segment", jobId: id, start, end, text });
      job.lastEnd = end;
    }
    const last = job.ended && take === job.length;
    const advance = last ? take : take - OVERLAP;
    dropSamples(job, advance);
    job.base += advance;
    post({ type: "progress", jobId: id, seconds: job.base / RATE });
    if (last) break;
  }
  if (job.ended && job.length === 0 && !job.cancelled) {
    jobs.delete(id);
    post({ type: "done", jobId: id });
  }
}

function schedule(id, job) {
  job.chain = job.chain.then(() => drain(id, job)).catch((err) => {
    jobs.delete(id);
    post({ type: "error", jobId: id, message: `전사 중 오류가 났습니다: ${err.message}`, fatal: false });
  });
}

self.onmessage = ({ data: m }) => {
  if (m.type === "start") {
    jobs.set(m.jobId, { chunks: [], length: 0, base: 0, lastEnd: 0, ended: false, cancelled: false, chain: Promise.resolve() });
    loadPipeline().catch((err) => {
      jobs.delete(m.jobId);
      post({ type: "error", jobId: m.jobId, message: `Whisper 모델을 불러오지 못했습니다: ${err.message}`, fatal: true });
    });
    return;
  }
  const job = jobs.get(m.jobId);
  if (!job) return;
  if (m.type === "chunk") {
    job.chunks.push(m.samples);
    job.length += m.samples.length;
    if (job.length >= WINDOW) schedule(m.jobId, job);
  } else if (m.type === "end") {
    job.ended = true;
    schedule(m.jobId, job);
  } else if (m.type === "cancel") {
    job.cancelled = true;
    jobs.delete(m.jobId);
  }
};
```

- [ ] **Step 2: `app/web/transcribe.js` 작성**

```js
"use strict";

/* ---------------- 브라우저 Whisper 전사 ----------------
 * 서버의 16kHz PCM 스트림을 Worker로 넘겨 전사하고, 끝나면 결과를 서버에 저장한다.
 * 한 번에 하나씩 처리한다. 탭을 닫으면 진행 중인 작업은 사라진다. */

const WHISPER_RATE = 16000;
const READ_AHEAD_SECONDS = 60;  // Worker보다 이만큼만 앞서 받는다(메모리 일정)
const tx = { worker: null, device: null, modelPercent: null, queue: [], current: null, finished: [], nextId: 1 };

function txKey(courseId, moduleItemId) { return `${courseId}:${moduleItemId}`; }

function ensureWorker() {
  if (tx.worker) return tx.worker;
  const w = new Worker("/whisper.worker.js", { type: "module" });
  w.addEventListener("message", onWorkerMessage);
  w.addEventListener("error", (e) => failAll(`Whisper를 불러오지 못했습니다: ${e.message || "네트워크를 확인하세요"}`));
  tx.worker = w;
  return w;
}

function onWorkerMessage({ data: m }) {
  if (m.type === "model-progress") { tx.modelPercent = m.percent; renderTxPanel(); return; }
  if (m.type === "device") {
    tx.device = m.device;
    tx.modelPercent = null;
    if (m.device === "wasm" && !store.get("whisper-cpu-notice")) {
      toast("이 브라우저는 WebGPU를 지원하지 않아 CPU로 전사합니다. 강의 길이보다 오래 걸릴 수 있습니다.");
      store.set("whisper-cpu-notice", "1");
    }
    renderTxPanel();
    return;
  }
  const job = tx.current;
  if (!job || m.jobId !== job.id) return;
  if (m.type === "progress") { job.seconds = m.seconds; job.wake?.(); renderTxPanel(); }
  else if (m.type === "segment") job.segments.push({ start: m.start, end: m.end, text: m.text });
  else if (m.type === "done") job.resolve();
  else if (m.type === "error") {
    job.reject(new Error(m.message));
    if (m.fatal) failAll(m.message);
  }
}

function failAll(message) {
  tx.worker?.terminate();
  tx.worker = null;
  tx.current?.reject?.(new Error(message));
  for (const job of tx.queue.splice(0)) tx.finished.push({ ...job, status: "failed", error: message });
  renderTxPanel();
}

function enqueueTranscription(items) {
  for (const it of items) {
    const key = txKey(it.courseId, it.moduleItemId);
    if (tx.current?.key === key || tx.queue.some((q) => q.key === key)) continue;
    tx.finished = tx.finished.filter((f) => f.key !== key);
    tx.queue.push({ ...it, key, id: tx.nextId++, status: "queued", seconds: 0, duration: it.duration || null });
  }
  renderTxPanel();
  if (!tx.current) runNext();
}

function cancelTranscription() {
  const job = tx.current;
  if (!job) return;
  job.status = "cancelled";
  job.ctrl.abort();
  tx.worker?.postMessage({ type: "cancel", jobId: job.id });
  job.reject(new Error("중지했습니다."));
}

async function runNext() {
  const job = tx.queue.shift() || null;
  tx.current = job;
  renderTxPanel();
  if (!job) return;
  job.status = "running";
  job.segments = [];
  job.ctrl = new AbortController();
  job.done = new Promise((resolve, reject) => { job.resolve = resolve; job.reject = reject; });
  job.done.catch(() => {});  // 처리하지 않은 거부 경고 방지(아래에서 await)
  try {
    const link = await api(job.audioPath, { method: "POST" });
    const res = await fetch(link.url, { signal: job.ctrl.signal });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error(typeof data?.detail === "string" ? data.detail : `오디오를 받지 못했습니다 (${res.status})`);
    }
    job.duration = Number(res.headers.get("X-Audio-Duration")) || job.duration;
    const worker = ensureWorker();
    worker.postMessage({ type: "start", jobId: job.id, duration: job.duration });
    await pumpAudio(res.body, job, worker);
    worker.postMessage({ type: "end", jobId: job.id });
    await job.done;
    const saved = await api(`/courses/${job.courseId}/videos/${job.moduleItemId}/transcript`, { method: "PUT", json: {
      course_name: job.courseName, title: job.title,
      text: job.segments.map((s) => s.text).join("\n"),
      segments: job.segments, model: `whisper-small/${tx.device || "unknown"}`, duration: job.totalSeconds,
    } });
    job.status = "done";
    onTranscriptSaved(job, saved.id);
  } catch (err) {
    if (job.status !== "cancelled") { job.status = "failed"; job.error = err.message; }
  }
  tx.finished.push(job);
  tx.current = null;
  runNext();
}

async function pumpAudio(body, job, worker) {
  const reader = body.getReader();
  let carry = null;
  let sent = 0;
  for (;;) {
    while (sent / WHISPER_RATE - job.seconds > READ_AHEAD_SECONDS) {
      await Promise.race([new Promise((r) => { job.wake = r; }), job.done]);
    }
    const { value, done } = await reader.read();
    if (done) break;
    let bytes = value;
    if (carry) {
      const merged = new Uint8Array(carry.length + bytes.length);
      merged.set(carry);
      merged.set(bytes, carry.length);
      bytes = merged;
      carry = null;
    }
    const even = bytes.length - (bytes.length % 2);  // 16비트 샘플 경계
    if (even < bytes.length) carry = bytes.slice(even);
    const pcm = new Int16Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + even));
    const samples = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768;
    worker.postMessage({ type: "chunk", jobId: job.id, samples }, [samples.buffer]);
    sent += samples.length;
  }
  job.totalSeconds = sent / WHISPER_RATE;
  if (sent === 0) throw new Error("오디오가 비어 있습니다. 소리가 없는 영상이거나 서버에서 변환하지 못했습니다.");
}

function onTranscriptSaved(job, id) {
  for (const list of [state.videoList?.items, state.savedVideos]) {
    const row = list?.find((v) => (v.lms_course_id ?? state.videoList?.courseId) === job.courseId && v.module_item_id === job.moduleItemId);
    if (row) row.transcript_id = id;
  }
  if (state.view === "videos" && state.videoTab === "videos" && state.videoList?.courseId === job.courseId) renderVideoList();
  if (id) toast(`「${job.title}」 전사를 마쳤습니다.`);  // id가 null이면 삭제
}

/* ---------- 목록에 붙는 버튼 (app.js의 renderVideoList가 호출) ---------- */

function txStatusOf(courseId, moduleItemId) {
  const key = txKey(courseId, moduleItemId);
  if (tx.current?.key === key) return "running";
  if (tx.queue.some((q) => q.key === key)) return "queued";
  return null;
}

function transcribeButton(v, courseId) {
  const parts = [];
  if (v.transcript_id) parts.push(`<button class="btn small" data-tx-open="${v.transcript_id}"><span class="badge transcribed">전사됨</span> 보기</button>`);
  if (!v.audioPath) return parts.join("");
  const st = txStatusOf(courseId, v.module_item_id);
  const label = st === "running" ? "전사 중" : st === "queued" ? "대기 중" : v.transcript_id ? "다시 전사" : "전사";
  parts.push(`<button class="btn small" data-tx="${v.module_item_id}" ${st ? "disabled" : ""}>${label}</button>`);
  return parts.join("");
}

function transcribeGroupButton(items) {
  const n = items.filter((v) => v.audioPath && !v.transcript_id).length;
  return n ? `<button class="btn small" data-tx-group="${esc(items[0].module_id)}">전체 전사 (${n})</button>` : "";
}

function wireTranscribe(box, items, courseId) {
  const toJob = (v) => ({ courseId, moduleItemId: v.module_item_id, title: v.title, courseName: v.courseName,
                          audioPath: v.audioPath, duration: v.duration });
  box.querySelectorAll("[data-tx]").forEach((b) => b.addEventListener("click", () => {
    const v = items.find((x) => String(x.module_item_id) === b.dataset.tx);
    if (v) { enqueueTranscription([toJob(v)]); renderVideoList(); }
  }));
  box.querySelectorAll("[data-tx-group]").forEach((b) => b.addEventListener("click", () => {
    enqueueTranscription(items.filter((v) => String(v.module_id) === b.dataset.txGroup && v.audioPath && !v.transcript_id).map(toJob));
    renderVideoList();
  }));
  box.querySelectorAll("[data-tx-open]").forEach((b) => b.addEventListener("click", () => openTranscript(Number(b.dataset.txOpen))));
}

/* ---------- 진행 패널 ---------- */

function renderTxPanel() {
  const panel = $("#transcribe-panel");
  if (!panel) return;
  const job = tx.current;
  const failed = tx.finished.filter((f) => f.status === "failed");
  panel.hidden = !job && !tx.queue.length && !failed.length;
  if (panel.hidden) return;
  const pct = job?.duration ? Math.min(100, Math.round((job.seconds / job.duration) * 100)) : null;
  const model = tx.modelPercent !== null && tx.modelPercent !== undefined ? `<p class="meta">Whisper 모델 받는 중 ${tx.modelPercent}% (처음 한 번만)</p>` : "";
  panel.innerHTML = `
    <strong>강의 전사</strong>
    ${job ? `<p class="title">${esc(job.title)}</p>
      <progress max="100" ${pct === null ? "" : `value="${pct}"`}></progress>
      <p class="meta">${pct === null ? fmtDuration(job.seconds || 0) + " 처리" : `${pct}%`}${tx.device ? ` · ${tx.device === "webgpu" ? "GPU" : "CPU"}` : ""}</p>
      ${model}<button class="btn small" id="tx-stop">중지</button>` : ""}
    ${tx.queue.length ? `<p class="meta">대기 ${tx.queue.length}개</p>` : ""}
    ${failed.length ? `<details><summary>실패 ${failed.length}개</summary><ul>${failed.map((f) => `<li>${esc(f.title)}: ${esc(f.error)}
      <button class="btn small" data-tx-retry="${esc(f.key)}">다시</button></li>`).join("")}</ul>
      <button class="btn small ghost" id="tx-clear">닫기</button></details>` : ""}`;
  $("#tx-stop")?.addEventListener("click", cancelTranscription);
  $("#tx-clear")?.addEventListener("click", () => { tx.finished = []; renderTxPanel(); });
  panel.querySelectorAll("[data-tx-retry]").forEach((b) => b.addEventListener("click", () => {
    const f = tx.finished.find((x) => x.key === b.dataset.txRetry);
    if (f) enqueueTranscription([f]);
  }));
}

window.addEventListener("beforeunload", (e) => {
  if (tx.current || tx.queue.length) { e.preventDefault(); e.returnValue = ""; }
});

/* ---------- 전사 결과 대화상자 ---------- */

function fmtStamp(sec) {
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

async function openTranscript(id) {
  const dlg = $("#transcript-dialog");
  dlg.innerHTML = `<p class="meta">불러오는 중</p>`;
  dlg.showModal();
  let t;
  try { t = await api(`/transcripts/${id}`); }
  catch (err) { dlg.innerHTML = `<p>${esc(err.message)}</p><button class="btn" id="tx-close">닫기</button>`; $("#tx-close").onclick = () => dlg.close(); return; }
  const plain = () => t.text;
  const stamped = () => t.segments.map((s) => `[${fmtStamp(s.start)}] ${s.text}`).join("\n");
  let withTime = false;
  const render = () => { $("#tx-text").textContent = withTime ? stamped() : plain(); };
  dlg.innerHTML = `
    <div class="dialog-head"><strong>${esc(t.title)}</strong><span class="meta">${esc(t.course_name)} · ${esc(t.model)}</span></div>
    <label class="check"><input type="checkbox" id="tx-time"><span>시간 표시</span></label>
    <pre id="tx-text" class="transcript-text"></pre>
    <div class="row dialog-actions">
      <button class="btn small" id="tx-copy">복사</button>
      <button class="btn small" id="tx-save">.txt로 받기</button>
      <button class="btn small danger" id="tx-delete">삭제</button>
      <button class="btn small primary" id="tx-close" style="margin-left:auto">닫기</button>
    </div>`;
  render();
  $("#tx-time").onchange = (e) => { withTime = e.target.checked; render(); };
  $("#tx-copy").onclick = async () => {
    try { await navigator.clipboard.writeText($("#tx-text").textContent); toast("복사했습니다."); }
    catch { toast("복사하지 못했습니다. 텍스트를 직접 선택해 복사하세요.", true); }
  };
  $("#tx-save").onclick = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([$("#tx-text").textContent], { type: "text/plain;charset=utf-8" }));
    a.download = `${t.title.replace(/[\\/:*?"<>|]/g, " ").trim() || "transcript"}.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  $("#tx-delete").onclick = async () => {
    try { await api(`/transcripts/${id}`, { method: "DELETE" }); }
    catch (err) { toast(err.message, true); return; }
    onTranscriptSaved({ courseId: t.lms_course_id, moduleItemId: t.module_item_id, title: t.title }, null);
    dlg.close();
  };
  $("#tx-close").onclick = () => dlg.close();
}
```

- [ ] **Step 3: `index.html`에 패널·대화상자·스크립트 추가**

`<div class="toast" …></div>` 다음에 추가:

```html
  <aside class="transcribe-panel" id="transcribe-panel" aria-live="polite" hidden></aside>
  <dialog class="transcript-dialog" id="transcript-dialog"></dialog>
```

`<script src="/app.js"></script>` 앞에 `<script src="/transcribe.js"></script>`를 넣는다(최종 순서: calendar, downloads, transcribe, app, week). `transcribe.js`는 `state`, `api`, `esc`, `toast`, `store`, `$`, `fmtDuration`, `renderVideoList`를 실행할 때만 참조하므로 app.js보다 먼저 불러와도 된다.

- [ ] **Step 4: 스타일 추가** (`app/web/style.css` 끝)

```css
.transcribe-panel { position: fixed; right: 16px; bottom: 16px; z-index: 40; width: min(320px, calc(100vw - 32px)); background: var(--surface); border-radius: var(--r-card); padding: 14px 16px; box-shadow: 0 8px 24px rgba(0,0,0,.12); display: grid; gap: 6px; }
.transcribe-panel progress { width: 100%; }
.transcribe-panel .title { font-weight: 600; margin: 0; }
.transcribe-panel .meta { margin: 0; }
.transcript-dialog { border: 0; border-radius: var(--r-card); padding: 20px; width: min(760px, calc(100vw - 32px)); max-height: calc(100vh - 64px); display: grid; gap: 10px; }
.transcript-dialog:not([open]) { display: none; }
.transcript-dialog::backdrop { background: rgba(0,0,0,.35); }
.dialog-head { display: grid; gap: 2px; }
.transcript-text { white-space: pre-wrap; word-break: keep-all; font: inherit; margin: 0; padding: 12px; background: var(--fill); border-radius: var(--r-btn); max-height: 60vh; overflow: auto; }
.dialog-actions { gap: 6px; flex-wrap: wrap; }
```

- [ ] **Step 5: 수동 확인**

서버를 띄우고(ffmpeg 설치 확인: `ffmpeg -version`) Chrome에서 `/#videos`를 연다.
Expected:
1. 짧은 영상(10분 이하)의 "전사"를 누르면 패널에 모델 내려받기 % → 진행률이 나온다. 끝나면 "전사됨 보기"가 생긴다.
2. "보기"를 누르면 한국어 본문이 보이고, 시간 표시 토글·복사·.txt 받기·삭제가 동작한다.
3. 진행 중에 "중지"를 누르면 멈추고 다음 대기 항목으로 넘어간다. 서버 로그에 ffmpeg 오류가 없고 `ps aux | grep ffmpeg`에 남은 프로세스가 없다.
4. 진행 중에 새로고침하면 브라우저가 떠나기 경고를 띄운다.
5. `chrome://flags`에서 WebGPU를 끄거나 Firefox에서 실행하면 CPU 안내 토스트가 한 번 뜨고 전사가 진행된다.
6. 다른 기기(또는 시크릿 창)에서 로그인하면 같은 영상에 "전사됨"이 보인다.

jsdelivr의 transformers.js 4.3.1을 모듈 Worker에서 import하지 못하면(콘솔에 import 오류) 다음 순서로 확인한다. `https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.1/+esm`로 바꾸고, 그래도 안 되면 동작하는 최신 4.x 버전으로 고정한다. 바꾼 주소는 Worker 상단 주석에 이유와 함께 남긴다.

- [ ] **Step 6: 커밋**

```bash
git add app/web/whisper.worker.js app/web/transcribe.js app/web/index.html app/web/style.css
git commit -m "feat(web): transcribe lectures locally with Whisper in a Web Worker"
```

---

### Task 9: README와 전체 회귀

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README 갱신**

"웹 대시보드" 목록 끝에 추가:

```markdown
- **강의 영상·수업자료**: 영상 탭과 수업자료 탭(commons 파일 + 위키 페이지 첨부)을 주차별로 보여준다. 받은 항목에는 "✓ 받음"이 붙고 "안 받은 것만"으로 거를 수 있다.
  기록은 서버가 다운로드 링크를 발급할 때 남기므로, 브라우저에서 저장을 취소해도 받은 것으로 표시된다.
  "전체 받기"는 고른 폴더에 2개씩 바로 저장한다(데스크톱 Chrome·Edge 전용, File System Access API).
- **강의 전사(Whisper)**: 전사는 서버가 아니라 **사용자 브라우저**에서 한다. 서버는 ffmpeg로 오디오만 16kHz PCM으로 바꿔 스트리밍하고(저장하지 않음), 결과 텍스트만 보관한다.
  처음 한 번 Whisper small 모델(수백 MB)을 내려받아 브라우저에 캐시한다. WebGPU가 있으면 GPU로, 없으면 CPU로 돌며 CPU는 강의 길이보다 오래 걸릴 수 있다.
  전사하는 동안 탭을 열어 두어야 하고, 한 번에 하나씩 처리한다. 결과는 다른 기기에서도 보고 .txt로 받을 수 있다.
```

"실행"의 `uv run playwright install chrome` 줄 아래에 추가:

```markdown
brew install ffmpeg                # 호스트에서 API를 띄울 때(전사용 오디오 변환). Docker 이미지에는 들어 있다
```

"실계정 확인" 표에 행 추가:

```markdown
| 수업자료 | `uv run python -m app.lms.materials list --course <id>` | 주차별 자료·위키 첨부가 LMS 화면과 같은지 |
| | `uv run python -m app.lms.materials get --course <id> --key <key>` | `최종:` 호스트가 허용 목록 안인지, 파일이 열리는지 |
```

"API 요약" 표에 행 추가:

```markdown
| `GET /courses/{id}/materials` · `POST /courses/{id}/materials/{key}/download-link` | 수업자료 목록(commons 파일 + 위키 첨부) / 10분 링크 발급(같은 `/downloads/{token}`으로 중계) |
| `POST /courses/{id}/videos/{item}/audio-link` · `POST /saved-videos/{id}/audio-link` · `GET /audio/{token}` | 전사용 16kHz 모노 PCM 스트림(ffmpeg, 서버 전체 `MAX_AUDIO_STREAMS`개) |
| `PUT /courses/{id}/videos/{item}/transcript` · `GET /transcripts` · `GET/DELETE /transcripts/{id}` | 브라우저 전사 결과 저장(최대 2MB, 다시 하면 덮어씀)·조회·삭제 |
```

"알려진 한계"에 추가:

```markdown
- 위키 첨부는 Canvas가 주는 서명된 파일 주소로 받는다. 그 주소가 허용 호스트(`ALLOWED_FILE_HOSTS`) 밖이면 받지 않는다(실계정으로 확인한 호스트만 추가).
- 다운로드 이력은 링크 발급 기준이다. 실제 저장 완료 여부는 알 수 없다.
- 전사는 브라우저 성능에 달려 있다. 탭을 닫으면 진행 중인 전사는 사라지고, 부분 결과는 저장하지 않는다.
```

테스트 개수 줄(`uv run pytest  # 62개 …`)은 실제 개수로 고친다.

- [ ] **Step 2: 전체 테스트**

Run: `uv run pytest -q`
Expected: 모두 PASS(기존 126개 + 신규). 실패가 있으면 해당 태스크로 돌아가 고친다.

Run: `uv run pytest --collect-only -q | tail -1`
Expected: 개수 출력. README의 테스트 개수를 이 값으로 맞춘다.

- [ ] **Step 3: 커밋**

```bash
git add README.md
git commit -m "docs: document materials, download history and browser transcription"
```
