# 텍스트 변환 오디오 추출 브라우저 이전 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **개정 (2026-11-06):** 첫 시도(DNR 규칙으로 사이트에 CORS 열기)는 실패했다. 규칙은 등록·매칭되지만 사이트에서 시작한 요청에는 헤더가 적용되지 않았다(요청 시작 원점에 대한 호스트 권한이 필요한 것으로 보인다). 운영자 결정대로 권한을 늘리지 않고 스펙 5.6(오프스크린 문서)으로 간다.

**Goal:** 전사용 오디오 추출(서버 ffmpeg)을 사용자 브라우저(확장 프로그램 필수)로 옮기고 서버의 ffmpeg 경로를 모두 지운다.

**Architecture:** 확장 프로그램의 오프스크린 문서가 학교 CDN mp4를 Range로 조금씩 읽어(기존 규칙 1이 Commons Referer/Origin을 붙인다) mp4box.js로 오디오 트랙을 꺼내고 WebCodecs `AudioDecoder`로 디코딩해 16kHz 모노 PCM으로 바꾼다. 페이지는 기존 브리지(`extensionRequest`)로 `audio-open` → `audio-read`(당겨 받기) → `audio-close`를 호출해 PCM을 받아 기존 Whisper 워커에 넘긴다. 서버의 `/audio`·`audio-link`·ffmpeg는 삭제한다.

