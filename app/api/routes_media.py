"""강의 영상·수업자료 내려받기 (개인 복습용).

- 본인이 수강하는 과목에서, LMS 모듈 목록으로 확인된 영상·자료만 받을 수 있다(임의 content_id·file_id 불가).
- 파일은 서버에 저장하지 않는다. 학교 서버에서 받는 대로 브라우저로 넘긴다.
- 동기화 때 저장해 둔 mp4 주소(saved_videos)로는 시청기간이 끝난 뒤에도 본인이 받을 수 있다.
- 브라우저 다운로드는 Authorization 헤더를 보낼 수 없으므로 10분짜리 서명 링크를 따로 발급한다.
"""

from collections import defaultdict
from contextlib import aclosing
from datetime import datetime

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.api.deps import CurrentUser, Db
from app.db.base import sessionmaker
from app.db.models import CourseRow, SavedVideo
from app.config import get_settings
from app.lms import auth
from app.lms.audio import SAMPLE_RATE, ffmpeg_path, pcm_stream
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
from app.services.downloads import downloaded_map, record_download, video_key
from app.services.lms_access import NoCredentials, lms_client_for
from app.services.transcripts import transcript_map

router = APIRouter(tags=["videos"])

MAX_DOWNLOADS_PER_USER = 2
_active: dict[int, int] = defaultdict(int)
_audio_streams = 0


class VideoOut(BaseModel):
    module_item_id: int
    module_id: int
    module_title: str
    title: str
    content_type: str
    duration: float | None
    size: int | None
    completed: bool
    available: bool
    thumbnail_url: str | None
    downloaded_at: datetime | None = None
    transcript_id: int | None = None


class LinkOut(BaseModel):
    url: str
    file_name: str
    expires_in_minutes: int = DOWNLOAD_LINK_MINUTES


class SavedVideoOut(BaseModel):
    id: int
    lms_course_id: int
    course_name: str
    module_item_id: int
    module_title: str
    title: str
    duration: float | None
    size: int | None
    saved: bool  # mp4 주소를 저장했는지
    resolve_error: str | None
    downloaded_at: datetime | None = None
    transcript_id: int | None = None


class MaterialOut(BaseModel):
    key: str
    kind: str
    module_id: int
    module_title: str
    title: str
    file_name: str
    size: int | None
    downloaded_at: datetime | None = None


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


_VIDEO_FIELDS = ("module_item_id", "module_id", "module_title", "title", "content_type", "duration", "size",
                 "completed", "available", "thumbnail_url")


@router.get("/courses/{course_id}/videos", response_model=list[VideoOut])
async def list_videos(course_id: int, user: CurrentUser, db: Db):
    """과목의 강의 영상 목록(완료한 영상 포함, LMS에서 실시간 조회)."""
    videos = await _videos(db, user.id, course_id)
    done = await downloaded_map(db, user.id, course_id)
    tmap = await transcript_map(db, user.id, course_id)
    return [VideoOut(**{k: getattr(v, k) for k in _VIDEO_FIELDS},
                     downloaded_at=done.get((course_id, video_key(v.module_item_id))),
                     transcript_id=tmap.get((course_id, v.module_item_id))) for v in videos]


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


@router.get("/courses/{course_id}/materials", response_model=list[MaterialOut])
async def list_materials(course_id: int, user: CurrentUser, db: Db):
    """과목의 수업자료(commons 파일 + 위키 첨부, LMS에서 실시간 조회)."""
    mats = await _materials(db, user.id, course_id)
    done = await downloaded_map(db, user.id, course_id)
    return [MaterialOut(**{k: getattr(m, k) for k in MaterialOut.model_fields if k != "downloaded_at"},
                        downloaded_at=done.get((course_id, m.key))) for m in mats]


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
    await record_download(db, user.id, course_id, mat.key, mat.kind, mat.file_name)
    return LinkOut(url=request.app.url_path_for("download_video", token=token), file_name=mat.file_name)


@router.get("/saved-videos", response_model=list[SavedVideoOut])
async def list_saved_videos(user: CurrentUser, db: Db, course_id: int | None = None):
    """동기화 때 저장해 둔 강의 영상(지난 학기·시청기간이 끝난 영상 포함)."""
    stmt = select(SavedVideo).where(SavedVideo.user_id == user.id)
    if course_id is not None:
        stmt = stmt.where(SavedVideo.lms_course_id == course_id)
    stmt = stmt.order_by(SavedVideo.course_name, SavedVideo.lms_course_id, SavedVideo.module_position, SavedVideo.item_position)
    done = await downloaded_map(db, user.id, course_id)
    tmap = await transcript_map(db, user.id, course_id)
    skip = {"saved", "downloaded_at", "transcript_id"}
    return [
        SavedVideoOut(**{k: getattr(r, k) for k in SavedVideoOut.model_fields if k not in skip}, saved=r.media_url is not None,
                      downloaded_at=done.get((r.lms_course_id, video_key(r.module_item_id))),
                      transcript_id=tmap.get((r.lms_course_id, r.module_item_id)))
        for r in (await db.scalars(stmt)).all()
    ]


