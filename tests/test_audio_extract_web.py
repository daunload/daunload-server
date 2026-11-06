"""확장 프로그램의 오디오 추출 모듈을 일반 Chrome 페이지에서 시험한다(확장 프로그램 없이)."""

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "ssu-downloader"
MEDIA = ROOT / "tests" / "fixtures" / "media"
pytestmark = pytest.mark.browser

NO_AUDIO = "이 영상에는 소리 트랙이 없습니다."
UNSUPPORTED = "이 브라우저가 이 영상의 소리 형식을 읽지 못합니다."
HTTP_FAILED = "학교 서버가 영상을 주지 않았습니다. 목록을 새로고침한 뒤 다시 시도하세요."

# 픽스처 이름으로 끝까지 뽑아 길이·재생 시간·가운데 1초의 양수 방향 영교차 수를 돌려준다.
EXTRACT_ALL = """async ({ name, chunkBytes }) => {
  const m = await import("/audio-extract.mjs");
  const signal = new AbortController().signal;
  const source = m.openCdnSource(`/media/${name}`, { signal, refresh: async () => { throw new Error("no refresh"); } });
  const gen = m.extractPcm(source, chunkBytes ? { signal, chunkBytes } : { signal });
  const parts = [];
  let step;
  while (!(step = await gen.next()).done) parts.push(step.value);
  const pcm = new Float32Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { pcm.set(p, at); at += p.length; }
  let crossings = 0;
  for (let i = 16001; i < 32000; i++) if (pcm[i - 1] < 0 && pcm[i] >= 0) crossings++;
  return { n: pcm.length, duration: step.value.duration, crossings, float32: parts.every((p) => p instanceof Float32Array) };
}"""

# 추출이 실패할 때 던진 오류를 돌려준다.
EXTRACT_ERROR = """async ({ name }) => {
  const m = await import("/audio-extract.mjs");
  const signal = new AbortController().signal;
  const source = m.openCdnSource(`/media/${name}`, { signal, refresh: async () => { throw new Error("no refresh"); } });
  try {
    for await (const _ of m.extractPcm(source, { signal })) {}
    return null;
  } catch (e) {
    return { typed: e instanceof m.AudioExtractError, code: e.code, message: e.message, cause: e.cause?.name ?? null };
  }
}"""


@pytest.fixture
async def page():
    ranges = []  # (파일 이름, 시작, 끝)
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)  # AAC 디코딩은 Chrome에만 있다
        page = await browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def route(request):
            url = urlparse(request.request.url)
            if url.hostname != "test.local":
                await request.abort()
                return
            path = url.path
            if path == "/":
                await request.fulfill(body="<!doctype html><title>t</title>", content_type="text/html")
            elif path.endswith(".mjs") and (EXTENSION / path.lstrip("/")).is_file():
                await request.fulfill(body=(EXTENSION / path.lstrip("/")).read_text(), content_type="text/javascript")
            elif path == "/media/expired.mp4":
                await request.fulfill(status=403, body="expired")
            elif path.startswith("/media/") and (MEDIA / path.removeprefix("/media/")).is_file():
                name = path.removeprefix("/media/")
                data = (MEDIA / name).read_bytes()
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", request.request.headers.get("range", ""))
                assert match, "Range 요청만 받는다"
                start = int(match[1])
                end = min(int(match[2]) if match[2] else len(data) - 1, len(data) - 1)
                ranges.append((name, start, end))
                await request.fulfill(
                    status=206,
                    body=data[start:end + 1],
                    headers={"Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes"},
                    content_type="video/mp4",
                )
            else:
                await request.fulfill(status=404, body="not found")

        await page.route("**/*", route)
        await page.expose_function("rangeCount", lambda: len(ranges))
        await page.goto("https://test.local/")  # WebCodecs는 보안 컨텍스트에서만 열린다
        yield page, ranges
        await browser.close()
    assert not errors, errors


def assert_tone(result):
    assert result["float32"]
    assert abs(result["n"] - 48000) <= 1600, result
    assert 2.9 <= result["duration"] <= 3.1, result
    assert 430 <= result["crossings"] <= 450, result


async def test_extract_faststart_yields_16k_mono(page):
    page, _ = page
    assert_tone(await page.evaluate(EXTRACT_ALL, {"name": "tone-faststart.mp4", "chunkBytes": None}))


async def test_extract_moov_at_end(page):
    page, ranges = page
    chunk = 1024  # moov(약 3.7KB)보다 작게 잡아 moov도 여러 Range로 나눠 읽게 한다
    assert_tone(await page.evaluate(EXTRACT_ALL, {"name": "tone-moov-end.mp4", "chunkBytes": chunk}))
    assert ranges
    assert not [r for r in ranges if r[2] - r[1] + 1 > chunk], ranges


async def test_extract_opus(page):
    page, _ = page
    result = await page.evaluate(EXTRACT_ALL, {"name": "tone-opus.mp4", "chunkBytes": None})
    assert abs(result["n"] - 48000) <= 1600, result
    assert 2.9 <= result["duration"] <= 3.1, result


