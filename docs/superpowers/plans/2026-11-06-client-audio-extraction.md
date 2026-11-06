# 텍스트 변환 오디오 추출 브라우저 이전 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전사용 오디오 추출(서버 ffmpeg)을 사용자 브라우저(확장 프로그램 필수)로 옮기고 서버의 ffmpeg 경로를 모두 지운다.

**Architecture:** 확장 프로그램의 declarativeNetRequest 규칙이 대시보드 원점에서 나간 CDN 요청에 Commons Referer/Origin과 CORS 응답 헤더를 붙인다. 페이지는 기존 `direct-download` API로 CDN 주소를 받아 Range 요청으로 mp4를 조금씩 읽고, mp4box.js로 오디오 트랙을 꺼내 WebCodecs `AudioDecoder`로 디코딩한 뒤 16kHz 모노 Float32로 바꿔 기존 Whisper 워커에 넘긴다. 서버의 `/audio`·`audio-link`·ffmpeg는 삭제한다.

**Tech Stack:** Vanilla JS(클래식 스크립트 + ES 모듈), mp4box.js 2.4.1(저장소에 포함), WebCodecs, Chrome MV3 확장(declarativeNetRequest), FastAPI, pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-11-06-client-audio-extraction-design.md`

## Global Constraints

- 확장 프로그램이 없으면 전사 불가. 서버 대체 경로(서버 ffmpeg, ffmpeg.wasm) 없음.
- 확장 프로그램에 새 호스트 권한을 추가하지 않는다. 필요해지면 Task 1에서 멈추고 스펙 5.6(오프스크린 문서)으로 계획을 다시 쓴다.
- 확장 규칙이 여는 CDN 호스트는 `commonscdn.com`(+ Task 1에서 운영 DB로 확인된 `ssu.ac.kr` 하위 도메인만 정확히). `ssu.ac.kr` 전체 금지.
- 휴대폰(`isPhoneDevice()`)에서는 전사 버튼·"전체 텍스트로 변환" 버튼을 숨긴다. 텍스트 보기는 유지.
- Whisper 워커 입력 형식(16kHz 모노 Float32 `chunk` 메시지), `READ_AHEAD_SECONDS = 60`, `MIN_AUDIO_RATIO = 0.97`, 저장 `model` 값 `whisper-small/<device>`는 바꾸지 않는다.
- 영상 전체를 메모리에 쌓지 않는다(Range 조각 단위로 처리, `moov`가 끝에 있어도 마찬가지).
- 사용자 문구(스펙 5.3 그대로):
  - 오디오 트랙 없음: `이 영상에는 소리 트랙이 없습니다.`
  - 디코딩 불가: `이 브라우저가 이 영상의 소리 형식을 읽지 못합니다.`
  - CDN 403/404/410(재발급 후에도): `학교 서버가 영상을 주지 않았습니다. 목록을 새로고침한 뒤 다시 시도하세요.`
  - 허용 목록 밖 호스트: `이 영상은 텍스트 변환을 지원하지 않습니다.`
- 확장 프로그램 새 버전(스토어 게시) → 그다음 서버 배포.

## Review Focus

1. **전사 도중 취소·로그아웃** — 진행 중 CDN `fetch`와 `AudioDecoder`가 즉시 멈추고 다음 작업이 시작돼야 한다. → Task 3 `test_cancel_aborts_cdn_fetch`.
2. **CDN 주소가 중간에 만료(403)** — 주소를 한 번만 재발급해 같은 바이트 위치부터 잇는다. 두 번째 403이면 오류 문구로 끝난다. → Task 2 `test_source_refreshes_url_once_on_403`.
3. **`moov`가 파일 끝인 mp4** — 끝까지 내려받아 쌓지 않고 정상 추출한다. → Task 2 `test_extract_moov_at_end`.
4. **구버전 확장 프로그램(ping에 `audio` 없음)** — 다운로드는 계속 되고, 전사만 "업데이트해 주세요" 안내. → Task 3 `test_old_extension_asks_update_for_transcribe`.
5. **스테레오·48kHz 원본** — 길이가 맞는 16kHz 모노로 나온다(샘플 수 ≈ 길이×16000). → Task 2 `test_extract_faststart_yields_16k_mono`.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `extensions/ssu-downloader/worker.js` (수정) | DNR 규칙 3 추가(사이트 → CDN CORS), `ping`에 `audio: true` |
| `extensions/ssu-downloader/manifest.json` (수정) | 버전 `1.3.0` |
| `app/web/vendor/mp4box.all.mjs` (생성) | mp4box.js 2.4.1 ESM 원본(라이선스 주석 포함) |
| `app/web/audio-extract.mjs` (생성) | `openCdnSource`, `extractPcm`, `makeResampler`, `AudioExtractError` |
| `app/web/transcribe.js` (수정) | PCM 스트림 대신 `extractPcm` 소비, 확장 확인, 주소 재발급 |
| `app/web/downloads.js` (수정) | `requireAudioExtension()` |
| `app/web/app.js` (수정) | `audioPath` → `mediaPath`(direct-download 경로), 휴대폰 숨김 |
| `app/main.py` (수정) | CSP `connect-src`에 `https://*.commonscdn.com` |
| `app/api/routes_media.py`, `app/config.py`, `app/lms/audio.py`(삭제), `Dockerfile`, env 예시, 테스트, 문서 | 서버 ffmpeg 경로 제거 |
| `tests/fixtures/media/*.mp4` (생성) | 추출 테스트용 짧은 mp4 |

