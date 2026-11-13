/* 강의 재생 화면(#playback)과 재생 묶음 폴링.
 * - 진행 중인 묶음이 없으면: 범위(주차·과목·전체)를 골라 재생 계획을 보고 시작한다.
 * - 진행 중이면: 지금 강의(위 카드) + 재생 목록(2개 이상일 때)만 보여 준다. 5초마다 서버에서 새로 받는다.
 * - 지난 재생: 실패한 묶음은 펼칠 때 영상별 오류를 불러온다.
 * 폴링(startPolling/stopPolling/finishActive)과 재생 중지(cancelActivePlayback)는 홈·진행 패널도 쓴다.
 */
import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import { needCred } from "../app";
import { ApiError, BATCH_STATUS, GAP_SECONDS, JOB_STATUS, api, dueText, dueTime, fmtDate, fmtDuration, state } from "../core";
import { renderTaskPanel } from "../tasks";
import type { Batch, BatchDetail, Job, Plan } from "../types";
import { PLAYBACK_WARNING, confirmDialog, toast } from "../ui";
import { Empty, Panel, PanelBody, Section, ViewHead, Warning } from "../ui/layout";
import { drawWeek, loadDashboardPlayback, renderWeek } from "./home";
import { AutoPlayCourses } from "./courses";
import { mountScreen } from "./mount";

export function viewPlayback(): void {
  if (needCred()) return;
  mountScreen("playback", <PlaybackView />);
}

function PlaybackView() {
  return (
    <>
      <ViewHead title="강의 재생" />
      {state.active ? <ActiveBatch b={state.active} /> : <PlanSection />}
      <AutoPlayCourses />
      <History />
    </>
  );
}

/* ---------- 진행률 계산 ---------- */

/** 재생 시간은 확인된 완료와 지금 강의가 보고한 위치를 합친다. */
function jobProgressPercent(job: Job): number {
  if (["done", "skipped"].includes(job.status)) return 100;
  if (job.status !== "playing" || !(job.duration > 0)) return 0;
  return Math.min(100, Math.max(0, (Number(job.progress_seconds) || 0) / job.duration * 100));
}

