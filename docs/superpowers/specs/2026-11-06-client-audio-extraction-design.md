# 텍스트 변환 오디오 추출의 브라우저 이전 설계

작성일: 2026-11-06 · 대상: `app/web/transcribe.js`, `app/web/whisper.worker.js`(주변), `extensions/ssu-downloader`, `app/api/routes_media.py`, `app/lms/audio.py`, `Dockerfile`, 문서

## 1. 목적

텍스트 변환(전사)에서 서버가 맡던 마지막 일, 즉 **ffmpeg로 영상에서 16kHz PCM을 뽑아 흘려보내는 일**을 사용자 브라우저로 옮긴다. Whisper 추론은 이미 브라우저(`whisper.worker.js`)에서 하므로, 이전이 끝나면 전사에 서버 CPU를 쓰지 않는다.

배경: 서버는 Hetzner CPX12(1 vCPU, 2GB RAM)이고 Chrome 자동 재생과 함께 돈다. 사용자는 약 20명, 30명까지 늘 것으로 본다. 아직 장애는 없고 **미리 하는 최적화**다. 운영자의 말: "확장 프로그램을 설치해야만 쓸 수 있도록 해줘. 서버 부하는 최소화해야한다."

## 2. 목표와 비목표

**목표**
- 전사 중 서버가 하는 일은 CDN 영상 주소 발급(기존 API)과 결과 텍스트 저장(기존 API)뿐이다.
- 서버의 ffmpeg 경로와 ffmpeg 설치를 없앤다.
- 1시간 넘는 강의도 브라우저 메모리를 일정하게 쓴다(영상 전체를 메모리에 올리지 않는다).
- 이번 학기 영상과 저장된(지난 학기) 영상 모두 지금처럼 전사할 수 있다.

**비목표**
- Whisper 모델·창 크기·결과 형식 변경. 워커에 들어가는 입력(16kHz 모노 Float32 조각)은 지금과 같다.
- 확장 프로그램 없는 사용자를 위한 대체 경로(서버 ffmpeg, ffmpeg.wasm 등).
- Chrome 외 브라우저 지원. 확장 프로그램이 Chrome 120 이상만 지원한다.
- 자동 재생(Chrome) 부하 최적화. 별도 설계로 다룬다.

**이렇게 되면 실패다**
- 서버가 여전히 영상 바이트를 받거나 디코딩한다.
- 긴 강의에서 탭 메모리가 영상 크기만큼 늘어 탭이 죽는다.
- 오디오가 중간에 끊겼는데 짧은 결과가 정상 완료로 저장된다(지금 `MIN_AUDIO_RATIO` 검사가 막는 일).
- 확장 프로그램 규칙이 넓어져 다른 사이트가 학교 CDN을 읽을 수 있게 되거나, 사용자의 일반 학교 사이트 이용에 영향을 준다.

## 3. 운영자가 정한 것

- 오디오 추출은 브라우저에서 한다.
- 확장 프로그램이 없으면 전사를 쓸 수 없다. 서버 대체 경로를 두지 않는다. 영상 다운로드처럼 설치 안내를 보인다.
- 서버 부하 최소화가 우선이다.
- 확장 프로그램에 새 호스트 권한(재동의 창)이 필요해지면 권한을 늘리지 않고 5.6의 오프스크린 문서 방식으로 간다.
- 휴대폰에서는 전사를 쓰지 못해도 된다. 다운로드처럼 휴대폰에서는 전사 버튼을 숨긴다.

## 4. 현재 구조 (바뀌기 전)

