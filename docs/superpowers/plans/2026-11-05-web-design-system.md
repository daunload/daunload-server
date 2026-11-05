# 웹 디자인 시스템과 사용성 개선 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `app/web`의 크기, 버튼, 문구, 확인, 진행 상황 규칙을 통일해 휴대폰에서도 버튼만 보고 동작을 예측할 수 있게 한다.

**Architecture:** 빌드 없는 순수 HTML/CSS/JS를 유지한다. 공통 UI는 `app/web/ui.js`에, 통합 진행 패널은 `app/web/tasks.js`에 둔다. 기존 화면은 이 함수들을 호출한다. 규칙은 `design-system/MASTER.md`에 문서로 남긴다.

**Tech Stack:** 순수 JavaScript, CSS, `<dialog>`, FastAPI 정적 파일 제공, pytest + Playwright(Chrome)

**Spec:** `docs/superpowers/specs/2026-11-05-web-design-system-design.md`

## 시작 전 필수 조건

작업 트리에 `app/web/*`, `tests/test_api.py` 등 커밋되지 않은 리디자인 변경이 있다. 각 Task의 커밋에 섞이지 않도록 먼저 사용자가 이 변경을 커밋하거나 정리해야 한다. 정리되기 전에는 Task 1을 시작하지 않는다.

기준선: `uv run pytest tests/test_week_web.py tests/test_web.py tests/test_videos_web.py tests/test_transcribe_web.py tests/test_calendar_web.py -q` → 37 passed (2026-11-05 확인)

## Global Constraints

- 색상, Pretendard·Black Han Sans 글꼴, 뚱이 콘셉트를 바꾸지 않는다.
- 프레임워크, 빌드 도구, Tailwind를 추가하지 않는다. 서버 API를 바꾸지 않는다.
- 제목 16px, 보조 정보 14px, 버튼과 모든 터치 영역 44px. 12px은 배지 같은 보조 요소에만 쓴다.
- 자주 쓰는 동작을 `⋯` 메뉴에 숨기지 않는다.
- 제목, 빈 화면, 로딩은 뚱이 말투를 유지하고, 버튼, 오류, 경고는 분명하게 쓴다.
- 일괄 재생 경고 문구는 항상 `직접 시청하지 않은 수강은 학칙상 문제가 될 수 있어요.`로 한다.
- 스킵 링크, `:focus-visible`, `prefers-reduced-motion`, `aria-*`를 유지한다.
- 사용자 입력과 서버 문자열은 `esc()` 또는 `textContent`로만 넣는다.

## Review Focus

1. 긴 강의명과 과목명이 320~375px에 들어오면, 버튼과 패널 줄이 줄바꿈되고 가로 스크롤이 생기지 않아야 한다. (Task 1, Task 4)
2. 5초 폴링 중 시트를 펼치고 키보드 포커스를 둔 상태여도, 시트가 닫히거나 포커스를 잃지 않아야 한다. (Task 4)
3. 작업이나 확인 창이 열린 채 로그아웃되거나 401을 받으면, 창이 닫히고 다음 사용자에게 이전 작업이 보이지 않아야 한다. (Task 3, Task 5)
4. `재생 중지` 확인 창이 열린 동안 재생이 끝나면, 확인을 눌러도 DELETE를 보내지 않고 오류도 나지 않아야 한다. (Task 4)
5. 실패 항목이 10개 이상이면 시트 안에서 스크롤되어 모든 버튼을 누를 수 있어야 한다. (Task 4)

---

### Task 1: 토큰, 크기, 버튼 CSS와 규칙 문서

**Files:**
- Modify: `app/web/style.css`, `.gitignore`
- Create: `design-system/MASTER.md`, `tests/test_design_system_web.py`