function playbackPosition(seconds: number): string {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}분 ${value % 60}초`;
}

function playbackOverallPercent(jobs: Job[]): number {
  const duration = jobs.reduce((sum, job) => sum + Math.max(0, job.duration || 0), 0);
  const played = jobs.reduce((sum, job) => sum + Math.max(0, job.duration || 0) * jobProgressPercent(job) / 100, 0);
  return duration > 0 ? Math.floor(played / duration * 100) : 0;
}

/** 진행 중인 묶음의 강의별 예상 종료 시각. 마감 전에 끝나지 않을 강의를 표시하는 데 쓴다. */
function playbackEnds(b: BatchDetail): Map<number, number> {
  let cursor = Date.now();
  const ends = new Map<number, number>();
  for (const j of b.jobs || []) {
    if (j.status !== "queued" && j.status !== "playing") continue;
    const remaining = j.status === "playing" ? Math.max(0, j.duration - j.progress_seconds) : j.duration;
    cursor = j.expected_end ? new Date(j.expected_end).getTime() : cursor + (remaining + GAP_SECONDS) * 1000;
    ends.set(j.module_item_id, cursor);
  }
  return ends;
}

const courseName = (job: Job) => state.courses.find((course) => course.lms_course_id === job.lms_course_id)?.name || "";

/* ---------- 계획 ---------- */

const videoTodos = () => state.todos.filter((t) => t.kind === "video");

interface PlanForm { scope: string; course: string; module: string }

function modulesOf(course: string): [number | null, string | null][] {
  return [...new Map(videoTodos().filter((t) => String(t.lms_course_id) === course).map((t) => [t.module_id, t.module_title])).entries()];
}

/** 선택 상자의 첫 옵션 값. 옵션 값과 똑같이 String()으로 만든다(주차 id가 없으면 "undefined"). */
const firstModule = (modules: [number | null, string | null][]) => (modules.length ? String(modules[0][0]) : "");

function PlanSection() {
  const videos = videoTodos();
  const courses = [...new Map(videos.map((t) => [t.lms_course_id, t.course_name])).entries()];
  const [form, setForm] = useState<PlanForm>(() => {
    const saved: Partial<PlanForm> = state.planForm || {};
    const course = saved.course ?? String(courses[0]?.[0] ?? "");
    const modules = modulesOf(course);
    const module = saved.module && modules.some(([id]) => String(id) === saved.module) ? saved.module : firstModule(modules);
    return { scope: saved.scope ?? "module", course, module };
  });
  if (!videos.length) return <Section id="plan-title" title="골라서 재생"><p class="text-sm text-ink-3">기간 안에 남은 영상이 없습니다.</p></Section>;
  const modules = modulesOf(form.course);
  const set = (patch: Partial<PlanForm>) => setForm((f) => {
    const next = { ...f, ...patch };
    if (patch.course !== undefined) next.module = firstModule(modulesOf(next.course));  // 과목을 바꾸면 첫 주차로
    return next;
  });

  const submit = async (e: Event) => {
    e.preventDefault();
    state.planForm = form;
    const q = new URLSearchParams({ scope: form.scope });
    if (form.scope !== "all") q.set("course_id", form.course);
    if (form.scope === "module") q.set("module_id", form.module);
    try {
      state.plan = await api<Plan>(`/playback/batches/preview?${q}`);
      state.planQuery = Object.fromEntries(q);
      viewPlayback();
    } catch (err) { toast((err as Error).message, true); }
  };

  return (
    <>
      <Section id="plan-title" title="골라서 재생" note="1배속으로 처음부터 끝까지 재생하고, 영상마다 LMS에서 완료로 바뀌었는지 확인합니다.">
        <form class="grid gap-3" id="plan-form" onSubmit={submit}>
          <div class="flex flex-wrap items-center gap-2">
            <label>범위<select class="select" name="scope" value={form.scope} onChange={(e) => set({ scope: e.currentTarget.value })}>
              <option value="module">주차 하나</option><option value="course">과목 하나</option><option value="all">모든 과목</option></select></label>
            <label>과목<select class="select" name="course" value={form.course} disabled={form.scope === "all"} onChange={(e) => set({ course: e.currentTarget.value })}>
              {courses.map(([id, n]) => <option key={id} value={String(id)}>{n}</option>)}</select></label>
            <label>주차<select class="select" name="module" value={form.module} disabled={form.scope !== "module"} onChange={(e) => set({ module: e.currentTarget.value })}>
              {modules.map(([id, n]) => <option key={String(id)} value={String(id)}>{n || `모듈 ${id}`}</option>)}</select></label>
          </div>
          <div><button class="btn">재생 계획 보기</button></div>
        </form>
      </Section>
      {state.plan && <PlanView plan={state.plan} />}
    </>
  );
}

function PlanView({ plan }: { plan: Plan }) {
  if (!plan.items.length) return <Section id="plan-view-title" title="재생 계획"><p class="text-sm text-ink-3">이 범위에는 재생할 영상이 없습니다.</p></Section>;
  return (
    <Section id="plan-view-title" title="재생 계획" flush>
      <PanelBody class="pt-0">
        <div class="summary-line">
          <span><b>{plan.items.length}</b>개 영상</span><span><b>{fmtDuration(plan.total_seconds)}</b>예상 소요</span>
          <span><b>{plan.expected_end ? fmtDate(new Date(plan.expected_end)) : ""}</b>예상 종료</span>
        </div>
        <p class="text-sm text-ink-3">현재 대기열을 반영한 예상입니다. 새 요청·재시도에 따라 달라집니다.</p>
        {plan.warnings.length > 0 && (
          <Warning>{plan.warnings.length}개 영상은 순서대로 재생하면 마감 전에 끝나지 않습니다. 마감이 이른 과목부터 재생하니, 필요하면 범위를 줄이세요.</Warning>
        )}
        <Warning>{PLAYBACK_WARNING}</Warning>
      </PanelBody>
      <ol class="steps">
        {plan.items.map((i) => (
          <li key={i.position} class={i.at_risk ? "risk" : ""}>
            <span class="num">{i.position + 1}</span>
            <span><span class="title">{i.title}</span><br />
              <span class="meta flex flex-wrap items-center gap-2 text-sm"><span>{i.course_name} {i.module_title}</span><span>{fmtDuration(i.duration)}</span>
                <span>예상 종료 {fmtDate(new Date(i.expected_end))}</span><span>{dueText(i.deadline)}</span></span></span>
            <span class={`status ${i.at_risk ? "failed" : "queued"}`}>{i.at_risk ? "마감 초과" : ""}</span>
          </li>
        ))}
      </ol>
      <div class="card-actions items-center px-5 pt-3 pb-5 max-phone:px-4">
        <button class="btn btn-primary" id="start-batch" onClick={startBatch}>{plan.items.length}개 재생 시작</button>
        <button class="btn btn-ghost" id="clear-plan" onClick={() => { state.plan = null; viewPlayback(); }}>계획 닫기</button>
      </div>
    </Section>
  );
}

/** 재생 계획 카드를 HTML 문자열로(테스트용 __ssu.planHtml). */
export function planHtml(plan: Plan): string {
  const box = document.createElement("div");
  render(<PlanView plan={plan} />, box);
  return box.innerHTML;
}

async function startBatch() {
  const q = state.planQuery || {};
  const body = { scope: q.scope, course_id: q.course_id ? Number(q.course_id) : null, module_id: q.module_id ? Number(q.module_id) : null };
  try {
    const batch = await api<Batch>("/playback/batches", { method: "POST", json: body });
    state.plan = null;
    state.active = await api<BatchDetail>(`/playback/batches/${batch.id}`);
    state.batches = await api<Batch[]>("/playback/batches?limit=10");
    toast(`영상 ${batch.total}개 재생을 시작했습니다`);
    startPolling();
    viewPlayback();
  } catch (err) { toast((err as Error).message, true); }
}

/* ---------- 진행 중 ---------- */

/** 재생이 정상이면 진행률이 말해 주므로 아무것도 쓰지 않는다. 확인이 필요할 때만 한 줄을 보인다. */
function PlaybackActivity({ job }: { job: Job }) {
  const updated = job.progress_updated_at ? new Date(job.progress_updated_at) : null;
  if (!updated || !Number.isFinite(updated.getTime())) return <p class="text-sm text-ink-3">재생 위치를 확인하는 중이에요</p>;
  const age = Math.max(0, Math.floor((Date.now() - updated.getTime()) / 1000));
  if (age <= 45) return null;
  return <Warning>재생 확인 필요 · 45초 넘게 진행 보고가 없어요 (마지막 {updated.toLocaleTimeString("ko-KR", { hour12: false })})</Warning>;
}

function ActiveBatch({ b }: { b: BatchDetail }) {
  const ends = playbackEnds(b);
  const jobs = [...(b.jobs || [])].sort((x, y) => x.position - y.position);
  const lateIds = new Set(jobs.filter((j) => (ends.get(j.module_item_id) ?? -Infinity) > dueTime(j)).map((j) => j.module_item_id));
  return <><PlaybackHero b={b} jobs={jobs} lateIds={lateIds} /><PlaybackQueue b={b} jobs={jobs} lateIds={lateIds} /></>;
}

/** 지금 강의 한 개가 화면의 주인공이다. 진행률과 제목은 여기서만 크게 보여 준다. */
function PlaybackHero({ b, jobs, lateIds }: { b: BatchDetail; jobs: Job[]; lateIds: Set<number> }) {
  const current = jobs.find((job) => job.status === "playing");
  const focus = current || (b.cancel_requested ? null : jobs.find((job) => job.status === "queued"));
  const waiting = b.status === "queued" && b.queue_position != null
    ? `다음 영상 대기 ${b.queue_position}번째 · 약 ${fmtDuration(b.estimated_wait_seconds || 0)} 후 시작` : "다음 강의를 기다리는 중";
  const stateLabel = b.cancel_requested ? "재생 중지 요청됨" : current ? "지금 재생 중" : waiting;
  const finished = (b.done_count || 0) + (b.skipped_count || 0);
  const extras = [b.failed_count ? `실패 ${b.failed_count}` : "", b.skipped_count ? `이미 완료 ${b.skipped_count}` : "",
    b.expected_end ? `예상 종료 ${fmtDate(new Date(b.expected_end))}` : ""].filter(Boolean).join(" · ");
  const overall = playbackOverallPercent(jobs);
  return (
    <Panel as="section" class="playback-hero" aria-label="지금 재생 상황">
      <PanelBody>
        <p class="playback-state" role="status">{stateLabel}</p>
        <h2>{focus ? focus.title : b.cancel_requested ? "예정된 강의는 재생하지 않아요" : "재생을 준비하고 있어요"}</h2>
        {focus && (
          <p class="meta">{[courseName(focus), current
            ? `${playbackPosition(Math.min(Math.max(0, current.progress_seconds || 0), Math.max(0, current.duration || 0)))} / ${playbackPosition(current.duration)}`
            : fmtDuration(focus.duration), dueText(focus.deadline)].filter(Boolean).join(" · ")}</p>
        )}
        {current && (
          <>
            <p class="playback-percent"><strong>{Math.floor(jobProgressPercent(current))}%</strong></p>
            <progress class="progress progress-info" value={jobProgressPercent(current)} max={100} aria-label="현재 강의 재생 진행률" />
            <PlaybackActivity job={current} />
          </>
        )}
        {focus && lateIds.has(focus.module_item_id) && <Warning>마감 전에 끝나지 않을 예정이에요</Warning>}
        {focus && focus.attempts > 1 && <p class="meta">{focus.attempts}번째 시도</p>}
        {focus?.error && <Warning>{focus.error}</Warning>}
        {b.deadline_at_risk && <Warning>대기 시간 때문에 일부 강의가 마감 전에 끝나지 않을 수 있어요</Warning>}
      </PanelBody>
      <PanelBody class="playback-overall">
        {b.total > 1 && (
          <>
            <p><strong>전체 진행률 {overall}%</strong> · {finished}/{b.total}개 완료</p>
            <progress class="progress progress-info" value={overall} max={100} aria-label="전체 재생 진행률" />
          </>
        )}
        {extras && <p class="meta">{extras}</p>}
        <p class="meta">창을 닫아도 서버에서 계속돼요</p>
        <details class="playback-help" open={!!state.playbackHelpOpen}
          onToggle={(e) => { state.playbackHelpOpen = (e.currentTarget as HTMLDetailsElement).open; }}>
          <summary>재생 방식 안내</summary>
          <ul>
            <li>사용자별로 강의를 한 개씩 번갈아 재생해요.</li>
            <li>예상 시간은 현재 대기열 기준이라 새 요청이나 재시도에 따라 달라져요.</li>
            <li>전체 진행률은 영상 길이 기준이고 이미 완료된 강의도 포함해요.</li>
            <li>학습 완료는 LMS에서 확인한 뒤 반영돼요.</li>
          </ul>
        </details>
      </PanelBody>
    </Panel>
  );
}

/** 강의가 2개 이상일 때만 목록을 둔다. 진행률은 위에서 보여 주므로 여기서는 순서와 상태만 쓴다. */
function PlaybackQueue({ b, jobs: all, lateIds }: { b: BatchDetail; jobs: Job[]; lateIds: Set<number> }) {
  // 중지를 요청했으면 이미 완료된 강의는 목록에서 뺀다(남은 강의만 보인다).
  const jobs = b.cancel_requested ? all.filter((j) => !COMPLETE.includes(j.status)) : all;
  if (b.total < 2 || !jobs.length) return null;
  const manyCourses = new Set(jobs.map((job) => job.lms_course_id)).size > 1;
  const nextId = b.cancel_requested ? null : jobs.find((job) => job.status === "queued")?.module_item_id;
  return (
    <Section id="playback-queue-title" title={`재생 목록 ${b.total}개`} class="playback-queue" flush>
      <ol class="steps">
        {jobs.map((j) => {
          const late = lateIds.has(j.module_item_id);
          const meta = [manyCourses ? courseName(j) : "", fmtDuration(j.duration), dueText(j.deadline),
            j.status === "queued" && !b.cancel_requested && j.expected_start ? `예상 시작 ${fmtDate(new Date(j.expected_start))}` : "",
            late ? "마감 전에 끝나지 않을 예정" : "", j.attempts > 1 ? `${j.attempts}번 시도` : "", j.error || ""].filter(Boolean);
          return (
            <li key={j.module_item_id} class={[late ? "risk" : "", j.status === "playing" ? "now" : ""].filter(Boolean).join(" ")}>
              <span class="num">{j.position + 1}</span>
              <span><span class="title">{j.title}</span><span class="meta flex flex-wrap items-center gap-2 text-sm">{meta.map((m, i) => <span key={i}>{m}</span>)}</span></span>
              <span class={`status ${j.status}`}>{j.module_item_id === nextId ? "다음 재생" : JOB_STATUS[j.status] || j.status}</span>
            </li>
          );
        })}
      </ol>
    </Section>
  );
}

/* ---------- 지난 재생 ---------- */

const SCOPE_NAME: Record<string, string> = { item: "영상 하나", module: "주차", course: "과목", all: "모든 과목", auto: "자동 수강" };

const COMPLETE = ["done", "skipped"];

/** 중지한 묶음의 영상 목록(끝난 묶음이라 바뀌지 않으므로 한 번만 받는다). */
const stoppedJobs = new Map<number, Job[] | "loading" | "error">();

/** 아직 LMS에서 완료되지 않은 강의인지. 재생이 끝나 완료된 강의는 /todos에서 빠진다. */
const stillPending = (j: Job) => !COMPLETE.includes(j.status)
  && state.todos.some((t) => t.kind === "video" && t.lms_item_id === j.module_item_id && t.lms_course_id === j.lms_course_id);

function History() {
  const past = state.batches.filter((b) => !["queued", "running"].includes(b.status));
  const stopped = past.filter((b) => b.status === "cancelled").map((b) => b.id);
  useEffect(() => {
    for (const id of stopped) {
      if (stoppedJobs.has(id)) continue;
      stoppedJobs.set(id, "loading");
      api<BatchDetail>(`/playback/batches/${id}`)
        .then((d) => { stoppedJobs.set(id, d.jobs || []); })
        .catch(() => { stoppedJobs.set(id, "error"); })
        .finally(() => { if (state.view === "playback") viewPlayback(); });
    }
  }, [stopped.join(",")]);
  // 중지한 묶음: 완료된 강의는 숨기고 남은 강의마다 재생 요청 버튼을 둔다. 같은 강의는 가장 최근 묶음에만 보인다.
  const seen = new Set<string>();
  const rows = past.map((b) => {
    const jobs = b.status === "cancelled" ? stoppedJobs.get(b.id) : undefined;
    if (!Array.isArray(jobs)) return { b, left: null };
    const left = jobs.filter((j) => stillPending(j) && !seen.has(`${j.lms_course_id}:${j.module_item_id}`));
    left.forEach((j) => seen.add(`${j.lms_course_id}:${j.module_item_id}`));
    return { b, left };
  }).filter(({ left }) => left === null || left.length > 0);
  if (!rows.length) return null;
  return (
    <Section id="playback-history-title" title="지난 재생" class="playback-history" flush>
      <ul class="list">
        {rows.map(({ b, left }) => {
          const failed = !!b.failed_count || b.status === "failed" || b.status === "partial";
          const counts = ([["완료", b.done_count], ["실패", b.failed_count], ["이미 완료", b.skipped_count]] as const).filter(([, n]) => n);
          return (
            <li key={b.id} data-batch-id={b.id}>
              <span class="history-head"><span class="title">{SCOPE_NAME[b.scope] || b.scope} {b.total}개</span>
                <span class={`status ${b.status}`}>{BATCH_STATUS[b.status] || b.status}</span></span>
              <span class="meta flex flex-wrap items-center gap-x-2.5 gap-y-0.5">
                {counts.map(([name, n]) => <span key={name}>{name} {n}</span>)}
                <span>{fmtDate(new Date(b.created_at))}</span>
                {!failed && b.error && <span>{b.error}</span>}
              </span>
              {left && <StoppedVideos jobs={left} />}
              {failed && <FailureDetails batchId={b.id} />}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

/** 중지해서 아직 완료되지 않은 강의. 재생이 진행 중이면 끝난 뒤에 요청할 수 있다. */
function StoppedVideos({ jobs }: { jobs: Job[] }) {
  const busy = !!state.active;
  return (
    <div class="stopped-videos grid gap-1.5 pt-1">
      <p class="text-sm text-ink-3">{busy ? "지금 재생이 끝나면 다시 요청할 수 있어요" : `완료되지 않은 강의 ${jobs.length}개`}</p>
      <ul class="m-0 grid list-none gap-1.5 p-0">
        {jobs.map((j) => (
          <li key={j.module_item_id} class="flex flex-wrap items-center gap-2">
            <span class="min-w-0 flex-1"><span class="title wrap-anywhere">{j.title}</span>
              <span class="meta block text-sm text-ink-3">{[courseName(j), fmtDuration(j.duration), dueText(j.deadline)].filter(Boolean).join(" · ")}</span></span>
            <button class="btn btn-sm" disabled={busy} aria-label={`${j.title} 재생 요청`} onClick={() => replayVideo(j)}>재생 요청</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

let replaying = false;

/** 영상 한 개를 다시 재생 대기열에 넣는다(항목 1개짜리 묶음). */
async function replayVideo(j: Job): Promise<void> {
  if (replaying || state.active) return;
  replaying = true;
  try {
    const batch = await api<Batch>(`/playback/items/${j.module_item_id}`, { method: "POST" });
    state.active = await api<BatchDetail>(`/playback/batches/${batch.id}`);
    state.batches = await api<Batch[]>("/playback/batches?limit=10");
    toast(`${j.title} 재생을 요청했어요`);
    startPolling();
  } catch (err) {
    toast((err as Error).message, true);
    if ((err as ApiError).status === 404) state.todos = await api("/todos").catch(() => state.todos);  // 이미 완료됐으면 목록에서 빠진다
  } finally {
    replaying = false;
    if (state.view === "playback") viewPlayback();
  }
}

type FailureState = { kind: "idle" } | { kind: "loading" } | { kind: "loaded"; batch: BatchDetail } | { kind: "error"; message: string };

/** 실패한 묶음: 펼칠 때 한 번 영상별 오류를 불러온다. 실패하면 다시 시도할 수 있다. */
function FailureDetails({ batchId }: { batchId: number }) {
  const [s, setS] = useState<FailureState>({ kind: "idle" });
  const load = async () => {
    setS({ kind: "loading" });
    try {
      setS({ kind: "loaded", batch: await api<BatchDetail>(`/playback/batches/${batchId}`) });
    } catch (err) {
      setS({ kind: "error", message: (err as Error).message });
    }
  };
  const onToggle = (e: Event) => {
    if ((e.currentTarget as HTMLDetailsElement).open && (s.kind === "idle" || s.kind === "error")) load();
  };
  return (
    <details class="playback-failure" data-batch-id={batchId} onToggle={onToggle}>
      <summary>실패한 영상과 오류 사유 보기</summary>
      <div class="playback-failure-content" aria-live="polite">
        {s.kind === "loading" && <p role="status">실패 내용을 불러오는 중입니다.</p>}
        {s.kind === "error" && <><p role="alert">{s.message}</p><button class="btn" onClick={load}>다시 시도</button></>}
        {s.kind === "loaded" && <FailureList batch={s.batch} />}
      </div>
    </details>
  );
}

function FailureList({ batch }: { batch: BatchDetail }) {
  const failed = batch.jobs.filter((job) => ["failed", "locked"].includes(job.status));
  return (
    <>
      {batch.error && <p>{batch.error}</p>}
      {failed.length ? (
        <ul>
          {failed.map((job) => (
            <li key={job.module_item_id}><strong>{job.title}</strong>
              <p>{job.error || batch.error || "오류 사유가 기록되지 않았습니다. 서버의 재생 워커 로그를 확인해야 합니다."}</p>
              <p class="text-sm text-ink-3">시도 {job.attempts}회 · 마지막 재생 위치 {fmtDuration(job.progress_seconds || 0)} / {fmtDuration(job.duration)}</p>
            </li>
          ))}
        </ul>
      ) : <p>영상별 실패 기록이 없습니다.</p>}
    </>
  );
}

/* ---------- 재생 중지 ---------- */

/** 확인 창을 거쳐 진행 중인 묶음을 멈춘다. 창이 열린 동안 묶음이 끝나거나 바뀌었으면 아무것도 보내지 않는다. */
export async function cancelActivePlayback(): Promise<void> {
  const id = state.active?.id;
  if (id == null) return;
  const ok = await confirmDialog({
    title: "재생을 중지할까요?",
    body: ["지금 재생 중인 강의도 바로 멈춰요. 끝까지 듣지 않은 강의는 완료되지 않아요."],
    confirmLabel: "재생 중지",
    danger: true,
  });
  if (!ok || state.active?.id !== id) return;
  try {
    await api(`/playback/batches/${id}`, { method: "DELETE" });
    if (state.active?.id !== id) return;
    state.active = { ...state.active, cancel_requested: true };
    toast("재생을 중지했어요.");
    try {
      const detail = await api<BatchDetail>(`/playback/batches/${id}`);
      if (state.active?.id === id) state.active = detail;
    } catch { /* 중지는 접수됐다. 진행 상황은 다음 폴링에서 다시 조회한다. */ }
    if (state.active?.id === id && !["queued", "running"].includes(state.active.status)) await finishActive();
  } catch (err) { toast((err as Error).message, true); }
  if (state.token) redrawPlayback();
}

/* ---------- 폴링 ---------- */

// 재생 중이면 5초마다 진행률을 받는다. 대기 중이면 차례가 오기까지 남은 시간의 절반(5~30초)만 기다린다.
// 탭이 가려져 있으면 쉬고, 다시 보이면 바로 한 번 받는다. 서버 대기열 계산은 모든 사용자 몫을 다시 하므로
// 아무도 보지 않는 탭이 5초마다 부르지 않게 한다.
const POLL_RUNNING_MS = 5000;
const POLL_QUEUED_MAX_MS = 30000;
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let polling = false;
let pollInFlight = false;

export function nextPollDelay(b: Pick<Batch, "status" | "estimated_wait_seconds"> | null): number {
  if (!b || b.status !== "queued" || b.estimated_wait_seconds == null) return POLL_RUNNING_MS;
  return Math.min(POLL_QUEUED_MAX_MS, Math.max(POLL_RUNNING_MS, (b.estimated_wait_seconds * 1000) / 2));
}

function schedulePoll(ms: number): void {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(pollOnce, ms);
}

async function pollOnce(): Promise<void> {
  pollTimer = null;
  if (!polling || pollInFlight) return;
  if (!state.active) return stopPolling();
  if (document.hidden) return;  // visibilitychange가 다시 깨운다
  pollInFlight = true;
  try {
    state.active = await api<BatchDetail>(`/playback/batches/${state.active.id}`);
    if (!["queued", "running"].includes(state.active.status)) await finishActive();
    redrawPlayback();
  } catch { /* 일시적 오류는 다음 주기에 다시 시도 */ }
  finally { pollInFlight = false; }
  if (polling && state.active) schedulePoll(nextPollDelay(state.active));
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && polling && !pollInFlight) void pollOnce();
});

function redrawPlayback(): void {
  if (state.view === "playback") viewPlayback();
  if (state.view === "dashboard") drawWeek();
  renderTaskPanel();
}

export function startPolling(): void {
  renderTaskPanel();
  if (polling) return;
  polling = true;
  schedulePoll(nextPollDelay(state.active));
}

export function stopPolling(): void {
  polling = false;
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
}

async function finishActive(): Promise<void> {
  const b = state.active!;
  stopPolling();
  toast(`강의 재생 ${BATCH_STATUS[b.status]}: 완료 ${b.done_count}, 실패 ${b.failed_count}`, b.status === "failed");
  state.active = null;
  renderTaskPanel();
  [state.todos, state.batches] = await Promise.all([api("/todos"), api<Batch[]>("/playback/batches?limit=10")]);
  renderTaskPanel();
  if (state.view === "dashboard") {
    loadDashboardPlayback();
    renderWeek();
  }
}

