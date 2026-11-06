# 사용 권한 승인 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자가 승인한 사용자만 서비스를 쓰게 하고, 승인 요청·결정 화면을 만들며, 주기 동기화를 자동 재생 과목만 2시간마다로 줄인다.

**Architecture:** `users`에 권한 상태 컬럼을 더하고, 기존 `CurrentUser` 의존성이 권한까지 확인하게 바꿔 모든 사용자 API를 기본으로 막는다. 권한 없이 열어야 하는 몇 경로만 새 `AuthedUser`를 쓴다. 백그라운드 작업은 `has_access`로 거르고, 동기화는 전체와 자동 재생 과목용 두 작업으로 나눈다.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, arq, 순수 HTML/CSS/JS, pytest, Playwright

**Spec:** `docs/superpowers/specs/2026-11-06-access-approval-design.md`

## Global Constraints

- 작업 브랜치는 `feat/admin-followups`다(이미 `34a0c1b`, `c636bba`가 있다). 푸시와 배포는 사용자에게 확인받은 뒤에만 한다.
- 권한 상태 값은 `none`, `pending`, `approved`, `rejected`, `revoked`이고 기본값은 `none`이다. 기존 사용자도 `none`으로 시작한다.
- `has_access(user) = is_admin(user) or user.access_status == "approved"`. 관리자는 저장된 값과 관계없이 승인된 것으로 본다.
- 막힌 요청은 HTTP 403, `detail = {"code": "ACCESS_REQUIRED", "message": "관리자 승인이 필요합니다."}`.
- 거절 후 다시 요청할 수 있는 시각은 `access_decided_at + 24시간`.
- 자동 재생 과목 동기화 주기는 120분이고 기존 지터를 유지한다. 작업 ID는 전체 `sync:{uid}`, 자동 재생 과목 `autosync:{uid}`.
- 어드민 쓰기 API는 `POST /admin/users/{student_id}/access` 하나뿐이다. 나머지 어드민 API는 GET이다.
- `design-system/MASTER.md`를 따른다: 44px 터치 영역, 토큰, SVG 아이콘, 서버 문자열은 `esc()`, 375·768·1024·1440px에서 가로 스크롤 없음.
- 테스트는 시각에 따라 결과가 달라지면 안 된다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 파일을 지정해 커밋하고 먼저 `git diff --staged`를 확인한다.

## Review Focus

1. 권한이 회수된 사용자가 이미 열어 둔 탭에서 버튼을 눌러도 데이터나 작업이 실행되지 않고 권한 화면으로 바뀐다.
2. 회수된 사용자가 회수 전에 받은 내려받기·오디오 링크로는 받을 수 없다.
3. 승인 직후 바로 전체 동기화가 돌고, 이미 큐에 있던 자동 재생 과목 동기화 때문에 버려지지 않는다.
4. 관리자 두 명이 같은 요청을 거의 동시에 처리해도 상태가 꼬이지 않고, 늦은 쪽은 409를 받는다.
5. 자동 재생 과목만 동기화해도 다른 과목의 할 일과 과목이 비활성화되지 않는다.

---

### Task 1: 권한 상태와 API 차단

**Files:**
- Create: `migrations/versions/e0f1a2b3c4d5_user_access_status.py` (`down_revision = "c8d9e0f1a2b3"`)
- Create: `app/services/access.py`
- Modify: `app/db/models.py` (`User`), `app/api/deps.py`, `app/api/routes_account.py` (`/me`, 로그인, `DELETE /me`), `app/api/routes_media.py` (서명 링크 경로와 다운로드 완료 보고), 서명 토큰을 쓰는 다른 경로
- Test: `tests/test_access_api.py`, 기존 테스트 픽스처

