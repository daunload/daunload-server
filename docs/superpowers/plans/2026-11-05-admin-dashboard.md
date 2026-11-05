# 관리자 대시보드 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `#admin`을 현황, 사용자, 활동, 문제 탭으로 된 조회 전용 대시보드로 바꾸고, 활동 추이를 위한 일별 기록을 시작한다.

**Architecture:** 일별 합계 테이블과 오류 시각 컬럼을 먼저 추가해 기록을 쌓는다. 조회는 `routes_admin.py`의 GET API가 맡고, 공통 계산은 `app/services/admin_data.py`에 둔다. 화면은 `app/web/admin.js`로 분리한 순수 JS다.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, arq, 순수 HTML/CSS/JS, pytest, Playwright

**Spec:** `docs/superpowers/specs/2026-11-05-admin-dashboard-design.md`

## Global Constraints

- 작업 브랜치는 `feat/admin-dashboard`다. `main`에 푸시하면 바로 운영에 배포되므로 푸시와 배포는 사용자에게 확인받은 뒤에만 한다.
- 어드민 API는 모두 GET, `AdminUser` 의존성, `Cache-Control: no-store`를 쓴다. 수정 API를 만들지 않는다.
- 응답에 절대 넣지 않는다: `password_hash`, `password_enc`, `session_enc`, `target_enc`, `token_version`, `media_url`, `PlaybackJob.video`, `Transcript.text`, `Transcript.segments`, `AssignmentRow.score`, `AssignmentRow.points_possible`.
- 일별 기록 보관 기간은 90일, 날짜 경계는 `get_settings().timezone`(Asia/Seoul), 기간 선택은 7, 30, 90일(기본 7)이다.
- 빌드 도구와 프론트 의존성을 추가하지 않는다. 그래프는 HTML/CSS로 그린다.
- `design-system/MASTER.md`를 따른다: 44px 터치 영역, `--text-*`와 색 토큰, SVG 아이콘, 시안 A의 먹선 테마, 서버 문자열은 `esc()`.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`을 붙인다. 커밋할 때는 파일을 지정하고 먼저 `git diff --staged`로 범위를 확인한다.

## Review Focus

1. 기록이 하나도 없는 서비스나 재생이 0건인 기간에서 실패율과 그래프가 오류나 `NaN` 없이 `-`와 빈 상태로 보인다.
2. 한국 시간 00:30의 로그인은 UTC로는 전날이어도 그 한국 날짜에 집계된다. 기간 시작도 한국 자정이다.
3. 서명된 주소나 2000자가 넘는 오류, `NULL` 오류가 들어와도 주소의 경로와 쿼리는 응답에 없고 화면이 깨지지 않는다.
4. `#admin/users/{학번}`을 직접 열거나 새로고침해도 그 화면이 뜨고, 일반 사용자는 데이터 요청 없이 권한 안내만 본다. 없는 학번은 빈 화면 대신 안내가 나온다.
5. 탭이나 필터를 빠르게 바꿔도 늦게 도착한 이전 응답이 현재 화면을 덮어쓰지 않는다.

---

### Task 1: 일별 기록과 오류 시각 저장

**Files:**
- Create: `app/services/activity.py`
- Create: `migrations/versions/c8d9e0f1a2b3_admin_daily_activity.py` (`down_revision = "b6c7d8e9f0a1"`)
- Modify: `app/db/models.py`, `app/api/routes_account.py` (`_record_login`), `app/services/downloads.py` (`record_download`), `app/services/lms_access.py`, `app/services/saved_videos.py`, `app/worker/main.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Produces: 모델 `UserDailyActivity(user_id, day: date, logins: int, downloads: int)`, 기본 키 `(user_id, day)`, `user_id`는 `ondelete="CASCADE"`, `day`에 인덱스
- Produces: `LmsCredential.last_error_at: datetime | None`, `SavedVideo.resolve_failed_at: datetime | None`
- Produces: `local_day(moment: datetime | None = None) -> date`, `async bump_daily(db, user_id, logins=0, downloads=0) -> None` (커밋하지 않음), `async prune_daily(db, keep_days=90) -> int`

- [ ] **Step 1: `tests/test_activity.py`에 실패하는 테스트 작성**

`tests.test_api`의 `env`, `_signup` 픽스처를 쓴다.

```python
def test_local_day_uses_korean_midnight():
    assert local_day(datetime(2026, 11, 4, 15, 30, tzinfo=UTC)) == date(2026, 11, 5)
    assert local_day(datetime(2026, 11, 4, 14, 59, tzinfo=UTC)) == date(2026, 11, 4)

