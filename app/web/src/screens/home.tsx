/* 홈: 남은 할 일 전부 (#week-panel)
 * 들어오자마자 재생할 강의와 낼 과제를 한 화면에서 보고, 버튼 한 번으로 끝낸다.
 *  - 강의가 있으면: "강의 N개 모두 재생" → 순서대로 재생하고 학습 완료까지 확인(학칙 확인 창은 처음 한 번만)
 *  - 과제만 남았으면: 마감이 가장 빠른 과제의 제출 화면을 연다(과제는 직접 제출해야 한다)
 * 강의는 /playback/batches/preview?scope=all(app.ts가 state.dashboardPlan에 둔다),
 * 과제는 /week의 assignments + later_assignments + late_assignments에서 가져온다.
 *
 * app.ts는 renderWeek()(불러와서 그리기)와 drawWeek()(지금 상태로 다시 그리기)만 부른다.
 * Preact가 바뀐 부분만 고치므로 5초 폴링으로 다시 그려도 포커스와 펼친 과목이 그대로 남는다.
 */
import { type ComponentChildren, render } from "preact";
import { loadCore, navigate } from "../app";
import { ApiError, WEEKDAY, api, dayName, dueTime, fmtDuration, fmtTime, safeUrl, state, store } from "../core";
import type { Assignment, Batch, BatchDetail, Job, Plan, Week } from "../types";
import { Icon, PLAYBACK_WARNING, confirmPlayback, toast } from "../ui";
import { startPolling } from "./playback";

interface Lecture {
  kind: "video"; title: string; course: string; courseId: number; deadline: string | null;
  id: number; duration: number; module: string; atRisk: boolean; late?: false;
}
interface Work {
  kind: "assignment" | "quiz"; title: string; course: string; deadline: string | null; url: string | null;
  status: string; late: boolean; upcoming: string | null;
}
interface Group { kind: "group"; key: string; course: string; items: Lecture[]; deadline: string | null; late?: false }
type Row = Lecture | Work | Group;

const week: { data: Week | null; error: string | null; lastActive: number | null; open: Set<string> } =
  { data: null, error: null, lastActive: null, open: new Set() };

const panel = () => document.getElementById("week-panel");

export async function renderWeek(): Promise<void> {
  const el = panel();
  if (!el) return;
  if (!week.data && !week.error) render(<Loading />, el);
  try {
    week.data = await api<Week>("/week");
    week.error = null;
  } catch (err) {
    week.error = (err as Error).message;
  }
  week.lastActive = state.active?.id ?? null;
  drawWeek();
}

export function drawWeek(): void {
  const el = panel();
  if (!el || (!week.data && !week.error)) return;
  render(<Home />, el);
}

/* ---------- 계산 ---------- */

function whenText(iso: string): string {
  const d = new Date(iso);
  const name = dayName(d); // 오늘 / 내일 / 10월 10일 (토)
  const short = name.length <= 2 ? name : `${d.getMonth() + 1}/${d.getDate()}(${WEEKDAY[d.getDay()]})`;
  return `${short} ${fmtTime(d)}`;
}

/** 마감순. 이미 마감이 지난 지각 제출 과제는 가장 급하므로 맨 앞에 둔다. */
const byDue = (a: Row, b: Row) => Number(b.late === true) - Number(a.late === true) || dueTime(a) - dueTime(b);
const dueShort = (iso: string | null) => (iso ? whenText(iso) : "마감 없음");

/** 지금 재생할 수 있는 강의 전부(마감이 다음 주 이후이거나 없는 것 포함). */
function lectures(): Lecture[] {
  return (state.dashboardPlan?.items || []).map((i) => ({ kind: "video", title: i.title, course: i.course_name, courseId: i.lms_course_id,
    deadline: i.deadline, id: i.module_item_id, duration: i.duration, module: i.module_title, atRisk: i.at_risk }));
}

/** 아직 낼 수 있는 과제·퀴즈 전부. 지각 제출 과제는 LMS가 잠그는 시각까지를 보여 준다. */
function assignments(d: Week): Work[] {
  const map = (list: Assignment[] | undefined, late = false): Work[] => (list || []).map((a) => ({
    kind: a.kind === "quiz" ? "quiz" : "assignment", title: a.title, course: a.course_name, deadline: late ? a.lock_at : a.deadline,
    url: a.url, status: a.status, late, upcoming: a.status === "upcoming" ? a.unlock_at : null }));
  return [...map(d.late_assignments, true), ...map(d.assignments), ...map(d.later_assignments)].sort(byDue);
}

