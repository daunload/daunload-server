"""Measure SSO reuse without storing credentials or calling school login again.

Call observe() with a freshly authenticated LmsSession. The session stays in
memory; only sanitized measurements are written to the output file. This tests
active SSO reuse, not idle expiry or revocation recovery.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from app.lms import auth
from app.lms.errors import LmsError, SsoExpired
from app.lms.session_probe import check

sso_cookies = auth.sso_cookies
sso_token = auth.sso_token


async def renew(user_id: str, cookies: list[dict]) -> tuple[dict, list[dict]]:
    token = sso_token(cookies)
    if not token:
        return {"state": "missing_sso_token"}, cookies
    try:
        # Canvas and portal cookies are discarded inside renew_from_sso on EVERY
        # cycle, so successful checks come from the SSO handoff only.
        session = await auth.renew_from_sso(auth.LmsSession(user_id, "", sso_cookies(cookies), token))
    except SsoExpired:
        return {"state": "sso_expired"}, cookies
    except LmsError as exc:
        return {"state": "renew_error", "error_type": type(exc).__name__}, cookies
    async with auth.new_http_client() as cookie_http:
        cookie_http.follow_redirects = False
        auth.import_cookies(cookie_http, session.cookies)
        cookie_result = await check(cookie_http, "/api/v1/users/self/profile", "cookie")
    async with auth.new_http_client() as bearer_http:
        bearer_http.follow_redirects = False
        bearer_http.headers["Authorization"] = "Bearer " + session.bearer_token
        bearer_result = await check(bearer_http,
                                    f"/learningx/api/v1/users/{quote(user_id, safe='')}/terms"
                                    "?include_invited_course_contained=true", "bearer")
    updated = sso_cookies(session.cookies)
    success = all(r["state"] == "authenticated" for r in (cookie_result, bearer_result))
    return {"state": "renewed" if success else "authentication_failed",
            "cookie": cookie_result, "bearer": bearer_result,
            "sso_token_changed": session.sso_token != token}, updated


async def observe(session: auth.LmsSession, output: Path, schedule: list[int]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never truncate an existing observation. Logs contain no identifiers,
    # response bodies, URLs, credentials, cookies or tokens.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    cookies = sso_cookies(session.cookies)
    original_token = sso_token(cookies)
    start = time.monotonic()
    with os.fdopen(fd, "w") as log:
        for offset in schedule:
            await asyncio.sleep(max(0, start + offset - time.monotonic()))
            try:
                result, cookies = await renew(session.user_id, cookies)
            except Exception as exc:
                result = {"state": "probe_error", "error_type": type(exc).__name__}
            record = {"at": datetime.now(UTC).isoformat(),
                      "elapsed_seconds": round(time.monotonic() - start),
                      "original_sso_token_still_used": sso_token(cookies) == original_token,
                      **result}
            line = json.dumps(record)
            log.write(line + "\n")
            log.flush()
            print(line, flush=True)
            if result["state"] != "renewed":
                break