**Interfaces:**
- Produces: CSS 토큰 `--text-xs:12px`, `--text-sm:14px`, `--text-md:16px`, `--text-lg:18px`, `--text-xl:22px`, `--touch:44px`, `--z-nav:10`, `--z-top:15`, `--z-skip:30`, `--z-panel:35`, `--z-toast:40`. 버튼 클래스 `.btn`, `.btn.primary`, `.btn.danger`, `.btn.ghost`, `.btn.link`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_design_system_web.py`에 `test_web.py`의 `dashboard`와 같은 방식의 픽스처를 만든다. 강의 영상 데이터에는 2개 버튼 줄과 3개 버튼 줄을 함께 넣는다.

```python
async def test_mobile_touch_targets_and_type(page):  # 375x812
    # 할 일, 강의 재생, 내려받기·텍스트 변환, 알림, 계정, 캘린더를 차례로 연다
    sizes = await page.evaluate("""[...document.querySelectorAll('.btn,.side button,.cal-nav')]
      .filter(e => e.offsetParent).map(e => e.getBoundingClientRect().height)""")
    assert sizes and min(sizes) >= 44
    # .video-row .title 16px, .video-row .meta 14px

async def test_mobile_video_density(page):  # 375x812
    # 진행 바가 없을 때 뷰포트 안에 .video-row가 4개 이상
    # 버튼 2개인 줄의 버튼 top 값이 같다

async def test_long_titles_never_scroll_sideways(page):  # 320, 375, 768, 1024, 1440
    # 80자 강의명과 40자 주차명으로 scrollWidth <= innerWidth
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_design_system_web.py -q`
Expected: FAIL (`min(sizes)`가 44 미만)

- [ ] **Step 3: `style.css` 정리**

`:root`에 토큰을 추가한다. 앞쪽 `.btn` 정의(103~122행)와 테마 영역 정의(528~539행)를 합쳐 한 곳에 둔다. 합친 결과의 모양은 지금 화면에 보이는 뒤쪽 정의를 따른다. `.btn`은 `white-space: normal`로 긴 문구를 줄바꿈한다. `.btn.small`과 `.btn.link`의 `min-height`도 `var(--touch)`로 맞춘다. `font-size` 값을 토큰으로 바꾸고 z-index도 토큰을 쓴다. `.row-actions`는 줄바꿈을 허용한다. 760px 이하에서 `.video-row`는 세로로 쌓는다.

- [ ] **Step 4: `.gitignore`와 `design-system/MASTER.md` 작성**

`.gitignore`에 `.superpowers/`를 추가한다. MASTER.md에는 토큰 표, 버튼 4종의 사용 기준(스펙 5.4), 확인 기준(5.3), 문구 규칙(5.5, 5.6), 완료 체크(8장)를 적는다. 예제 코드는 뒤 Task의 `confirmDialog`, `icon`을 그대로 쓴다.

- [ ] **Step 5: 전체 웹 테스트 통과 확인**

Run: `uv run pytest tests/test_design_system_web.py tests/test_week_web.py tests/test_web.py tests/test_videos_web.py tests/test_transcribe_web.py tests/test_calendar_web.py -q`
Expected: 메뉴 이름을 쓰는 새 테스트를 제외하고 PASS. 새 테스트는 Task 2 전까지 `data-view`로 화면을 연다.

- [ ] **Step 6: 커밋**

```bash
git add app/web/style.css .gitignore design-system/MASTER.md tests/test_design_system_web.py
git commit -m "feat(web): add design tokens and 44px touch targets"
```

### Task 2: 문구와 아이콘 통일

**Files:**
- Create: `app/web/ui.js`
- Modify: `app/web/index.html`, `app/web/app.js`, `app/web/week.js`, `app/web/transcribe.js`, `README.md`, `docs/chrome-download-extension.md`
- Test: `tests/test_week_web.py`, `tests/test_web.py`, `tests/test_videos_web.py`, `tests/test_transcribe_web.py`

**Interfaces:**
- Produces: `icon(name: "external" | "download") -> string` (`aria-hidden` SVG 문자열), `PLAYBACK_WARNING: string`. `ui.js`는 `index.html`에서 다른 스크립트보다 먼저 불러온다.

- [ ] **Step 1: 기존 테스트의 이름을 새 이름으로 변경**

| 지금 | 변경 후 |
|---|---|
| `강의 N개 한 번에 듣기` | `이번 주 강의 N개 재생` |
| `모두 재생 (N개)` | `전체 강의 N개 재생` |
| `미리보기` / `미리보기 닫기` | `재생 계획 보기` / `계획 닫기` |
| `N개 영상 재생 시작` | `N개 재생 시작` |
| `{제목} 제출하기 (새 탭)` | `{제목} LMS에서 제출 (새 탭)` |
| `{제목} 제출하러 가기` | `{제목} LMS에서 제출` |
| `새로고침이나 하자` | `LMS 동기화` |
| `목록 새로고침` | `목록 다시 불러오기` |
| `전사` / `다시 전사` / `전사 중` | `텍스트로 변환` / `다시 변환` / `변환 중` |
| `전사됨 보기` | `텍스트 보기` |
| `전체 전사 (N)` | `{주차} N개 텍스트로 변환` |
| `전체 받기 (N)` | `{주차} N개 내려받기` |
| 메뉴 `영상 재생` | `강의 재생` |
| 메뉴와 제목 `강의 영상` | `내려받기·텍스트 변환` |
| 요약 카드 `↗` | `›` |

새 검증 2개를 추가한다.

```python
async def test_external_buttons_show_new_tab_icon(page):
    # 제출 링크와 '학교 LMS' 링크 안에 svg[data-icon="external"]가 있다
