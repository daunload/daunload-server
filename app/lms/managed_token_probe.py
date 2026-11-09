"""SSU Canvas에서 서버 관리 토큰의 발급·복구·폐기가 실제 코드 경로로 되는지 본인 계정으로 확인한다.

    .venv/bin/python -m app.lms.managed_token_probe

학교 비밀번호는 입력받아 이 프로세스 메모리에서만 쓴다. 확인용 토큰은 같은 로그인 세션으로 바로 폐기하고,
폐기됐는지 다시 확인한다. 토큰·쿠키·CSRF 값 원문은 출력하지 않는다.
"""
from __future__ import annotations

import asyncio
import getpass

from app.lms import auth, personal_token as pt
from app.lms.errors import LmsError


async def main() -> None:
    student_id = input("학번: ").strip()
    password = getpass.getpass("학교 비밀번호(저장하지 않음): ")
    async with auth.new_http_client() as http:
        await auth.login(http, student_id, password)
        del password
        print("1. 학교 로그인: 성공")
        try:
            created = await pt.issue_managed_token(http, student_id)
        except pt.ManagedTokenUnavailable:
            print("2. 토큰 발급: 학교 Canvas가 막고 있습니다(401/403).")
            return
        except LmsError as e:
            print(f"2. 토큰 발급: 실패 ({type(e).__name__}: {e})")
            return
        print(f"2. 토큰 발급: 성공 · 만료일 {'없음(무기한)' if created.expires_at is None else created.expires_at.isoformat()}")
        try:
            try:
                session = await pt.session_from_token(created.token, student_id)
                print(f"3. 토큰으로 LearningX 세션 복구: {'성공' if session.bearer_token else '실패'}")
            except LmsError as e:
                print(f"3. 토큰으로 LearningX 세션 복구: 실패 ({type(e).__name__}: {e})")
        finally:
            revoked = await pt.revoke_token(http, created.token_id)
            async with auth.new_http_client() as check:
                r = await pt._request(check, "GET", "/api/v1/users/self/profile",
                                      headers={"Authorization": f"Bearer {created.token}"})
            dead = r.status_code == 401
            print(f"4. 확인용 토큰 폐기: {'완료' if revoked and dead else '실패'} (폐기 후 토큰 조회 HTTP {r.status_code})")
            if not dead:
                print("   Canvas 설정 > 승인된 통합에서 ‘SSU LMS 자동 수강’ 항목을 직접 삭제하세요.")


if __name__ == "__main__":
    asyncio.run(main())
