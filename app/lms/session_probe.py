"""Read-only session measurement, without password access or automatic login.

python -m app.lms.session_probe --student-id YOUR_ID
python -m app.lms.session_probe --student-id YOUR_ID --samples 97 --interval 1800

Uses only the selected account's cached session. Does not update the database.
Repeated sampling measures active use, not idle timeout. Other users of the same
session can affect results. Cookie/token values and response bodies are never logged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.lms.auth import CANVAS_BASE_URL, LmsSession, import_cookies


async def check(http: httpx.AsyncClient, path: str, kind: str) -> dict:
    try:
        response = await http.get(CANVAS_BASE_URL + path)
    except httpx.HTTPError as exc:
        return {"state": "network_error", "error_type": type(exc).__name__}
    result = {"http_status": response.status_code,
              "set_cookie_count": len(response.headers.get_list("set-cookie"))}
    if response.status_code == 401:
        return {**result, "state": "unauthorized"}
    if response.is_redirect:
        return {**result, "state": "redirect_requires_review"}
    if response.status_code != 200:
        return {**result, "state": "unexpected_status"}
    try:
        body = response.text.lstrip()
        if body.startswith("while(1);"):
            body = body[len("while(1);"):]
        data = json.loads(body)
    except ValueError:
        return {**result, "state": "unexpected_body"}
    valid = (isinstance(data, dict) and bool(data.get("id"))) if kind == "cookie" else (
        isinstance(data, list) or
        isinstance(data, dict) and isinstance(data.get("enrollment_terms"), list)
    )
    return {**result, "state": "authenticated" if valid else "unexpected_body"}


async def measure(session: LmsSession, samples: int, interval: float) -> None:
    # Isolate bearer and cookie authentication so one cannot mask the other.
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as cookie_http, \
            httpx.AsyncClient(timeout=25, follow_redirects=False) as bearer_http:
        import_cookies(cookie_http, session.cookies)
        bearer_http.headers["Authorization"] = "Bearer " + session.bearer_token
        start = time.monotonic()
        for index in range(samples):
            if index:
                await asyncio.sleep(interval)
            before = {(c.domain, c.path, c.name): c.value for c in cookie_http.cookies.jar}
            cookie = await check(cookie_http, "/api/v1/users/self/profile", "cookie")
            # Never reuse cookies issued to the bearer probe as authentication.
            bearer_http.cookies.clear()
            bearer = await check(bearer_http,
                                 f"/learningx/api/v1/users/{quote(session.user_id, safe='')}/terms"
                                 "?include_invited_course_contained=true", "bearer") if session.bearer_token else {
                                     "state": "missing_token"}
            after = {(c.domain, c.path, c.name): c.value for c in cookie_http.cookies.jar}
            print(json.dumps({"at": datetime.now(UTC).isoformat(),
                              "elapsed_seconds": round(time.monotonic() - start),
                              "cookie": cookie, "bearer": bearer,
                              "cookie_jar_changed": before != after}), flush=True)
            if cookie["state"] != "authenticated" or bearer["state"] != "authenticated":
                break  # Do not retry failed authentication or silently relogin.


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=2700)
    args = parser.parse_args()
    if args.samples < 1 or args.interval < 60:
        parser.error("samples must be positive; interval must be at least 60 seconds")

    from sqlalchemy import select
    from app.db.base import init_engine, sessionmaker
    from app.db.models import LmsCredential
    from app.security import decrypt

    engine = init_engine()
    try:
        async with sessionmaker()() as db:
            # Select ONLY the session, never the password or password hash.
            token = await db.scalar(select(LmsCredential.session_enc).where(
                LmsCredential.student_id == args.student_id))
        if not token:
            print('{"state":"no_cached_session"}')
            return 1
        session = LmsSession.from_dict(json.loads(decrypt(token)))
        if session.user_id != args.student_id:
            print('{"state":"session_identity_mismatch"}')
            return 1
        await measure(session, args.samples, args.interval)
        return 0
    except Exception as exc:
        # Connection errors may contain URLs; never print exception messages.
        print(json.dumps({"state": "probe_error", "error_type": type(exc).__name__}))
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
