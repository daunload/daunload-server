from datetime import UTC, datetime

import httpx
import respx

from app.lms.media import CONTENT_PHP
from app.lms.probe import _check_content_php, probe_items

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def _item(mid, cid, *, due=None, late=None, unlock=None, ctype="movie", extra=None):
    icd = {"content_id": cid, "content_type": ctype, "view_url": f"https://canvas.ssu.ac.kr/v/{mid}", "duration": 600}
    icd.update(extra or {})
    return {"module_item_id": mid, "title": f"영상{mid}",
            "content_data": {"item_content_type": "commons", "title": f"영상{mid}", "due_at": due, "late_at": late,
                             "unlock_at": unlock, "item_content_data": icd}}


def _modules(*items, unlock=None):
    return [{"module_id": 1, "title": "1주차", "position": 1, "unlock_at": unlock, "module_items": list(items)}]


def test_period_states_use_late_then_due_and_module_unlock():
    rows = probe_items(_modules(
        _item(1, "a", due="2026-10-01T00:00:00Z"),                                   # 마감 지남
        _item(2, "b", due="2026-10-01T00:00:00Z", late="2026-11-01T00:00:00Z"),      # 지각 제출 기간 안
        _item(3, "not_open", unlock="2026-11-05T00:00:00Z"),                         # 아직 안 열림
        _item(4, "c"),                                                               # 날짜 없음
    ), NOW)
    assert [(r.module_item_id, r.period) for r in rows] == [
        (1, "기간 종료"), (2, "기간 중"), (3, "기간 전"), (4, "기간 정보 없음")]
    assert rows[2].content_id == "not_open" and not rows[2].has_content_id
    assert rows[0].has_content_id


def test_module_unlock_applies_when_item_has_none():
    rows = probe_items(_modules(_item(1, "a"), unlock="2026-11-05T00:00:00Z"), NOW)
    assert rows[0].period == "기간 전"


def test_unknown_fields_are_reported_for_hidden_ids():
    rows = probe_items(_modules(_item(1, "not_open", due="2026-10-01T00:00:00Z",
                                      extra={"origin_content_id": "zz9", "is_locked": True})), NOW)
    assert rows[0].extra_fields == {"origin_content_id": "zz9", "is_locked": True}


def test_only_commons_items_are_listed():
    mods = _modules(_item(1, "a"), {"module_item_id": 2, "title": "과제", "content_type": "assignment", "content_data": {}})
    assert [r.module_item_id for r in probe_items(mods, NOW)] == [1]


CDN = "https://ssuin-object.commonscdn.com/media/abc"


@respx.mock
async def test_check_content_php_reports_without_downloading():
    respx.get(CONTENT_PHP, params={"content_id": "ok"}).respond(
        200, text=f"<content><content_playing_info><media_uri>{CDN}/a.mp4</media_uri></content_playing_info></content>")
    respx.get(CONTENT_PHP, params={"content_id": "doc"}).respond(
        200, text="<content><content_type>sharedocs</content_type><content_playing_info/></content>")
    respx.get(CONTENT_PHP, params={"content_id": "gone"}).respond(404)
    async with httpx.AsyncClient() as http:
        assert await _check_content_php(http, "ok") == "mp4 주소 있음 (ssuin-object.commonscdn.com)"
        assert "sharedocs" in await _check_content_php(http, "doc")
        assert await _check_content_php(http, "gone") == "HTTP 404"