@router.post("/saved-videos/{saved_id}/download-link", response_model=LinkOut)
async def create_saved_link(saved_id: int, user: CurrentUser, db: Db, request: Request):
    row = await db.get(SavedVideo, saved_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "저장된 영상이 아닙니다.")
    if row.media_url is None:
        raise HTTPException(409, row.resolve_error or "아직 영상 주소를 저장하지 못했습니다. 다음 동기화 뒤에 다시 시도하세요.")
    token = create_download_token(user.id, {"media_url": row.media_url, "file_name": row.file_name})
    await record_download(db, user.id, row.lms_course_id, video_key(row.module_item_id), "video", row.title)
    return LinkOut(url=request.app.url_path_for("download_video", token=token), file_name=row.file_name)


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


# ----------------------------------------------------------------------
# 중계
# ----------------------------------------------------------------------

RELAY_KINDS = {"video", "commons_file", "canvas_file"}


async def _check_hop(request: httpx.Request) -> None:
    """리다이렉트마다 호스트를 확인한다. 최종 주소만 보면 중간 주소로는 이미 요청이 나간 뒤다."""
    check_file_host(str(request.url))  # 영상·자료 허용 호스트는 같다(ssu.ac.kr, commonscdn.com)


def _relay_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0),
                             event_hooks={"request": [_check_hop]})


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
        headers["Accept-Encoding"] = "identity"  # 디코딩 없이 그대로 넘기므로 압축 응답을 받으면 파일이 깨진다
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


class _Slot:
    """동시 실행 자리. 검사와 증가 사이에 await가 없어서 동시 요청도 한도를 넘지 못한다.
    release()는 여러 번 불러도 한 번만 돌려놓는다(스트림 정리와 BackgroundTask 양쪽에서 부른다)."""

    def __init__(self, user_id: int, busy: str, audio: bool = False) -> None:
        global _audio_streams
        if _active[user_id] >= MAX_DOWNLOADS_PER_USER:
            raise HTTPException(429, f"동시에 {MAX_DOWNLOADS_PER_USER}개까지 받을 수 있습니다. {busy}")
        if audio and _audio_streams >= get_settings().max_audio_streams:
            raise HTTPException(429, "서버가 바쁩니다. 잠시 뒤 다시 시도하세요.")
        _active[user_id] += 1
        if audio:
            _audio_streams += 1
        self.user_id, self.audio, self.held = user_id, audio, True

    def release(self) -> None:
        global _audio_streams
        if self.held:
            self.held = False
            _active[self.user_id] -= 1
            if self.audio:
                _audio_streams -= 1


@router.get("/downloads/{token}", name="download_video")
async def download_video(token: str, request: Request):
    decoded = decode_download_token(token)
    if decoded is None or decoded[1].get("kind", "video") not in RELAY_KINDS:
        raise HTTPException(401, "다운로드 링크가 만료되었거나 올바르지 않습니다. 목록에서 다시 받으세요.")
    user_id, data = decoded
    slot = _Slot(user_id, "진행 중인 다운로드가 끝난 뒤 다시 시도하세요.")
    try:
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
    except BaseException:
        slot.release()
        raise

    async def cleanup():
        slot.release()
        with anyio.CancelScope(shield=True):
            await upstream.aclose()
            await http.aclose()

    async def body():
        try:
            async for chunk in upstream.aiter_raw(1 << 16):
                yield chunk
        finally:  # 중간에 끊기거나 업스트림 오류가 나면 BackgroundTask가 돌지 않는다
            await cleanup()

    out_headers = {k: upstream.headers[k] for k in ("content-length", "content-range", "accept-ranges") if k in upstream.headers}
    out_headers["Content-Disposition"] = content_disposition(data["file_name"])
    out_headers["Cache-Control"] = "no-store"
    media_type = "video/mp4" if data.get("kind", "video") == "video" else upstream.headers.get("content-type", "application/octet-stream")
    return StreamingResponse(body(), status_code=upstream.status_code, media_type=media_type,
                             headers=out_headers, background=BackgroundTask(cleanup))


@router.get("/audio/{token}", name="stream_audio")
async def stream_audio(token: str):
    decoded = decode_download_token(token)
    if decoded is None or decoded[1].get("kind") != "audio":
        raise HTTPException(401, "전사 링크가 만료되었거나 올바르지 않습니다. 다시 시도하세요.")
    user_id, data = decoded
    if ffmpeg_path() is None:
        raise HTTPException(503, "서버에 ffmpeg가 설치되어 있지 않습니다.")
    slot = _Slot(user_id, "진행 중인 작업이 끝난 뒤 다시 시도하세요.", audio=True)
    try:
        if saved_url := data.get("media_url"):
            url = check_media_host(saved_url)
        else:
            async with _relay_client() as http:
                url = await resolve_media_url(http, data["content_id"])
    except BaseException as e:
        slot.release()
        if isinstance(e, MediaError):
            raise HTTPException(422, str(e)) from e
        if isinstance(e, httpx.HTTPError):
            raise HTTPException(502, f"학교 서버에 연결하지 못했습니다: {e}") from e
        raise

    async def body():
        try:
            async with aclosing(pcm_stream(url, COMMONS_HEADERS)) as pcm:
                async for chunk in pcm:
                    yield chunk
        finally:
            slot.release()

    headers = {"X-Audio-Sample-Rate": str(SAMPLE_RATE), "Cache-Control": "no-store"}
    if data.get("duration"):
        headers["X-Audio-Duration"] = str(data["duration"])
    return StreamingResponse(body(), media_type="application/octet-stream",
                             headers=headers, background=BackgroundTask(slot.release))