---

### Task 1: 확장 프로그램 규칙으로 사이트에 CDN 읽기 허용 (스파이크 겸 구현)

이 Task의 테스트가 실패하면(규칙으로 CORS를 못 연다, Range가 preflight에 막힌다, 새 호스트 권한이 필요하다) **여기서 멈추고** 결과를 보고한다. 나머지 Task는 이 방식이 된다는 전제다.

**Files:**
- Modify: `extensions/ssu-downloader/worker.js` (`configureRules`, `ping` 응답)
- Modify: `extensions/ssu-downloader/manifest.json` (`version`)
- Test: `tests/test_download_extension.py`

**Interfaces:**
- Produces: `ping` 응답 `{ version: 1, sessions: true, queue: true, audio: true }`. 대시보드 원점에서 `fetch("https://<x>.commonscdn.com/...", { headers: { Range: "bytes=N-M" } })`가 206과 읽을 수 있는 `Content-Range`를 돌려준다.

- [ ] **Step 1: 운영 DB 호스트 확인 (운영자에게 요청)**

운영자에게 다음 결과를 받는다. `commonscdn.com` 외 호스트가 있으면 그 정확한 호스트명을 Step 4의 `AUDIO_DOMAINS`에 넣는다. 답이 없으면 `commonscdn.com`만 쓴다.

```sql
SELECT substring(media_url from '^https://([^/]+)/') AS host, count(*) FROM saved_videos
WHERE media_url IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_download_extension.py`의 `school_cdn` 핸들러에 `/range.mp4`를 추가한다: Referer/Origin이 Commons일 때만 `Range: bytes=a-b`를 해석해 206, `Content-Range: bytes a-b/1000`, 본문 `b'x' * (b-a+1)`. `OPTIONS` 요청이 오면 `seen`에 기록하고 405를 준다.

`test_site_can_read_cdn_with_range_through_extension` 추가(기존 테스트와 같은 브라우저 실행 인자):

```python
page = await context.new_page()
await page.goto('https://daunload.cloud/')          # route로 빈 페이지
await page.add_script_tag(path=str(ROOT / 'app/web/downloads.js'))
assert await page.evaluate('extensionRequest("ping")') == {'version': 1, 'sessions': True, 'queue': True, 'audio': True}
result = await page.evaluate('''async () => {
  const r = await fetch("https://download-test.commonscdn.com/range.mp4", { headers: { Range: "bytes=10-19" } });
  return { status: r.status, range: r.headers.get("content-range"), len: (await r.arrayBuffer()).byteLength };
}''')
assert result == {'status': 206, 'range': 'bytes 10-19/1000', 'len': 10}
assert not any(method == 'OPTIONS' for method, *_ in seen_methods)   # preflight 없음
# 다른 원점은 읽지 못한다
other = await context.new_page(); await other.goto('https://daunload.cloud.example.org/')
assert await other.evaluate('''() => fetch("https://download-test.commonscdn.com/range.mp4").then(() => "ok", () => "blocked")''') == 'blocked'
# 일반 탭에서 학교 CDN을 직접 열 때 Referer/Origin을 바꾸지 않는다
```