async def test_dashboard_has_one_primary_button(page):
    # 첫 화면의 보이는 .btn.primary는 1개, '전체 강의 N개 재생'은 일반 버튼
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_week_web.py tests/test_web.py tests/test_videos_web.py tests/test_transcribe_web.py -q`
Expected: 새 이름을 찾지 못해 FAIL

- [ ] **Step 3: 코드의 문구, 아이콘, 버튼 종류 변경**

위 표대로 `app.js`, `week.js`, `transcribe.js`, `index.html`을 바꾼다. 사용자에게 보이는 나머지 `전사` 문구도 `텍스트 변환`으로 바꾼다. `VIEW_NAMES.videos`와 `VIEW_NAMES.playback`을 함께 바꾼다. `내려받기` 버튼에 다운로드 아이콘, 새 탭 링크와 일정 칩에 새 탭 아이콘을 넣는다. `#dashboard-play-all`에서 `primary`를 뺀다.

- [ ] **Step 4: 문서의 이름 변경**

`README.md`와 `docs/chrome-download-extension.md`의 버튼 이름을 표에 맞춘다.

- [ ] **Step 5: 통과 확인**

Run: 위 Step 2 명령과 `tests/test_design_system_web.py`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add app/web README.md docs/chrome-download-extension.md tests
git commit -m "feat(web): make button labels state scope and result"
```

### Task 3: 공통 확인 창

**Files:**
- Modify: `app/web/ui.js`, `app/web/index.html`, `app/web/style.css`, `app/web/app.js`, `app/web/week.js`, `app/web/transcribe.js`
- Test: `tests/test_confirm_web.py`, `tests/test_week_web.py`, `tests/test_transcribe_web.py`

**Interfaces:**
- Produces:
  ```ts
  confirmDialog({ title: string, body?: string[], warnings?: string[], confirmLabel: string,
                  danger?: boolean, requireText?: string, requireLabel?: string }): Promise<boolean>
  closeConfirmDialog(): void  // 열린 창을 false로 닫는다. logout()에서 호출
  ```
  모든 문자열은 `textContent`로 넣는다. 창은 `index.html`의 `<dialog id="confirm-dialog">`이다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
async def test_week_play_confirms_with_warning_then_starts(page):
    # '이번 주 강의 2개 재생' 클릭 → dialog에 '학칙' 포함, calls == []
    # '2개 재생 시작' 클릭 → calls == [POST scope=week]
async def test_all_play_confirms_with_warning(page):
    # '전체 강의 2개 재생' 클릭 → dialog에 '학칙' 포함, 확인 후 POST scope=all
async def test_dialog_focus_escape_and_return(page):
    # 첫 포커스는 '취소', Escape로 닫으면 calls == [], 포커스는 원래 버튼
async def test_double_open_shows_one_dialog(page):
    # 버튼을 두 번 빠르게 클릭해도 오류 없이 창 1개, 확인하면 POST 1회
async def test_notification_channel_delete_confirms(page):
    # '삭제' 클릭만으로는 DELETE 없음, 확인 후 DELETE 1회
async def test_account_delete_requires_student_id(page):
    # 틀린 학번이면 '계정 삭제' disabled, 20260001 입력 시 활성화, 확인 후 DELETE /me
async def test_logout_with_open_dialog_closes_it(page):
    # 창이 열린 채 API가 401을 주면 창이 닫히고 로그인 화면이 보인다
async def test_plan_view_start_shows_warning_without_dialog(page):
    # '재생 계획 보기' 결과에 '학칙' 포함, 'N개 재생 시작' 클릭 시 창 없이 POST
async def test_transcript_delete_confirms_over_transcript_dialog(dashboard):
    # 텍스트 창의 '삭제' → 확인 창 → 확인 후 DELETE, 취소하면 텍스트 창이 유지된다
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_confirm_web.py -q`
Expected: FAIL (`#confirm-dialog` 없음)