# 이름과 확인할 내용
# test_bump_daily_adds_to_the_same_row: 두 번 호출하면 한 줄에 logins=2, downloads=1
# test_login_bumps_daily_logins: 로그인 후 오늘 줄 logins == 1
# test_record_download_bumps_daily_downloads: record_download 2회 후 downloads == 2
# test_prune_keeps_exactly_90_days: 오늘-90일 줄은 남고 오늘-91일 줄은 지워지며 반환값 1
# test_account_delete_removes_daily_rows: DELETE /me 후 해당 줄 0개
# test_lms_error_time_set_and_cleared: _mark 호출 시 last_error_at 설정, 성공 경로에서 None
# test_resolve_error_time_set_and_cleared: MediaError 시 resolve_failed_at 설정, 해결 시 None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_activity.py -q`
Expected: `app.services.activity`가 없어 FAIL

- [ ] **Step 3: 모델, 마이그레이션, `activity.py` 구현**

`bump_daily`는 동시 요청에도 안전하게 upsert한다. `db.bind.dialect.name`에 따라 `sqlalchemy.dialects.postgresql.insert` 또는 `sqlalchemy.dialects.sqlite.insert`를 고르고, 둘 다 `on_conflict_do_update`로 더한다. 마이그레이션의 시각 컬럼은 `sa.DateTime(timezone=True)`다.

- [ ] **Step 4: 기록 코드 연결**

`_record_login`과 `record_download`의 `commit` 직전에 `bump_daily`를 부른다. `last_error_at`은 `lms_access._mark`와 `worker/main.py`의 `cred.status, cred.last_error = "error", str(e)`에서 `utcnow()`로 넣고, `lms_access`에서 `last_error`를 `None`으로 만드는 두 곳에서 함께 비운다. `resolve_failed_at`은 `saved_videos.py`의 `except MediaError`에서 넣고 `resolved_at`을 넣을 때 비운다. `WorkerSettings.cron_jobs`에 매일 04:10 `prune_daily`를 도는 cron을 추가한다.

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_activity.py tests/test_api.py -q`
Expected: 모두 PASS

- [ ] **Step 6: 마이그레이션을 로컬 Postgres에서 확인**

Run: `DATABASE_URL=postgresql+asyncpg://lms:lms@127.0.0.1:5432/lms uv run alembic upgrade head && DATABASE_URL=postgresql+asyncpg://lms:lms@127.0.0.1:5432/lms uv run alembic downgrade -1 && DATABASE_URL=postgresql+asyncpg://lms:lms@127.0.0.1:5432/lms uv run alembic upgrade head`
Expected: 오류 없이 끝남

- [ ] **Step 7: 커밋** — `feat(admin): record daily activity and error times`

### Task 2: 개인정보처리방침과 서비스 안 안내

**Files:**
- Modify: `app/web/privacy.html`, `store-assets/chrome-web-store/privacy-practices.md`, `app/web/index.html`, `app/web/app.js`, `app/web/style.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `PRIVACY_NOTICE_KEY = "privacy-notice-2026-11"`, `#privacy-notice` 요소

- [ ] **Step 1: 실패하는 브라우저 테스트 작성**

```python
async def test_privacy_notice_shows_once(dashboard):
    page, _ = dashboard
    await page.goto("http://dashboard.test/#todos")
    notice = page.locator("#privacy-notice")
    await expect(notice).to_contain_text("개인정보처리방침")
    await expect(notice.get_by_role("link")).to_have_attribute("href", "/privacy.html")
    await notice.get_by_role("button", name="닫기").click()
    await expect(notice).to_be_hidden()
    await page.reload()
    await expect(page.locator("#view")).not_to_be_empty()
    await expect(notice).to_be_hidden()
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_web.py -q -k privacy_notice`
Expected: FAIL