**Interfaces:**
- Produces: `User.access_status`, `access_requested_at`, `access_decided_at`, `access_decided_by`, `access_reject_count`
- Produces: `app/services/access.py`: `has_access(user: User) -> bool`, `access_retry_at(user: User) -> datetime | None`, `async user_has_access(db, user_id: int) -> bool`
- Produces: `deps.AuthedUser`(로그인만 확인), `deps.CurrentUser`(로그인 + `has_access`, 아니면 403 `ACCESS_REQUIRED`), `deps.AdminUser`(로그인 + 관리자)
- Produces: `GET /me`에 `access_status`(관리자는 `approved`), `access_requested_at`, `access_retry_at`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# test_unapproved_user_is_blocked_everywhere: none 사용자로 app.routes의 모든 사용자 경로(관리자, /auth, GET /me,
#   DELETE /me, /access/request, 정적 파일 제외)를 호출해 403과 detail.code == "ACCESS_REQUIRED"
# test_open_paths_work_without_access: GET /me 200(access_status "none"), DELETE /me 204
# test_admin_is_always_approved: ADMIN_STUDENT_IDS 학번은 access_status가 none이어도 /todos 200, /me는 "approved"
# test_login_does_not_queue_sync_for_unapproved: 승인 안 된 사용자 로그인 시 큐에 sync_user_job 없음, 승인된 사용자는 있음
# test_signed_links_check_owner_access: 승인 때 받은 /downloads/{token}, /audio/{token}이 회수 뒤에는 403
# test_revoked_access_blocks_existing_token: 승인된 토큰으로 /todos 200 → DB에서 revoked로 바꾼 뒤 같은 토큰 403
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_access_api.py -q` / Expected: FAIL

- [ ] **Step 3: 구현**

`CurrentUser`의 의미를 바꿔 모든 기존 경로가 자동으로 막히게 한다. 권한 없이 열어야 하는 경로(`GET /me`, `DELETE /me`, 이후 Task 2의 `POST /access/request`)만 `AuthedUser`로 바꾼다. `AdminUser`는 `AuthedUser` 위에서 관리자를 확인한다. 서명 토큰 경로는 토큰의 사용자 ID로 `user_has_access`를 확인한다.

- [ ] **Step 4: 기존 테스트 픽스처 갱신**

`tests/test_api.py`의 `_signup`을 쓰는 일반 사용자와, 사용자를 직접 만드는 다른 테스트 헬퍼가 기본으로 `approved`가 되게 한다(설계 12장). 승인 전 상황은 새 테스트에서만 만든다. 웹 테스트의 가짜 `/me` 응답에는 `access_status: "approved"`를 넣는다.

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest -q` 단독 실행 / Expected: 모두 PASS

- [ ] **Step 6: 커밋** — `feat(access): gate every user API behind admin approval`

### Task 2: 요청과 관리자 결정

**Files:**
- Modify: `app/services/access.py`, `app/api/routes_account.py` 또는 새 `app/api/routes_access.py`(앱에 등록), `app/api/routes_admin.py`, `app/services/notify.py`(필요하면)
- Test: `tests/test_access_api.py`

**Interfaces:**
- Consumes: Task 1의 컬럼, `has_access`, `AuthedUser`, `AdminUser`
- Produces: `POST /access/request` → `{access_status, access_requested_at, access_retry_at}`
- Produces: `GET /admin/access-requests` → `{total, items: [{student_id, requested_at, previous_status, reject_count}]}`, 요청 시각 오래된 순
- Produces: `POST /admin/users/{student_id}/access` body `{"action": "approve"|"reject"|"revoke"}` → 바뀐 사용자의 `{student_id, access_status, access_decided_at, access_decided_by}`
- Produces: `GET /admin/users` 줄에 `access_status`, 쿼리 `access`(다섯 값) 필터, `GET /admin/overview`에 `pending_count`, `GET /admin/users/{id}/summary`에 `access_status`
- Produces: `async notify_admins_of_request(db, user: User) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# test_request_transitions: none→pending, revoked→pending, pending 재요청은 200이고 시각·알림 그대로,
#   approved는 400, 24시간 안의 rejected는 429와 access_retry_at, 24시간 뒤에는 pending
# test_request_notifies_every_admin_discord_once: 관리자 2명의 사용 중인 discord 채널에 각 1번(respx), telegram·꺼진 채널 제외,
#   pending 재요청은 추가 전송 없음, 회수 후 재요청은 다시 전송, 전송 실패(500)여도 요청은 200
# test_admin_decisions_table: approve는 pending·none·rejected·revoked에서 approved, reject는 pending에서만(거절 횟수 +1),
#   revoke는 approved에서만, 그 밖은 409, 대상이 관리자면 400, 일반 사용자 호출은 403, 결정 시각·결정자 저장
# test_approve_queues_full_sync: 승인하면 queue에 ("sync_user_job", uid, "sync:{uid}") 
# test_revoke_cancels_active_batch: 대기·진행 중 묶음에 cancel_requested True
# test_access_requests_list_and_filters: 대기 목록 순서와 previous_status, /admin/users?access=pending, overview pending_count
# test_concurrent_decisions_conflict: 같은 pending에 approve와 reject를 차례로 보내면 두 번째는 409
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_access_api.py -q` / Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현**