- [ ] **Step 3: `confirmDialog` 구현**

`showModal()`을 쓴다. 이미 열려 있으면 같은 Promise를 돌려준다. 닫을 때 호출 직전의 `document.activeElement`로 포커스를 돌려주고, 그 요소가 사라졌으면 `#view`로 보낸다. `requireText`가 있으면 입력값을 trim해서 일치할 때만 확인 버튼을 활성화한다.

- [ ] **Step 4: 동작 연결**

- 이번 주 재생: `week.confirming`, `week.plan`, `.cta-confirm`을 없앤다. 미리보기 API 결과로 창을 연다.
  - 제목 `이번 주 강의 N개를 순서대로 재생할까요?`
  - 내용 `1배속 · 약 {시간} · {시각}쯤 끝나요`
  - 경고 `PLAYBACK_WARNING`, 위험 강의가 있으면 `마감 전에 끝나지 않는 강의가 N개 있어요`
  - 확인 `N개 재생 시작`
- 전체 재생: 제목만 `전체 강의 N개를`으로 다르고 `state.dashboardPlan`을 쓴다.
- 재생 계획 화면: `planHtml`에 `.warning`으로 `PLAYBACK_WARNING`을 넣는다. 창은 띄우지 않는다.
- 알림 채널: 제목 `이 알림 채널을 삭제할까요?`, 확인 `알림 채널 삭제`, danger
- 텍스트 삭제: 제목 `변환한 텍스트를 삭제할까요?`, 확인 `텍스트 삭제`, danger
- 계정: `dataset.armed` 방식을 없앤다. 기존 안내문을 내용으로 쓰고, `requireText: state.me.student_id`, `requireLabel: "확인을 위해 학번을 입력하세요"`, 확인 `계정 삭제`, danger
- `logout()`에서 `closeConfirmDialog()`를 호출한다.

- [ ] **Step 5: 기존 테스트 수정과 통과 확인**

`test_week_web.py`의 `.cta-confirm` 검증과 재생 시작 테스트를 창 기준으로 바꾼다. `test_transcribe_web.py:82`의 삭제에 확인 단계를 넣는다.

Run: `uv run pytest tests/test_confirm_web.py tests/test_week_web.py tests/test_transcribe_web.py tests/test_web.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add app/web tests
git commit -m "feat(web): confirm risky actions in one shared dialog"
```

### Task 4: 하단 진행 패널 (재생, 텍스트 변환)

**Files:**
- Create: `app/web/tasks.js`, `tests/test_tasks_web.py`
- Modify: `app/web/index.html`, `app/web/style.css`, `app/web/app.js`, `app/web/week.js`, `app/web/transcribe.js`, `tests/test_week_web.py`, `tests/test_transcribe_web.py`