- [ ] **Step 3: 안내 구현**

로그인 후 본문 위에 `.notice-inline`으로 보여 준다. 문구: `개인정보처리방침이 바뀌었어요. 일별 이용 횟수를 90일간 보관하고, 운영자가 문제 확인을 위해 이용 기록을 볼 수 있어요.` + `자세히 보기` 링크 + `닫기` 버튼. 닫으면 `store.set(PRIVACY_NOTICE_KEY, "1")`. 첫 로그인 사용자에게도 같은 규칙을 쓴다.

- [ ] **Step 4: 방침 수정**

`privacy.html`의 적용 범위를 daunload.cloud 웹서비스 전체로 넓히고, 처리 목적에 서비스 운영과 문제 확인을 더한다. 처리 데이터에 일별 로그인·다운로드 횟수와 재생·출석·제출 기록을 적는다. 운영자 열람, 90일 보관, 계정 삭제 시 삭제, 비밀번호·세션·전사 본문·점수는 열람하지 않음을 명시하고 시행일을 고친다. `privacy-practices.md`에서 어긋나는 문장을 맞춘다.

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_web.py -q`
Expected: 모두 PASS

- [ ] **Step 6: 커밋** — `docs(privacy): cover service-wide records and operator access`

Task 1, 2까지가 1단계 배포 범위다. 여기서 멈추고 사용자에게 배포 여부를 묻는다.

### Task 3: 현황, 활동, 문제 API

**Files:**
- Create: `app/services/admin_data.py`, `tests/test_admin_api.py`
- Modify: `app/api/routes_admin.py`

**Interfaces:**
- Consumes: Task 1의 `UserDailyActivity`, `local_day`, 오류 시각 컬럼
- Produces: `clean_error(text: str | None) -> str | None`, `period_start(days: int) -> datetime`, `failed_job_filter()`(시도한 `failed`/`locked` job 조건)
- Produces: `GET /admin/overview?days=` → `{days, total_users, active_users, playback_done, playback_auto, playback_seconds, downloads, playback_failed, failure_rate: float|null, tracking_since: date|null, problem_count, series: [{day, playback_done, logins, downloads}], recent_activity: [...], recent_problems: [...]}`
- Produces: `GET /admin/activities?kind=&student_id=&days=&offset=&limit=` → `{total, counts, activities: [AdminActivityOut + student_id]}`
- Produces: `GET /admin/problems?kind=&student_id=&lms_course_id=&days=&offset=&limit=` → `{total, counts, problems: [{kind, occurred_at: datetime|null, student_id, course_name, title, error, not_run: int}]}`, `kind`는 `playback`, `batch`, `lms`, `resolve`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_clean_error_strips_url_path_and_limits_length():
    assert clean_error("실패 https://cdn.test/a/b.mp4?token=SECRET 끝") == "실패 https://cdn.test 끝"
    assert len(clean_error("가" * 3000)) == 2000
    assert clean_error(None) is None

# 이름과 확인할 내용
# test_admin_new_endpoints_require_admin: 세 경로 모두 비로그인 401, 일반 사용자 403
# test_overview_empty_service: failure_rate is None, series 길이 == days, tracking_since is None
# test_overview_metrics: 수동 재생 사용자는 active에 포함, scope="auto"만 있는 사용자는 제외,
#   playback_auto 집계, failure_rate == 실패/(done+실패), downloads는 일별 기록 합
# test_period_starts_at_korean_midnight: 기간 시작 직전 job은 제외, 직후는 포함
# test_failure_counts_only_attempted_jobs: started_at 없는 failed는 playback_failed와 problems의 playback에서 제외되고
#   묶음 줄의 not_run == 개수, locked는 실패로 집계
# test_problems_lms_ignores_period_and_resolve_respects_it: 오래된 invalid LMS는 포함, 오래된 resolve 실패와
#   resolve_failed_at이 없는 줄은 제외
# test_problems_clean_errors_and_hide_secrets: 응답에 token=SECRET 없음, last_error_at 없는 LMS 줄의 occurred_at은 null
# test_activities_filter_by_student_and_kind: 필터와 페이지, student_id 포함, 응답에 오류 없음
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_admin_api.py -q`
Expected: FAIL

