# 수업자료 다운로드 · 다운로드 이력 · 썸네일 · 브라우저 Whisper 전사 설계

- 작성일: 2026-10-29
- 참고: soongsil-lms-downloader v1.7.0(수업자료), v1.6.0(로컬 Whisper), 위키 파일 다운로드, 내 라이브러리

## 목표

| # | 기능 | 성공 기준 |
|---|---|---|
| ① | commons 파일 항목(pptx/pdf/zip 등) 다운로드 | 과목의 "수업자료"에서 주차별로 보고 받을 수 있다 |
| ② | 위키 페이지 첨부파일 다운로드 | 위키 본문에 첨부된 Canvas 파일이 ①과 같은 목록에 나온다 |
| ③ | 다운로드 이력 | 받은 영상·자료에 표시가 붙고 "안 받은 것만" 골라 받을 수 있다 |
| ④ | 영상 썸네일 | 강의 영상 목록에 썸네일이 보인다(실패하면 숨김) |
| ⑤ | Whisper 전사 | 사용자 브라우저에서 Whisper로 전사하고, 결과를 서버에 저장해 어느 기기에서든 보고 내려받는다 |

### 하지 않는 것

- 서버에서 Whisper/AI를 실행하지 않는다. Gemini 등 외부 AI API를 쓰지 않는다.
- 파일을 서버에 저장하지 않는다(지금의 중계 방식 유지). 오디오도 저장하지 않고 스트리밍만 한다.
- 자동 전사(동기화 때 자동 실행), 전사 결과 요약, 전체 검색은 이번 범위가 아니다.
- 서버에서 zip으로 묶어 주는 일괄 다운로드는 하지 않는다(브라우저 큐로 처리).

## 전체 구조

```
app/lms/materials.py      (신규) 모듈 → 수업자료 목록. commons 파일 + 위키 첨부 추출
app/lms/media.py          CourseVideo에 thumbnail_url 추가, 허용 호스트 검사 재사용
app/lms/audio.py          (신규) ffmpeg로 mp4 → 16kHz 모노 PCM 스트림
app/api/routes_media.py   다운로드 중계를 kind별로 일반화, 자료·오디오 링크 발급, 이력 기록
app/api/routes_transcripts.py (신규) 전사 결과 저장·조회·삭제·.txt
app/services/downloads.py (신규) 다운로드 이력 기록·조회
app/db/models.py          DownloadRecord, Transcript 추가 (+ Alembic 마이그레이션 1개)
app/web/                  수업자료 탭, 이력 배지·필터·받기 큐, 썸네일, 전사 UI
app/web/whisper.worker.js (신규) transformers.js Whisper Web Worker
app/web/transcribe.js     (신규) 전사 큐·진행률·저장
```

## ①② 수업자료

### 목록 추출 (`app/lms/materials.py`)

```python
@dataclass(frozen=True)
class CourseMaterial:
    key: str            # "c{module_item_id}" (commons) | "f{canvas_file_id}" (위키 첨부)
    kind: str           # "commons_file" | "canvas_file"
    course_id: int
    module_id: int
    module_title: str
    module_position: int
    item_position: int
    title: str          # 항목/위키 페이지 제목
    file_name: str      # 저장할 파일 이름(확장자 포함)
    source_id: str      # commons content_id | canvas file id
    size: int | None

async def course_materials(client: LmsClient, course: Course, modules: list[Module]) -> list[CourseMaterial]
```

- **commons 파일**: `content_data.item_content_type == "commons"`이고 `item_content_data.content_type == "file"`인 항목이다. `content_id`가 없거나 `"not_open"`이면 건너뛴다. 파일 이름은 `item_content_data.file_name`이고, 없으면 제목을 쓴다.
  `ItemContentData`에 `file_name: str | None` 필드를 추가한다.
