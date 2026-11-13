# 아유 하기싫어 웹 디자인 규칙

대상: `app/web` (Vite + Preact + Tailwind CSS, 9장). 색상, Pretendard·Black Han Sans 글꼴, 뚱이 콘셉트는 바꾸지 않는다. 런타임 의존성은 Preact 하나만 쓴다. 공통 컴포넌트는 daisyUI 5(빌드 때만 쓰는 Tailwind CSS 플러그인, 런타임 JS 없음)를 뚱이 테마로 맞춰 쓴다. 기준은 하나다. **버튼을 본 순간 어떤 동작인지 예측할 수 있어야 한다.**

설계 배경: `docs/superpowers/specs/2026-11-05-web-design-system-design.md`

## 0. 화면 틀 (모든 화면이 같다)

메뉴는 네 개다: `학습 현황`(홈), `강의 재생`, `내려받기·텍스트 변환`, `설정`(관리자는 `사용자 관리`가 더 있다). 화면은 모두 같은 순서로 쌓는다.

```
ViewHead   제목 상자(h1) + 한 줄 부제(선택) + 오른쪽 동작(선택)     ui/layout.tsx
Section    제목(h2) + 한 줄 설명(선택) + 본문을 담은 흰 카드, 필요한 만큼   ui/layout.tsx
```

- **폭과 정렬:** 본문은 모든 화면에서 같은 폭(최대 920px)으로 왼쪽에 붙인다. 화면마다 폭이나 가운데 정렬을 따로 두지 않는다. 예외는 표가 넓은 관리자 화면(`.admin-workspace`, 1200px) 하나다.
- **제목:** 화면 제목은 `ViewHead` 하나뿐이다. 홈도 같은 상자를 쓰고 날짜와 요약은 부제에 쓴다.
- **묶음:** 화면 안의 묶음은 `Section`(`id`, `title`, `note`, `actions`, `flush`)으로 그린다. 제목·설명·여백을 손으로 만들지 않는다(`<h2 class="card-title px-5 …">` 금지). 목록이 카드 끝까지 닿으면 `flush`를 쓴다.
- **안내·범례는 그 묶음 안에:** 묶음에 딸린 설명, 학칙 경고, 범례를 카드 사이에 떠 있는 별도 상자나 카드 밖 글로 두지 않는다. 설명은 `note`, 범례는 본문 끝에 둔다. 화면 전체에 걸친 안내(개인정보 변경, 다시 로그인)만 `ViewHead` 위 `.alert.alert-outline`이다.
- **빈 상태:** 화면 전체가 빌 때만 뚱이 사진이 붙는 `Empty`를 쓴다. 묶음 하나가 비었으면 그 `Section` 안에 한 줄로 쓴다.
- **화면을 늘리지 않는다:** 설정 성격의 기능은 `설정`의 묶음으로, 강의 재생에 관한 설정(자동 수강 과목)은 `강의 재생`의 묶음으로 넣는다. 새 메뉴는 기존 화면 어디에도 속하지 않을 때만 만든다. 합친 화면의 예전 주소는 `core.ts`의 `VIEW_ALIASES`로 새 화면에 보낸다(`#courses` → `#playback`, `#notifications`·`#account` → `#settings`).

| 화면 | 묶음(Section) 순서 |
|---|---|
| 학습 현황 `#dashboard` | `재생할 강의 N개`(모두 재생 버튼 → 과목별로 접은 강의 목록, 펼치면 강의마다 재생 버튼) → `직접 낼 과제·퀴즈 N개`(6-2) |
| 강의 재생 `#playback` | 지금 재생(또는 `골라서 재생` → `재생 계획`) → `자동 수강 과목` → `지난 재생` |
| 내려받기·텍스트 변환 `#videos` | 탭(영상·수업자료) → 목록 |
| 설정 `#settings` | `LMS 연결` → `개인 액세스 토큰` → `알림 받을 곳` → `계정 삭제` |

## 1. 토큰 (`app/web/src/styles/tokens.css`의 `:root`, Tailwind 테마는 `app.css`)