- [ ] **Step 3: `admin_data.py`와 세 엔드포인트 구현**

`clean_error`는 `https?://[^\s/]+\S*`를 `스킴://호스트`로 바꾼 뒤 2000자로 자른다. 일별 묶음은 DB 방언 차이를 피하려고 기간 안의 행을 가져와 Python에서 한국 날짜로 묶는다(최대 90일, 어드민 요청에서만). 시각이 naive면 UTC로 본다. 활동 UNION은 기존 `user_activities`에서 `activity_union(user_id: int | None)`로 뽑아 재사용한다. 실패 시각은 `coalesce(job.finished_at, batch.finished_at)`이다. `tracking_since`는 `UserDailyActivity.day`의 최솟값이다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_admin_api.py tests/test_api.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋** — `feat(admin): add overview, activity and problem APIs`

### Task 4: 사용자 목록과 상세 API

**Files:**
- Modify: `app/api/routes_admin.py`, `tests/test_admin_api.py`

**Interfaces:**
- Consumes: Task 3의 `clean_error`, `failed_job_filter`, `period_start`
- Produces: `GET /admin/users?q=&lms_status=&has_problem=&sort=&offset=&limit=`. `sort`는 `activity`(기본), `created`, `student_id`. 줄에 기존 필드와 `course_count, playback_done, playback_failed, download_count, transcript_count, channels: list[str]`을 더한다.
- Produces: `GET /admin/users/{student_id}/summary` → 학번, `is_admin`, 동의·가입·로그인 정보, `lms: {status, last_error, last_error_at, last_login_at, last_synced_at, next_sync_at}|null`, `counts: {courses, todos, playback_done, playback_failed, downloads, saved_videos, transcripts, channels}`
- Produces: 페이지 있는 `GET /admin/users/{student_id}/` 하위 경로 `courses`, `todos`, `assignments`, `playback`(`status`, `lms_course_id`, `days` 필터), `downloads`, `saved-videos`, `transcripts`, `notifications`. 응답은 `{total, items}`이고 필드는 스펙 5장 표를 따른다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
FORBIDDEN = ("PRIVATE", "password", "session_enc", "target_enc", "media_url", "token_version", "score", "points_possible", "segments")

# 이름과 확인할 내용
# test_users_list_counts_without_mixing_users: 두 사용자의 건수가 섞이지 않음(join 곱셈 없음)
# test_users_filters_and_sort: lms_status, has_problem(7일 안 실패 또는 LMS 비정상), sort 3종
# test_user_detail_routes_require_admin_and_404: 모든 하위 경로 401/403/없는 학번 404
# test_user_detail_never_exposes_private_fields: 모든 하위 경로 응답 본문에 FORBIDDEN 문자열 없음.
#   전사 text, 알림 target, 영상 media_url, 과제 score에 "PRIVATE" 값을 넣어 준비
# test_playback_tab_groups_batches_and_cleans_errors: 묶음 안에 job, 오류에 URL 경로 없음, 필터 동작
# test_saved_videos_report_only_whether_address_exists: has_media_url == True, media_url 키 없음
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_admin_api.py -q`
Expected: FAIL

- [ ] **Step 3: 구현**

목록의 건수는 사용자 페이지를 먼저 구한 뒤 `user_id IN (...)`으로 항목별 집계 쿼리를 한 번씩 돌려 합친다. 응답 모델은 필드를 직접 나열한다. 기간 조건 컬럼의 인덱스가 필요한지 로컬 Postgres의 `EXPLAIN`으로 보고, 필요하면 Task 1 다음 마이그레이션을 추가한다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_admin_api.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋** — `feat(admin): add user list metrics and detail APIs`

### Task 5: Postgres에서 어드민 쿼리 확인

**Files:**
- Create: `tests/test_admin_postgres.py`

- [ ] **Step 1: 테스트 작성**

`tests/test_queue_postgres.py`의 격리 스키마 방식과 `skipif(not os.getenv("QUEUE_TEST_DATABASE_URL"))`를 따른다. 재생, 다운로드, 전사, 저장 영상, LMS 오류가 있는 사용자를 만들고, Task 3, 4의 모든 GET 경로와 필터가 200인지, `bump_daily`를 두 번 호출해 합이 맞는지 확인한다.

- [ ] **Step 2: 실행**

Run: `QUEUE_TEST_DATABASE_URL=postgresql+asyncpg://lms:lms@127.0.0.1:5432/lms QUEUE_TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run pytest tests/test_admin_postgres.py -q`
Expected: 모두 PASS. 실패하면 UNION 형식 등 원인을 Task 3, 4 코드에서 고친다.