마지막 줄은: 확장과 무관한 `about:blank` 탭에서 `page.goto('https://download-test.commonscdn.com/test.mp4')` 후 서버가 받은 헤더에 `Origin: https://commons.ssu.ac.kr`가 **없음**을 단언한다.

기존 `test_extension_bridge_download_and_validation`의 `ping` 단언도 `'audio': True`를 포함하도록 고친다.

- [ ] **Step 3: 실행해서 실패 확인**

Run: `uv run pytest tests/test_download_extension.py -m browser -v`
Expected: FAIL (`ping`에 `audio` 없음, fetch가 CORS 오류)

- [ ] **Step 4: `configureRules()`에 규칙 3 추가, `ping`에 `audio: true`**

- `const AUDIO_DOMAINS = ["commonscdn.com"];` (Step 1 결과 반영)
- 사이트 원점은 하드코딩하지 않는다: `chrome.runtime.getManifest().content_scripts[0].matches[0]`에서 원점과 호스트를 구한다(`scripts/build-download-extension.py`가 이 값을 빌드 원점으로 바꾼다).
- 규칙 3: `condition: { initiatorDomains: [siteHost], requestDomains: AUDIO_DOMAINS, resourceTypes: ["xmlhttprequest"] }`, `action.requestHeaders`: Referer/Origin을 규칙 1과 같은 값으로 `set`, `action.responseHeaders`: `Access-Control-Allow-Origin: <siteOrigin>`, `Access-Control-Expose-Headers: Content-Range, Content-Length, Accept-Ranges`를 `set`.
- `removeRuleIds: [1, 2, 3]`.
- `manifest.json` `version`을 `1.3.0`으로. `host_permissions`는 그대로.

- [ ] **Step 5: 실행해서 통과 확인**

Run: `uv run pytest tests/test_download_extension.py -m browser -v`
Expected: PASS. 실패하면 원인(CORS / preflight / 권한 경고)을 적어 운영자에게 보고하고 중단.

- [ ] **Step 6: Commit**

```bash
git add extensions/ssu-downloader tests/test_download_extension.py
git commit -m "feat(extension): let the dashboard read school CDN media for transcription"
```

---

### Task 2: 브라우저 오디오 추출 모듈

**Files:**
- Create: `app/web/vendor/mp4box.all.mjs` (npm `mp4box@2.4.1`의 `dist/mp4box.all.mjs` 그대로, 맨 위에 BSD-3 라이선스 주석)
- Create: `app/web/audio-extract.mjs`
- Create: `tests/fixtures/media/tone-faststart.mp4`, `tone-moov-end.mp4`, `video-only.mp4`
- Test: `tests/test_audio_extract_web.py`

**Interfaces:**
- Produces (`app/web/audio-extract.mjs`, ES 모듈):
  - `export class AudioExtractError extends Error` — `code`: `"no_audio" | "unsupported_codec" | "http"`. `message`는 Global Constraints의 문구.
  - `export function openCdnSource(url: string, { signal: AbortSignal, refresh: () => Promise<string> }): Source` — `Source = { size(): Promise<number>, read(start: number, end: number): Promise<Uint8Array> }` (`end` 포함). Range 요청. 403/404/410이면 `refresh()`로 주소를 **한 번** 바꿔 같은 범위를 다시 요청하고, 다시 실패하면 `AudioExtractError("http")`.
  - `export function makeResampler(inRate: number, outRate: number): (mono: Float32Array) => Float32Array` — 상태를 가진 선형 보간. 조각 경계에서 샘플이 빠지거나 겹치지 않는다.
  - `export async function* extractPcm(source: Source, { signal: AbortSignal, rate = 16000, chunkBytes = 2 * 1024 * 1024 }): AsyncGenerator<Float32Array, { duration: number }>` — 모노 `rate`Hz 조각을 낸다. 소비자가 다음 값을 당길 때만 다음 Range를 읽는다(역압). 반환값의 `duration`은 오디오 트랙 길이(초).

- [ ] **Step 1: 테스트용 mp4 만들기**