| 토큰 | 값 | 용도 |
|---|---|---|
| `--text-xs` | 12px | 배지, 범례 같은 보조 요소에만 쓴다. 본문과 버튼에는 쓰지 않는다 |
| `--text-sm` | 14px | 보조 정보(`.meta`), 버튼, 라벨 |
| `--text-md` | 16px | 제목(`.video-row .title`), 본문 |
| `--text-lg` | 18px | 작은 제목 |
| `--text-xl` | 22px | 화면 제목 |
| `--touch` | 44px | 버튼과 모든 터치 영역의 최소 높이 |
| `--gutter` | 16px (760px 이하 12px) | 화면 좌우 여백 |
| `--z-nav` | 10 | 모바일 메뉴 |
| `--z-top` | 15 | 상단 바 |
| `--z-skip` | 30 | 본문 바로가기 링크 |
| `--z-panel` | 35 | 진행 패널 |
| `--z-toast` | 40 | 토스트 |

모바일(760px 이하)의 좌우 여백은 상단 바, 메뉴, 제목, 카드 모두 `--gutter`(12px)로 맞추고, 카드 안쪽은 16px로 맞춘다.

글자 크기와 z-index는 숫자를 직접 쓰지 않고 토큰을 쓴다. 새 터치 영역은 `min-height: var(--touch)`로 만든다. 공간이 좁아도 44px을 유지한다.

## 2. 버튼 4종

| 종류 | 클래스 | 용도 | 모양 |
|---|---|---|---|
| 주요 동작 | `.btn.btn-primary` | 화면에서 가장 중요한 일 하나 | 분홍 배경, 먹선 그림자 |
| 일반 동작 | `.btn` | 사이트 안에서 바로 실행 | 흰 배경, 먹선 테두리, 필요하면 아이콘 |
| 사이트 밖으로 이동 | `.btn` + 새 탭 아이콘 | 새 탭 열기 (예: LMS에서 제출) | 일반 모양 + 새 탭 아이콘 |
| 위험한 동작 | `.btn.btn-error.btn-outline` | 삭제, 재생 중지 | 빨간 글자와 테두리, 가운데 창으로 확인 |

보조 클래스: `.btn-soft`(얇은 연한 테두리), `.btn-ghost`(테두리 없는 보조), `.btn-link`(글자 링크), `.btn-sm`(좁은 곳), `.btn-block`(전체 너비, 54px), `.btn-square`(44px 정사각 아이콘 버튼). daisyUI의 `btn-sm`은 32px로 줄지만 여기서는 좌우 여백만 줄고 44px을 유지한다(`daisy.css`). 눌림 상태(`aria-pressed="true"`)는 검정 바탕이다. 모두 터치 영역은 44px이다. `.btn`은 `white-space: normal`이라 긴 문구는 줄바꿈된다.

- 한 화면의 주요 동작은 하나만 둔다.
- **반복되는 목록은 약하게 그린다.** 강의 줄에서 아직 받지 않은 `내려받기`만 `.btn-soft`이고, 받은 뒤의 `내려받기`, 비활성 버튼, 텍스트 변환 버튼은 `.btn-ghost`다. 주차 머리줄의 일괄 버튼은 `.btn-link`다.
- **강의·자료 줄의 동작은 44px 아이콘 버튼으로 둔다(`.btn-square`).** 내용은 `iconOnly(이름, 라벨)`로 만들어 접근 가능한 이름을 유지하고, 터치 화면을 위해 뜻은 목록 위 `.icon-legend`에 한 번만 적는다. 수강 상태도 아이콘(`VIDEO_STATUS`)이며 모양이 달라 색에만 기대지 않는다. 한 줄의 버튼은 2개까지다(텍스트 변환 또는 보기 + 내려받기). `다시 변환`은 텍스트 창 안에 둔다. 주차 일괄 버튼은 `아이콘 + 전체 N`으로 쓰고 전체 문구는 `aria-label`과 `title`에 둔다.
- **같은 동작을 두 번 보여 주지 않는다.** 주차 일괄 버튼은 대상이 2개 이상일 때만 보인다.
- 자주 쓰는 동작을 `⋯` 메뉴에 숨기지 않는다. 버튼이 많으면 다음 줄로 넘긴다.
- 강의 줄의 버튼은 모바일과 PC 모두 제목 오른쪽 한 줄에 둔다.
- 접는 안내의 화살표는 글자(`⌄`)를 쓰지 않는다. `--chevron` 아이콘을 쓰고 펼치면 180도 돌아가며, 줄 전체에 커서와 hover 배경을 준다.

## 3. 확인 방식