**Interfaces:**
- Consumes: `confirmDialog`
- Produces:
  ```ts
  renderTaskPanel(): void            // 상태가 바뀔 때마다 호출
  taskItems(): TaskItem[]            // { key, kind, title, pct|null, note, failed, detail?, actions: [{label, cls, run}] }
  failedPlaybackBatches(batches, dismissedIds, now): Batch[]
  cancelActivePlayback(): Promise<void>  // app.js. 확인 창 → DELETE → state.active 갱신
  ```
  DOM: `<aside id="task-panel">` 안에 `<button id="task-bar" aria-expanded>`와 `<div id="task-sheet">`. `#transcribe-panel`을 대체한다. `tasks.js`는 `ui.js` 다음에 불러온다. `body.has-task-bar`는 본문 아래 여백을 준다. CSS 변수 `--ad-inset`은 Task 6에서 값을 넣는다(기본 `0px`).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
async def test_bar_hidden_without_tasks(page):
    # 작업과 실패가 없으면 #task-panel 숨김, body에 has-task-bar 없음
async def test_bar_summarizes_and_sheet_controls_playback(page):
    # 재생 중이면 #task-bar에 '작업 1개 진행 중'
    # 펼치면 '강의 재생 0/2개', '창을 닫아도 서버에서 계속돼요', '재생 중지'
    # '재생 중지' → 확인 창 → DELETE
async def test_transcribe_failure_stays_until_retry_or_close(dashboard):
    # 실패 시 '실패 1개', '이 탭을 열어 두세요'는 실행 중에만
    # '다시 시도'는 작업을 다시 넣고, '닫기'는 그 항목만 없앤다
async def test_failed_batch_within_7_days_links_to_reason(page):
    # finished_at 1일 전, failed_count 1 → '실패 사유 보기' 클릭 시 #playback, 시트 닫힘
    # '닫기' 후 새로고침해도 사라진 상태
async def test_old_or_only_cancelled_batches_are_not_failures(page):
    # finished_at 8일 전 또는 cancelled + failed_count 0 → 패널 숨김
async def test_corrupt_dismissed_storage_is_ignored(page):
    # dismissed-playback-failures 값이 '{깨짐'이어도 pageerror 없이 패널이 그려진다
async def test_poll_keeps_sheet_open_and_focus(page):
    # 시트를 열고 '재생 중지'에 포커스, 폴링 1회 후에도 열림과 포커스 유지
async def test_batch_finishing_during_stop_dialog_sends_nothing(page):
    # 창이 열린 동안 폴링으로 done이 되면 확인 후 DELETE 없음, pageerror 없음
async def test_ten_failures_scroll_inside_sheet(page):  # 375x667
    # 시트 높이가 뷰포트 안이고, 마지막 '닫기'를 스크롤해 누를 수 있다
async def test_bar_does_not_cover_last_row(page):  # 375x812
    # 맨 아래로 스크롤하면 마지막 줄의 bottom <= #task-bar의 top
async def test_long_title_in_sheet_wraps(page):  # 320
    # 80자 제목에서도 scrollWidth <= innerWidth
async def test_first_screen_shows_summary_without_stop(page):
    # 재생 중 #week-panel에 '강의 재생 중 0/2개'와 '재생 현황 보기', '재생 중지' 없음
    # #dashboard-playback에 .dashboard-playback-progress 없음
async def test_playback_view_keeps_rows_without_stop(page):
    # #playback에 영상별 .steps 유지, #cancel-batch 없음
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_tasks_web.py -q`
Expected: FAIL (`#task-panel` 없음)

- [ ] **Step 3: `tasks.js` 구현**