- [ ] **Step 3: 커밋** — `test(admin): run dashboard queries on PostgreSQL`

### Task 6: 어드민 화면 뼈대와 현황 탭

**Files:**
- Create: `app/web/admin.js`, `tests/test_admin_web.py`
- Modify: `app/web/index.html`, `app/web/app.js` (`navigate`, `initialView`, 456행 근처, 기존 `viewAdmin`, `loadAdminActivities`, `loadAdminSettings` 제거), `app/web/style.css`, `tests/test_web.py` (기존 어드민 테스트 3개를 새 화면 기준으로 옮김)

**Interfaces:**
- Consumes: Task 3 API
- Produces: `viewAdmin(path: string[])`, `state.adminPath`, `adminRequest(path, render)`(요청 번호로 늦은 응답을 버림), `adminTable(columns, rows)`, `adminPager(total, offset, limit, onPage)`, `adminState`(로딩·빈·오류+재시도 표시)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests.test_web`의 `dashboard` 픽스처를 쓴다. `data` 키는 쿼리 없는 경로다.

```python
# test_admin_tabs_routes_and_back: 탭 클릭 시 주소가 #admin/users, 뒤로 가기로 #admin/overview 화면 복귀
# test_admin_deep_link_survives_reload: #admin/problems를 직접 열고 reload해도 문제 탭 선택
# test_admin_non_admin_sends_no_requests: is_admin False면 /admin/ 요청 0건, 권한 안내 표시
# test_overview_cards_chart_and_problems: 지표 4개, 그래프 전환, 표 대안 존재, "YYYY-MM-DD부터 집계" 문구
# test_overview_empty_shows_dash: failure_rate null이면 "-", series 전부 0이어도 오류 없음
# test_stale_response_does_not_overwrite: 첫 요청 지연 후 탭을 바꿔도 현재 탭 유지
# test_overview_no_sideways_scroll: 375, 1440px에서 scrollWidth <= innerWidth
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_admin_web.py -q`
Expected: FAIL

- [ ] **Step 3: 라우팅 구현**

`parseHash(hash) -> {view, path}`를 `app.js`에 두고 `/`로 나눠 `decodeURIComponent`한다. `navigate`와 `initialView`, `hashchange` 처리가 모두 이를 쓴다. `#admin`은 `#admin/overview`로 바꾼다. 다른 화면의 동작은 변하지 않아야 한다. `.workspace`에 어드민일 때 `admin-workspace`(최대 폭 1200px)를 붙인다.

- [ ] **Step 4: 공통 조각과 현황 탭 구현**