PC와 휴대폰 모두 같은 가운데 창(`<dialog>` + `showModal()`)을 쓴다. 창에는 하는 일, 범위, 예상 시간, 필요한 경고를 보여 준다. 첫 포커스는 취소 버튼, Esc로 닫고 포커스는 원래 버튼으로 돌려준다.

창 높이는 `100vh`가 아닌 `100dvh` 기준으로 제한한다(휴대폰 주소창). 760px 이하에서는 좌우 `--gutter`만 남기고 넓히며 안쪽 여백은 16px, 버튼은 한 줄을 나눠 채운다. 내용이 길면 본문만 안에서 스크롤하고 버튼 줄은 항상 보이게 둔다. 한글이 글자 단위로 끊기지 않도록 `break-keep`을 쓴다.

| 구분 | 동작 | 방식 |
|---|---|---|
| 바로 실행 | 내려받기, 텍스트 변환, 동기화, 테스트 메시지 | 확인 없이 실행 |
| 처음 한 번만 확인 | 홈의 `강의 N개 모두 재생` | 첫 재생 때만 가운데 창으로 학칙 경고를 확인하고, 동의(`localStorage`의 `playback-consent-v1`) 뒤에는 바로 실행한다. 경고는 버튼 아래 글로 계속 보인다 |
| 확인 후 실행 | 재생 중지, 알림 채널 삭제, 전사본 삭제 | 가운데 창에서 한 번 확인 |
| 강한 확인 | 계정 삭제 | 가운데 창 + 지워지는 내용 안내 + 학번 입력 (일치할 때만 삭제 활성화) |

확인 버튼에는 결과를 적는다(`8개 재생 시작`, `알림 채널 삭제`). 일괄 재생 경고 문구는 범위와 관계없이 항상 "직접 시청하지 않은 수강은 학칙상 문제가 될 수 있어요."로 한다. 내려받기처럼 자주 쓰는 동작에는 확인 창을 끼우지 않는다.

공통 함수는 `app/web/src/ui.ts`(컴포넌트는 `ui/`)에 있다. 새 화면은 직접 만들지 말고 이 함수를 쓴다. 아래는 사용 모습이다.

```js
// ui.ts: confirmDialog({ title, body?: string[], warnings?: string[], confirmLabel, danger?, requireText?, requireLabel? }), closeConfirmDialog(), icon
const ok = await confirmDialog({
  title: "알림 채널을 삭제할까요?",
  body: ["이 채널로 더 이상 알림을 보내지 않아요."],  // 문자열은 textContent로 들어가므로 esc() 불필요
  confirmLabel: "알림 채널 삭제",
  danger: true,
});
if (ok) await deleteChannel(id);

button.innerHTML = `${icon("external")}LMS에서 제출`;
```

사용자 입력과 서버 문자열은 `esc()` 또는 `textContent`로만 넣는다.

## 4. 문구 규칙

- **뚱이 말투 유지:** 제목, 빈 화면, 로딩 (예: "할 거 없음. 누워도 됨")
- **분명하게 작성:** 버튼, 오류, 경고. 농담 때문에 뜻이 모호해지면 안 된다.
- 버튼 문구는 범위와 결과가 드러나게 쓴다.

| 지금 | 변경 후 |
|---|---|
| 강의 N개 한 번에 듣기 | 강의 N개 모두 재생 |
| 모두 재생 (N개) | 전체 강의 N개 재생 |
| 미리보기 | 재생 계획 보기 |
| 제출하기 | LMS에서 제출 + 새 탭 아이콘 |
| 새로고침이나 하자 | LMS 동기화 |
| 목록 새로고침 | 목록 다시 불러오기 |
| 전사 / 전체 전사 | 텍스트로 변환 |
| 전체 받기 (N) | 주차 머리줄의 `전체 내려받기 (N)`, `전체 텍스트로 변환 (N)` (스크린 리더 이름은 주차 이름 포함) |
| 중지 | 재생 중지 |
| 메뉴: 영상 재생 | 강의 재생 |
| 메뉴: 강의 영상 | 내려받기·텍스트 변환 |
| 요약 카드의 ↗ | › |

## 5. 접근성

스킵 링크, `:focus-visible`, `prefers-reduced-motion`, `aria-*`를 유지한다. 가로 스크롤이 생기면 안 된다. 긴 강의명과 주차명은 `overflow-wrap`으로 줄바꿈한다.