- **위키 첨부**: 모듈 항목의 `content_type == "wiki_page"`이고 페이지 slug(`content_data.url` 또는 Canvas `page_url`)가 있으면 `GET /api/v1/courses/{id}/pages/{slug}`(쿠키 인증)의 `body`에서 `a.instructure_file_link`를 찾는다.
  - `data-api-endpoint`(`…/api/v1/courses/{cid}/files/{fid}`) 또는 `href`(`/courses/{cid}/files/{fid}…`)에서 파일 ID를 뽑는다. 파일 ID를 못 뽑은 링크는 버린다.
  - 다른 과목의 파일을 가리키는 링크(`cid`가 다름)는 버린다.
  - 같은 파일이 여러 페이지에 있으면 한 번만 넣는다(`key` 기준, 먼저 나온 주차).
  - 이름은 `title` 속성, 없으면 링크 텍스트를 쓴다. HTML 파싱은 표준 라이브러리 `html.parser`로 한다.
  - 페이지 하나를 가져오다 실패하면 그 페이지만 건너뛰고 경고 로그를 남긴다.
- 파일 형식은 거르지 않는다.
- 정렬: `(module_position, item_position)`, 위키 첨부는 본문 순서를 유지한다.
- 실제 응답에서 commons 파일 항목의 `content_type` 값과 위키 항목의 slug 필드를 확정해야 한다. 개발용 CLI로 실계정 확인 후 fixture를 만든다(아래 "실계정 확인").

### 다운로드 주소

| kind | 주소 | 인증 |
|---|---|---|
| commons_file | `https://commons.ssu.ac.kr/index.php?module=xn_media_content2013&act=dispXn_media_content2013DownloadContent&content_id={id}` | `COMMONS_HEADERS`(Referer) |
| canvas_file | `https://canvas.ssu.ac.kr/courses/{cid}/files/{fid}/download?download_frd=1` | 사용자의 LMS 세션 쿠키(`lms_client_for`) |

- 리다이렉트를 따라간다. **최종 호스트**를 허용 목록으로 검사한다. 영상용 `ALLOWED_MEDIA_HOSTS`와 별개로 `ALLOWED_FILE_HOSTS`를 두고, 초기값은 `ssu.ac.kr`, `commonscdn.com`이다. Canvas가 외부 스토리지(예: instructure 파일 도메인)로 보내면 실계정 확인 후 목록에 추가한다.
- `LmsClient`에 쿠키 세션으로 스트리밍 요청을 보내는 `stream_file(url, headers)`를 추가한다(내부 `_http` 직접 노출 대신).

### API

| | |
|---|---|
| `GET /courses/{id}/materials` | 수업자료 목록(LMS에서 실시간 조회) + `downloaded_at` |
| `POST /courses/{id}/materials/{key}/download-link` | 목록을 다시 조회해 `key`가 있을 때만 10분 링크 발급 → 이력 기록 |

- 응답 모델 `MaterialOut`: `key, kind, module_id, module_title, title, file_name, size, downloaded_at`
- 목록에 없는 key는 404를 돌려준다(임의 파일 ID 거부, 영상과 같은 원칙).

### 중계 일반화 (`GET /downloads/{token}`)

토큰 데이터에 `kind`를 넣는다. 기존 토큰(영상)은 `kind`가 없으면 `"video"`로 본다.

| kind | 토큰 데이터 | 업스트림 |
|---|---|---|
| video | `content_id` 또는 `media_url`, `file_name` | 지금과 같음 |
| commons_file | `content_id`, `file_name` | commons 다운로드 주소 |
| canvas_file | `course_id`, `file_id`, `file_name` | Canvas 다운로드 주소, `lms_client_for(user_id)` 세션 |

- Range 이어받기, 사용자당 동시 2개, `Cache-Control: no-store`는 그대로 둔다. `media_type`은 업스트림 `content-type`을 따르고, 없으면 `application/octet-stream`을 쓴다.
- canvas_file은 DB 세션이 필요하므로 라우트가 `Db` 의존성을 받는다. 자격증명이 없거나 로그인에 실패하면 400을 돌려준다.

## ③ 다운로드 이력

### 테이블 `download_records`

| 컬럼 | |
|---|---|
| id | PK |
| user_id | FK users, CASCADE, index |
| lms_course_id | int |
| item_key | str(64): 영상은 `v{module_item_id}`, 자료는 `CourseMaterial.key` |
| kind | `video` / `commons_file` / `canvas_file` |
| title | str(500), 표시용 |
| first_at, last_at | datetime |
| count | int |