/** 같은 과목의 강의가 2개 이상이면 한 줄로 묶는다. 줄 순서는 가장 빠른 마감순이다. */
function groupRows(tasks: (Lecture | Work)[]): Row[] {
  const groups = new Map<string, Group>();
  const rows: Row[] = [];
  for (const t of tasks) {
    if (t.kind !== "video") { rows.push(t); continue; }
    const key = String(t.courseId ?? t.course);
    if (!groups.has(key)) { const g: Group = { kind: "group", key, course: t.course, items: [], deadline: null }; groups.set(key, g); rows.push(g); }
    groups.get(key)!.items.push(t);
  }
  return rows.map((r) => (r.kind === "group" && r.items.length === 1 ? r.items[0] : r))
    .map((r) => (r.kind === "group" ? { ...r, deadline: r.items.find((i) => i.deadline)?.deadline ?? null } : r))
    .sort(byDue);
}

const jobFor = (t: Lecture): Job | undefined =>
  state.active?.jobs?.find((j) => j.module_item_id === t.id && (t.courseId == null || j.lms_course_id === t.courseId));

/* ---------- 조각 ---------- */

const STATE_TONE = { on: "text-blue", done: "text-green", bad: "text-red", "": "text-ink-3" } as const;

function State({ tone = "", children }: { tone?: keyof typeof STATE_TONE; children: ComponentChildren }) {
  return <span class={`task-state ${tone} text-sm font-semibold ${STATE_TONE[tone]}`}>{children}</span>;
}

function JobBadge({ job }: { job?: Job }) {
  if (!job) return null;
  const pct = job.duration ? Math.min(100, Math.round((job.progress_seconds / job.duration) * 100)) : 0;
  switch (job.status) {
    case "playing": return <State tone="on">듣는 중 {pct}%</State>;
    case "done": case "skipped": return <State tone="done">완료</State>;
    case "failed": return <State tone="bad">실패</State>;
    case "locked": return <State tone="bad">잠김</State>;
    case "queued": return <State>대기</State>;
    default: return null;
  }
}

/** 묶음 줄의 상태 한 개: 듣는 중 > 실패 > 모두 완료 > 일부 완료 */
function GroupBadge({ items }: { items: Lecture[] }) {
  const jobs = items.map(jobFor).filter((j): j is Job => !!j);
  if (!jobs.length) return null;
  const playing = jobs.find((j) => j.status === "playing");
  if (playing) return <JobBadge job={playing} />;
  const failed = jobs.filter((j) => ["failed", "locked"].includes(j.status)).length;
  if (failed) return <State tone="bad">실패 {failed}</State>;
  const done = jobs.filter((j) => ["done", "skipped"].includes(j.status)).length;
  if (done === items.length) return <State tone="done">완료</State>;
  return <State>{done ? `${done}/${items.length} 완료` : "대기"}</State>;
}

/** 첫 줄(가장 급한 일)의 마감은 진하게, 지각 제출은 빨갛게. */
function Due({ first, late, children }: { first: boolean; late?: boolean; children: ComponentChildren }) {
  const tone = first ? "font-bold text-ink" : late ? "late font-semibold text-red" : "text-ink-2";
  return <span class={`task-due text-sm ${tone}`}>{children}</span>;
}

const ROW = "flex items-center gap-3 px-5 py-3.5 max-phone:px-4";
const MAIN = "task-main min-w-0 flex-1";
const TITLE = "task-title truncate font-semibold";
const SUB = "task-sub truncate text-sm text-ink-3";
const SIDE = "task-side flex shrink-0 flex-col items-end gap-1";