- 요약: 진행 중이 있으면 `작업 N개 진행 중`, 실패가 있으면 ` · 실패 M개`를 붙인다. 실패만 있으면 `실패 M개`.
- 재생 줄: `state.active`. 제목 `강의 재생 {완료}/{전체}개`. 대기 중이면 `대기 {순번}번째`. 자동 수강 묶음도 포함한다.
- 재생 실패 기준: `queued`, `running`이 아니고, `finished_at`이 7일 이내이고, `failed_count > 0` 또는 `status === "failed"`. 닫은 ID는 `localStorage`의 `dismissed-playback-failures`에 JSON 배열로 저장하고, 파싱 실패는 빈 배열로 본다.
- 변환 줄: `tx.current`, `tx.queue`, 모델 내려받기 진행률. 중지 `변환 중지`(확인 없음, 일반 버튼).
- 변환 실패 줄: `tx.finished`의 실패 항목. `다시 시도`와 그 항목만 없애는 `닫기`.
- 다시 그릴 때 `aria-expanded`를 유지하고, 포커스된 버튼의 `data-action-key`를 기억했다가 복원한다.

- [ ] **Step 4: 기존 코드 연결과 제거**

`renderTxPanel` 호출을 `renderTaskPanel`로 바꾸고 `renderTxPanel`과 `#transcribe-panel`을 삭제한다. `startPolling`, `finishActive`, `loadCore`, `logout` 뒤에도 `renderTaskPanel()`을 호출한다. `cancelBatch`, `week-stop`, `#cancel-batch`, `.dashboard-playback-progress`를 삭제한다. `week.js`의 `.cta-progress`는 `강의 재생 중 {완료}/{전체}개` 요약과 `재생 현황 보기`만 남긴다. 토큰이 없으면 패널을 숨긴다.

- [ ] **Step 5: CSS**

760px 이하는 좌우 8px, 아래 8px의 플로팅 바와 `max-height: 80dvh`, 내부 스크롤이 있는 시트로 만든다. 761px 이상은 오른쪽 아래 `width: 320px` 패널로 만든다. `bottom`은 `calc(8px + var(--ad-inset))`를 쓴다. 토스트는 바 위에 표시한다. 시트에 전환 효과를 넣으면 `prefers-reduced-motion`에서 끈다.

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_tasks_web.py tests/test_week_web.py tests/test_transcribe_web.py tests/test_web.py -q`
Expected: PASS. 기존의 `.cta-progress`, `중지`, `#transcribe-panel` 검증은 새 DOM에 맞게 고친 뒤 통과해야 한다.

- [ ] **Step 7: 커밋**

```bash
git add app/web tests
git commit -m "feat(web): unify playback and transcription progress in a bottom panel"
```

### Task 5: 여러 개 받기를 패널로 이동

**Files:**
- Modify: `app/web/tasks.js`, `app/web/downloads.js`, `app/web/app.js`, `app/web/style.css`
- Test: `tests/test_videos_web.py`, `tests/test_tasks_web.py`

**Interfaces:**
- Consumes: `renderTaskPanel`, `taskItems`
- Produces:
  ```ts
  runBulkDownload(items): Promise<void>  // app.js. wireBulk와 재시도가 함께 사용
  // onUpdate의 stat에 failedItems: Item[]를 추가한다
  ```

- [ ] **Step 1: 실패하는 테스트 작성과 기존 테스트 수정**

`#bulk-status` 검증을 `#task-panel` 기준으로 바꾼다.

```python
async def test_bulk_progress_note_and_stop_in_panel(dashboard):
    # 실행 중 '다운로드 0/4개', 영상은 '이 탭과 다운로드 창을 열어 두세요'
    # 수업자료는 '이 탭을 열어 두세요'
    # '다운로드 중지'는 확인 창 없이 멈추고 #bulk-status는 DOM에 없다
async def test_bulk_failure_can_retry_only_failed_items(dashboard):
    # 4개 중 1개 실패 → '다운로드 실패 1개' 유지, '다시 시도'는 그 1개만 다시 요청
    # 성공하면 줄이 사라지고 ✓ 받음 표시
async def test_bulk_success_clears_row_and_toasts(dashboard):
    # 모두 성공하면 패널 숨김, 토스트 '4개를 모두 받았어요'
async def test_single_material_not_in_panel(dashboard):
    # 단일 수업자료 내려받기는 패널에 나타나지 않는다
async def test_logout_clears_bulk_and_transcribe_tasks(dashboard):
    # 실행 중 로그아웃 → 중지되고, 다시 로그인해도 패널이 숨김 상태
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_videos_web.py tests/test_tasks_web.py -q`
Expected: FAIL

