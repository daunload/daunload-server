# SSU LMS 서버

숭실대 LMS(lms.ssu.ac.kr, Canvas + LearningX)에서 수강 과목의 강의·과제·퀴즈·공지·출석을 모아 보여주고,
기간 안의 미수강 온라인 강의를 헤드리스 Chrome으로 **실제 재생**(1배속, 끝까지)한 뒤 LMS에서 완료로 바뀌었는지 확인하는 서버.
로그인·조회 흐름은 [chlwhdtn03/LMS-API](https://github.com/chlwhdtn03/LMS-API)(Apache-2.0)를 Python으로 옮겼다.

> ⚠️ 실제로 재생하더라도 본인이 시청하지 않은 수강은 학칙상 문제가 될 수 있다. 자동 재생은 과목별 opt-in이며 기본값은 꺼짐이다.
> 여러 사용자의 학교 계정 비밀번호를 (암호화해) 보관하므로 운영자가 보호 책임을 진다.

## 구조

```
app/lms/        LMS 연동 (네트워크·브라우저만, DB 없음)
  auth.py         SSO → LMS 게이트웨이 → Canvas 로그인, RSA 복호화
  client.py       조회 API (learningx=Bearer, /api/v1=쿠키), 페이지네이션, 세션 만료 시 재로그인
  todo.py         미수강 영상 / 미제출 과제·퀴즈 판정
  player.py       Playwright + Chrome 재생기 (1배속 강제, ended까지 재생, 완료 재확인)
  batch.py        일괄 재생: 순서 정렬, 마감 점검, 재시도, 잠김 처리, 취소, 이어가기
app/services/   DB와 엮는 계층: 자격증명·세션 캐시, 동기화, 일괄 재생 실행, 알림
app/api/        FastAPI 라우터 (JWT)
app/worker/     arq 워커 (주기 동기화, 재생, 마감 알림, 재시작 시 이어가기)
migrations/     Alembic
```

## 실행

```bash
cp .env.example .env    # FERNET_KEY, JWT_SECRET 채우기
uv sync
uv run playwright install chrome   # Chrome이 이미 설치돼 있으면 생략 가능
```

**Docker (API·Postgres·Redis)**

```bash
docker compose up -d            # API: http://localhost:8000/docs (alembic upgrade 자동 실행)
```

**워커**
- linux/amd64 서버: `docker compose --profile amd64 up -d` (컨테이너 안에 Google Chrome 포함)
- Apple Silicon Mac: Chrome이 amd64 전용이라 컨테이너에서는 에뮬레이션으로 죽는다. 호스트에서 실행한다.
  ```bash
  ./scripts/run-worker.sh   # 워커가 종료되면(예: Redis 재시작) 5초 뒤 자동으로 다시 띄운다
  ```

Playwright 기본 Chromium은 H.264/AAC를 재생하지 못하므로 `PLAYBACK_BROWSER_CHANNEL=chrome`(기본값)을 유지한다.

## 웹 대시보드

API 서버 주소(`http://localhost:8000/`)로 접속하면 대시보드가 열린다. 별도 빌드 없이 `app/web/`의 정적 파일을 FastAPI가 함께 내보낸다.

- **학습 현황 (기본 첫 화면)**: 과제·퀴즈와 미수강 강의를 나란히 표시한다. 제출 완료/미제출/마감 초과, 제출률, 남은 강의 시간과 LMS 바로가기를 화면 전환 없이 확인한다. 과목 선택과 검색은 두 목록에 함께 적용된다.
- 제출률은 `제출 완료 / (제출 완료 + 미제출)`이며 면제·상태 미확인은 제외해 별도로 표시한다. 마감 없는 과제와 아직 열리지 않은 미제출 과제도 포함한다. 강의 시간은 남은 영상의 전체 길이 합계이며 실제 남은 재생 시간과 다를 수 있다.
- 데스크톱은 두 열, 모바일은 두 목록을 세로로 배치한다. 긴 목록은 각 목록 안에서 스크롤하며, 제출 완료 내역과 7일 일정은 필요할 때 펼친다.
- **학습 요약**: 24시간 내 마감, 남은 할 일, 미수강 영상, 수강 과목을 카드로 확인한다.
- **할 일 탐색**: 종류·24시간 내 마감 필터와 제목·과목명 검색. 요약 카드를 누르면 해당 필터로 바로 이동한다. 모바일에서는 **전체 메뉴**를 펼쳐 화면을 선택한다. 화면 주소(`#todos`, `#courses` 등)와 브라우저 뒤로 가기를 지원한다.
- **반응형 화면**: 할 일을 일정표보다 먼저 보여주고, 과목·설정에서는 요약을 숨긴다. 모바일 일정은 접힌 상태로 시작하며, 펼치면 긴 제목까지 읽을 수 있는 날짜별 목록으로 표시한다.
- **앞으로 7일 레일**: 날짜별 마감(영상·과제·퀴즈). 재생 중이거나 미리보기한 재생 계획이 있으면 예상 구간을 띠로 겹쳐 그리고,
  마감 전에 끝나지 않을 영상은 빨간 테두리로 표시한다.
- 할 일(종류 필터), 과목(주차별 출석표, 자동 수강 켜기), 공지(과목 필터, 본문은 텍스트로만 표시),
  영상 재생(범위 선택 → 미리보기 → 시작, 진행률 5초마다 갱신, 중지), 알림 채널, LMS 계정 등록.

## 실계정 확인 (개발용 CLI, `.env`의 `LMS_ID`/`LMS_PASSWORD` 사용)

| 단계 | 명령 | 확인할 것 |
|---|---|---|
| M1 로그인 | `uv run python -m app.lms.auth` | 이름·학과 출력, Bearer 토큰 확보 |
| M2 조회 | `uv run python -m app.lms.client` | 과목·출석·할 일·공지가 LMS 화면과 일치 |
| M3 플레이어 관찰 | `uv run python -m app.lms.player inspect <view_url>` | 창이 뜸. 프레임·`<video>`·버튼 클래스·진도 요청 출력 → `player.py` 상단 셀렉터와 비교 |
| M3 재생 | `uv run python -m app.lms.player play <course_id>` | 가장 짧은 미수강 영상 1개 재생 후 "완료 확인: 성공" |
| M3.5 일괄 | `uv run python -m app.lms.batch preview --course <id> [--module <id>]` | 순서가 LMS 화면과 같은지, 마감 경고 |
| | `uv run python -m app.lms.batch run --course <id> --module <id>` | 모든 항목 `done` |
| 영상 받기 | `uv run python -m app.lms.download list --course <id>` | 주차별 영상, 크기, 공개 여부 |
| | `uv run python -m app.lms.download get --course <id> [--module <id>] [--item <id>] [--out 폴더]` | `downloads/과목명/`에 mp4 저장. 끊기면 다시 실행해 이어받기 |

`view_url`은 M2 출력 대신 `GET /courses/{id}/modules` 응답의 `item_content_data.view_url`에서 얻을 수 있다.

## API 요약 (`/docs`에서 전체 확인)

| | |
|---|---|
| `POST /auth/login` (form: `username`=학번, `password`=학교 비밀번호, 첫 로그인만 `agree_terms=true`) | JWT 발급. 처음이거나 비밀번호가 바뀌었으면 실제 LMS 로그인으로 확인하고 암호화 저장·첫 동기화 예약, 그 외에는 저장된 해시로 확인(LMS 접속 없음). 학번별 실패 5회/10분 제한 |
| `GET /me` · `DELETE /me` · `GET /lms/credentials` | 내 정보 / 계정과 모든 데이터 삭제 / LMS 연결 상태 |
| `POST /sync` | 즉시 동기화 |
| `GET /courses` · `/courses/{id}/attendance` · `/courses/{id}/modules` (실시간) | |
| `PUT /courses/{id}/auto-play` `{enabled}` | 자동 수강(동기화 때 새 미수강 영상을 자동으로 묶어 재생) |
| `GET /week` | 이번 주(월~일, `TIMEZONE` 기준) 들어야 할 강의, 내야 할 과제·퀴즈, 이번 주에 놓친 과제 |
| `GET /todos?kind=video\|assignment\|quiz` · `GET /announcements?since=` | 마감순 할 일, 공지 |
| `GET /playback/batches/preview?scope=module\|course\|all\|week&course_id=&module_id=` | 묶일 영상, 순서, 예상 시각, 마감 경고 |
| `POST /playback/batches` · `POST /playback/items/{module_item_id}` | 일괄 / 단일 재생 시작 (사용자당 동시에 1개) |
| `GET /playback/batches/{id}` · `DELETE /playback/batches/{id}` | 진행 상황 / 취소(재생 중 영상도 중단) |
| `GET /courses/{id}/videos` · `POST /courses/{id}/videos/{module_item_id}/download-link` · `GET /downloads/{token}` | 강의 영상 목록(실시간) / 본인 과목·확인된 영상만 10분 링크 발급 / 서버 저장 없이 학교 CDN에서 중계(Range 이어받기, 사용자당 동시 2개) |
| `POST /notifications/channels` `{kind: discord\|telegram, ...}` · `POST .../{id}/test` | 알림 채널 |

알림: 새 공지, 마감 24시간·3시간 전, 일괄 재생 결과, LMS 로그인 실패. 첫 동기화 때 기존 공지는 알리지 않는다.

## 테스트

```bash
uv run pytest            # 62개. test_player는 실제 Chrome으로 가짜 플레이어 페이지를 재생한다
uv run pytest -m "not browser"
```

## 알려진 한계

- 강의 영상 내려받기는 soongsil-lms-downloader의 방식(content.php → mp4)을 옮겼다. 개인 복습용이며, 교수의 다운로드 허용 여부를
  알려 주는 값이 LMS·Commons 어디에도 없어 구분하지 못한다. `readystream` 형식은 확인하지 못해 목록에서 제외했다.
- 동기화할 때 열린 영상의 mp4 주소를 `saved_videos`에 저장해 두고(한 번에 최대 20개씩), 시청기간이 끝나거나 지난 학기 과목이 되어도
  본인이 그 주소로 받을 수 있게 한다(`/saved-videos`). 영상 파일은 저장하지 않는다. 지금 CDN은 Referer만 확인하지만,
  학교가 주소에 서명을 붙이거나 파일을 지우면 저장된 주소는 쓸 수 없게 된다(410으로 알린다).

- Commons 플레이어의 실제 DOM은 실계정으로만 볼 수 있다. `player.py`의 `PLAY_SELECTORS`/`CONFIRM_SELECTORS`는
  추정값이며, `<video>`를 모든 프레임에서 찾아 직접 재생하므로 셀렉터가 틀려도 동작할 가능성이 높지만 `inspect`로 확인해야 한다.
- 선수 조건 잠김은 API로 알 수 없어 "같은 과목에서 실패 후 다음 항목의 영상을 찾지 못함"으로 판단한다.
- 로그인은 HTML 문자열(`iframe.src`, `loginCryption`) 파싱에 의존한다. 바뀌면 `LmsParseError`가 난다.
