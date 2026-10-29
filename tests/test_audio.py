import asyncio
import shutil
import subprocess

import pytest
import respx

from app.api import routes_media
from app.lms import audio
from app.lms.media import CONTENT_PHP
from tests.test_api import env  # noqa: F401
from tests.test_materials_api import _with_course

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")


def _sine(path, seconds):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "aac", str(path)], check=True)
    return str(path)


def test_ffmpeg_args_pass_headers_and_pcm_format():
    args = audio.ffmpeg_args("ffmpeg", "https://x/a.mp4", {"Referer": "https://commons.ssu.ac.kr/"})
    assert args[args.index("-headers") + 1] == "Referer: https://commons.ssu.ac.kr/\r\n"
    assert args[-10:] == ["-i", "https://x/a.mp4", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"]


@pytest.mark.ffmpeg
@needs_ffmpeg
async def test_pcm_stream_outputs_16k_mono_s16le(tmp_path):
    src = _sine(tmp_path / "a.m4a", 2)
    data = b"".join([c async for c in audio.pcm_stream(src)])
    assert abs(len(data) - 2 * 16000 * 2) < 3200  # 2초 × 16kHz × 2바이트 (인코더 지연 허용)


@pytest.mark.ffmpeg
@needs_ffmpeg
async def test_pcm_stream_kills_ffmpeg_on_close(tmp_path, monkeypatch):
    src = _sine(tmp_path / "long.m4a", 120)
    procs = []
    real = asyncio.create_subprocess_exec

    async def spy(*a, **k):
        p = await real(*a, **k)
        procs.append(p)
        return p

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", spy)
    gen = audio.pcm_stream(src, chunk=1024)
    assert await gen.__anext__()
    await gen.aclose()
    assert procs[0].returncode is not None


def _fake_stream(monkeypatch, seen):
    async def fake(source, headers=None, chunk=1 << 16):
        seen.append((source, headers))
        yield b"\x01\x00" * 8

    monkeypatch.setattr(routes_media, "pcm_stream", fake)
    monkeypatch.setattr(routes_media, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")


CDN = "https://ssuin-object.commonscdn.com/media/abc"
XML = f"<content><content_playing_info><media_uri>{CDN}/a.mp4</media_uri></content_playing_info></content>"


@respx.mock
async def test_audio_link_and_stream(env, monkeypatch):
    api, _ = env
    h = await _with_course(api)
    seen = []
    _fake_stream(monkeypatch, seen)
    respx.get(CONTENT_PHP).respond(200, text=XML)
    r = await api.post("/courses/45446/videos/11/audio-link", headers=h)
    assert r.status_code == 200 and r.json()["url"].startswith("/audio/")
    got = await api.get(r.json()["url"])
    assert got.status_code == 200 and got.content == b"\x01\x00" * 8
    assert got.headers["x-audio-sample-rate"] == "16000"
    assert seen == [(f"{CDN}/a.mp4", routes_media.COMMONS_HEADERS)]
    assert routes_media._active[1] == 0  # 끝나면 동시 실행 수를 돌려놓는다


@respx.mock
async def test_audio_link_rules(env, monkeypatch):
    api, _ = env
    h = await _with_course(api)
    _fake_stream(monkeypatch, [])
    assert (await api.post("/courses/45446/videos/12/audio-link", headers=h)).status_code == 404  # 자료(영상 아님)
    assert (await api.post("/courses/99999/videos/11/audio-link", headers=h)).status_code == 404


@respx.mock
async def test_tokens_are_not_interchangeable(env, monkeypatch):
    api, _ = env
    h = await _with_course(api)
    _fake_stream(monkeypatch, [])
    audio_url = (await api.post("/courses/45446/videos/11/audio-link", headers=h)).json()["url"]
    dl_url = (await api.post("/courses/45446/videos/11/download-link", headers=h)).json()["url"]
    assert (await api.get("/downloads/" + audio_url.rsplit("/", 1)[1])).status_code == 401
    assert (await api.get("/audio/" + dl_url.rsplit("/", 1)[1])).status_code == 401


@respx.mock
async def test_audio_without_ffmpeg_is_503(env, monkeypatch):
    api, _ = env
    h = await _with_course(api)
    monkeypatch.setattr(routes_media, "ffmpeg_path", lambda: None)
    url = (await api.post("/courses/45446/videos/11/audio-link", headers=h)).json()["url"]
    assert (await api.get(url)).status_code == 503


@pytest.mark.ffmpeg
@needs_ffmpeg
async def test_client_disconnect_through_starlette_kills_ffmpeg(tmp_path, monkeypatch):
    """uvicorn처럼 Starlette가 스트림을 취소해도(anyio 취소 범위) ffmpeg가 남지 않아야 한다."""
    from starlette.responses import StreamingResponse

    src = _sine(tmp_path / "long.m4a", 600)
    procs = []
    real = asyncio.create_subprocess_exec

    async def spy(*a, **k):
        p = await real(*a, **k)
        procs.append(p)
        return p

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", spy)
    sent, disconnect = 0, asyncio.Event()

    async def send(message):
        nonlocal sent
        sent += 1
        if sent == 3:
            disconnect.set()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    await StreamingResponse(audio.pcm_stream(src))({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
    for _ in range(50):
        if procs[0].returncode is not None:
            break
        await asyncio.sleep(0.1)
    assert procs[0].returncode is not None