## 6. 진행 패널 (`app/web/src/tasks.tsx`)

강의 재생과 텍스트 변환의 진행과 실패는 `<aside id="task-panel">` 한곳에 모은다. 작업과 실패가 없거나 로그아웃 상태면 숨긴다. 상태를 바꾼 코드는 `renderTaskPanel()`을 부른다. 새 작업 종류는 `taskItems()`에 줄을 더한다.

- **바(`#task-bar`):** `작업 N개 진행 중 · 실패 M개`를 요약한다. 누르면 시트(`#task-sheet`)를 펼치고 Esc로 접는다.
- **위치:** 760px 이하는 좌우와 아래 8px의 플로팅 바, 761px 이상은 오른쪽 아래 320px 패널. 시트는 `max-height: 80dvh`에서 안쪽으로 스크롤한다. 아래쪽 값은 `calc(8px + var(--ad-inset))`를 쓴다.
- **여백:** 바가 있으면 `body.has-task-bar`가 본문 아래를 비우고, 토스트는 바 위에 뜬다.
- **중지:** `재생 중지`는 패널에만 두고 가운데 창에서 확인한다. `변환 중지`는 확인 없는 일반 버튼이다. 첫 화면과 `강의 재생` 화면은 진행 상황만 보여 준다.
- **실패:** 사용자가 닫을 때까지 남는다. 재생 실패는 끝난 지 7일 안의 묶음만 보여 주고, 닫은 ID는 `localStorage`의 `dismissed-playback-failures`에 저장한다. 텍스트 변환 실패에는 `다시 시도`와 `닫기`를 둔다.

## 6-1. 강의 재생 화면

한 화면에서 같은 사실을 두 번 말하지 않는다.

- **위 카드(`.playback-hero`):** 지금 강의의 상태, 제목, 위치, 진행률을 보여 주고, 그 아래에 전체 진행률을 둔다. 전체 진행률은 강의가 2개 이상일 때만 보인다. 진행 막대는 지금 강의와 전체, 최대 2개다.
- **재생 목록:** 강의가 2개 이상일 때만 별도 카드로 둔다. 순서, 시간, 마감, 상태만 쓰고 진행 막대는 넣지 않는다.
- **숫자:** 0인 실패, 이미 완료, 완료는 쓰지 않는다.
- **문제 없는 상태:** 재생이 정상이면 따로 말하지 않고, 확인이 필요할 때만 경고(`.alert.alert-error.alert-soft`) 한 줄을 보인다.
- **설명 문구:** `창을 닫아도 서버에서 계속돼요` 한 줄만 보이고, 나머지는 `재생 방식 안내`에 접어 둔다. 5초 폴링으로 다시 그려도 펼침 상태와 포커스를 유지한다.
- **지난 재생:** 별도 카드에 두고 상태는 줄 오른쪽에 색으로 표시한다.

## 6-2. 홈 (`app/web/src/screens/home.tsx`)

들어오자마자 **무엇을 재생해야 하는지와 무엇을 내야 하는지**를 보고, **버튼 한 번**으로 끝낸다. 순서는 `제목(ViewHead, 부제는 날짜 · 요약) → 재생할 강의(Section) → 직접 낼 과제·퀴즈(Section)`이고 탭, 요약 카드, 일정표, 뚱이 사진은 두지 않는다.