```bash
mkdir -p tests/fixtures/media
ffmpeg -f lavfi -i "sine=frequency=440:duration=3:sample_rate=48000" -f lavfi -i "color=size=64x64:duration=3" \
  -ac 2 -c:a aac -c:v libx264 -movflags +faststart tests/fixtures/media/tone-faststart.mp4
ffmpeg -f lavfi -i "sine=frequency=440:duration=3:sample_rate=48000" -f lavfi -i "color=size=64x64:duration=3" \
  -ac 2 -c:a aac -c:v libx264 tests/fixtures/media/tone-moov-end.mp4
ffmpeg -f lavfi -i "color=size=64x64:duration=1" -c:v libx264 tests/fixtures/media/video-only.mp4
```

확인: `ffprobe`로 `tone-moov-end.mp4`의 `moov`가 `mdat` 뒤에 있는지 본다(`ffprobe -v trace … 2>&1 | grep -E "type:'(moov|mdat)'"`).

- [ ] **Step 2: mp4box 넣기**

`npm pack mp4box@2.4.1`로 받아 `package/dist/mp4box.all.mjs`를 `app/web/vendor/mp4box.all.mjs`로 복사하고 라이선스 주석을 붙인다. (스펙은 jsdelivr를 적었지만, 테스트가 외부 네트워크 없이 돌도록 저장소에 넣는다. 서버는 정적 파일만 준다.)

- [ ] **Step 3: 실패하는 테스트 작성**

`tests/test_audio_extract_web.py` (`pytestmark = pytest.mark.browser`, `channel="chrome"` — AAC 디코딩에 필요). 페이지 `http://test.local/`을 route로 빈 HTML로 주고, `/audio-extract.mjs`·`/vendor/mp4box.all.mjs`는 `app/web`에서, `/media/<name>`은 픽스처에서 Range를 해석해 206으로 준다. route는 받은 Range 목록을 기록한다. 페이지 안에서 `import("/audio-extract.mjs")` 후:

- `test_extract_faststart_yields_16k_mono`: 모든 조각 길이 합 `n`에 대해 `abs(n - 3 * 16000) <= 1600`, 반환 `duration`이 2.9~3.1. 440Hz 확인: 가운데 1초 구간의 양수 방향 영교차 수가 430~450.
- `test_extract_moov_at_end`: `tone-moov-end.mp4`도 같은 단언. 추가로 기록된 Range 중 `end - start + 1 > chunkBytes`인 요청이 없다(`chunkBytes`를 `64 * 1024`로 주고 단언).
- `test_backpressure_reads_lazily`: `chunkBytes = 16 * 1024`로 주고 첫 조각 하나만 받은 뒤 멈췄을 때, 기록된 Range 끝 위치의 최댓값이 파일 크기보다 작다.
- `test_video_only_raises_no_audio`: `AudioExtractError`, `code === "no_audio"`, 메시지 `이 영상에는 소리 트랙이 없습니다.`
- `test_unsupported_codec`: `AudioDecoder.isConfigSupported`를 `async () => ({ supported: false })`로 바꾼 뒤 `code === "unsupported_codec"`, 메시지 `이 브라우저가 이 영상의 소리 형식을 읽지 못합니다.`
- `test_source_refreshes_url_once_on_403`: `/media/expired.mp4`는 항상 403, `refresh`는 `/media/tone-faststart.mp4`를 돌려준다 → 추출 성공, `refresh` 호출 1회. `refresh`도 403 주소를 돌려주면 `code === "http"`, 메시지 `학교 서버가 영상을 주지 않았습니다. 목록을 새로고침한 뒤 다시 시도하세요.`
- `test_abort_stops_reading`: 첫 조각 뒤 `AbortController.abort()` → 제너레이터가 `AbortError`로 끝나고 이후 Range 요청이 기록되지 않는다.
- `test_resampler_is_continuous`: `makeResampler(48000, 16000)`에 48000샘플을 1000·7·47993 길이로 나눠 넣으면 출력 합이 16000±1.

- [ ] **Step 4: 실행해서 실패 확인**

Run: `uv run pytest tests/test_audio_extract_web.py -v`
Expected: FAIL (`/audio-extract.mjs` 없음)

- [ ] **Step 5: `app/web/audio-extract.mjs` 구현**

