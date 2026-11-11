/* 관리자 사용자 상세(#admin/users/{학번}[/{탭}]): 요약 머리(권한 버튼) + 9개 기록 탭.
 * 요약은 adminUi.detail에 두고 탭을 옮겨도 다시 받지 않는다. 탭별 조건과 페이지 위치도 그 사용자에게 남긴다. */
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { viewHash } from "../../app";
import { KIND, fmtDuration, fmtSize, state } from "../../core";
import { Attendance } from "../courses";
import {
  BackIcon,
  ADMIN_BATCH_SCOPE, ADMIN_BATCH_STATUS, ADMIN_CHANNEL, ADMIN_PERIODS, ADMIN_PLAYBACK_STATUS, AccessButtons, AccessTag, Day,
  EmptyText, ErrorBox, JobTag, KindSeg, LmsTag, Loaded, Pager, type Row, Skeleton, Table, Tag, Time, activityLabel, adminNumber,
  adminUi, adminUserPath, courseTitle, useAdmin, useRefocusKind,
} from "./shared";

const LIMIT = 20;
const TABS = [
  { key: "timeline", label: "전체 타임라인" }, { key: "courses", label: "과목·출석" }, { key: "todos", label: "할 일·과제" },
  { key: "playback", label: "재생" }, { key: "downloads", label: "다운로드" }, { key: "saved", label: "저장 영상" },
  { key: "transcripts", label: "전사" }, { key: "notifications", label: "알림" }, { key: "lms", label: "LMS" },
];
const PLAYBACK_FILTER = ["failed", "locked", "done", "queued", "playing", "skipped", "cancelled"];  // API가 받는 job 상태 순서
const SUBMISSION: Record<string, string> = { unsubmitted: "미제출", submitted: "제출함", excused: "면제", unknown: "확인 불가" };
const DOWNLOAD_KIND: Record<string, string> = { video: "영상", commons_file: "강의 자료", canvas_file: "첨부 파일" };

type TabState = { offsets: Record<string, number>; kind?: string; status?: string; course?: string; days?: string };

const When = ({ iso, none = "-" }: { iso?: string | null; none?: string }) => <Time iso={iso} none={none} />;
const Err = ({ children }: { children: ComponentChildren }) => <span class="admin-err">{children}</span>;

/* ---------- 머리 ---------- */

function Kpi({ label, value, bad }: { label: string; value: number; bad?: boolean }) {
  return <div class={`card admin-kpi${bad ? " bad" : ""}`}><span class="admin-kpi-label">{label}</span><strong>{adminNumber(value)}</strong></div>;
}

function Head({ s, reload }: { s: Row; reload: () => void }) {
  const lms = s.lms;
  return (
    <>
      <div class="admin-who"><h2>{s.student_id}</h2>{s.is_admin ? <Tag label="관리자" tone="warn" /> : <Tag label="일반 사용자" />}
        <AccessTag status={s.access_status} /><AccessButtons id={s.student_id} status={s.access_status} isAdmin={s.is_admin} reload={reload} /></div>
      <p class="admin-sub">가입 <Day iso={s.created_at} /> · 로그인 {adminNumber(s.login_count)}회 · 최근 로그인 <When iso={s.last_login_at} none="기록 없음" /> · 이용 동의 <Day iso={s.consent_at} /></p>
      <p class="admin-sub"><LmsTag status={lms?.status} prefix="LMS " />{lms && <> 동기화 <When iso={lms.last_synced_at} none="기록 없음" /></>}
        {lms?.last_error && <> <Err>마지막 오류: {lms.last_error}</Err></>}</p>
      <div class="admin-kpis admin-sum">
        <Kpi label="수강 과목" value={s.counts.courses} /><Kpi label="남은 할 일" value={s.counts.todos} /><Kpi label="재생 완료" value={s.counts.playback_done} />
        <Kpi label="재생 실패" value={s.counts.playback_failed} bad={!!s.counts.playback_failed} /><Kpi label="다운로드" value={s.counts.downloads} />
      </div>
    </>
  );
}