- **재생할 강의 카드:** 재생할 수 있는 강의 전부(`scope=all` 미리보기)를 **과목별 한 줄**(`과목 / 강의 N개 · 분량 / 가장 빠른 마감 / 재생 상태`)로 접어 `<details>`에 둔다. 과목 순서는 실제 재생 순서(마감 빠른 순)다. 펼치면 그 과목의 강의가 재생 순서대로 `제목 / 주차 · 길이 / 마감`과 그 강의만 재생하는 작은 `재생` 버튼(일반 버튼)으로 보인다. 펼침 상태는 5초 폴링으로 다시 그려도 남는다. 카드 맨 위(목록 앞)에 `강의 N개 모두 재생`을 둬서 강의가 많아도 버튼이 스크롤 없이 보인다. 재생 중이면 버튼 자리에 진행 요약과 `재생 현황 보기`, 줄마다 `듣는 중 %`·`완료` 상태가 붙는다.
- **직접 낼 과제·퀴즈 카드:** 아직 낼 수 있는 과제·퀴즈 전부(`/week`의 `assignments` + `later_assignments` + `late_assignments`)를 마감순으로, 줄마다 `LMS에서 제출`. 지각 제출이 열린 과제는 맨 앞에 `지각 제출 ○○까지`로 둔다. 강의가 없으면 가장 급한 과제의 `LMS에서 제출`이 이 카드 맨 위의 주요 버튼이 된다.
- **주요 버튼 하나:** 강의가 있으면 `강의 N개 모두 재생`, 없으면 가장 급한 과제 제출. 둘이 동시에 분홍이 되지 않는다.
- **제목은 사실대로:** "이번 주 할 일을 모두 끝냈어요. 누워도 됨"은 재생할 강의도 낼 과제도 없을 때만 쓴다. 남은 게 있으면 `할 일이 N개 남았어요`, 강의 목록을 확인하지 못했으면 그렇게 말한다.
- **없앤 화면:** 할 일, 미제출 과제, 캘린더 화면과 위쪽 요약 카드·7일 일정표는 홈과 같은 데이터를 다시 보여 줄 뿐이라 없앴다. 공지 화면과 새 공지 알림도 없앴다(학교 LMS에서 확인). 예전 주소(`#todos`, `#announcements` 등)는 홈(`#dashboard`)으로 보낸다. 같은 데이터를 보여 주는 화면을 다시 만들지 않는다.

## 7. 피드백 규칙 (`app/web/src/ui.ts`(컴포넌트는 `ui/`))

- **토스트:** `toast(msg, bad?)`는 `#toast` 묶음에 항목을 더한다. 성공은 3200ms 뒤 사라지고, 오류는 `role="alert"`와 `닫기` 버튼이 있어 사용자가 닫을 때까지 남는다. 같은 오류가 이미 떠 있으면 늘리지 않고, 최대 3개까지 두며 넘치면 가장 오래된 것부터 지운다. 로그아웃하면 `clearToasts()`로 비운다.
- **처리 중:** 서버를 기다리는 버튼은 `withPending(btn, "처리 중", fn)`으로 감싼다. 기다리는 동안 `disabled`와 `처리 중` 문구를 보이고, 끝나면 원래 모습으로 되돌린다.
- **스켈레톤:** 목록을 불러오는 동안은 `skeletonRows(n)`을 쓰고 컨테이너에 `aria-busy="true"`를 둔다. `prefers-reduced-motion`에서는 움직임을 끈다. 제목과 빈 화면의 뚱이 말투는 그대로 둔다.
- **비활성 이유:** 막힌 버튼은 이유를 마우스를 올리지 않아도 보이는 글자로 옆에 쓴다(`title`에 기대지 않는다). 영상은 `resolve_error`, 없으면 `지금은 내려받을 수 없어요`, 지난 과목의 수업자료 탭은 `지난 과목은 수업자료를 볼 수 없어요`.
- **휴대폰:** `isPhoneDevice()`(`(hover: none) and (pointer: coarse)`)는 다운로드 안내에만 쓴다. 영상 내려받기와 수업자료 전체 받기는 비활성이고, 화면 위쪽에 `PC 크롬에서 받을 수 있어요`를 한 번만 쓴다. 줄별 이유와 설치 안내는 보이지 않는다. 수업자료 한 개 받기는 그대로 쓸 수 있다.
- **설치 안내:** 웹 스토어 링크(`EXTENSION_STORE_URL`)가 주요 동작이다. 영상 탭을 열 때 `detectExtension()`이 연결을 확인하되 목록을 기다리게 하지 않는다. 응답이 없는 동안은 펼쳐 두고, 응답이 오면 접어 두며, 사용자가 접으면 그 세션에서는 다시 펼치지 않는다.
- **확장프로그램 없음:** 연결 확인이 시간 초과되면(`EXTENSION_MISSING`) 토스트 대신 설치 안내를 펼치고 문구를 그 안에 쓴다. 버전이 맞지 않는 경우는 오류 토스트다.
- **광고:** `syncAdInset()`이 화면 아래에 붙은 `ins.adsbygoogle[data-anchor-status="displayed"]`의 높이를 `--ad-inset`에 넣어 진행 패널과 토스트가 광고 위로 올라온다. 이 속성은 공개 API가 아니다.

## 7-1. 관리자 화면 (`app/web/src/screens/admin/`)