- `import { createFile, DataStream } from "./vendor/mp4box.all.mjs";`
- 레이아웃 탐색: 0부터 최상위 상자 머리(8바이트, `size == 1`이면 16바이트)만 Range로 읽으며 건너뛰어 `moov`의 위치와 크기, `mdat`의 범위를 찾는다. `moov`를 통째로 읽어 `appendBuffer`(해당 `fileStart`)한 뒤 `onReady`에서 오디오 트랙을 고른다. 없으면 `no_audio`.
- 디코더 설정: `codec = track.codec`, `sampleRate`, `numberOfChannels`, `description` = 트랙 `stsd` 첫 항목의 `esds.esd.descs[0].descs[0].data`. `AudioDecoder.isConfigSupported` 실패 시 `unsupported_codec`.
- `setExtractionOptions(audioTrackId)` 후 `mdat`를 `chunkBytes` 단위로 읽어 `appendBuffer`; `onSamples`로 받은 샘플을 `EncodedAudioChunk`로 디코딩하고 `releaseUsedSamples`를 호출한다. 디코더 출력 `AudioData`는 `copyTo`(`format: "f32-planar"`)로 채널별로 받아 평균 → `makeResampler` → `yield`. 쓴 `AudioData`는 `close()`.
- 다 읽으면 `decoder.flush()` 후 남은 출력까지 내보내고 `{ duration: track.duration / track.timescale }`를 반환. `finally`에서 디코더 `close()`.

- [ ] **Step 6: 실행해서 통과 확인**

Run: `uv run pytest tests/test_audio_extract_web.py -v`
Expected: PASS (8개)

- [ ] **Step 7: Commit**

```bash
git add app/web/audio-extract.mjs app/web/vendor tests/fixtures/media tests/test_audio_extract_web.py
git commit -m "feat(transcripts): extract 16 kHz mono audio from CDN mp4 in the browser"
```

---

### Task 3: 화면을 브라우저 추출로 전환

**Files:**
- Modify: `app/web/transcribe.js` (`runNext`, `pumpAudio`, 버튼 함수의 `audioPath` → `mediaPath`)
- Modify: `app/web/downloads.js` (`requireAudioExtension` 추가)
- Modify: `app/web/app.js:1326, 1349-1350` (`mediaPath`, 휴대폰 숨김)
- Modify: `app/main.py` (CSP)
- Test: `tests/test_transcribe_web.py`, `tests/test_design_system_web.py:26`, `tests/test_access_web.py:272-273`, `tests/test_web.py`(CSP 단언이 있으면)

**Interfaces:**
- Consumes: Task 2의 `openCdnSource`, `extractPcm`, `AudioExtractError`. Task 1의 `ping.audio`.
- Produces: `async function requireAudioExtension(): Promise<void>` (`downloads.js`) — `requireDownloadExtension()`을 부른 뒤 `audio`가 없으면 `"아유 하기싫어 확장프로그램을 업데이트해 주세요."` 오류. 확장이 없으면 기존과 같은 `code: "EXTENSION_MISSING"` 오류. 영상 항목 필드 `v.mediaPath: string | null`.

- [ ] **Step 1: 실패하는 테스트로 바꾸기**

`tests/test_transcribe_web.py`: 가짜 Worker는 유지. `audio-link`·`/audio/tok` 모킹을 지우고 다음으로 바꾼다.
- `data["/courses/1/videos/12/direct-download"] = {"url": "https://cdn.commonscdn.com/a.mp4", "file_name": "a.mp4", "receipt": "r"}`
- `page.route("https://cdn.commonscdn.com/**", …)`은 `tone-faststart.mp4`를 Range 206 + `Access-Control-Allow-Origin: *`로 준다. 영상 `duration`은 3.
- 페이지 로드 뒤 `page.evaluate("window.requireAudioExtension = async () => {}")`.