function LmsTab({ s }: { s: Row }) {
  const l = s.lms;
  if (!l) return <EmptyText>LMS 계정을 연결하지 않았습니다.</EmptyText>;
  const row = (k: string, v: ComponentChildren) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>;
  return (
    <>
      <h3 class="sr-only">LMS</h3>
      <dl class="admin-dl">
        {row("상태", <LmsTag status={l.status} />)}
        {row("마지막 오류", l.last_error ? <><Err>{l.last_error}</Err> <When iso={l.last_error_at} none="" /></> : "없음")}
        {row("마지막 로그인", <When iso={l.last_login_at} none="기록 없음" />)}
        {row("마지막 동기화", <When iso={l.last_synced_at} none="기록 없음" />)}
        {row("다음 동기화", <When iso={l.next_sync_at} none="예정 없음" />)}
      </dl>
    </>
  );
}

/* ---------- 페이지 단위 목록 ---------- */

/**
 * 상세 탭의 한 목록. st.offsets[name]에 지금 위치를 둔다(같은 사용자의 다른 탭에 다녀와도 남는다).
 * 넘칠 때만 페이지 버튼을 붙인다. params는 추가 조건(바뀌면 다시 불러온다).
 */
function Paged({ path, st, name, params = {}, again, children, part, cls = "contents" }: {
  path: string; st: TabState; name: string; params?: Record<string, string>; again: number; part?: string;
  /** 탭 카드에 바로 그리는 목록은 contents(카드의 간격을 따른다), 한 탭에 여럿이거나 위에 버튼이 붙으면 admin-paged. */
  cls?: string;
  children: (d: Row) => ComponentChildren;
}) {
  const [, redraw] = useState(0);
  const offset = st.offsets[name] || 0;
  const q = new URLSearchParams({ ...params, offset: String(offset), limit: String(LIMIT) });
  const [load, reload] = useAdmin(`${path}?${q}`, again);
  return (
    <Loaded load={load} retry={reload} cls={cls} attrs={part ? { "data-part": part } : undefined}>
      {(d: Row) => (
        <>
          {children(d)}
          {(d.total > LIMIT || offset > 0) && <Pager total={d.total} offset={offset} limit={LIMIT} onPage={(o) => { st.offsets[name] = o; redraw((n) => n + 1); }} />}
        </>
      )}
    </Loaded>
  );
}

const rowsOr = (rows: Row[], message: string, table: () => ComponentChildren) => (rows.length ? table() : <EmptyText>{message}</EmptyText>);

/* ---------- 탭 ---------- */

const TIMELINE_KINDS = { all: "전체", playback: "재생", download: "다운로드", transcript: "전사" };

function Timeline({ id, st, again }: TabProps) {
  st.kind ||= "all";
  const [, redraw] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const refocus = useRefocusKind(root, st.kind, true);
  return (
    <div class="contents" ref={root}>
      <Paged path={`${adminUserPath(id)}/activities`} st={st} name="timeline" again={again} cls="admin-paged" params={st.kind === "all" ? {} : { kind: st.kind }}>
        {(d) => (
          <>
            <KindSeg kinds={TIMELINE_KINDS} counts={d.counts} current={st.kind!} label="활동 종류"
              onPick={(k) => { st.kind = k; st.offsets.timeline = 0; refocus(); redraw((n) => n + 1); }} />
            {rowsOr(d.activities, "기록된 활동이 없습니다.", () => (
              <Table caption="전체 타임라인" rows={d.activities} columns={[
                { label: "시각", render: (a) => <Time iso={a.recorded_at} />, cls: "admin-nowrap" },
                { label: "종류", render: (a) => (a.kind === "playback" ? <JobTag job={a} /> : <Tag label={activityLabel(a)} />) },
                { label: "과목 · 제목", render: (a) => courseTitle(a.course_name, a.title) },
              ]} />
            ))}
          </>
        )}
      </Paged>
    </div>
  );
}