- `UniqueConstraint(user_id, lms_course_id, item_key)`
- **다운로드 링크를 발급할 때** upsert한다(서버는 브라우저 저장 완료를 알 수 없다. 이 한계는 README에 적는다). 저장된 영상(`/saved-videos/{id}/download-link`)도 `v{module_item_id}`로 기록한다.
- 오디오 링크(⑤)는 이력에 넣지 않는다.

### 응답·UI

- `VideoOut`, `SavedVideoOut`, `MaterialOut`에 `downloaded_at: datetime | None`(= `last_at`)을 붙인다. 과목 단위로 한 번에 조회한다(`services/downloads.py: downloaded_map(db, user_id, course_id) -> dict[str, datetime]`).
- 웹: 받은 항목에 ✓ 배지를 붙이고, "안 받은 것만" 토글과 주차별 "전체 받기"를 둔다.
- **전체 받기 큐**(브라우저): `<a download>`는 끝났는지 알 수 없어 동시 수를 지킬 수 없다. 그래서 `showDirectoryPicker()`로 폴더를 한 번 고르게 하고, 항목마다 링크를 발급해 `fetch` 응답을 그 폴더의 파일에 스트리밍으로 쓴다(메모리에 올리지 않음). 동시 2개(서버 제한과 같음), "n/m 완료" 진행 표시, 429를 받으면 3초 뒤 다시 시도(최대 5번), 중지 버튼을 둔다. 이 API가 없는 브라우저(Firefox, Safari, 모바일)에서는 "전체 받기" 버튼을 비활성화하고 이유를 보여준다. 개별 내려받기는 지금처럼 `<a download>`를 쓴다.

## ④ 썸네일

- `CourseVideo`와 `VideoOut`에 `thumbnail_url: str | None`(`item_content_data.thumbnail_url`)을 추가한다. `https` 주소만 내보내고 그 밖은 `None`으로 둔다.
- 웹: `<img loading="lazy" referrerpolicy="no-referrer">`로 표시하고, `onerror`면 숨긴다. 서버 중계는 하지 않는다.
- `saved_videos`에는 넣지 않는다(지난 학기 썸네일은 중요하지 않음).

## ⑤ 브라우저 Whisper 전사

### 흐름

```
[웹] 전사 버튼 → POST /courses/{cid}/videos/{mid}/audio-link (또는 /saved-videos/{id}/audio-link)
     → GET /audio/{token}  ── 서버: ffmpeg가 CDN mp4를 읽어 16kHz 모노 s16le PCM 스트리밍
     → 메인 스레드가 스트림을 읽어 Worker로 넘김
     → Worker(transformers.js Whisper)가 30초 창 단위로 전사, 진행률·부분 결과 보고
     → 완료: PUT /courses/{cid}/videos/{mid}/transcript  (text + segments)
```

### 서버: 오디오 스트림 (`app/lms/audio.py`, `GET /audio/{token}`)

- 토큰 데이터: `kind="audio"`, `content_id` 또는 `media_url`, `duration`. 영상 다운로드 링크와 **같은 소유권 검사**를 거친 뒤 발급한다(과목 목록에서 확인된 열린 영상만).
- 실행 명령:
  `ffmpeg -nostdin -loglevel error -headers "Referer: https://commons.ssu.ac.kr/\r\nOrigin: https://commons.ssu.ac.kr\r\n" -i <url> -vn -ac 1 -ar 16000 -f s16le pipe:1`
  - `<url>`은 `check_media_host`를 통과한 주소만 쓴다. 인자는 리스트로 넘긴다(셸을 쓰지 않음).
  - `asyncio.create_subprocess_exec`로 stdout을 64KB씩 스트리밍한다.
  - 응답이 끝나거나 클라이언트가 끊으면 프로세스를 종료(`terminate` → 5초 뒤 `kill`)한다.