- **쓰기는 하나뿐:** 관리자 화면에서 바꾸는 동작은 사용 권한 승인·거절·회수 하나다. 나머지 버튼은 탭 이동, 필터, 페이지 이동, `다시 불러오기`뿐이고 API는 GET만 부르며, 쓰기는 `POST /admin/users/{학번}/access`만 쓴다.
- **사용 승인:** 현황에 승인 대기 요청 표, 사용자 탭에 `권한 관리` 열·필터와 `승인`·`거절`·`권한 회수`·`바로 승인` 버튼, 사용자 상세 머리글에 같은 버튼을 둔다. 관리자 계정 줄에는 버튼을 두지 않는다. 409(이미 처리됨)는 오류로 놀라게 하지 않고 최신 상태로 다시 불러온다. 배포 직후에는 관리자를 뺀 모든 사용자가 막혀 있으므로 `바로 승인`(대기 없이 승인)이 기존 사용자를 들여보내는 길이다.
- **넓은 폭:** 관리자 화면에서는 `.workspace`에 `.admin-workspace`를 붙여 본문 최대 폭을 1200px로 넓힌다. 다른 화면의 폭은 그대로다.
- **표:** 표는 `<Table columns rows>`(`screens/admin/shared.tsx`)로 그린다. 760px 이하에서는 머리글을 숨기고 각 줄을 카드로 바꾸며, 칸마다 `data-label`의 머리글을 앞에 붙인다. 표 밖으로 가로 스크롤을 만들지 않는다. 학번 링크는 터치 영역 44px을 유지한다(`.admin-linked`). 사용자 목록처럼 줄 전체를 누를 수 있는 표만 예외로 줄 클릭이 링크를 대신한다.
- **상태:** 탭마다 로딩 스켈레톤, 빈 상태, 오류와 `다시 시도`를 `<Loaded>`로 그린다.
- **늦은 응답:** 관리자 API는 `useAdmin(path)`로만 부른다. 그 사이 조건(path)이 바뀌었거나 화면이 사라졌으면 늦게 온 응답은 버린다(그리지도, 오류를 띄우지도 않는다).
- **보여 주지 않는 데이터:** 비밀번호와 그 해시, LMS 세션, 알림 대상 주소와 토큰, 영상 원본 주소, 전사 본문, 과제 점수는 화면에 넣지 않는다. 영상 주소는 `주소 있음`·`주소 없음`으로만 쓴다. 서버 문자열은 JSX로만 넣는다(HTML로 해석하지 않는다).
- **문구:** 마감이 비어 있으면 `마감 없음`으로 쓰고 `new Date(null)`을 그리지 않는다. 동기화에서 빠진 할 일(`active=false`)은 완료로 단정하지 않고 `목록에서 사라짐(완료 또는 마감)`으로 쓴다. 시각이 저장되지 않은 문제는 `시각 기록 없음`이다. 재생 묶음 범위는 `ADMIN_BATCH_SCOPE`의 이름(`전체 재생 묶음` 등)으로 쓰고 `all` 같은 값을 그대로 보이지 않는다.
- **숫자:** 문제 탭 이름의 `문제 N건`은 네 종류의 합이고, 현황의 `재생 실패`는 시도한 영상만 센다. 다운로드는 `완료 보고 또는 링크 발급 횟수`라고 설명을 붙인다.

## 8. 성능 (첫 화면을 막지 않는다)

- **나눠 받기:** 특정 화면에서만 쓰는 큰 코드는 `import()`로 받는다(예: `renderAdminView()`가 `screens/admin`을 받는다). 첫 화면 묶음에서 그 모듈을 정적으로 import하지 않는다(`tests/test_web_serving.py`가 확인한다).
- **인라인 금지:** CSP가 인라인 스크립트와 `onload=` 속성을 막는다. 늦게 붙일 자원은 `src/assets.ts`에 둔다.
- **글꼴:** 글꼴 CSS는 `preload` + `media="print" data-font`로 받고 `assets.ts`가 적용한다. 렌더링을 막는 `<link rel="stylesheet">`로 되돌리지 않는다.
- **광고:** AdSense 로더는 `load` 뒤 브라우저가 한가할 때 `loadAds()`가 붙인다. 사이트 확인은 `<meta name="google-adsense-account">`가 맡는다.
- **이미지:** 사진은 `public/`의 WebP(640px, 품질 78)로 두고 `width`·`height`를 적는다. 첫 화면의 큰 사진(로그인 화면 뚱이)에만 `fetchpriority="high"`를 쓴다. 지금 화면에 없는 사진은 미리 받지 않는다(표정 사진은 로그인 화면이 뜬 뒤 `preloadAuthMoods()`).
- **캐시:** `/assets/*`는 이름에 내용 해시가 붙어 1년 캐시(`immutable`), `index.html`은 `no-cache`다(`app/main.py`).