function Courses({ id, st, again }: TabProps) {
  return (
    <Paged path={`${adminUserPath(id)}/courses`} st={st} name="courses" again={again}>
      {(d) => rowsOr(d.items, "과목이 없습니다.", () => (
        <Table caption="과목과 출석" rows={d.items} columns={[
          { label: "과목", render: (c) => c.name },
          { label: "교수", render: (c) => c.professors || "-" },
          { label: "학기", render: (c) => (c.active ? <Tag label="현재 학기" tone="ok" /> : <Tag label="지난 학기" />) },
          { label: "자동 재생", render: (c) => (c.auto_play ? "켜짐" : "꺼짐") },
          { label: "주차별 출석", render: (c) => <Attendance attendance={c.attendance} empty="출석 기록 없음" /> },
        ]} />
      ))}
    </Paged>
  );
}

function Todos({ id, st, again }: TabProps) {
  return (
    <>
      <section aria-labelledby="admin-todos-title"><h3 id="admin-todos-title">할 일</h3>
        <Paged path={`${adminUserPath(id)}/todos`} st={st} name="todos" again={again} part="todos" cls="admin-paged">
          {(d) => rowsOr(d.items, "할 일이 없습니다.", () => (
            <Table caption="할 일" rows={d.items} columns={[
              { label: "종류", render: (t) => KIND[t.kind] || t.kind },
              { label: "과목 · 제목", render: (t) => courseTitle(t.course_name, t.title) },
              { label: "마감", render: (t) => <When iso={t.deadline} none="마감 없음" />, cls: "admin-nowrap" },
              // active가 false면 동기화에서 사라졌다는 뜻일 뿐이라 완료로 쓰지 않는다
              { label: "상태", render: (t) => <Tag label={t.active ? "남아 있음" : "목록에서 사라짐(완료 또는 마감)"} /> },
            ]} />
          ))}
        </Paged>
      </section>
      <section aria-labelledby="admin-assign-title"><h3 id="admin-assign-title">과제</h3>
        <Paged path={`${adminUserPath(id)}/assignments`} st={st} name="assignments" again={again} part="assignments" cls="admin-paged">
          {(d) => rowsOr(d.items, "과제가 없습니다.", () => (
            <Table caption="과제" rows={d.items} columns={[
              { label: "종류", render: (a) => KIND[a.kind] || a.kind },
              { label: "과목 · 제목", render: (a) => courseTitle(a.course_name, a.title) },
              { label: "제출 상태", render: (a) => <Tag label={SUBMISSION[a.submission_state] || a.submission_state} tone={a.submission_state === "submitted" ? "ok" : ""} /> },
              { label: "제출 시각", render: (a) => <When iso={a.submitted_at} />, cls: "admin-nowrap" },
              { label: "마감", render: (a) => <When iso={a.deadline} none="마감 없음" />, cls: "admin-nowrap" },
              { label: "미제출 여부", render: (a) => (a.missing ? <Tag label="마감 지나 미제출" tone="bad" /> : "-") },
            ]} />
          ))}
        </Paged>
      </section>
    </>
  );
}

