"""강의 영상 내려받기 (개인 복습용).

- 본인이 수강하는 과목에서, LMS 모듈 목록으로 확인된 영상만 받을 수 있다(임의 content_id 불가).
- 영상 파일은 서버에 저장하지 않는다. 학교 CDN에서 받는 대로 브라우저로 넘긴다.
- 동기화 때 저장해 둔 mp4 주소(saved_videos)로는 시청기간이 끝난 뒤에도 본인이 받을 수 있다.
- 브라우저 다운로드는 Authorization 헤더를 보낼 수 없으므로 10분짜리 서명 링크를 따로 발급한다.
"""

from collections import defaultdict

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.api.deps import CurrentUser, Db
from app.db.models import CourseRow, SavedVideo
from app.lms.errors import InvalidCredentials, LmsError
from app.lms.media import (
    COMMONS_HEADERS,
    MediaError,
    check_media_host,
    content_disposition,
    course_videos,
    resolve_media_url,
)
from app.lms.models import Course
from app.security import DOWNLOAD_LINK_MINUTES, create_download_token, decode_download_token
from app.services.lms_access import NoCredentials, lms_client_for

router = APIRouter(tags=["videos"])

MAX_DOWNLOADS_PER_USER = 2
_active: dict[int, int] = defaultdict(int)


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


async def _videos(db, user_id: int, course_id: int):
    row = await db.scalar(select(CourseRow).where(CourseRow.user_id == user_id, CourseRow.lms_course_id == course_id))
    if row is None:
        raise HTTPException(404, "수강 중인 과목이 아닙니다.")
    try:
        async with lms_client_for(db, user_id) as client:
            modules = await client.get_modules(course_id)
    except (NoCredentials, InvalidCredentials) as e:
        raise HTTPException(400, str(e)) from e
    except LmsError as e:
        raise HTTPException(502, f"LMS 조회 실패: {e}") from e
    return course_videos(Course(id=course_id, name=row.name), modules)


@router.get("/courses/{course_id}/videos", response_model=list[VideoOut])
async def list_videos(course_id: int, user: CurrentUser, db: Db):
    """과목의 강의 영상 목록(완료한 영상 포함, LMS에서 실시간 조회)."""
    return [VideoOut(**{k: getattr(v, k) for k in VideoOut.model_fields}) for v in await _videos(db, user.id, course_id)]


@router.post("/courses/{course_id}/videos/{module_item_id}/download-link", response_model=LinkOut)
async def create_link(course_id: int, module_item_id: int, user: CurrentUser, db: Db, request: Request):
    video = next((v for v in await _videos(db, user.id, course_id) if v.module_item_id == module_item_id), None)
    if video is None:
        raise HTTPException(404, "이 과목에서 해당 영상을 찾을 수 없습니다.")
    if not video.available:
        raise HTTPException(409, "아직 열리지 않은 영상입니다.")
    token = create_download_token(user.id, {"content_id": video.content_id, "file_name": video.file_name})
    return LinkOut(url=request.app.url_path_for("download_video", token=token), file_name=video.file_name)


@router.get("/saved-videos", response_model=list[SavedVideoOut])
async def list_saved_videos(user: CurrentUser, db: Db, course_id: int | None = None):
    """동기화 때 저장해 둔 강의 영상(지난 학기·시청기간이 끝난 영상 포함)."""
    stmt = select(SavedVideo).where(SavedVideo.user_id == user.id)
    if course_id is not None:
        stmt = stmt.where(SavedVideo.lms_course_id == course_id)
    stmt = stmt.order_by(SavedVideo.course_name, SavedVideo.lms_course_id, SavedVideo.module_position, SavedVideo.item_position)
    return [
        SavedVideoOut(**{k: getattr(r, k) for k in SavedVideoOut.model_fields if k != "saved"}, saved=r.media_url is not None)
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
    return LinkOut(url=request.app.url_path_for("download_video", token=token), file_name=row.file_name)


@router.get("/downloads/{token}", name="download_video")
async def download_video(token: str, request: Request):
    decoded = decode_download_token(token)
    if decoded is None:
        raise HTTPException(401, "다운로드 링크가 만료되었거나 올바르지 않습니다. 목록에서 다시 받으세요.")
    user_id, data = decoded
    if _active[user_id] >= MAX_DOWNLOADS_PER_USER:
        raise HTTPException(429, f"동시에 {MAX_DOWNLOADS_PER_USER}개까지 받을 수 있습니다. 진행 중인 다운로드가 끝난 뒤 다시 시도하세요.")

    http = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0))
    try:
        if saved_url := data.get("media_url"):
            media_url = check_media_host(saved_url)
        else:
            media_url = await resolve_media_url(http, data["content_id"])
        headers = dict(COMMONS_HEADERS)
        if rng := request.headers.get("range"):  # 브라우저 이어받기
            headers["Range"] = rng
        upstream = await http.send(http.build_request("GET", media_url, headers=headers), stream=True)
    except MediaError as e:
        await http.aclose()
        raise HTTPException(422, str(e)) from e
    except httpx.HTTPError as e:
        await http.aclose()
        raise HTTPException(502, f"학교 서버에 연결하지 못했습니다: {e}") from e
    if upstream.status_code not in (200, 206):
        await upstream.aclose()
        await http.aclose()
        if saved_url and upstream.status_code in (403, 404, 410):
            raise HTTPException(410, "학교 서버가 저장된 주소의 영상을 더 이상 제공하지 않습니다.")
        raise HTTPException(502, f"학교 서버가 영상을 주지 않았습니다 (HTTP {upstream.status_code}).")

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
    return StreamingResponse(body(), status_code=upstream.status_code, media_type="video/mp4",
                             headers=out_headers, background=BackgroundTask(cleanup))