상태 전이는 `access.py`의 한 함수에서 표로 처리하고, 바꿀 때는 현재 상태를 조건으로 건 `UPDATE ... WHERE access_status = :expected`로 해서 동시 처리를 막는다(영향 행이 0이면 409). `previous_status`는 요청 직전 상태를 저장해 둔 값이다. 필요하면 `access_previous_status` 컬럼을 Task 1 마이그레이션 다음 번호로 추가한다. 디스코드 전송은 `send_to_channel`을 쓰고 실패는 로그만 남긴다.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_access_api.py tests/test_admin_api.py tests/test_api.py -q` / Expected: PASS

- [ ] **Step 5: 커밋** — `feat(access): add access requests and admin decisions`

### Task 3: 백그라운드 작업 축소와 차단

**Files:**
- Modify: `app/services/sync.py`, `app/worker/main.py`, `app/services/notify.py`, `app/services/playback.py`(`run_batch` 시작), `app/config.py`
- Test: `tests/test_worker.py`, `tests/test_sync_scope.py`(새 파일)

**Interfaces:**
- Consumes: Task 1의 `has_access`, `user_has_access`
- Produces: `sync_user(db, user_id, now=None, course_ids: set[int] | None = None) -> SyncResult`
- Produces: arq 작업 `auto_sync_user_job(ctx, user_id: int) -> str`, 설정 `auto_sync_interval_minutes: int = 120`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# test_schedule_picks_only_approved_users_with_auto_play: 승인+자동재생 과목 있음만 autosync 작업, 작업 ID "autosync:{uid}";
#   승인 안 됨·자동재생 없음·과거 학기 과목만 있음은 제외
# test_auto_sync_only_touches_auto_play_courses: course_ids={A}로 동기화하면 B 과목의 할 일·과목은 active 그대로,
#   A 과목의 사라진 할 일만 비활성화, last_synced_at은 그대로, next_sync_at은 약 120분 뒤
# test_full_sync_job_not_dropped_by_auto_sync: autosync가 큐에 있어도 sync_user_job이 들어간다(서로 다른 작업 ID)
# test_unapproved_get_no_notifications: notify_user가 승인 안 된 사용자에게 보내지 않음, deadline_reminders도 제외
# test_worker_cancels_batch_of_unapproved_owner: 소유자가 revoked인 queued 묶음은 재생하지 않고 cancelled로 끝남
# test_auto_sync_creates_auto_batch: 자동 재생 과목 동기화 뒤 새 영상이 있으면 자동 묶음 생성(기존 규칙)
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_worker.py tests/test_sync_scope.py -q` / Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현**

전체 동기화(`sync_user_job`)는 로그인·버튼·승인 때만 큐에 들어간다. `schedule_syncs`는 Global Constraints의 대상만 고르고 `auto_sync_user_job`을 넣는다. 부분 동기화는 고른 과목만 갱신하며 고르지 않은 과목의 줄을 비활성화하지 않는다. 기존 `sync_interval_minutes`를 쓰는 곳은 새 설정으로 바꾸고 쓰지 않는 설정은 지운다.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q` 단독 실행 / Expected: 모두 PASS

- [ ] **Step 5: 커밋** — `feat(sync): sync only auto-play courses every two hours for approved users`

### Task 4: 사용자 권한 화면

**Files:**
- Create: `app/web/access.js`(`index.html`에서 `app.js` 다음에 불러옴)
- Modify: `app/web/app.js`(`boot`, `api`의 403 처리), `app/web/index.html`, `app/web/style.css`
- Test: `tests/test_access_web.py`

**Interfaces:**
- Consumes: `GET /me`의 `access_status`, `access_requested_at`, `access_retry_at`, `POST /access/request`
- Produces: `showAccessScreen(me)`, `api()`가 `ACCESS_REQUIRED`를 받으면 `showAccessScreen`으로 전환

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# test_access_screen_states: none·pending·rejected(재요청 시각 전)·revoked 각각 시안의 제목과 버튼 상태, 다른 메뉴 없음
# test_request_button_sends_and_shows_pending: 요청 보내기 → POST /access/request → 대기 중 화면
# test_rejected_retry_button_disabled_until_retry_at: retry_at 전에는 버튼 비활성과 시각 안내
# test_access_required_mid_session_switches_screen: 앱 사용 중 API가 403 ACCESS_REQUIRED면 권한 화면으로
# test_access_screen_has_logout_delete_and_recheck: 로그아웃, 계정 삭제(기존 확인 창), 상태 다시 확인 동작
# test_access_screen_no_sideways_scroll: 375, 1440px
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_access_web.py -q` / Expected: FAIL