function Batch({ b, courses }: { b: Row; courses: Map<number, string> }) {
  const status = ADMIN_BATCH_STATUS[b.status] || { label: b.status, tone: "" };
  const scope = ADMIN_BATCH_SCOPE[b.scope] || b.scope;
  return (
    <article class="admin-batch">
      <div class="admin-batch-head"><h3>{scope} 재생{b.lms_course_id != null && <> · <span data-course={b.lms_course_id}>{courses.get(b.lms_course_id) || `과목 ${b.lms_course_id}`}</span></>}</h3>
        <Tag label={status.label} tone={status.tone} /></div>
      <p class="admin-sub">완료 {adminNumber(b.done_count)} · 실패 {adminNumber(b.failed_count)} · 건너뜀 {adminNumber(b.skipped_count)} / 전체 {adminNumber(b.total)}
        {" "}· 생성 <Time iso={b.created_at} /> · 시작 <When iso={b.started_at} none="기록 없음" /> · 종료 <When iso={b.finished_at} none="기록 없음" /></p>
      {b.error && <p class="admin-err">{b.error}</p>}
      {b.jobs.length ? (
        <Table caption={`${scope} 재생 묶음의 영상`} rows={b.jobs} columns={[
          { label: "영상", render: (j) => <>{j.title}{j.error && <div class="admin-err">{j.error}</div>}</> },
          { label: "길이", render: (j) => fmtDuration(j.duration), cls: "admin-nowrap" },
          { label: "상태", render: (j) => <JobTag job={j} /> },
          { label: "시도", render: (j) => `${adminNumber(j.attempts)}회` },
          { label: "진행", render: (j) => `${fmtDuration(j.progress_seconds || 0)} / ${fmtDuration(j.duration)}`, cls: "admin-nowrap" },
          { label: "시작", render: (j) => <When iso={j.started_at} />, cls: "admin-nowrap" },
          { label: "종료", render: (j) => <When iso={j.finished_at} />, cls: "admin-nowrap" },
        ]} />
      ) : <EmptyText>묶음에 영상이 없습니다.</EmptyText>}
    </article>
  );
}

function Playback({ id, st, again }: TabProps) {
  st.status ??= ""; st.course ??= ""; st.days ??= "";
  const [, redraw] = useState(0);
  // 과목 필터와 묶음의 과목 이름용. 실패해도 재생 기록은 그대로 보인다.
  const [courseLoad] = useAdmin(`${adminUserPath(id)}/courses?limit=100`);
  const courseList: Row[] = courseLoad.kind === "ok" ? courseLoad.data.items : [];
  const courses = new Map<number, string>(courseList.map((c) => [c.lms_course_id, c.name]));
  const params: Record<string, string> = {};
  if (st.status) params.status = st.status;
  if (st.course) params.lms_course_id = st.course;
  if (st.days) params.days = st.days;
  const change = (e: Event) => {
    const form = e.currentTarget as HTMLFormElement;
    const v = (n: string) => (form.elements.namedItem(n) as HTMLSelectElement).value;
    st.status = v("status"); st.course = v("course"); st.days = v("days");
    st.offsets.playback = 0;
    redraw((n) => n + 1);
  };
  return (
    <>
      <form class="admin-filters" aria-label="재생 기록 조건" onSubmit={(e) => e.preventDefault()} onChange={change}>
        <label>상태<select class="select" name="status" value={st.status}><option value="">전체</option>
          {PLAYBACK_FILTER.map((k) => <option key={k} value={k}>{ADMIN_PLAYBACK_STATUS[k]}</option>)}</select></label>
        <label>과목<select class="select" name="course" value={st.course}><option value="">전체</option>
          {courseList.map((c) => <option key={c.lms_course_id} value={String(c.lms_course_id)}>{c.name}</option>)}</select></label>
        <label>기간<select class="select" name="days" value={st.days}><option value="">전체 기간</option>
          {ADMIN_PERIODS.map((d) => <option key={d} value={String(d)}>최근 {d}일</option>)}</select></label>
      </form>
      <Paged path={`${adminUserPath(id)}/playback`} st={st} name="playback" again={again} params={params} cls="admin-paged">
        {(d) => rowsOr(d.items, "조건에 맞는 재생 기록이 없습니다.", () => d.items.map((b: Row) => <Batch key={b.id} b={b} courses={courses} />))}
      </Paged>
    </>
  );
}