- 응답: `application/octet-stream`, 헤더 `X-Audio-Sample-Rate: 16000`, `X-Audio-Duration: <초>`(진행률 계산용). 길이를 미리 알 수 없으므로 Range는 지원하지 않는다.
- 동시 실행 수는 다운로드와 같은 사용자당 카운터(2개)를 공유하고, 서버 전체 ffmpeg 수는 `MAX_AUDIO_STREAMS`(기본 4)로 제한한다. 넘으면 429를 돌려준다.
- ffmpeg가 없으면(`shutil.which` 실패) 503 "서버에 ffmpeg가 설치되어 있지 않습니다"를 돌려준다. ffmpeg가 0이 아닌 코드로 끝나면 stderr 끝부분을 로그로 남기고 스트림을 끊는다. 브라우저는 길이가 모자란 것으로 실패를 감지한다.
- 설치: Dockerfile에 `apt-get install -y ffmpeg`, README에 Mac 호스트용 `brew install ffmpeg`를 적는다.

### 브라우저: Worker (`app/web/whisper.worker.js`)

- 모듈 Worker로 `https://cdn.jsdelivr.net/npm/@huggingface/transformers@<고정 버전>`을 import한다. 버전은 참고 프로젝트의 4.3.1을 우선 쓰고, 정적 페이지에서 동작하지 않으면 동작하는 최신 4.x로 고정한다.
- 모델: `onnx-community/whisper-small`, `language: "korean"`, `task: "transcribe"`, `return_timestamps: true`.
  - WebGPU 어댑터가 있으면 `device: "webgpu"`, 인코더 fp16, 디코더 q4로 돌린다. 없거나 초기화에 실패하면 `device: "wasm"`, `dtype: "q8"`로 돌린다.
  - 모델 파일은 브라우저 Cache Storage에 남는다. 첫 실행 때 내려받기 진행률을 `model-progress`로 보고한다.
- 메시지 프로토콜:
  - in: `{type:"start", jobId, duration}`, `{type:"chunk", jobId, samples: Float32Array}`, `{type:"end", jobId}`, `{type:"cancel", jobId}`
  - out: `{type:"model-progress", percent}`, `{type:"progress", jobId, seconds}`, `{type:"segment", jobId, start, end, text}`, `{type:"done", jobId}`, `{type:"error", jobId, message, fatal}`
- 처리: 받은 샘플을 버퍼에 쌓고 30초(480,000 샘플)가 차면 그 창을 전사한다. 마지막 창이 아니면 끝 시각이 "창 길이 - 5초" 안에 있는 세그먼트만 내고, 다음 창은 마지막으로 낸 세그먼트의 끝에서 시작한다(창 끝에서 잘린 문장을 처음부터 다시 듣는다). 끊을 곳이 없을 만큼 긴 세그먼트는 25초에서 자르고 넘어간다. 규칙은 `app/web/whisper-window.mjs`에 있다. 세그먼트 시각은 전체 기준 절대 시각으로 바꾼다. `end`를 받으면 남은 버퍼를 전사한다. 메모리는 창 2개 분량만 쓴다.

### 브라우저: 큐와 UI (`app/web/transcribe.js`)

- 메인 스레드가 `fetch(/audio/{token})`의 `ReadableStream`을 읽어 Int16 → Float32로 바꾼 뒤 Worker로 보낸다(transferable). 홀수 바이트 경계를 처리한다. 브라우저 쪽에서 앞서 갈 수 있는 양은 약 60초로 제한한다(Worker가 따라올 때까지 읽기를 멈춤).
- **한 번에 하나**만 전사한다. 영상별 "전사" 버튼과 주차별 "전체 전사"를 대기열에 넣는다. 진행률(초/전체 초), 중지, 실패 항목 다시 시도를 둔다.
- 탭을 닫으면 진행 중인 작업은 사라진다. 진행 중에는 `beforeunload` 경고를 띄운다. 부분 결과는 저장하지 않는다.
- 완료되면 서버에 저장하고, 목록에 "전사됨" 배지를 붙인다. 미리보기 모달(타임스탬프 토글, 복사, .txt 받기)을 둔다.
- WebGPU가 없으면(wasm) "CPU로 실행해 느릴 수 있습니다"를 처음 한 번 안내한다.

### 서버: 전사 결과 저장

테이블 `transcripts`:

| 컬럼 | |
|---|---|
| id | PK |
| user_id | FK users, CASCADE, index |
| lms_course_id | int |
| module_item_id | int |
| course_name, title | str, 표시용 |
| text | Text, 전체 본문 |
| segments | JSON, `[{start, end, text}]` |
| model | str(100), 예: `whisper-small/webgpu` |
| duration | float, nullable |
| created_at, updated_at | datetime |