## 9. 구조와 빌드 (`app/web`)

```
app/web/index.html        정적 마크업(로그인, 메뉴, 대화상자 틀). 진입점 <script type="module" src="/src/main.ts">
app/web/public/           이름이 고정된 파일(이미지, privacy.html, ads.txt)
app/web/src/main.ts       모듈을 불러오고 boot(). window.__ssu(테스트·디버깅 손잡이)
app/web/src/core.ts       state, store, api, 날짜·시간 형식, esc. 다른 모듈을 import하지 않는다(types.ts만 예외)
app/web/src/types.ts      서버 API 응답 타입(app/api/schemas.py와 맞춘다)
app/web/src/ui.ts, ui/    아이콘·토스트·확인 창(Preact)과 작은 공통 도구
app/web/src/extension.js  확장 프로그램 창구 ext.request. 다른 모듈을 import하지 않는다
app/web/src/screens/      Preact 화면: home, playback(+ courses: 자동 수강 과목 묶음), videos, transcript, settings(+ notifications: 알림 받을 곳 묶음), mount(#view에 붙이기)
app/web/src/ui/layout.tsx 화면 뼈대(ViewHead, Section, Panel, PanelBody, Empty, Warning), ui/pending.tsx(처리 중 버튼)
app/web/src/tasks.tsx     하단 진행 패널
app/web/src/app.ts        로그인·부팅·동기화·화면 전환(마크업은 index.html의 정적 HTML)
app/web/src/screens/admin/ 관리자 화면(관리자만 import()로 받는다): index(머리·탭), overview, users, detail, feeds, shared
app/web/src/*.ts          access(승인 대기), downloads·transcribe(내려받기·변환 엔진). extension.js만 JS다(테스트가 실제 확장 프로그램 페이지에 그대로 넣는다)
app/web/src/styles/       app.css(진입점·Tailwind 테마), tokens.css(토큰), base.css(요소 기본 모양), daisy.css(daisyUI를 뚱이 모양으로 덮어쓰기), components.css(daisyUI에 없는 공통 클래스), legacy.css(화면 전용으로 남은 규칙)
```