- [ ] **Step 3: 구현**

`startBulkDownload`의 `failed`에서 `stat.failedItems`에 항목을 넣는다. 항목 객체에 있는 `onDone`을 유지해 재시도 성공 시 받음 표시가 남게 한다. `MANAGER_CLOSED`로 멈춘 경우 오류 문구는 실패 줄에 보여 주고 `다시 시도`는 실패 항목에만 적용한다. `wireBulk`는 `runBulkDownload`를 호출한다. `renderBulkStatus`와 `#bulk-status`, `.bulk-status` CSS를 삭제한다. `logout()`에 `stopBulkDownload()`, `cancelTranscription()`, `tx.queue = []`, `tx.finished = []`를 추가한다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_videos_web.py tests/test_tasks_web.py tests/test_download_extension.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/web tests
git commit -m "feat(web): show bulk download progress and retries in the panel"
```

### Task 6: 피드백, 휴대폰 안내, 광고 겹침

**Files:**
- Modify: `app/web/ui.js`, `app/web/tasks.js`, `app/web/app.js`, `app/web/downloads.js`, `app/web/style.css`, `app/web/index.html`, `design-system/MASTER.md`
- Test: `tests/test_feedback_web.py`, `tests/test_videos_web.py`

**Interfaces:**
- Produces:
  ```ts
  toast(msg: string, bad?: boolean): void   // ui.js로 이동. 시그니처 유지
  clearToasts(): void
  withPending<T>(btn, label: string, fn: () => Promise<T>): Promise<T>
  skeletonRows(n: number): string
  isPhoneDevice(): boolean  // matchMedia("(hover: none) and (pointer: coarse)")
  syncAdInset(): void       // tasks.js
  // extensionRequest("ping") 시간 초과 시 Error.code === "EXTENSION_MISSING"
  ```

- [ ] **Step 1: 실패하는 테스트 작성**

```python
async def test_error_toast_stays_until_closed_success_expires(page):
    # 오류는 4초 뒤에도 보이고 '닫기'로 사라진다. 성공은 3.2초 뒤 사라진다
    # 오류가 떠 있을 때 성공이 와도 오류는 남는다. 같은 오류는 중복되지 않고, 최대 3개
async def test_missing_extension_opens_guide(dashboard):
    # '내려받기' 클릭 → details 펼침, 연결 실패 문구를 안내 안에 표시, 토스트 없음
async def test_outdated_extension_uses_toast(dashboard):
    # version 불일치 → 안내를 펼치지 않고 오류 토스트
async def test_disabled_reason_visible_without_hover(dashboard):
    # 내려받을 수 없는 영상 줄에 resolve_error 글자가 보인다
    # 값이 없으면 '지금은 내려받을 수 없어요'
    # 지난 과목에서 수업자료 탭이 비활성인 이유 '지난 과목은 수업자료를 볼 수 없어요'
async def test_phone_disables_download_with_one_notice(mobile_page):  # is_mobile=True, has_touch=True
    # 영상 내려받기와 수업자료 전체 받기 disabled, 'PC 크롬에서 받을 수 있어요'는 1개
    # 줄마다 이유 문구가 없다. 단일 수업자료는 활성 상태
async def test_async_buttons_show_pending(page):
    # 응답을 지연한 동안 알림 채널 '추가하기', '테스트 메시지 보내기'가 disabled + 처리 중 문구
async def test_lists_show_skeleton_while_loading(dashboard):
    # 응답 지연 중 #video-list[aria-busy=true] .skeleton이 보이고, 완료 후 사라진다
