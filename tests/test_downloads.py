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
