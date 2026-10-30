"""시청기간이 끝난 강의 영상을 LMS가 어떻게 주는지 실계정으로 확인하는 개발용 CLI.

모델(LmsModel)은 모르는 필드를 버리므로, 가공하지 않은 모듈 JSON을 그대로 본다.
기간이 끝난 영상에도 content_id가 남는지, 다른 필드가 붙는지, content.php가 응답하는지를 출력한다.

  python -m app.lms.probe --course <id>                 commons 항목마다 기간 상태·content_id·추가 필드
  python -m app.lms.probe --course <id> --check         기간이 끝난 항목은 content.php까지 물어본다
  python -m app.lms.probe --course <id> --dump <item>   한 항목의 원본 JSON 전체

지난 학기 과목 ID도 넣을 수 있다(이번 학기 목록에서 찾지 않는다).
영상 파일은 받지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.lms.todo import parse_dt

# ItemContentData 모델에 있는 필드. 이 밖의 필드는 숨은 식별자 후보로 보여준다.
KNOWN_FIELDS = {"duration", "view_url", "content_id", "content_type", "total_file_size", "thumbnail_url", "file_name"}


@dataclass(frozen=True)
class ProbeRow:
    module_title: str
    module_item_id: int
    title: str
    content_type: str | None
    content_id: str | None  # LMS가 준 값 그대로("not_open" 포함)
    view_url: str | None
    unlock_at: datetime | None
    end_at: datetime | None  # late_at, 없으면 due_at
    period: str  # 기간 전 / 기간 중 / 기간 종료 / 기간 정보 없음
    extra_fields: dict[str, Any]

    @property
    def has_content_id(self) -> bool:
        return bool(self.content_id) and self.content_id != "not_open"


def _dt(value: Any) -> datetime | None:
    try:
        return parse_dt(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _period(unlock: datetime | None, end: datetime | None, now: datetime) -> str:
    if unlock and now < unlock:
        return "기간 전"
    if end:
        return "기간 종료" if now > end else "기간 중"
    return "기간 정보 없음"


def probe_items(raw_modules: list[dict], now: datetime) -> list[ProbeRow]:
    rows = []
    for module in sorted(raw_modules, key=lambda m: m.get("position") or 0):
        for item in module.get("module_items") or []:
            data = item.get("content_data") or {}
            if data.get("item_content_type") != "commons":
                continue
            icd = data.get("item_content_data") or {}
            unlock = _dt(data.get("unlock_at")) or _dt(module.get("unlock_at"))
            end = _dt(data.get("late_at")) or _dt(data.get("due_at"))
            cid = icd.get("content_id")
            rows.append(ProbeRow(
                module_title=module.get("title") or "", module_item_id=item.get("module_item_id") or 0,
                title=data.get("title") or item.get("title") or "", content_type=icd.get("content_type"),
                content_id=None if cid is None else str(cid), view_url=icd.get("view_url"),
                unlock_at=unlock, end_at=end, period=_period(unlock, end, now),
                extra_fields={k: v for k, v in icd.items() if k not in KNOWN_FIELDS},
            ))
    return rows


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


async def _check_content_php(http, content_id: str) -> str:
    """content.php가 이 ID에 응답하는지, mp4 주소를 뽑을 수 있는지만 알려준다."""
    from app.lms.media import CONTENT_PHP, COMMONS_HEADERS, MediaError, content_type_of, extract_media_url

    try:
        resp = await http.get(CONTENT_PHP, params={"content_id": content_id}, headers=COMMONS_HEADERS)
    except Exception as e:  # noqa: BLE001  확인용 출력이므로 원인만 보여준다
        return f"요청 실패: {e}"
    if resp.status_code != 200:
        return f"HTTP {resp.status_code}"
    url = extract_media_url(resp.text)
    if not url:
        return f"응답은 있으나 mp4 주소 없음 (형식 {content_type_of(resp.text)})"
    try:
        from app.lms.media import check_media_host

        check_media_host(url)
    except MediaError:
        return f"mp4 주소 있음, 허용 호스트 밖 ({urlparse(url).hostname})"
    return f"mp4 주소 있음 ({urlparse(url).hostname})"


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"


async def main(argv: list[str]) -> int:
    import httpx

    from app.config import get_settings
    from app.lms.client import LmsClient

    p = argparse.ArgumentParser(prog="python -m app.lms.probe")
    p.add_argument("--course", type=int, required=True)
    p.add_argument("--check", action="store_true", help="기간이 끝난 항목은 content.php까지 확인")
    p.add_argument("--dump", type=int, metavar="MODULE_ITEM_ID", help="한 항목의 원본 JSON 출력")
    args = p.parse_args(argv)

    s = get_settings()
    async with await LmsClient.login(s.lms_id, s.lms_password) as client:
        raw = await client.get_json(f"/learningx/api/v1/courses/{args.course}/modules", bearer=True,
                                    params={"include_detail": "true"})
    if not isinstance(raw, list):
        print("모듈 목록이 아닙니다:", str(raw)[:300])
        return 1

    if args.dump is not None:
        item = next((i for m in raw for i in m.get("module_items") or [] if i.get("module_item_id") == args.dump), None)
        print(json.dumps(item, ensure_ascii=False, indent=2) if item else "해당 항목이 없습니다.")
        return 0 if item else 1

    rows = probe_items(raw, datetime.now(UTC))
    if not rows:
        print("commons 항목이 없습니다. 과목 ID를 확인하거나 --dump로 원본을 보세요.")
        return 1
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as http:
        for r in rows:
            print(f"[{r.module_title}] {r.title}  item={r.module_item_id}  type={r.content_type}")
            print(f"    기간: {r.period}  (열림 {_fmt(r.unlock_at)} / 종료 {_fmt(r.end_at)})")
            print(f"    content_id: {r.content_id!r}  view_url: {r.view_url}")
            if r.extra_fields:
                print(f"    추가 필드: {json.dumps(r.extra_fields, ensure_ascii=False)[:300]}")
            if args.check and r.period == "기간 종료" and r.has_content_id:
                print(f"    content.php: {await _check_content_php(http, r.content_id)}")

    ended = [r for r in rows if r.period == "기간 종료"]
    with_id = [r for r in ended if r.has_content_id]
    print(f"\n요약: commons 항목 {len(rows)}개, 기간 종료 {len(ended)}개, 그중 content_id가 있는 항목 {len(with_id)}개")
    if ended and not with_id:
        print("기간이 끝난 항목에는 content_id가 없습니다. 추가 필드와 --dump 출력을 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