1. 화면이 `POST .../audio-link`(이번 학기) 또는 `POST /saved-videos/{id}/audio-link`를 호출해 서명 링크를 받는다.
2. `GET /audio/{token}`이 ffmpeg를 띄워 CDN mp4를 읽고 16kHz 모노 s16le PCM을 스트리밍한다(`app/lms/audio.py`, 서버 전체 `max_audio_streams`, 사용자당 2개).
3. `transcribe.js`의 `pumpAudio`가 PCM을 Float32로 바꿔 `READ_AHEAD_SECONDS`(60초)만큼만 앞서 워커에 넘긴다.
4. 끝나면 `X-Audio-Duration`(LMS 영상 길이)과 실제 길이를 비교해(`MIN_AUDIO_RATIO` 0.97) 끊김을 잡고, `PUT .../transcript`로 저장한다.

영상 주소는 `resolve_media_url`이 content.php XML에서 **mp4 주소만** 뽑는다(`app/lms/media.py:114`). mp4를 못 찾으면 "내려받을 수 없는 콘텐츠 형식"으로 거절하므로, 전사 대상은 모두 mp4다.

## 5. 새 구조

```
[화면] --POST direct-download--> [API] (주소만 발급, 기존과 동일)
   |
   |  fetch(Range) — 확장 프로그램 규칙이 Referer/Origin과 CORS 헤더를 붙인다
   v
[학교 CDN mp4] --> [오디오 추출 Worker: mp4 분해 → AAC 디코딩 → 모노·16kHz]
                         |
                         v  Float32 조각 (지금과 같은 형식)
                   [whisper.worker.js] --> [화면] --PUT transcript--> [API]
```

### 5.1 영상 주소 발급 (서버 변경 없음)

이미 확장 다운로드가 쓰는 `POST /courses/{c}/videos/{i}/direct-download`, `POST /saved-videos/{id}/direct-download`를 그대로 쓴다. 이 API는 CDN 주소(`url`)와 다운로드 완료 확인용 `receipt`를 준다. 다운로드 이력은 `POST /downloads/complete`를 불러야 남으므로, 전사는 `receipt`를 버리고 완료 보고를 하지 않는다. 지금도 전사는 다운로드 이력에 남지 않는다.

### 5.2 확장 프로그램: 사이트에 CDN 읽기 허용

`worker.js`의 `configureRules()`에 규칙을 하나 더한다.

- 조건: `initiatorDomains: ["daunload.cloud"]`, `requestDomains`는 **영상이 실제로 있는 호스트만**, `resourceTypes: ["xmlhttprequest"]`. 기본은 `commonscdn.com`이다. 서버는 `ssu.ac.kr` 영상 주소도 허용하므로(`app/lms/media.py`의 `ALLOWED_MEDIA_HOSTS`), 구현 전에 운영 DB의 `saved_videos.media_url` 호스트 목록을 뽑아 `ssu.ac.kr` 아래 영상 호스트가 있으면 **그 하위 도메인만 정확히** 추가한다. `ssu.ac.kr` 전체는 열지 않는다. 목록 밖 호스트의 영상은 "이 영상은 텍스트 변환을 지원하지 않습니다"로 안내한다.
- 요청 헤더: 기존 규칙처럼 `Referer`, `Origin`을 Commons로 바꾼다.
- 응답 헤더: `Access-Control-Allow-Origin: https://daunload.cloud`, `Access-Control-Expose-Headers: Content-Range, Content-Length`.
- 기존 규칙 1·2(확장 자신이 시작한 요청)는 그대로 둔다.

`ping` 응답에 기능 표시를 더한다(예: `{ version: 1, sessions: true, queue: true, audio: true }`). `ping`을 읽는 곳은 `app/web/downloads.js`뿐이고 필드를 더하기만 하므로 기존 다운로드는 영향이 없다. 화면은 `audio`가 없으면 "확장 프로그램을 업데이트해 주세요"를 보인다. 확장 프로그램 버전을 올리고 스토어에 다시 낸다. 스토어 설명과 `privacy.html`에 "텍스트 변환 때 사이트가 학교 CDN 영상을 직접 읽도록 허용한다"는 문장을 넣는다.