- **import 규칙:** 전역 변수를 쓰지 않는다. 필요한 이름은 import한다. `core.js`와 `extension.js`는 아무것도 import하지 않는다. 최상위에서 바로 실행되는 코드는 `core`·`ui`처럼 먼저 평가되는 모듈의 값만 쓴다(순환 import에서 초기화 전 값을 읽으면 TDZ 오류가 난다). 화면 모듈끼리의 호출은 함수 안에서만 한다.
- **화면 추가:** 먼저 0장대로 기존 화면의 묶음으로 넣을 수 없는지 본다. 새 화면이면 `screens/이름.tsx`에 `viewX()`를 두고 `mountScreen("이름", <X />)`로 그린다. 다시 그리기는 같은 함수를 다시 부르면 된다(같은 컨테이너에 diff로 그려 포커스·펼침이 남는다). `app.ts`의 `renderView()` 표에 넣는다.
- **정적 UI는 HTML에:** 첫 화면(시작·로그인·승인 대기)과 앱 뼈대(상단 바·메뉴)는 `index.html`의 정적 HTML이다. JS가 오기 전에 그려지므로 Preact로 옮기지 않는다.
- **새 화면·컴포넌트:** Preact(TSX) + Tailwind 유틸리티로 만든다. 색·글자 크기·반경·간격은 토큰 이름만 쓴다(`bg-pink`, `text-ink-2`, `text-sm`, `rounded-card`, `min-h-touch`). Tailwind 기본 색 팔레트는 꺼 두었다. 기존 화면을 옮기면 그 화면의 `legacy.css` 규칙을 지운다.
- **공통 컴포넌트(daisyUI 5):** 정적 HTML·HTML 문자열·TSX가 같이 쓴다. 테마(`ttungi`)는 `app.css`의 `@plugin "daisyui/theme"`, 뚱이 모양(먹선, 분홍 주요 버튼 그림자, 44px 터치 영역, 16px 본문)은 `daisy.css`가 맞춘다.

  | 용도 | 클래스 |
  |---|---|
  | 버튼 | `btn` + `btn-primary` · `btn-error btn-outline`(위험) · `btn-ghost` · `btn-soft` · `btn-link` + `btn-sm` · `btn-block` · `btn-square` |
  | 카드 | `card`(흰 카드) · `card-body` · `card-title` · `card-actions` |
  | 안내 | `alert alert-error alert-soft`(빨간 경고) · `alert alert-outline`(흰 안내) · `empty`(뚱이 빈 화면, `components.css`) |
  | 입력 | `input` · `select` · `checkbox`(라벨은 `flex min-h-touch items-center gap-2`) · `toggle` · `progress progress-info` |
  | 탭 | `tabs tabs-box` > `tab`(`aria-selected` 또는 `aria-current="page"`면 검정 바탕) |
  | 대화상자 | `<dialog class="modal">` > `.modal-box`에 그린다 |
  | 토스트 | `#toast.toast` > `alert`(위치는 `daisy.css`의 `#toast`) |
  | 불러오는 중 | `skeleton-list`(`components.css`) > `skeleton` |

  daisyUI는 utilities 레이어 안 하위 레이어에 들어가므로 legacy.css로는 덮어쓸 수 없다. 한 곳만 다르면 유틸리티를 덧붙인다(`class="btn btn-sm px-2.5"`): Tailwind 유틸리티가 daisyUI보다 강하다. 모든 곳의 모양을 바꿀 때는 `daisy.css`에 쓰되, daisyUI가 그 규칙을 두는 단계(기본 l1.l2.l3, 변형 l1.l2, 일부 변형·hover l1) 바로 뒤의 `ttungi` 레이어에 둔다. 관리자 표(`admin-table`)는 휴대폰에서 카드로 바뀌는 전용 모양이라 daisyUI `table`을 쓰지 않는다.
- **테스트 손잡이:** 테스트는 모듈 안 값을 `window.__ssu`로만 본다. 확장 프로그램 응답은 `__ssu.ext.request`를 바꿔 끼운다. 새 값을 테스트에서 봐야 하면 `main.ts`의 `__ssu`에 더한다.
- **명령:** `npm run dev`(5173, API는 8000으로 넘긴다) · `npm run build`(→ `app/web-dist`) · `npm run typecheck`. FastAPI는 `app/web-dist`를 서빙하고, 없으면 화면 자리에 빌드 안내(503)만 보인다. 배포 이미지는 Dockerfile의 `web` 단계에서 빌드하며 최종 이미지에는 Node가 없다.
- **브라우저 테스트:** 빌드 결과(`app/web-dist`)를 쓴다. 먼저 `npm run build`를 돌린다.

## 10. 완료 체크

- [ ] 새 화면·묶음이 0장의 틀(`ViewHead` → `Section`)을 따르고, 손으로 만든 카드 제목이나 카드 밖 안내 글이 없다.

- [ ] 375, 768, 1024, 1440px에서 가로 스크롤이 없고 진행 패널이 내용을 가리지 않는다.
- [ ] 휴대폰 크기에서 모든 버튼의 터치 영역이 44px 이상이다.
- [ ] 375x812px의 `내려받기·텍스트 변환` 화면에서, 진행 바가 없고 목록까지 스크롤했을 때 강의 줄이 4개 이상 보인다. 버튼이 2개인 줄은 버튼이 한 줄에 들어간다.
- [ ] 홈의 첫 일괄 재생, 재생 중지, 알림 채널 삭제, 전사본 삭제, 계정 삭제가 모두 같은 확인 창을 거친다. `재생 계획 보기`를 거치는 재생은 그 화면에서 학칙 경고를 보여 준다.
- [ ] 키보드만으로 진행 패널과 확인 창을 조작할 수 있다.
- [ ] 이름을 바꾼 버튼에 의존하는 브라우저 테스트와 `README.md`, `docs/chrome-download-extension.md`를 함께 고쳤다.
- [ ] 실제 사이트에서 로그인 화면과 주요 화면, 휴대폰 화면의 진행 바와 하단 광고 겹침을 확인했다.