async def test_bar_rises_above_anchor_ad(page):  # 375x812
    # 화면 아래에 고정된 ins[data-anchor-status="displayed"] 높이 60px를 넣으면
    # #task-bar의 bottom <= 광고의 top, 없애면 원위치
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_feedback_web.py -q`
Expected: FAIL

- [ ] **Step 3: 토스트, 처리 중 상태, 스켈레톤 구현**

`#toast`를 메시지 묶음 컨테이너로 바꾼다. 항목별로 성공은 3200ms 뒤 제거하고, 오류는 `닫기` 버튼을 두고 `role="alert"`로 표시한다. 알림 추가·테스트와 계정·텍스트 삭제 실행에 `withPending`을 쓴다. 영상 목록과 첫 화면 강의 목록의 로딩을 `skeletonRows`로 바꾼다. 로딩 문구가 있는 화면 제목의 뚱이 말투는 유지한다. `prefers-reduced-motion`에서는 스켈레톤 애니메이션을 끈다. `logout()`에서 `clearToasts()`를 호출한다.

- [ ] **Step 4: 다운로드 안내와 비활성 이유 구현**

`state.extensionMissing`이 true면 설치 안내 `details`를 펼치고 문구를 안에 보여 준다. 휴대폰에서는 영상 다운로드와 수업자료 전체 받기를 비활성으로 두고, 화면 위쪽에 `PC 크롬에서 받을 수 있어요`를 한 번만 표시한다. 휴대폰에서는 줄별 이유와 설치 안내 `details`를 표시하지 않는다.

- [ ] **Step 5: 광고 겹침 구현**

`syncAdInset()`은 화면 아래에 붙은 `ins.adsbygoogle[data-anchor-status="displayed"]`의 높이를 `--ad-inset`에 넣는다. `MutationObserver`로 `body`의 추가·속성 변경을 감시하고 `resize`에서도 다시 계산한다. 해당 속성은 공개 API가 아니므로 Task 7의 실제 확인을 대체하지 못한다.

- [ ] **Step 6: 규칙 문서 갱신과 통과 확인**

MASTER.md에 토스트, 처리 중, 스켈레톤, 비활성 이유 규칙을 추가한다.

Run: `uv run pytest tests/test_feedback_web.py tests/test_videos_web.py tests/test_web.py -q`
Expected: PASS. `test_missing_extension_never_falls_back_to_relay`는 토스트 대신 안내 기준으로 고친다.

- [ ] **Step 7: 커밋**

```bash
git add app/web design-system tests
git commit -m "feat(web): add persistent errors, disabled reasons, and phone notices"
```

### Task 7: 전체 검증

**Files:**
- Test: 전체. 코드 변경은 검증에서 발견한 문제의 수정만

- [ ] **Step 1: 전체 테스트**

Run: `uv run pytest -q`
Expected: 기존 환경 의존 테스트를 제외한 실패 없음. 실패가 있으면 이번 변경과의 관련 여부를 함께 보고한다.

- [ ] **Step 2: 남은 옛 이름 확인**

Run: `grep -rnE '한 번에 듣기|모두 재생|새로고침이나|전체 받기|전체 전사|transcribe-panel|bulk-status|cta-confirm' app/web tests README.md docs/chrome-download-extension.md`
Expected: 결과 없음

- [ ] **Step 3: 로컬 앱을 브라우저로 확인**

`superpowers` 시안과 비교하며 375, 768, 1024, 1440px에서 첫 화면, 강의 재생, 내려받기·텍스트 변환, 알림, 계정을 확인한다. 키보드만으로 패널과 확인 창을 조작한다.

- [ ] **Step 4: 실제 사이트 확인을 사용자에게 요청**

배포는 사용자의 결정이다. 배포 후 사용자에게 `daunload.cloud`의 휴대폰 화면에서 앵커 광고와 진행 바가 겹치지 않는지, 로그인 화면과 주요 화면이 정상인지 확인을 요청한다. 확인 전에는 이 항목을 완료로 보고하지 않는다.