function Downloads({ id, st, again }: TabProps) {
  return (
    <Paged path={`${adminUserPath(id)}/downloads`} st={st} name="downloads" again={again}>
      {(d) => rowsOr(d.items, "다운로드 기록이 없습니다.", () => (
        <Table caption="다운로드" rows={d.items} columns={[
          { label: "제목", render: (r) => r.title },
          { label: "종류", render: (r) => DOWNLOAD_KIND[r.kind] || r.kind },
          { label: "횟수", render: (r) => `${adminNumber(r.count)}회` },
          { label: "처음", render: (r) => <Time iso={r.first_at} />, cls: "admin-nowrap" },
          { label: "마지막", render: (r) => <Time iso={r.last_at} />, cls: "admin-nowrap" },
        ]} />
      ))}
    </Paged>
  );
}

function Saved({ id, st, again }: TabProps) {
  return (
    <Paged path={`${adminUserPath(id)}/saved-videos`} st={st} name="saved" again={again}>
      {(d) => rowsOr(d.items, "저장한 영상이 없습니다.", () => (
        <Table caption="저장 영상" rows={d.items} columns={[
          { label: "영상", render: (v) => <>{v.title}<div class="admin-sub">{v.course_name} · {v.module_title}</div></> },
          { label: "파일명", render: (v) => v.file_name },
          { label: "길이 · 용량", render: (v) => [v.duration ? fmtDuration(v.duration) : "", fmtSize(v.size)].filter(Boolean).join(" · ") || "-" },
          // 주소 자체는 API가 주지 않는다. 있는지만 보여 준다.
          { label: "주소", render: (v) => <>{v.has_media_url ? <Tag label="주소 있음" tone="ok" /> : <Tag label="주소 없음" tone="warn" />}{v.resolve_error && <div class="admin-err">{v.resolve_error}</div>}</> },
          { label: "확보 시각", render: (v) => (v.resolved_at ? <Time iso={v.resolved_at} /> : v.resolve_failed_at ? <>실패 <Time iso={v.resolve_failed_at} /></> : "-"), cls: "admin-nowrap" },
        ]} />
      ))}
    </Paged>
  );
}

function Transcripts({ id, st, again }: TabProps) {
  return (
    <Paged path={`${adminUserPath(id)}/transcripts`} st={st} name="transcripts" again={again}>
      {(d) => rowsOr(d.items, "전사 기록이 없습니다.", () => (
        <Table caption="전사" rows={d.items} columns={[
          { label: "과목 · 제목", render: (t) => courseTitle(t.course_name, t.title) },
          { label: "모델", render: (t) => t.model },
          { label: "길이", render: (t) => (t.duration ? fmtDuration(t.duration) : "-") },
          { label: "생성", render: (t) => <Time iso={t.created_at} />, cls: "admin-nowrap" },
          { label: "수정", render: (t) => <Time iso={t.updated_at} />, cls: "admin-nowrap" },
        ]} />
      ))}
    </Paged>
  );
}

function Notifications({ id, st, again }: TabProps) {
  return (
    <Paged path={`${adminUserPath(id)}/notifications`} st={st} name="notifications" again={again}>
      {(d) => (
        <>
          <h3>알림 채널</h3>
          {rowsOr(d.channels, "등록한 알림 채널이 없습니다.", () => (
            <Table caption="알림 채널" rows={d.channels} columns={[
              { label: "종류", render: (c) => ADMIN_CHANNEL[c.kind] || c.kind },
              { label: "사용", render: (c) => (c.enabled ? <Tag label="사용 중" tone="ok" /> : <Tag label="꺼짐" />) },
              { label: "등록일", render: (c) => <Day iso={c.created_at} /> },
            ]} />
          ))}
          <h3>발송 기록 <span class="admin-count">{adminNumber(d.total)}건</span></h3>
          {rowsOr(d.items, "발송 기록이 없습니다.", () => (
            <Table caption="알림 발송 기록" rows={d.items} columns={[
              { label: "알림 키", render: (n) => <code>{n.key}</code> },
              { label: "시각", render: (n) => <Time iso={n.created_at} />, cls: "admin-nowrap" },
            ]} />
          ))}
        </>
      )}
    </Paged>
  );
}