**가장 먼저 확인할 위험(스파이크)**: 아래가 실제 Chrome에서 되는지 본 뒤 나머지를 만든다. 안 되면 5.6의 대안으로 간다.
1. DNR의 응답 헤더 수정이 CORS 검사보다 먼저 적용돼 페이지(및 페이지가 띄운 Worker)의 `fetch`가 성공한다.
2. `Range: bytes=N-M` 요청이 사전 요청(preflight) 없이 나가거나, 나가더라도 규칙으로 통과한다.
3. 현재 `host_permissions`(`*.ssu.ac.kr`, `*.commonscdn.com`)만으로 이 규칙이 동작한다. `daunload.cloud` 호스트 권한이 추가로 필요하면 **권한을 추가하지 않고 5.6으로 간다**(운영자 결정).

### 5.3 브라우저 오디오 추출

새 모듈 Worker(예: `app/web/audio.worker.mjs`)가 한 작업을 맡는다.

1. CDN mp4를 Range 요청으로 조각(예: 2~4MB)씩 받는다.
2. mp4box.js(2.4.1 고정, 테스트가 외부 네트워크 없이 돌도록 `app/web/vendor/`에 넣는다)로 오디오 트랙만 꺼낸다. 영상 트랙 샘플은 버린다.
3. WebCodecs `AudioDecoder`로 AAC를 디코딩한다(트랙의 `esds`로 `description` 구성).
4. 채널을 평균해 모노로, 16kHz로 리샘플링해 Float32 조각을 만든다.
5. 조각을 whisper 워커로 넘긴다. 지금 `pumpAudio`의 `READ_AHEAD_SECONDS` 역압(Whisper보다 60초 이상 앞서면 받기를 멈춤)을 그대로 지킨다.

**메모리 상한**: `moov`가 파일 끝에 있는 mp4도 있을 수 있다. 이때도 영상 전체를 쌓지 않는다. 앞부분에서 `moov`를 못 찾으면 끝부분을 Range로 먼저 읽어 `moov`를 얻은 뒤, 샘플 표의 오프셋 순서대로 받아 처리한다.

**주소 만료 대비**: ffmpeg는 연결 하나로 끝까지 읽었지만, 새 방식은 Whisper 속도에 맞춰(CPU 모드면 강의 길이보다 오래) Range 요청을 여러 번 나눠 보낸다. 중간에 403/410이 오면 `direct-download`로 주소를 **한 번** 다시 받아 같은 바이트 위치부터 이어 받는다. 그래도 실패하면 위 오류 문구로 끝낸다. (저장 영상 기능이 주소를 오래 재사용하는 것으로 보아 만료는 드물 것으로 짐작하지만 보장되지 않는다.)

**끊김 검사**: 서버의 `X-Audio-Duration` 헤더가 없어지므로, 기준 길이는 목록의 `duration`(LMS 값)을 쓰고 없으면 mp4의 오디오 트랙 길이를 쓴다. `MIN_AUDIO_RATIO` 검사는 그대로 둔다.

**오류 문구** (사용자에게 보이는 말):
- 확장 프로그램 없음/구버전: 기존 다운로드 안내와 같은 설치·업데이트 안내.
- 오디오 트랙 없음: "이 영상에는 소리 트랙이 없습니다."
- 디코딩 불가 코덱(`AudioDecoder.isConfigSupported` 실패): "이 브라우저가 이 영상의 소리 형식을 읽지 못합니다."
- CDN 403/404/410: "학교 서버가 영상을 주지 않았습니다. 목록을 새로고침한 뒤 다시 시도하세요."

### 5.4 화면 변경

