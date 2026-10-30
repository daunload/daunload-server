"""강의 영상에서 오디오만 뽑아 16kHz 모노 PCM(s16le)으로 흘려보낸다. 브라우저 Whisper 전사용.

파일로 저장하지 않는다. ffmpeg가 원본 주소를 직접 읽고, 표준 출력을 그대로 응답으로 넘긴다.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator

import anyio

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffmpeg_args(ffmpeg: str, source: str, headers: dict[str, str] | None = None) -> list[str]:
    args = [ffmpeg, "-nostdin", "-loglevel", "error"]
    if headers:
        args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    if source.startswith("https://"):  # 응답이 재생 목록이어도 file:, http: 등 다른 프로토콜로 새지 않게
        args += ["-protocol_whitelist", "https,tls,tcp"]
    return args + ["-i", source, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"]


async def pcm_stream(source: str, headers: dict[str, str] | None = None, chunk: int = 1 << 16) -> AsyncIterator[bytes]:
    """ffmpeg를 띄워 PCM을 chunk 단위로 내보낸다. 소비자가 중간에 그만두면(aclose) 프로세스를 끝낸다."""
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_args(ffmpeg_path() or "ffmpeg", source, headers),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        while data := await proc.stdout.read(chunk):
            yield data
        await proc.wait()
        if proc.returncode:
            err = (await proc.stderr.read())[-500:].decode(errors="replace")
            log.warning("ffmpeg 실패(%s): %s", proc.returncode, err)
    finally:
        if proc.returncode is None:
            # 클라이언트가 끊으면 취소된 채로 여기에 온다. 정리 중의 await가 다시 취소되지 않게 막는다.
            with anyio.CancelScope(shield=True):
                proc.kill()  # 가득 찬 파이프에 막힌 ffmpeg는 SIGTERM에 바로 반응하지 않는다
                # 읽기를 멈춘 파이프는 EOF까지 비워야 wait()가 끝난다
                await asyncio.gather(_drain(proc.stdout), _drain(proc.stderr), proc.wait())


async def _drain(stream: asyncio.StreamReader) -> None:
    while await stream.read(1 << 16):
        pass