async def test_backpressure_reads_lazily(page):
    page, ranges = page
    first = await page.evaluate("""async () => {
      const m = await import("/audio-extract.mjs");
      const signal = new AbortController().signal;
      const source = m.openCdnSource("/media/tone-faststart.mp4", { signal, refresh: async () => "" });
      const gen = m.extractPcm(source, { signal, chunkBytes: 16 * 1024 });
      const step = await gen.next();
      window.pending = gen;  // 더 당기지 않는다
      return step.value.length;
    }""")
    assert first > 0
    size = (MEDIA / "tone-faststart.mp4").stat().st_size
    assert max(end for _, _, end in ranges) < size - 1, (ranges, size)
    assert not [r for r in ranges if r[2] - r[1] + 1 > 16 * 1024], ranges


async def test_video_only_raises_no_audio(page):
    page, _ = page
    assert await page.evaluate(EXTRACT_ERROR, {"name": "video-only.mp4"}) == {
        "typed": True, "code": "no_audio", "message": NO_AUDIO, "cause": None,
    }


async def test_unsupported_codec(page):
    page, _ = page
    await page.evaluate("AudioDecoder.isConfigSupported = async () => ({ supported: false })")
    assert await page.evaluate(EXTRACT_ERROR, {"name": "tone-faststart.mp4"}) == {
        "typed": True, "code": "unsupported_codec", "message": UNSUPPORTED, "cause": None,
    }


async def test_decoder_error_becomes_unsupported_codec(page):
    """디코딩 도중 error 콜백이 오고 코덱이 닫혀도 원래 오류를 담은 사용자 문구로 끝난다."""
    page, _ = page
    await page.evaluate("""() => {
      const Real = AudioDecoder;
      window.AudioDecoder = class extends Real {
        constructor(init) {
          super(init);
          this.fail = init.error;
        }
        decode(chunk) {
          super.decode(chunk);
          if (this.failing) return;
          this.failing = true;  // 다음 Range를 읽는 사이에 오류가 나서 코덱이 닫힌다
          setTimeout(() => { this.close(); this.fail(new DOMException("bad frame", "EncodingError")); }, 0);
        }
      };
      AudioDecoder.isConfigSupported = Real.isConfigSupported.bind(Real);
    }""")
    result = await page.evaluate("""async () => {
      const m = await import("/audio-extract.mjs");
      const signal = new AbortController().signal;
      const source = m.openCdnSource("/media/tone-faststart.mp4", { signal, refresh: async () => "" });
      try {
        for await (const _ of m.extractPcm(source, { signal, chunkBytes: 1024 })) {}
        return null;
      } catch (e) {
        return { typed: e instanceof m.AudioExtractError, code: e.code, message: e.message, cause: e.cause?.name ?? null };
      }
    }""")
    assert result == {"typed": True, "code": "unsupported_codec", "message": UNSUPPORTED, "cause": "EncodingError"}


async def test_source_refreshes_url_once_on_403(page):
    page, _ = page
    result = await page.evaluate("""async () => {
      const m = await import("/audio-extract.mjs");
      const signal = new AbortController().signal;
      const attempt = async (fresh) => {
        let refreshes = 0;
        const source = m.openCdnSource("/media/expired.mp4", { signal, refresh: async () => { refreshes++; return fresh; } });
        try {
          const bytes = await source.read(4, 7);
          return { refreshes, ok: new TextDecoder().decode(bytes), size: await source.size() };
        } catch (e) {
          return { refreshes, code: e.code, message: e.message, typed: e instanceof m.AudioExtractError };
        }
      };
      return { good: await attempt("/media/tone-faststart.mp4"), bad: await attempt("/media/expired.mp4") };
    }""")
    size = (MEDIA / "tone-faststart.mp4").stat().st_size
    assert result["good"] == {"refreshes": 1, "ok": "ftyp", "size": size}
    assert result["bad"] == {"refreshes": 1, "code": "http", "message": HTTP_FAILED, "typed": True}


async def test_abort_stops_reading(page):
    page, _ = page
    result = await page.evaluate("""async () => {
      const m = await import("/audio-extract.mjs");
      const controller = new AbortController();
      const source = m.openCdnSource("/media/tone-faststart.mp4", { signal: controller.signal, refresh: async () => "" });
      const gen = m.extractPcm(source, { signal: controller.signal, chunkBytes: 16 * 1024 });
      await gen.next();
      const before = await window.rangeCount();
      controller.abort();
      let error = null;
      try { await gen.next(); } catch (e) { error = e.name; }
      await new Promise((resolve) => setTimeout(resolve, 300));
      return { error, before, after: await window.rangeCount() };
    }""")
    assert result["error"] == "AbortError"
    assert result["after"] == result["before"]


async def test_resampler_is_continuous(page):
    page, _ = page
    total = await page.evaluate("""async () => {
      const m = await import("/audio-extract.mjs");
      const resample = m.makeResampler(48000, 16000);
      const input = new Float32Array(48000).map((_, i) => Math.sin(i / 10));
      return [input.subarray(0, 1000), input.subarray(1000, 1007), input.subarray(1007)]
        .reduce((n, part) => n + resample(part).length, 0);
    }""")
    assert abs(total - 16000) <= 1