- 전사 버튼의 활성 조건: 지금은 `v.audioPath`가 있으면 보인다. 이를 "영상 주소를 받을 수 있는 항목"(다운로드의 `linkPath`가 `direct-download`인 항목과 같은 조건)으로 바꾼다.
- 휴대폰에서는 전사 버튼과 "전체 텍스트로 변환" 버튼을 숨긴다. 다운로드가 쓰는 `phone` 판정(`app.js`의 `!phone` 조건)을 그대로 쓴다. 이미 만든 텍스트 보기는 휴대폰에서도 된다.
- 전사를 누를 때 확장 프로그램 확인(`ping`)을 먼저 하고, 없으면 다운로드와 같은 설치 안내를 띄운다(`app.js:1243`의 기존 안내 재사용).
- 작업 패널의 진행률, 대기열, 취소, 로그아웃 처리(`epoch`)는 그대로다. 취소하면 진행 중인 CDN `fetch`도 끊는다.
- 저장 시 `model` 값 형식(`whisper-small/<device>`)은 그대로다.

### 5.5 지우는 것

- 서버: `GET /audio/{token}`, `POST .../audio-link` 두 개, `_Slot`의 오디오 분기와 `_audio_streams`, `app/lms/audio.py`, `Settings.max_audio_streams`, 다운로드 토큰의 `kind: "audio"` 처리.
- `Dockerfile`의 ffmpeg 설치.
- 테스트: `tests/test_audio.py` 삭제, `tests/test_access_api.py`의 오디오 경로 사례 제거, `tests/test_transcribe_web.py` 갱신.
- 문서: README의 ffmpeg 설치·API 표·텍스트 변환 설명, `docs/hetzner-cpx12-deployment.md`·`docs/hetzner-cpx02-deployment.md`의 오디오 추출 관련 절차와 문구.

배포 순서: 확장 프로그램 새 버전이 스토어에 올라간 뒤 서버를 배포한다. 그 사이 구버전 확장 사용자는 "업데이트해 주세요" 안내를 본다.

### 5.6 스파이크 실패 시 대안

DNR로 CORS를 열 수 없거나 새 호스트 권한이 필요하면, 확장 프로그램이 **오프스크린 문서**(`chrome.offscreen`)에서 CDN을 읽고 오디오 추출까지 해서 16kHz Float32 조각만 페이지로 넘긴다(브리지 `postMessage` 경로). 조각은 초당 64KB 수준이라 메시지로 넘겨도 부담이 작다. 이 경우 오디오 추출 코드는 확장 프로그램 안에 둔다. 서버 쪽 변경과 화면의 나머지 흐름은 같다.

## 6. 내가 정한 것 (운영자 확인 전)

- 주소 발급은 새 API를 만들지 않고 `direct-download`를 재사용한다.
- mp4 분해는 mp4box.js, 디코딩은 WebCodecs. `decodeAudioData`(파일 전체 필요)와 ffmpeg.wasm(수십 MB, 느림)은 쓰지 않는다.
- 확장 규칙은 `commonscdn.com`만 연다.
- 서버 ffmpeg는 배포 즉시 완전히 지운다(기능 플래그로 남기지 않는다).

## 7. 구현자에게 맡기는 것

- 리샘플링 방법(선형 보간, 구간별 `OfflineAudioContext` 등). Whisper 인식 품질이 지금과 비슷하면 된다.
- Range 조각 크기, mp4box.js 버전(2주 이상 된 고정 버전).
- 오디오 추출 Worker와 whisper 워커를 하나로 합칠지 나눌지. 나누면 조각은 페이지를 거치거나 `MessageChannel`로 직접 잇는다.
- 테스트 방식. 최소한 짧은 mp4 표본으로 "추출 → 16kHz 길이 확인"과 확장 없음 안내를 검사한다.

## 8. 확인 방법

- 실제 Chrome + 새 확장 프로그램으로 50분 이상 강의 1개를 전사해, 탭 메모리가 일정하고 결과 길이가 영상 길이의 97% 이상인지 본다.
- 전사하는 동안 서버 `docker stats`에서 api CPU가 평소 수준인지 본다.
- 확장 프로그램을 끄면 설치 안내가 뜨고, 구버전이면 업데이트 안내가 뜨는지 본다.
- 학교 사이트를 일반 탭에서 쓸 때 요청 헤더가 바뀌지 않는지 본다(규칙의 `initiatorDomains` 범위).
