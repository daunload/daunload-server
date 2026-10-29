"""본인 PC에 강의 영상을 내려받는 CLI (개인 복습용). `.env`의 LMS_ID/LMS_PASSWORD를 쓴다.

  python -m app.lms.download list --course <id>
  python -m app.lms.download get  --course <id> [--module <id>] [--item <module_item_id>] [--out 폴더]

이미 받은 파일은 건너뛰고, 중간에 끊긴 파일(.part)은 이어받는다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from app.config import get_settings
from app.lms.client import LmsClient
from app.lms.media import MediaError, course_videos, download_to_file, resolve_media_url, safe_file_name


def _mb(n: int | None) -> str:
    return f"{n / 1e6:,.0f}MB" if n else "?"


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m app.lms.download")
    p.add_argument("command", choices=["list", "get"])
    p.add_argument("--course", type=int, required=True)
    p.add_argument("--module", type=int)
    p.add_argument("--item", type=int, help="module_item_id")
    p.add_argument("--out", type=Path, default=Path("downloads"))
    args = p.parse_args(argv)

    s = get_settings()
    async with await LmsClient.login(s.lms_id, s.lms_password) as client:
        term = await client.get_current_term()
        course = next((c for c in await client.get_courses(term.id) if c.id == args.course), None)
        if course is None:
            print("이번 학기 수강 과목이 아닙니다. `python -m app.lms.client`로 과목 ID를 확인하세요.")
            return 1
        videos = course_videos(course, await client.get_modules(course.id))
    videos = [v for v in videos if (args.module is None or v.module_id == args.module)
              and (args.item is None or v.module_item_id == args.item)]

    if args.command == "list":
        for v in videos:
            state = "완료" if v.completed else "미수강"
            if not v.available:
                state = "미공개"
            print(f"[{v.module_id}] {v.module_title} / {v.title}  item={v.module_item_id}  {_mb(v.size)}  {state}")
        return 0

    folder = args.out / safe_file_name(course.name)
    folder.mkdir(parents=True, exist_ok=True)
    failed = 0
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as http:
        for v in videos:
            dest = folder / v.file_name
            if not v.available:
                print(f"건너뜀(미공개): {v.title}")
                continue
            if dest.exists():
                print(f"이미 있음: {dest.name}")
                continue
            print(f"받는 중: {dest.name} ({_mb(v.size)})")
            try:
                url = await resolve_media_url(http, v.content_id)

                def progress(done, total):
                    pct = f"{done / total * 100:5.1f}%" if total else ""
                    print(f"  {_mb(done)} / {_mb(total)} {pct}", end="\r")

                await download_to_file(http, url, dest, progress)
                print(f"\n  완료: {dest}")
            except (MediaError, httpx.HTTPError) as e:
                failed += 1
                print(f"\n  실패: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