function GroupRow({ g, first, divided }: { g: Group; first: boolean; divided: boolean }) {
  const seconds = g.items.reduce((s, i) => s + (i.duration || 0), 0);
  return (
    <li class={`task task-group block ${divided ? "divide-line" : ""}`}>
      <details class="group" data-key={g.key} open={week.open.has(g.key)}
        onToggle={(e) => { const d = e.currentTarget as HTMLDetailsElement; if (d.open) week.open.add(g.key); else week.open.delete(g.key); }}>
        <summary class={`${ROW} min-h-touch cursor-pointer list-none transition-colors duration-150 hover:bg-fill-hover focus-visible:-outline-offset-4 motion-reduce:transition-none [&::-webkit-details-marker]:hidden ${first ? "bg-pink-soft" : ""}`}>
          <div class={MAIN}>
            <p class={TITLE}>{g.course}</p>
            <p class={SUB}>강의 {g.items.length}개{seconds ? ` · ${fmtDuration(seconds)}` : ""}</p>
          </div>
          <div class={SIDE}><Due first={first}>{dueShort(g.deadline)}</Due><GroupBadge items={g.items} /></div>
          <span class="chevron group-open:rotate-180" aria-hidden="true" />
        </summary>
        <ol class="task-sublist m-0 list-none pt-0 pr-5 pb-1.5 pl-9 max-phone:pr-4 max-phone:pl-6">
          {g.items.map((i) => (
            <li key={i.id} class="divide-line flex items-center gap-3 py-2.5">
              <div class={MAIN}>
                <p class="task-title font-medium wrap-anywhere">{i.title}</p>
                <p class="task-sub text-sm text-ink-3">
                  {[i.module, i.duration ? fmtDuration(i.duration) : "", i.atRisk ? "마감 전 완료가 어려울 수 있어요" : ""].filter(Boolean).join(" · ")}
                </p>
              </div>
              <div class={SIDE}><Due first={first}>{dueShort(i.deadline)}</Due><JobBadge job={jobFor(i)} /></div>
            </li>
          ))}
        </ol>
      </details>
    </li>
  );
}

const KIND_LABEL = { video: "강의", assignment: "과제", quiz: "퀴즈" } as const;

function TaskRow({ t, first, divided }: { t: Lecture | Work; first: boolean; divided: boolean }) {
  const job = t.kind === "video" ? jobFor(t) : undefined;
  const url = t.kind !== "video" ? safeUrl(t.url) : null;
  const submit = !job && t.kind !== "video" && url && t.status !== "upcoming";
  const sub = t.kind !== "video" && t.upcoming ? `${KIND_LABEL[t.kind]} · ${whenText(t.upcoming)}에 열려요`
    : `${KIND_LABEL[t.kind]} · ${t.course}${t.kind === "video" && t.duration ? ` · ${fmtDuration(t.duration)}` : ""}`;
  return (
    <li class={`task ${ROW} ${first ? "bg-pink-soft" : ""} ${divided ? "divide-line" : ""}`}>
      <div class={MAIN}><p class={TITLE}>{t.title}</p><p class={SUB}>{sub}</p></div>
      <div class={SIDE}>
        {t.late ? <Due first={first} late>지각 제출 {dueShort(t.deadline)}까지</Due> : <Due first={first}>{dueShort(t.deadline)}</Due>}
        {submit
          ? <a class="btn small" href={url!} target="_blank" rel="noopener" aria-label={`${t.title} LMS에서 제출 (새 탭)`}>LMS에서 제출 <Icon name="external" /></a>
          : <JobBadge job={job} />}
      </div>
    </li>
  );
}

const NOTE = "cta-note text-center text-sm text-ink-3";

/** 주요 동작 하나: 재생 중이면 진행 요약, 강의가 있으면 전부 재생, 과제만 있으면 가장 급한 과제 제출 */
function Cta({ videos, work }: { videos: Lecture[]; work: Work[] }) {
  const a = state.active;
  if (state.playbackStarting) return <button class="btn primary block" disabled>재생 요청 중…</button>;
  if (a) {
    return (
      <div class="cta-progress grid gap-2.5 rounded-card border-2 border-ink bg-surface p-5">
        <strong>강의 재생 중 {a.done_count + a.skipped_count}/{a.total}개</strong>
        <button class="btn" id="week-details" onClick={() => navigate("playback")}>재생 현황 보기</button>
      </div>
    );
  }
  if (videos.length) {
    const risky = videos.filter((v) => v.atRisk).length;
    const seconds = videos.reduce((s, v) => s + (v.duration || 0), 0);
    return (
      <>
        <button class="btn primary block" id="dashboard-play-all" disabled={state.dashboardLoading} onClick={confirmDashboardPlayback}>
          강의 {videos.length}개 모두 재생
        </button>
        <p class={NOTE}>{fmtDuration(seconds)} 분량 · 마감 빠른 순서로 1배속 재생{work.length ? " · 과제·퀴즈는 직접 제출해야 해요" : ""}</p>
        {risky > 0 && <p class="cta-note warning-text text-center text-sm font-semibold text-red">{risky}개는 마감 전에 다 듣지 못할 수 있어요</p>}
        {playbackConsented() && <p class={NOTE}>{PLAYBACK_WARNING}</p>}
      </>
    );
  }
  const next = work.find((x) => safeUrl(x.url) && x.status !== "upcoming");
  if (next) {
    return (
      <>
        <a class="btn primary block" href={safeUrl(next.url)!} target="_blank" rel="noopener">{next.title} LMS에서 제출 <Icon name="external" /></a>
        <p class={NOTE}>학교 LMS에서 제출하면 다음 동기화 때 목록에서 빠져요</p>
      </>
    );
  }
  return null;
}