**Tech Stack:** Vanilla JS, mp4box.js 2.4.1(확장 프로그램에 포함), WebCodecs, Chrome MV3(`chrome.offscreen`), FastAPI, pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-11-06-client-audio-extraction-design.md` (5.6 채택)

## Global Constraints

- 확장 프로그램이 없으면 전사 불가. 서버 대체 경로(서버 ffmpeg, ffmpeg.wasm) 없음.
- 확장 프로그램에 새 **호스트** 권한을 추가하지 않는다. 경고가 없는 API 권한 `offscreen`만 추가한다.
- 오디오용 CDN 허용 호스트 `AUDIO_DOMAINS = ["commonscdn.com"]`(정확히 같거나 `.`+호스트로 끝남). `ssu.ac.kr` 영상은 받지 않는다.
- 휴대폰(`isPhoneDevice()`)에서는 전사 버튼·"전체 텍스트로 변환" 버튼을 숨긴다. 텍스트 보기는 유지.
- Whisper 워커 입력 형식(16kHz 모노 Float32 `chunk` 메시지), `READ_AHEAD_SECONDS = 60`, `MIN_AUDIO_RATIO = 0.97`, 저장 `model` 값 `whisper-small/<device>`는 바꾸지 않는다.
- 영상 전체를 메모리에 쌓지 않는다(Range 조각 단위, `moov`가 끝에 있어도 마찬가지). 페이지가 `audio-read`를 부를 때만 다음 조각을 읽는다.
- 브리지 메시지 한 번에 PCM 최대 `READ_CHUNK_SECONDS = 5`초. PCM은 16비트 LE를 base64로 보낸다.
- 사용자 문구(스펙 5.3 그대로):
  - 오디오 트랙 없음: `이 영상에는 소리 트랙이 없습니다.`
  - 디코딩 불가: `이 브라우저가 이 영상의 소리 형식을 읽지 못합니다.`
  - CDN 403/404/410(재발급 후에도): `학교 서버가 영상을 주지 않았습니다. 목록을 새로고침한 뒤 다시 시도하세요.`
  - 허용 목록 밖 호스트: `이 영상은 텍스트 변환을 지원하지 않습니다.`
  - 구버전 확장: `아유 하기싫어 확장프로그램을 업데이트해 주세요.`
- 확장 프로그램 새 버전 `1.3.0`(스토어 게시) → 그다음 서버 배포.

## Review Focus

1. **전사 도중 취소·로그아웃** — 오프스크린의 CDN 읽기와 디코더가 멈추고(`audio-close`) 다음 작업이 시작돼야 한다. → Task 2 `test_audio_close_stops_fetching`, Task 3 `test_cancel_closes_extension_job`.
2. **CDN 주소가 중간에 만료(403)** — 주소를 한 번만 재발급해 같은 바이트 위치부터 잇는다. 두 번째 403이면 오류 문구. → Task 1 `test_source_refreshes_url_once_on_403`, Task 2 `test_audio_refresh_round_trip`, Task 3 `test_refresh_reissues_url`.
3. **`moov`가 파일 끝인 mp4** — 끝까지 쌓지 않고 정상 추출. → Task 1 `test_extract_moov_at_end`.
4. **구버전 확장 프로그램(ping에 `audio` 없음)** — 다운로드는 계속 되고, 전사만 업데이트 안내. → Task 3 `test_old_extension_asks_update_for_transcribe`.
5. **서비스 워커가 잠든 뒤 이어서 읽기** — MV3 서비스 워커는 30초 유휴면 종료될 수 있다. 작업 상태는 오프스크린 문서에 두고, 서비스 워커는 상태 없이 중계만 한다. → Task 2 `test_audio_survives_service_worker_restart`.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `extensions/ssu-downloader/vendor/mp4box.all.mjs` (생성) | mp4box.js 2.4.1 ESM 원본(라이선스 주석) |
| `extensions/ssu-downloader/audio-extract.mjs` (생성) | `openCdnSource`, `extractPcm`, `makeResampler`, `AudioExtractError` — 환경 독립 모듈 |
| `extensions/ssu-downloader/offscreen.html`, `offscreen.js` (생성) | 작업 상태 보관, 추출기 실행, `audio-*` 처리 |
| `extensions/ssu-downloader/worker.js` (수정) | `audio-*` 중계, 오프스크린 문서 생성, `ping`에 `audio: true` |
| `extensions/ssu-downloader/manifest.json` (수정) | `permissions`에 `offscreen`, 버전 `1.3.0` |
| `scripts/build-download-extension.py` (수정) | 새 파일을 빌드 산출물에 포함 |
| `app/web/transcribe.js`, `downloads.js`, `app.js` (수정) | 확장 프로그램에서 PCM 당겨 받기, 휴대폰 숨김 |
| 서버·문서 | ffmpeg 경로 제거 |
| `tests/fixtures/media/*.mp4` (생성) | 추출 테스트용 mp4 |

---

### Task 1: 오디오 추출 모듈 (확장 프로그램 안, 환경 독립)

**Files:**
- Create: `extensions/ssu-downloader/vendor/mp4box.all.mjs` (npm `mp4box@2.4.1`의 `dist/mp4box.all.mjs` 그대로, 맨 위에 BSD-3 라이선스 주석)
- Create: `extensions/ssu-downloader/audio-extract.mjs`
- Create: `tests/fixtures/media/tone-faststart.mp4`, `tone-moov-end.mp4`, `tone-opus.mp4`, `video-only.mp4`
- Test: `tests/test_audio_extract_web.py`

**Interfaces:**
- Produces (`audio-extract.mjs`, ES 모듈, DOM 없이 `fetch`·WebCodecs만 사용):
  - `export class AudioExtractError extends Error` — `code`: `"no_audio" | "unsupported_codec" | "http"`, `message`는 Global Constraints 문구.
  - `export function openCdnSource(url: string, { signal: AbortSignal, refresh: () => Promise<string> }): Source` — `Source = { size(): Promise<number>, read(start: number, end: number): Promise<Uint8Array> }` (`end` 포함). Range 요청. 403/404/410이면 `refresh()`로 주소를 **한 번** 바꿔 같은 범위를 다시 요청, 다시 실패하면 `AudioExtractError("http")`.
  - `export function makeResampler(inRate: number, outRate: number): (mono: Float32Array) => Float32Array` — 상태를 가진 선형 보간.
  - `export async function* extractPcm(source: Source, { signal: AbortSignal, rate = 16000, chunkBytes = 2 * 1024 * 1024 }): AsyncGenerator<Float32Array, { duration: number }>` — 모노 `rate`Hz 조각. 소비자가 당길 때만 다음 Range를 읽는다. 반환값 `duration`은 오디오 트랙 길이(초). AAC(`mp4a.*`)와 Opus(mp4box 코덱 문자열 `Opus` → WebCodecs `opus`, `description` 생략 가능)를 지원한다.

- [ ] **Step 1: 테스트용 mp4 만들기**

```bash
mkdir -p tests/fixtures/media
ffmpeg -f lavfi -i "sine=frequency=440:duration=3:sample_rate=48000" -f lavfi -i "color=size=64x64:duration=3" \
  -ac 2 -c:a aac -c:v libx264 -movflags +faststart tests/fixtures/media/tone-faststart.mp4
ffmpeg -f lavfi -i "sine=frequency=440:duration=3:sample_rate=48000" -f lavfi -i "color=size=64x64:duration=3" \
  -ac 2 -c:a aac -c:v libx264 tests/fixtures/media/tone-moov-end.mp4
ffmpeg -f lavfi -i "sine=frequency=440:duration=3:sample_rate=48000" -f lavfi -i "color=size=64x64:duration=3" \
  -ac 2 -c:a libopus -c:v libx264 -movflags +faststart tests/fixtures/media/tone-opus.mp4
ffmpeg -f lavfi -i "color=size=64x64:duration=1" -c:v libx264 tests/fixtures/media/video-only.mp4
```

확인: `tone-moov-end.mp4`는 `moov`가 `mdat` 뒤에 있다(`ffprobe -v trace … 2>&1 | grep -E "type:'(moov|mdat)'"`). 생성 명령은 `tests/fixtures/media/README.md`에 남긴다. (Opus 픽스처는 Task 2의 확장 테스트용: 그 테스트는 AAC를 디코딩하지 못하는 Playwright Chromium으로 돌아간다.)

- [ ] **Step 2: mp4box 넣기**

`npm pack mp4box@2.4.1`로 받아 `package/dist/mp4box.all.mjs`를 `extensions/ssu-downloader/vendor/mp4box.all.mjs`로 복사하고 라이선스 주석을 붙인다.

- [ ] **Step 3: 실패하는 테스트 작성**

`tests/test_audio_extract_web.py` (`pytestmark = pytest.mark.browser`, `channel="chrome"` — AAC 디코딩에 필요, 확장 프로그램 없이 일반 페이지에서 모듈만 시험). 페이지 `http://test.local/`을 route로 빈 HTML로 주고, `/audio-extract.mjs`·`/vendor/mp4box.all.mjs`는 `extensions/ssu-downloader`에서, `/media/<name>`은 픽스처에서 Range를 해석해 206으로 준다. route는 받은 Range 목록을 기록한다. 페이지에서 `import("/audio-extract.mjs")` 후:

- `test_extract_faststart_yields_16k_mono`: 조각 길이 합 `n`이 `abs(n - 48000) <= 1600`, 반환 `duration` 2.9~3.1, 가운데 1초의 양수 방향 영교차 430~450.
- `test_extract_moov_at_end`: `tone-moov-end.mp4`도 같은 단언. `chunkBytes = 64 * 1024`일 때 `end - start + 1 > chunkBytes`인 Range 요청이 없다.
- `test_extract_opus`: `tone-opus.mp4`도 샘플 수·`duration` 단언.
- `test_backpressure_reads_lazily`: `chunkBytes = 16 * 1024`, 첫 조각만 받고 멈추면 기록된 Range 끝 위치 최댓값 < 파일 크기.
- `test_video_only_raises_no_audio`: `code === "no_audio"`, 메시지 `이 영상에는 소리 트랙이 없습니다.`
- `test_unsupported_codec`: `AudioDecoder.isConfigSupported`를 `async () => ({ supported: false })`로 바꾸면 `code === "unsupported_codec"`, 메시지 `이 브라우저가 이 영상의 소리 형식을 읽지 못합니다.`
- `test_source_refreshes_url_once_on_403`: `/media/expired.mp4`는 항상 403, `refresh`가 `/media/tone-faststart.mp4`를 주면 성공하고 `refresh` 1회. `refresh`도 403 주소면 `code === "http"`, 메시지 `학교 서버가 영상을 주지 않았습니다. 목록을 새로고침한 뒤 다시 시도하세요.`
- `test_abort_stops_reading`: 첫 조각 뒤 abort → `AbortError`로 끝나고 이후 Range 요청 없음.
- `test_resampler_is_continuous`: `makeResampler(48000, 16000)`에 48000샘플을 1000·7·47993으로 나눠 넣으면 출력 합 16000±1.

- [ ] **Step 4: 실행해서 실패 확인**

Run: `uv run pytest tests/test_audio_extract_web.py -v`
Expected: FAIL (`/audio-extract.mjs` 없음)

- [ ] **Step 5: `audio-extract.mjs` 구현**

- `import { createFile } from "./vendor/mp4box.all.mjs";`
- 레이아웃 탐색: 0부터 최상위 상자 머리(8바이트, `size == 1`이면 16바이트)만 Range로 읽어 건너뛰며 `moov` 위치·크기와 `mdat` 범위를 찾는다. `moov`를 통째로 읽어 `appendBuffer`(해당 `fileStart`), `onReady`에서 오디오 트랙 선택. 없으면 `no_audio`.
- 디코더 설정: AAC는 `codec = track.codec`, `description` = 트랙 `stsd` 첫 항목의 `esds.esd.descs[0].descs[0].data`. Opus는 `codec = "opus"`. `sampleRate`, `numberOfChannels`는 트랙 값. `AudioDecoder.isConfigSupported` 실패 시 `unsupported_codec`.
- `setExtractionOptions(audioTrackId)` 후 `mdat`를 `chunkBytes` 단위로 읽어 `appendBuffer`; `onSamples` 샘플 → `EncodedAudioChunk` → 디코딩, `releaseUsedSamples`. 출력 `AudioData`는 `copyTo`(`format: "f32-planar"`)로 채널별로 받아 평균 → 리샘플 → `yield`, 쓴 `AudioData`는 `close()`.
- 끝나면 `flush()` 후 남은 출력 내보내고 `{ duration: track.duration / track.timescale }` 반환. `finally`에서 디코더 `close()`.

- [ ] **Step 6: 실행해서 통과 확인**

Run: `uv run pytest tests/test_audio_extract_web.py -v`
Expected: PASS (9개)

- [ ] **Step 7: Commit**

```bash
git add extensions/ssu-downloader/audio-extract.mjs extensions/ssu-downloader/vendor tests/fixtures/media tests/test_audio_extract_web.py
git commit -m "feat(extension): extract 16 kHz mono audio from CDN mp4"
```

---

### Task 2: 확장 프로그램 오프스크린 추출 파이프라인과 브리지 프로토콜

시작 전: 작업 트리에 첫 시도(DNR 규칙 3)의 커밋하지 않은 변경이 있을 수 있다. 규칙 3과 `AUDIO_DOMAINS`의 규칙용 사용, `ping`에서 `configureRules()`를 부르는 변경은 버린다. `ping`의 `audio: true`, 버전 `1.3.0`, 테스트 픽스처가 요청 메서드를 기록하게 한 변경은 써도 된다. `git diff`로 확인하고 정리한다.

**Files:**
- Create: `extensions/ssu-downloader/offscreen.html`, `extensions/ssu-downloader/offscreen.js`
- Modify: `extensions/ssu-downloader/worker.js`, `extensions/ssu-downloader/manifest.json`
- Modify: `scripts/build-download-extension.py` (`assets`에 `offscreen.html`, `offscreen.js`, `audio-extract.mjs`, `vendor/mp4box.all.mjs`)
- Test: `tests/test_download_extension.py`

**Interfaces:**
- Consumes: Task 1의 `openCdnSource`, `extractPcm`, `AudioExtractError`.
- Produces (페이지에서 `extensionRequest(action, data)`로 호출, 브리지가 결과 객체를 그대로 돌려준다; 오류는 `{ error: <문구> }`):
  - `ping` → `{ version: 1, sessions: true, queue: true, audio: true }`
  - `audio-open` `{ job: string(UUID), url: string }` → `{ opened: true }`. `url`이 HTTPS·포트 없음·`AUDIO_DOMAINS`가 아니면 `{ error: "이 영상은 텍스트 변환을 지원하지 않습니다." }`. 열려 있던 다른 작업은 닫는다(한 번에 한 작업).
  - `audio-read` `{ job, url?: string }` → 다음 중 하나:
    - `{ pcm: string(base64, Int16 LE, 16kHz 모노), seconds: number }` — 최대 `READ_CHUNK_SECONDS = 5`초
    - `{ refresh: true }` — 추출기가 주소 재발급을 요청함. 다음 `audio-read`에 새 `url`을 넣어야 이어진다.
    - `{ done: true, duration: number }`
    - `{ error: <문구> }`
  - `audio-close` `{ job }` → `{ closed: true }` (모르는 작업이어도 같은 응답).
- 내부: 서비스 워커는 `audio-*`를 `chrome.offscreen.createDocument({ url: "offscreen.html", reasons: ["WORKERS"], justification: "Decode lecture audio for in-browser transcription" })`로 만든(이미 있으면 재사용) 문서에 `chrome.runtime.sendMessage({ target: "offscreen-audio", ... })`로 넘기고 응답을 그대로 돌려준다. 작업 상태(추출기, 대기 중인 재발급)는 `offscreen.js`만 가진다. `offscreen.js`는 `sender.id === chrome.runtime.id`이고 `sender.tab`이 없는 메시지만 받는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_download_extension.py`의 `school_cdn` 핸들러에 Range 지원 경로를 더한다: `/tone-opus.mp4`(Task 1 픽스처를 Range로 206, Commons Referer/Origin일 때만), `/expired.mp4`(항상 403). 요청마다 `(method, path, headers)`를 기록한다. 페이지 헬퍼로 `open → read 반복 → close`를 돌려 PCM 샘플 수를 세는 JS 함수를 둔다.

- `test_audio_pipeline_through_extension`: `ping`이 `audio: true` 포함. `https://download-test.commonscdn.com/tone-opus.mp4`를 끝까지 읽으면 샘플 합 48000±1600, `done.duration` 2.9~3.1, 각 `pcm` 조각 ≤ 5초분(80000샘플). CDN이 받은 모든 요청에 Commons Referer/Origin.
- `test_audio_rejects_other_hosts`: `https://evil.example/a.mp4`, `https://commons.ssu.ac.kr/a.mp4`, `http://download-test.commonscdn.com/a.mp4`는 모두 `이 영상은 텍스트 변환을 지원하지 않습니다.`
- `test_audio_refresh_round_trip`: `/expired.mp4`로 열면 `audio-read`가 `{refresh: true}`, 다음 `audio-read`에 `/tone-opus.mp4` 주소를 넣으면 끝까지 이어진다.
- `test_audio_close_stops_fetching`: 첫 `pcm` 뒤 `audio-close` → 이후 CDN 요청이 기록되지 않는다(0.5초 대기 후 확인), 같은 `job`으로 `audio-read`하면 `{ error }`.
- `test_audio_survives_service_worker_restart`: 첫 `pcm` 뒤 서비스 워커를 강제로 멈추고(Playwright `context.service_workers[0]`의 CDP 세션으로 `ServiceWorker.stopAllWorkers`, 또는 `chrome://serviceworker-internals` 대신 쓸 수 있는 방법) 다시 `audio-read` → 이어서 끝까지 읽힌다.
- 신뢰하지 않는 원점(`https://daunload.cloud.example.org`)에서는 `audio-open`이 연결되지 않는다(기존 테스트의 untrusted 블록에 단언 추가).

페이지에서 CDN(127.0.0.1로 매핑)으로의 요청은 확장 프로그램이 보내므로 사이트 CORS는 필요 없다. Chrome의 로컬 네트워크 접근 검사가 막으면 브라우저 인자에 `--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessSendPreflights`를 더한다(테스트 전용).

- [ ] **Step 2: 실행해서 실패 확인**

Run: `uv run pytest tests/test_download_extension.py -v`
Expected: FAIL (`audio-open` 미지원)

- [ ] **Step 3: 구현**

- `manifest.json`: `permissions`에 `"offscreen"`, `version` `"1.3.0"`. `host_permissions` 그대로.
- `worker.js`: `AUDIO_DOMAINS`와 URL 검사(기존 `validateDownload`와 같은 방식), `ensureOffscreen()`, `handle()`에서 `audio-open`/`audio-read`/`audio-close`를 검사 후 오프스크린으로 중계. `job`은 기존 작업 번호 형식(`/^[a-zA-Z0-9-]{16,80}$/`)만 허용. `ping`에 `audio: true`.
- `offscreen.js` (모듈 스크립트): `jobs`(최대 1개) = `{ id, ctrl: AbortController, iterator, pendingRefresh: {resolve} | null, refreshAsked: boolean }`.
  - `audio-open`: 기존 작업 abort 후 새 작업. `openCdnSource(url, { signal, refresh })`의 `refresh`는 `refreshAsked = true`로 표시하고 다음 `audio-read`의 `url`로 풀리는 Promise를 돌려준다.
  - `audio-read`: `url`이 있고 `pendingRefresh`가 있으면 그 `url`로 푼다. 그다음 이터레이터에서 5초분이 모이거나 끝날 때까지 당긴다. 당기는 도중 `refreshAsked`가 켜지면 지금까지 모은 PCM이 있으면 그것을, 없으면 `{ refresh: true }`를 돌려준다(이터레이터의 대기 중인 `next()`는 다음 `audio-read`에서 이어서 기다린다). Float32 → Int16(클램프) → base64.
  - 오류: `AudioExtractError`면 그 `message`, 그 밖에는 `학교 서버가 영상을 주지 않았습니다. 목록을 새로고침한 뒤 다시 시도하세요.`로 `{ error }`, 작업 정리.
  - `audio-close`: abort, 작업 삭제.
- `scripts/build-download-extension.py`의 `assets`에 새 파일 4개.

- [ ] **Step 4: 실행해서 통과 확인**

Run: `uv run pytest tests/test_download_extension.py -v` 그리고 `uv run pytest -q -m 'not browser and not ffmpeg'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add extensions/ssu-downloader scripts/build-download-extension.py tests/test_download_extension.py
git commit -m "feat(extension): decode lecture audio in an offscreen document for transcription"
```

---

### Task 3: 화면을 확장 프로그램 추출로 전환

**Files:**
- Modify: `app/web/transcribe.js` (`runNext`, `pumpAudio`, 버튼 함수의 `audioPath` → `mediaPath`)
- Modify: `app/web/downloads.js` (`requireAudioExtension` 추가)
- Modify: `app/web/app.js:1326, 1349-1350` (`mediaPath`, 휴대폰 숨김)
- Test: `tests/test_transcribe_web.py`, `tests/test_design_system_web.py:26`, `tests/test_access_web.py:272-273`

**Interfaces:**
- Consumes: Task 2의 브리지 프로토콜(`ping.audio`, `audio-open`/`audio-read`/`audio-close`).
- Produces: `async function requireAudioExtension(): Promise<void>` (`downloads.js`) — `requireDownloadExtension()`을 부른 뒤 `audio`가 없으면 `아유 하기싫어 확장프로그램을 업데이트해 주세요.` 오류. 확장이 없으면 기존과 같은 `code: "EXTENSION_MISSING"` 오류. 영상 항목 필드 `v.mediaPath: string | null`.

- [ ] **Step 1: 실패하는 테스트로 바꾸기**

`tests/test_transcribe_web.py`: 가짜 Whisper Worker는 유지. `audio-link`·`/audio/tok` 모킹을 지우고:
- `data["/courses/1/videos/12/direct-download"] = {"url": "https://cdn.commonscdn.com/a.mp4", "file_name": "a.mp4", "receipt": "r"}`
- 페이지 로드 뒤 `window.extensionRequest`를 가짜로 바꾼다: `ping` → `{version:1, sessions:true, queue:true, audio:true}`, `audio-open` → `{opened:true}`, `audio-read` → 3초짜리 440Hz 사인을 Int16 base64로 1초씩 세 번 준 뒤 `{done:true, duration:3}`. 호출 기록을 `window.__ext`에 남긴다. 영상 `duration`은 3.

테스트:
- `test_transcribe_pulls_pcm_from_extension_and_saves`: 기존 저장 단언 유지, `window.__samples` 48000±1, `audio-open`의 `url`이 `direct-download`가 준 주소, 끝나면 `audio-close` 호출, `/downloads/complete`·`/audio` 요청 0회.
- `test_transcribe_without_extension_shows_install_guide`: `extensionRequest`가 `code: "EXTENSION_MISSING"` 오류를 던지면 설치 안내(`#video-list details`)가 펼쳐지고 작업은 실패.
- `test_old_extension_asks_update_for_transcribe`: `ping`이 `audio` 없이 응답하면 작업 실패 문구 `아유 하기싫어 확장프로그램을 업데이트해 주세요.`
- `test_transcribe_hidden_on_phone`: 휴대폰 UA에서 `[data-tx]`, `[data-tx-group]` 0개, 텍스트가 있는 항목의 `[data-tx-open]`은 보인다.
- `test_cancel_closes_extension_job`: 두 번째 `audio-read`를 늦추고 취소 → `audio-close` 호출, 이후 `audio-read` 없음, 대기열의 다음 작업이 시작된다.
- `test_refresh_reissues_url`: 첫 `audio-read`가 `{refresh:true}`면 `direct-download`를 한 번 더 부르고, 다음 `audio-read`에 그 `url`을 넣는다.
- `test_extension_error_is_shown`: `audio-read`가 `{ error: "이 영상에는 소리 트랙이 없습니다." }`를 주면 작업이 그 문구로 실패한다(`extensionRequest`가 `result.error`를 오류로 바꾸는 기존 동작을 따른다).
- `test_short_audio_is_rejected`: 영상 `duration`이 10이면(실제 3초) `오디오가 중간에 끊겼습니다` 오류.

`test_design_system_web.py:26`, `test_access_web.py:272-273`의 `audio-link`·`/audio/tok` 모킹은 `direct-download`와 가짜 `extensionRequest`로 바꾼다(access 테스트는 `direct-download`가 `REQUIRED`를 돌려줄 때 접근 화면으로 가는지 단언).

- [ ] **Step 2: 실행해서 실패 확인**

Run: `uv run pytest tests/test_transcribe_web.py tests/test_design_system_web.py tests/test_access_web.py -v`
Expected: FAIL

- [ ] **Step 3: 구현**

- `downloads.js`: `requireAudioExtension()`.
- `app.js:1349`: `v.mediaPath = phone ? null : v.linkPath` (`phone`은 그 함수의 `isPhoneDevice()` 값). `:1326` 범례 조건을 `v.mediaPath`로.
- `transcribe.js`:
  - `audioPath` → `mediaPath` 전부.
  - `runNext`: `await requireAudioExtension()`(오류 `code === "EXTENSION_MISSING"`이면 `reportDownloadError(err)`도 부른다). `link = await api(job.mediaPath, { method: "POST" })`, `job.extJob = crypto.randomUUID()`, `audio-open`. `X-Audio-Duration` 대신 `job.duration`(없으면 `done.duration`)을 끊김 기준으로.
  - `pumpAudio(job, worker)`: `audio-read`를 반복. `refresh`면 `direct-download`를 다시 불러 다음 `audio-read`에 `url`을 넣는다. `pcm`이면 base64 → Int16 → Float32(`/ 32768`) → 워커 `chunk`. `done`이면 끝. `READ_AHEAD_SECONDS` 대기 루프와 취소(`job.ctrl.signal`) 확인은 그대로 둔다.
  - `finally`: `extensionRequest("audio-close", { job: job.extJob })`(실패 무시). "서버의 동시 실행 자리" 주석을 고친다.
  - 파일 머리 주석: "확장 프로그램이 학교 CDN에서 읽어 오디오를 뽑아 주고, 브라우저 Whisper가 텍스트로 바꾼다."

- [ ] **Step 4: 실행해서 통과 확인**

Run: `uv run pytest tests/test_transcribe_web.py tests/test_design_system_web.py tests/test_access_web.py tests/test_web.py tests/test_videos_web.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/web tests
git commit -m "feat(transcripts): pull transcription audio from the extension"
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
- Modify: `README.md:46, 104, 142, 150`, `docs/hetzner-cpx12-deployment.md`, `docs/hetzner-cpx02-deployment.md`, `docs/hetzner-cx23-deployment.md`, `docs/github-actions-deployment.md`, `app/web/privacy.html:60` 부근, `store-assets/chrome-web-store/description.txt`, `extensions/ssu-downloader/manifest.json`의 `description`

**Interfaces:**
- Consumes: Task 3 이후 화면이 `audio-link`·`/audio/`를 부르지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_access_api.py`에 추가:

```python
def test_audio_routes_are_gone():
    paths = {p for _, p in _all_routes(app)}
    assert not any(p.startswith("/audio/") or p.endswith("/audio-link") for p in paths)
```

`tests/test_production_env.py`에 추가: `Dockerfile` 내용에 `ffmpeg`가 없고, `Settings()`에 `max_audio_streams` 속성이 없다.

- [ ] **Step 2: 실행해서 실패 확인**

Run: `uv run pytest tests/test_access_api.py::test_audio_routes_are_gone -v`
Expected: FAIL

- [ ] **Step 3: 위 Files 목록대로 삭제·수정**

- `_Slot(user_id, busy)`만 남긴다(사용자당 2개 제한은 수업자료 중계에 계속 쓴다).
- `test_access_api.py`의 `_signed`에서 `/audio/{token}` 분기를 지우고, 회수 테스트는 `download`만 확인한다.
- 문서: "서버는 ffmpeg로 오디오를 뽑는다"를 "확장 프로그램이 학교 CDN에서 읽어 사용자 브라우저에서 오디오를 뽑는다. 서버는 영상 주소 발급과 결과 텍스트 저장만 한다"로. 배포 문서의 `MAX_AUDIO_STREAMS` 단계와 표의 행은 삭제. README 기능 설명에 "텍스트 변환은 PC Chrome + 확장 프로그램에서만" 한 줄.
- `privacy.html`·스토어 설명·manifest `description`: "텍스트 변환을 쓰면 확장 프로그램이 학교 CDN에서 강의 영상을 읽어 브라우저 안에서 소리만 뽑아 연동 웹사이트에 넘긴다. 영상과 소리는 서버로 보내지 않는다."

- [ ] **Step 4: 남은 흔적 검사와 전체 테스트**

Run: `git grep -n -i -E "max_audio|audio-link|stream_audio|pcm_stream|ffmpeg|/audio/" -- . ':!docs/superpowers' ':!tests/fixtures'`
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

- [ ] 빌드: `uv run python scripts/build-download-extension.py` → Chrome에 압축 해제 로드. 업데이트 시 권한 재동의 창이 뜨지 않는지 확인(`offscreen`은 경고 없는 권한).
- [ ] 50분 이상 강의 1개 전사: 탭·확장 프로그램 메모리가 일정하고, 저장된 텍스트 길이가 영상 길이의 97% 이상.
- [ ] 전사 중 서버 `sudo docker stats --no-stream`에서 api CPU가 평소 수준.
- [ ] 확장 끔 → 설치 안내, 구버전(1.2.5) → 업데이트 안내, 다운로드는 구버전에서도 동작.
- [ ] 스토어에 1.3.0 제출 → 게시 확인 후 서버 배포.