시안 `admin-direction.html`의 A안을 따른다. `.admin-*` 스타일은 `style.css`에 토큰으로 추가한다. 그래프는 일별 재생 완료(기본), 로그인, 다운로드를 고르며, 각 막대에 값이 들어간 `title`과 접을 수 있는 표를 둔다. 다운로드 카드 설명은 `완료 보고 또는 링크 발급 횟수`다. `index.html`에 `/admin.js`를 `/app.js` 뒤에 불러온다.

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_admin_web.py tests/test_web.py tests/test_design_system_web.py -q`
Expected: 모두 PASS

- [ ] **Step 6: 커밋** — `feat(admin): replace admin view with dashboard overview`

### Task 7: 사용자 목록과 상세 화면

**Files:**
- Modify: `app/web/admin.js`, `app/web/style.css`, `tests/test_admin_web.py`

**Interfaces:**
- Consumes: Task 4 API, Task 6의 `adminRequest`, `adminTable`, `adminPager`, `adminState`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# test_users_table_search_filters_sort: 요청에 q, lms_status, has_problem, sort, offset이 들어가고 표에 건수 표시
# test_row_opens_detail_by_click_and_keyboard: 학번 링크로 #admin/users/{학번}, 목록 링크로 복귀
# test_detail_header_and_counts: 요약 숫자와 LMS 상태, 마지막 오류
# test_every_detail_tab_loads_and_paginates: 타임라인, 과목·출석, 할 일·과제, 재생, 다운로드, 저장 영상, 전사, 알림, LMS
# test_detail_escapes_server_strings: 제목과 오류의 <img> 태그가 요소로 만들어지지 않음
# test_unknown_student_shows_notice: 404면 "사용자를 찾을 수 없습니다"와 목록 링크
# test_student_id_with_special_chars_routes: "a/b#1" 형태도 인코딩되어 올바른 API를 부름
# test_detail_tables_fit_mobile: 375px에서 페이지 가로 스크롤 없음
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_admin_web.py -q`
Expected: FAIL

- [ ] **Step 3: 구현**

시안 `admin-users.html`을 따른다. 상세 탭 주소는 `#admin/users/{학번}/{탭}`이다. 주차별 출석은 기존 `.attend` 표시를 재사용한다. 760px 이하의 표는 줄마다 `data-label`로 라벨을 붙인 카드로 바꾼다. 줄 전체가 아니라 학번을 실제 링크로 두어 키보드로도 열 수 있게 한다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_admin_web.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋** — `feat(admin): add user list and detail screens`

### Task 8: 활동과 문제 탭, 문서 정리

**Files:**
- Modify: `app/web/admin.js`, `app/web/style.css`, `app/api/routes_admin.py` (쓰지 않게 된 `/settings` 제거), `tests/test_api.py`, `tests/test_admin_web.py`, `README.md`, `design-system/MASTER.md`

**Interfaces:**
- Consumes: Task 3 API, Task 6 공통 조각

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# test_activity_tab_filters_and_links_student: 종류, 학번, 기간이 요청에 들어가고 학번이 상세로 연결
# test_problems_tab_filters_and_shows_error: 종류, 학번, 과목, 기간 필터, 오류 표시, not_run이면 "미실행 N개"
# test_problem_without_time_says_no_record: occurred_at null이면 "시각 기록 없음"
# test_problem_tab_count_label: 탭 이름에 문제 건수, 현황 실패 카드와 별개
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_admin_web.py -q`
Expected: FAIL

- [ ] **Step 3: 구현과 정리**

두 탭을 구현한다. 새 화면이 쓰지 않는 `/admin/users/{id}/settings`와 그 테스트, 쓰지 않는 `.admin-settings`·`.admin-activity` 스타일을 지운다. `README.md`의 어드민 설명을 새 화면 기준으로 바꾸고 `MASTER.md`에 어드민 절을 추가한다(조회 전용, 넓은 폭, 표의 모바일 카드 변환, 민감 데이터 제외).

- [ ] **Step 4: 전체 검증**

Run: `uv run pytest -q && QUEUE_TEST_DATABASE_URL=postgresql+asyncpg://lms:lms@127.0.0.1:5432/lms QUEUE_TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run pytest tests/test_admin_postgres.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 실제 화면 확인**

가짜 데이터로 375, 768, 1024, 1440px의 네 탭과 상세 화면을 캡처해 시안 A와 비교한다. 겹침, 가로 스크롤, 잘린 표가 없어야 한다.

- [ ] **Step 6: 커밋** — `feat(admin): add activity and problem screens`
