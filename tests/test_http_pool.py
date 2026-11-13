import asyncio

import httpx
import respx

from app.lms import auth
from app.lms.http_pool import close_shared_transports, shared_transport


async def test_clients_share_one_pool_per_loop_and_closing_a_client_keeps_it():
    a, b = auth.new_http_client(), auth.new_http_client()
    assert a._transport is b._transport is shared_transport()
    await a.aclose()
    with respx.mock:
        respx.get("https://canvas.ssu.ac.kr/x").respond(200, text="ok")
        assert (await b.get("https://canvas.ssu.ac.kr/x")).text == "ok"  # 다른 클라이언트를 닫아도 풀은 살아 있다
    await b.aclose()
    await close_shared_transports()


async def test_cookies_stay_per_client():
    a, b = auth.new_http_client(), auth.new_http_client()
    a.cookies.set("s", "user-a", domain="canvas.ssu.ac.kr")
    with respx.mock:
        route = respx.get("https://canvas.ssu.ac.kr/x").respond(200)
        await b.get("https://canvas.ssu.ac.kr/x")
        assert "cookie" not in route.calls.last.request.headers
    await a.aclose(); await b.aclose()


def test_each_event_loop_gets_its_own_pool():
    async def get():
        return shared_transport()
    assert asyncio.run(get()) is not asyncio.run(get())


def test_client_outside_a_loop_still_works():
    client = auth.new_http_client()
    assert isinstance(client, httpx.AsyncClient)