테스트:
- `test_transcribe_extracts_in_browser_and_saves`: 기존 저장 단언 유지, `window.__samples`가 48000±1600, `/downloads/complete` 호출 0회, `/audio`로 시작하는 요청 0회.
- `test_transcribe_without_extension_shows_install_guide`: `requireAudioExtension`이 `Object.assign(new Error("…"), { code: "EXTENSION_MISSING" })`를 던지게 하면 설치 안내(`#video-list details`)가 펼쳐지고 작업이 실패로 남는다.
- `test_old_extension_asks_update_for_transcribe`: `extensionRequest`가 `ping`에 `{version:1, sessions:true, queue:true}`를 돌려주게 하면(`requireAudioExtension`은 실제 구현 사용) 토스트에 `확장프로그램을 업데이트해 주세요`.
- `test_transcribe_hidden_on_phone`: 휴대폰 UA로 열면 `[data-tx]`, `[data-tx-group]` 0개, 기존 텍스트가 있는 항목의 `[data-tx-open]`은 보인다.
- `test_cancel_aborts_cdn_fetch`: CDN route가 두 번째 Range부터 응답을 늦추게 하고 진행 중 취소 → 이후 CDN 요청이 더 오지 않고, 대기열의 다음 작업이 시작된다.
- `test_unsupported_host_is_rejected`: `direct-download`가 `https://media.ssu.ac.kr/a.mp4`(허용 목록 밖)를 주면 CDN 요청 없이 `이 영상은 텍스트 변환을 지원하지 않습니다.`로 실패.
- `test_short_audio_is_rejected`: 영상 `duration`을 10으로 주면(실제 3초) 기존 `오디오가 중간에 끊겼습니다` 오류.

`test_design_system_web.py:26`, `test_access_web.py:272-273`의 `audio-link`·`/audio/tok` 모킹은 `direct-download`·CDN route로 바꾼다(access 테스트는 `direct-download`가 `REQUIRED`를 돌려줄 때 접근 화면으로 가는지 단언).

- [ ] **Step 2: 실행해서 실패 확인**

Run: `uv run pytest tests/test_transcribe_web.py tests/test_design_system_web.py tests/test_access_web.py -v`
Expected: FAIL

- [ ] **Step 3: 구현**

- `downloads.js`: `requireAudioExtension()` (Interfaces 참고).
- `app.js:1349`: `v.mediaPath = phone ? null : v.linkPath` (`linkPath`는 이미 `direct-download` 경로이거나 `null`). `phone`은 그 함수에서 이미 구하는 `isPhoneDevice()` 값을 쓴다. `:1326` 범례 조건을 `v.mediaPath`로.
- `transcribe.js`:
  - `audioPath` 이름을 모두 `mediaPath`로.
  - `runNext`: 작업 시작 시 `await requireAudioExtension()`. 오류의 `code === "EXTENSION_MISSING"`이면 `reportDownloadError(err)`를 부른다. `const { openCdnSource, extractPcm } = await import(\`/audio-extract.mjs${ASSET_QUERY}\`)`. `link = await api(job.mediaPath, { method: "POST" })`, `refresh = async () => (await api(job.mediaPath, { method: "POST" })).url`. fetch 전에 `link.url`의 호스트가 `AUDIO_HOSTS`(`["commonscdn.com"]` + Task 1 결과, 같은 호스트이거나 `.`+호스트로 끝남)에 드는지 보고, 아니면 `이 영상은 텍스트 변환을 지원하지 않습니다.`로 실패시킨다. `X-Audio-Duration` 대신 `job.duration`(없으면 `extractPcm` 반환 `duration`)을 끊김 기준으로.
  - `pumpAudio(pcm, job, worker)`: `ReadableStream` 대신 `extractPcm` 제너레이터에서 Float32 조각을 당긴다. Int16 변환·`carry` 처리를 지운다. `READ_AHEAD_SECONDS` 대기 루프는 그대로 둔다.
  - `finally`의 "서버의 동시 실행 자리" 주석을 고친다. `job.ctrl.abort()`는 유지(CDN fetch를 끊는다).
  - 파일 머리 주석을 "확장 프로그램을 거쳐 학교 CDN에서 직접 읽어 브라우저에서 오디오를 뽑는다"로.
- `app/main.py` CSP `connect-src`에 `https://*.commonscdn.com`(+ Task 1에서 추가한 호스트)을 넣는다. 위 주석에 이유 한 줄.

- [ ] **Step 4: 실행해서 통과 확인**

Run: `uv run pytest tests/test_transcribe_web.py tests/test_design_system_web.py tests/test_access_web.py tests/test_web.py tests/test_videos_web.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/web app/main.py tests
git commit -m "feat(transcripts): transcribe from the school CDN via the extension"
```

---

### Task 4: 서버 ffmpeg 경로와 흔적 제거