const SECTION = "today grid gap-1.5";
const SUBTITLE = "today-sub text-sm text-ink-3";

function Loading() {
  return <section class={SECTION}><p class={SUBTITLE}>불러오는 중… 나도 하기 싫어…</p></section>;
}

function Heading({ title, sub }: { title: string; sub: string }) {
  return <><h1 id="week-heading" class="caption">{title}</h1><p class={SUBTITLE}>{sub}</p></>;
}

function Home() {
  if (week.error) {
    return (
      <section class={SECTION}>
        <h1 class="caption">할 일을 불러오지 못했어요</h1>
        <p class={SUBTITLE}>{week.error}</p>
        <div class="cta mt-4 grid gap-2"><button class="btn primary block" id="week-retry" onClick={renderWeek}>다시 불러오기</button></div>
      </section>
    );
  }
  const d = week.data!;
  const videos = lectures();
  const work = assignments(d);
  const remaining = [...videos, ...work].sort(byDue);
  const synced = !!state.cred?.last_synced_at;
  const planPending = !state.dashboardPlan && !state.dashboardError;

  const now = new Date();
  const today = `${now.getMonth() + 1}월 ${now.getDate()}일 ${WEEKDAY[now.getDay()]}요일`;
  let head, photo;
  if (!synced) {
    head = <Heading title="첫 동기화를 기다리고 있어요" sub="학교 LMS에서 과목과 할 일을 가져오는 중이에요." />;
    photo = "steam";
  } else if (remaining.length) {
    const first = remaining.find((t) => t.deadline && !t.late);  // 지각 제출 과제의 잠금 시각은 '마감'이 아니다
    const parts = [videos.length && `강의 ${videos.length}개`, work.length && `과제·퀴즈 ${work.length}개`].filter(Boolean).join(" · ");
    head = <Heading title={`아유… 할 일이 ${remaining.length}개 남았어요`} sub={`${parts}${first ? ` · 가장 빠른 마감 ${whenText(first.deadline!)}` : ""}`} />;
    photo = "think";
  } else if (state.dashboardError) {
    head = <Heading title="남은 강의를 확인하지 못했어요" sub="낼 과제는 없어요. 아래에서 다시 시도해 주세요." />;
    photo = "blank";
  } else if (planPending) {
    head = <Heading title="남은 강의를 확인하고 있어요" sub="잠깐만… 나도 하기 싫어…" />;
    photo = "think";
  } else {
    head = <Heading title="이번 주 할 일을 모두 끝냈어요. 누워도 됨" sub="새 강의나 과제가 열리면 여기에 알려 드릴게요." />;
    photo = "drool";
  }

  const cta = <Cta videos={videos} work={work} />;
  return (
    <section class={SECTION} aria-labelledby="week-heading">
      <div class="today-head flex items-end justify-between gap-4">
        <div class="today-title grid min-w-0 gap-1.5">
          <p class="today-date text-sm text-ink-3">{today}</p>
          {head}
        </div>
        <img class="today-photo size-32 shrink-0 rounded-btn border-2 border-ink object-cover max-phone:size-21"
          src={`/patrick-${photo}.webp`} alt="" width={128} height={128} />
      </div>
      <div class="cta mt-4 grid gap-2 empty:hidden">{cta}</div>
      {state.dashboardError && (
        <div class="notice-inline" role="alert">
          <p>{state.dashboardError}</p>
          <button class="btn" id="dashboard-playback-retry" onClick={loadDashboardPlayback}>다시 시도</button>
        </div>
      )}
      {remaining.length > 0 ? (
        <ul class="task-list mt-[18px] mb-0 list-none overflow-hidden rounded-card border-2 border-ink bg-surface p-0">
          {groupRows(remaining).map((r, i) => r.kind === "group"
            ? <GroupRow key={`g:${r.key}`} g={r} first={i === 0} divided={i > 0} />
            : <TaskRow key={r.kind === "video" ? `v:${r.courseId}:${r.id}` : `a:${r.course}:${r.title}:${r.deadline}`} t={r} first={i === 0} divided={i > 0} />)}
        </ul>
      ) : state.dashboardLoading && !state.dashboardPlan ? (
        <div class="task-list mt-[18px] overflow-hidden rounded-card border-2 border-ink bg-surface p-0" aria-busy="true">
          <p class="sr-only" role="status">강의 목록을 불러오는 중이에요</p>
          <div class="skeleton-list" aria-hidden="true">{[0, 1, 2].map((n) => <div key={n} class="skeleton" />)}</div>
        </div>
      ) : null}
    </section>
  );
}