- `UniqueConstraint(user_id, lms_course_id, module_item_id)`. 다시 전사하면 덮어쓴다.

| API | |
|---|---|
| `PUT /courses/{cid}/videos/{mid}/transcript` | 저장(upsert). 본인 과목(`CourseRow`)인지 확인한다. 본문 최대 2MB(`text` + `segments`), segments 최대 20,000개. 제목·과목명은 본문에서 받는다(본인 데이터). |
| `GET /transcripts?course_id=` | 목록(본문 제외) |
| `GET /transcripts/{id}` | 본문 + 구간 |
| `DELETE /transcripts/{id}` | 삭제 |

- .txt 받기는 웹에서 `GET /transcripts/{id}` 본문을 Blob으로 저장한다(별도 엔드포인트 없음).

- `VideoOut`, `SavedVideoOut`에 `transcript_id: int | None`을 붙인다.
- `DELETE /me`는 CASCADE로 이력과 전사를 함께 지운다.

## 오류 처리 요약

| 상황 | 결과 |
|---|---|
| 목록에 없는 자료 key / 영상 | 404 |
| 아직 열리지 않은 영상의 오디오 링크 | 409 |
| 링크 만료 | 401 (목록에서 다시 받으라고 안내) |
| 동시 다운로드·오디오 초과 | 429 |
| 업스트림 호스트가 허용 목록 밖 | 422 |
| 업스트림 실패 | 502 |
| ffmpeg 없음 | 503 |
| 전사 본문이 너무 큼 | 413 |
| Worker 모델 로드 실패 | 그 작업과 대기열 모두 실패(`fatal`), 안내 표시 |

## 테스트

- `tests/test_materials.py`: commons 파일 항목 추출, 열리지 않은 항목 제외, 위키 HTML fixture에서 파일 링크 추출(`data-api-endpoint`/`href` 두 형태, 다른 과목 링크 제외, 중복 제거), 페이지 실패 시 건너뛰기. fixture: `tests/fixtures/modules_files.json`, `tests/fixtures/wiki_page.json`
- `tests/test_media_api.py`(기존 `test_media.py` 확장 가능): kind별 토큰 중계(respx), canvas_file이 세션 쿠키를 쓰는지, 허용 호스트 밖 리다이렉트 거부, 목록에 없는 key 404, 링크 발급 시 이력 upsert(count 증가), `downloaded_at` 응답
- `tests/test_audio.py`(`@pytest.mark.ffmpeg`, ffmpeg 없으면 skip): 기존 `tests/fixtures/player/clip.webm`을 로컬 HTTP로 제공 → 출력이 s16le 16kHz 길이와 맞는지, 클라이언트가 끊으면 프로세스가 종료되는지. ffmpeg 없을 때 503(모킹)
- `tests/test_transcripts.py`: upsert, 본인 과목이 아니면 404, 크기 제한 413, 목록·조회·삭제, 다른 사용자 접근 404, `transcript_id` 응답
- 마이그레이션: `alembic upgrade head`가 sqlite 테스트 DB와 Postgres에서 통과
- 브라우저 Whisper는 자동 테스트하지 않는다. 수동 확인: 짧은 영상 1개 전사(WebGPU·wasm 각각), 중지, 탭 새로고침 경고

## 실계정 확인 (개발용 CLI)

`uv run python -m app.lms.materials list --course <id>` / `get --course <id> --key <key> [--out 폴더]`

- commons 파일 항목의 `content_type` 실제 값, 위키 항목의 slug 필드 이름, Canvas 파일 다운로드의 최종 리다이렉트 호스트를 확인해 fixture와 `ALLOWED_FILE_HOSTS`를 확정한다.

## 문서

README에 다음을 추가한다: 수업자료, 다운로드 이력(링크 발급 기준), 썸네일, 전사(브라우저에서 실행, 탭 유지 필요, 첫 실행 때 모델 다운로드(수백 MB, 이후 브라우저 캐시), 서버 ffmpeg 필요), 새 API 표 행, 알려진 한계.