interface TabProps { id: string; st: TabState; again: number }

const TAB_VIEWS: Record<string, (p: TabProps) => ComponentChildren> = {
  timeline: Timeline, courses: Courses, todos: Todos, playback: Playback, downloads: Downloads,
  saved: Saved, transcripts: Transcripts, notifications: Notifications,
};

/* ---------- 화면 ---------- */

export function UserDetail({ id, tabKey, again, onNotFound }: { id: string; tabKey?: string; again: number; onNotFound: () => void }) {
  let tab = tabKey || "timeline";
  const unknown = !TABS.some((t) => t.key === tab);
  if (unknown) tab = "timeline";
  useEffect(() => {
    if (unknown) { state.adminPath = ["users", id]; history.replaceState(null, "", viewHash()); }
  }, [unknown, id]);

  const viewer = state.me!.student_id;
  if (adminUi.detail?.viewer !== viewer || adminUi.detail.id !== id) adminUi.detail = { viewer, id, summary: null, tabs: {} };
  const detail = adminUi.detail;
  const st: TabState = (detail.tabs[tab] ||= { offsets: {} });
  const label = TABS.find((t) => t.key === tab)!.label;

  // 요약은 탭을 옮겨도 다시 받지 않는다: 처음 그릴 때 저장해 둔 것이 있으면 쓰고, 다시 불러오기·권한 처리 뒤에만 새로 받는다.
  const [useCache, setUseCache] = useState(!!detail.summary);
  const [summaryLoad, retrySummary] = useAdmin(useCache ? null : `${adminUserPath(id)}/summary`, again);
  const firstAgain = useRef(again);
  useEffect(() => { if (again !== firstAgain.current) setUseCache(false); }, [again]);
  useEffect(() => { if (summaryLoad.kind === "ok") detail.summary = summaryLoad.data; }, [summaryLoad]);
  const summary = useCache ? detail.summary : summaryLoad.kind === "ok" ? summaryLoad.data : null;
  const notFound = !summary && summaryLoad.kind === "error" && summaryLoad.status === 404;
  useEffect(() => { if (notFound) onNotFound(); }, [notFound]);
  const reloadSummary = () => { detail.summary = null; setUseCache(false); retrySummary(); };

  if (notFound) {
    return (
      <div class="card admin-box" role="alert"><p><strong>사용자를 찾을 수 없습니다.</strong> 학번을 확인하세요.</p>
        <a class="admin-more" href="#admin/users">사용자 목록으로</a></div>
    );
  }
  const headState = summary ? null : summaryLoad;
  const View = TAB_VIEWS[tab];
  return (
    <>
      <a class="admin-back" href="#admin/users"><BackIcon />사용자 목록</a>
      <section class="admin-user-head" aria-label="사용자 요약" aria-busy={headState?.kind === "loading" ? "true" : undefined}>
        {summary ? <Head s={summary} reload={reloadSummary} />
          : headState?.kind === "error" ? <ErrorBox message={headState.message} retry={retrySummary} /> : <Skeleton rows={2} />}
      </section>
      <nav class="admin-subtabs tabs tabs-box" aria-label="사용자 기록">
        {TABS.map((t) => (
          <a key={t.key} class="tab admin-tab" href={`#admin/users/${encodeURIComponent(id)}/${t.key}`} data-admin-subtab={t.key}
            aria-current={t.key === tab ? "page" : undefined}>{t.label}</a>
        ))}
      </nav>
      <section class="card admin-box admin-user-tab" aria-label={label}
        aria-busy={tab === "lms" && !summary && headState?.kind === "loading" ? "true" : undefined}>
        {tab === "lms"
          ? (summary ? <LmsTab s={summary} /> : headState?.kind === "error" ? <ErrorBox message={headState.message} retry={retrySummary} /> : <Skeleton />)
          : <View id={id} st={st} again={again} />}
      </section>
    </>
  );
}