/* ---------- 강의 전부 재생 ---------- */

/** 재생할 수 있는 강의 전부(state.dashboardPlan)를 불러온다. 그 사이 화면을 떠나거나 로그아웃했으면 버린다. */
export async function loadDashboardPlayback(): Promise<void> {
  const root = panel();
  if (!root) return;
  const token = state.token;
  const valid = () => root.isConnected && state.token === token && state.view === "dashboard";
  state.dashboardLoading = true;
  state.dashboardError = null;
  drawWeek();
  try {
    const plan = await api<Plan>("/playback/batches/preview?scope=all");
    if (valid()) state.dashboardPlan = plan;
  } catch (err) {
    if (valid()) state.dashboardError = (err as Error).message;
  } finally {
    if (valid()) {
      state.dashboardLoading = false;
      drawWeek();
    }
  }
}

/* 학칙 확인 창은 처음 한 번만 띄운다. 동의한 뒤에는 버튼 한 번으로 바로 재생하고, 경고는 버튼 아래 글로 계속 보인다. */
const PLAYBACK_CONSENT_KEY = "playback-consent-v1";
const playbackConsented = () => store.get(PLAYBACK_CONSENT_KEY) === "1";

async function confirmDashboardPlayback(): Promise<void> {
  if (state.playbackStarting || state.active || !state.dashboardPlan?.items.length) return;
  if (!playbackConsented()) {
    if (!(await confirmPlayback(state.dashboardPlan, "전체"))) return;
    store.set(PLAYBACK_CONSENT_KEY, "1");
  }
  await startDashboardPlayback();
}

async function startDashboardPlayback(): Promise<void> {
  if (state.playbackStarting || state.active || !state.dashboardPlan?.items.length) return;
  const token = state.token;
  state.playbackStarting = true;
  drawWeek();
  try {
    const batch = await api<Batch>("/playback/batches", { method: "POST", json: { scope: "all" } });
    if (state.token !== token) return;
    // 이어지는 상세 요청이 실패해도 접수된 대기열 항목은 지킨다.
    state.active = { ...batch, jobs: [] };
    startPolling();
    toast(`강의 ${batch.total}개를 재생 대기열에 등록했습니다`);
    try {
      const detail = await api<BatchDetail>(`/playback/batches/${batch.id}`);
      if (state.token === token && state.active?.id === batch.id) state.active = detail;
    } catch { /* 진행 상황은 다음 폴링에서 다시 조회한다. */ }
  } catch (err) {
    if (state.token !== token) return;
    toast((err as Error).message, true);
    const status = (err as ApiError).status;
    if (status === 409) {
      try { await loadCore(); } catch (refreshError) { toast((refreshError as Error).message, true); }
    }
    if (status === 404) await loadDashboardPlayback();
  } finally {
    if (state.token === token) {
      state.playbackStarting = false;
      drawWeek();
    }
  }
}

// 재생 진행률을 갱신하고(app.ts 폴링이 state.active를 갱신), 끝나면 목록을 새로 불러온다.
setInterval(() => {
  if (!panel()) return;
  const activeId = state.active?.id ?? null;
  if (week.lastActive !== null && activeId === null) { renderWeek(); loadDashboardPlayback(); }
  else if (activeId !== null) { week.lastActive = activeId; drawWeek(); }
}, 5000);