- [ ] **Step 3: 구현**

시안 `access-flow.html` 1번의 문구를 따른다. 화면은 기존 시작 화면(`#startup`)과 같은 자리에 두고, 승인된 사용자에게는 지금과 똑같이 앱이 뜬다. 승인 전에는 `loadCore`의 다른 API를 부르지 않는다.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_access_web.py tests/test_web.py -q` / Expected: PASS

- [ ] **Step 5: 커밋** — `feat(web): show the access request screen to unapproved users`

### Task 5: 관리자 권한 관리 화면

**Files:**
- Modify: `app/web/admin.js`, `app/web/style.css`
- Test: `tests/test_admin_web.py`

**Interfaces:**
- Consumes: Task 2의 관리자 API와 응답 필드, 기존 `adminRequest`, `adminTable`, `confirmDialog`, `withPending`, `toast`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# test_overview_shows_access_requests: pending이 있으면 지표 위에 "권한 요청 N건" 표, 없으면 표 없음
# test_approve_and_reject_from_overview: 버튼 → POST action, 줄이 빠지고 토스트, 처리 중 버튼 비활성
# test_users_tab_access_column_filter_and_buttons: 권한 열, access 필터 요청, 상태별 버튼(승인·거절·권한 회수·바로 승인), 관리자 줄은 버튼 없음
# test_revoke_needs_confirmation: 권한 회수는 확인 창(재생 중지 안내), 취소하면 요청 없음
# test_decision_conflict_shows_error_and_reloads: 409면 오류 토스트와 목록 다시 불러오기
# test_detail_header_shows_access_and_actions: 사용자 상세 머리말에 상태와 버튼
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_admin_web.py -q` / Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현** — 시안 2, 3번을 따른다. 권한 탭 이름은 승인됨·대기 중·거절됨·회수됨·요청 안 함.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_admin_web.py -q` / Expected: PASS

- [ ] **Step 5: 커밋** — `feat(admin): approve, reject and revoke access from the dashboard`

### Task 6: 개인정보처리방침, 문서, Postgres 확인

**Files:**
- Modify: `app/web/privacy.html`, `store-assets/chrome-web-store/privacy-practices.md`(어긋나는 문장만), `app/web/app.js`(`PRIVACY_NOTICE_KEY`를 `privacy-notice-2026-11-access`로, 안내 문구), `README.md`, `design-system/MASTER.md`, `tests/test_admin_postgres.py`, `tests/test_web.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# test_privacy_notice_shows_again_after_access_change: 예전 키를 닫았어도 새 키로 다시 보임
# test_admin_postgres: Task 2의 access 필터, 대기 목록, overview pending_count, 결정 API를 Postgres 격리 스키마에서 실행
```

- [ ] **Step 2: 실패 확인 후 구현**

방침에 권한 요청·결정 기록(요청 시각, 결정 시각, 결정자, 거절 횟수), 승인 전에는 학교 정보를 동기화하지 않는다는 점, 주기 동기화를 자동 재생 과목 2시간 간격으로 줄였다는 점을 적고 시행일을 배포일로 고친다. 안내 문구: `개인정보처리방침이 바뀌었어요. 관리자가 승인한 사용자만 서비스를 쓸 수 있고, 권한 요청과 결정 기록을 보관해요.` README와 MASTER.md 어드민 절에 승인 흐름, 쓰기 동작 하나, 배포 직후 전원이 막힌다는 점과 `바로 승인`을 적는다.

- [ ] **Step 3: 전체 검증**

Run: `uv run pytest -q && QUEUE_TEST_DATABASE_URL=postgresql+asyncpg://lms:lms@127.0.0.1:5432/lms QUEUE_TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run pytest tests/test_admin_postgres.py -q`
Expected: 모두 PASS

- [ ] **Step 4: 마이그레이션 확인** — 격리된 방법으로 Postgres에서 `upgrade head`와 `downgrade -1`이 되는지 확인하고, 공용 로컬 DB는 원래 버전으로 되돌린다.

- [ ] **Step 5: 커밋** — `docs(access): update privacy policy and admin docs for access approval`
