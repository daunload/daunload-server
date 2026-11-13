"""학교 서버(canvas·lms·commons) 요청이 함께 쓰는 연결 풀.

httpx.AsyncClient를 요청마다 새로 만들면 매번 TCP·TLS 연결을 새로 맺는다. 클라이언트(쿠키·헤더)는 사용자마다
따로 두되, 아래 공용 전송 계층을 끼워 연결만 다시 쓴다. 쿠키와 인증 헤더는 요청마다 클라이언트가 붙이므로
연결을 같이 써도 사용자끼리 섞이지 않는다.

연결은 이벤트 루프에 묶이므로 루프마다 풀을 하나씩 둔다(웹 서버·워커는 루프 하나, 테스트는 테스트마다 새 루프).
클라이언트를 닫아도 공용 풀은 닫히지 않는다. 프로세스를 끝낼 때 close_shared_transports()로 닫는다.
"""

from __future__ import annotations

import asyncio
import weakref

import httpx

_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30.0)


class _SharedTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport(limits=_LIMITS)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        """클라이언트가 닫힐 때 불린다. 다른 클라이언트가 쓰는 풀이므로 닫지 않는다."""


_transports: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _SharedTransport] = weakref.WeakKeyDictionary()


def shared_transport() -> httpx.AsyncBaseTransport:
    loop = asyncio.get_running_loop()
    transport = _transports.get(loop)
    if transport is None:
        transport = _transports[loop] = _SharedTransport()
    return transport


async def close_shared_transports() -> None:
    transport = _transports.pop(asyncio.get_running_loop(), None)
    if transport is not None:
        await transport.inner.aclose()