**Files:**
- Delete: `app/lms/audio.py`, `tests/test_audio.py`
- Modify: `app/api/routes_media.py` (`audio-link` 두 개, `stream_audio`, `_Slot`의 `audio` 인자·`_audio_streams`, `SAMPLE_RATE`·`ffmpeg_path`·`pcm_stream` import, 토큰 `kind: "audio"` 분기)
- Modify: `app/config.py` (`max_audio_streams` 삭제), `.env.example:18-19`, `deploy/hetzner/.env.example:11`
- Modify: `Dockerfile:13` (ffmpeg 설치 줄 삭제)
- Modify: `pyproject.toml:34` (`ffmpeg` 마커 삭제), `.github/workflows/deploy.yml:29` (`-m 'not browser'`)
- Modify: `tests/test_access_api.py:60-61, 193-196`, `tests/test_production_env.py:30`, `tests/test_deploy.py:123, 141` (`MAX_AUDIO_STREAMS`를 다른 기존 키로, 예: `SYNC_JITTER_MINUTES=10`)
- Modify: `README.md:46, 104, 142, 150`, `docs/hetzner-cpx12-deployment.md`, `docs/hetzner-cpx02-deployment.md`, `docs/hetzner-cx23-deployment.md`, `docs/github-actions-deployment.md` (위 grep 줄들), `app/web/privacy.html:60` 부근, `store-assets/chrome-web-store/description.txt`, `extensions/ssu-downloader/manifest.json`의 `description`

**Interfaces:**
- Consumes: Task 3 이후 화면이 `audio-link`·`/audio/`를 부르지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_access_api.py`에 추가:

```python
def test_audio_routes_are_gone():
    paths = {p for _, p in _all_routes(app)}
    assert not any(p.startswith("/audio/") or p.endswith("/audio-link") for p in paths)
```

`tests/test_production_env.py`(또는 새 `tests/test_static_version.py` 옆)에 추가: `Dockerfile` 내용에 `ffmpeg`가 없고, `Settings()`에 `max_audio_streams` 속성이 없다.

- [ ] **Step 2: 실행해서 실패 확인**

Run: `uv run pytest tests/test_access_api.py::test_audio_routes_are_gone -v`
Expected: FAIL

- [ ] **Step 3: 위 Files 목록대로 삭제·수정**

- `_Slot(user_id, busy)`만 남긴다(사용자당 2개 제한은 수업자료 중계에 계속 쓴다).
- `test_access_api.py`의 `_signed`에서 `/audio/{token}` 분기를 지우고, 회수 테스트는 `download`만 확인한다.
- 문서: "서버는 ffmpeg로 오디오를 뽑는다"를 "확장 프로그램을 거쳐 브라우저가 학교 CDN에서 직접 오디오를 뽑는다. 서버는 영상 주소 발급과 결과 텍스트 저장만 한다"로. 배포 문서의 `MAX_AUDIO_STREAMS` 단계와 표의 해당 행은 삭제. 휴대폰에서는 전사를 쓸 수 없다는 점을 README 기능 설명에 한 줄.
- `privacy.html`·스토어 설명·manifest `description`에 "텍스트 변환 때 연동 웹사이트가 학교 CDN 영상을 직접 읽도록 허용한다(영상은 사용자 브라우저에서만 처리되고 서버로 보내지 않는다)"를 넣는다.

- [ ] **Step 4: 남은 흔적 검사와 전체 테스트**

Run: `git grep -n -i -E "max_audio|audio-link|stream_audio|pcm_stream|ffmpeg|/audio/" -- . ':!docs/superpowers'`
Expected: 출력 없음

Run: `uv run pytest -q`
Expected: 모두 PASS (브라우저 테스트 포함, 로컬 Chrome 필요)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(transcripts): drop server-side ffmpeg audio extraction"
```

---

### Task 5: 실제 환경 확인 (수동, 운영자와 함께)

- [ ] 빌드: `uv run python scripts/build-download-extension.py` → Chrome에 압축 해제 로드.
- [ ] 50분 이상 강의 1개 전사: 작업 관리자에서 탭 메모리가 일정하고, 저장된 텍스트 길이가 영상 길이의 97% 이상.
- [ ] 전사 중 서버 `sudo docker stats --no-stream`에서 api CPU가 평소 수준.
- [ ] 확장 끔 → 설치 안내, 구버전(1.2.5) → 업데이트 안내, 다운로드는 구버전에서도 동작.
- [ ] 일반 탭에서 학교 사이트 이용 시 요청 헤더가 바뀌지 않음(DevTools Network).
- [ ] 스토어에 1.3.0 제출 → 게시 확인 후 서버 배포.
