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
