/* 하단 진행 패널(#task-panel)
 * 강의 재생, 텍스트 변환, 여러 개 받기의 진행과 실패를 한곳에 모은다. 상태가 바뀌면 renderTaskPanel()을 부른다.
 * 막대(#task-bar: 요약·실패 점·펼치기)는 index.html에 있고, 펼친 시트(#task-sheet)의 목록을 Preact로 그린다.
 * Preact는 같은 버튼을 그대로 두므로 다시 그려도 포커스가 남는다. 버튼이 사라지거나 시트를 접으면 막대로 포커스를 돌려준다.
 */
import { render } from "preact";
import { navigate } from "./app";
import { fmtDate, fmtDuration, state, store } from "./core";
import { type BulkStat, stopBulkDownload } from "./downloads";
import { cancelActivePlayback } from "./screens/playback";
import { runBulkDownload } from "./screens/videos";
import { type TxJob, cancelTranscription, enqueueTranscription, tx } from "./transcribe";
import type { Batch, BatchDetail } from "./types";

interface TaskAction { label: string; cls: string; run: () => unknown }
interface TaskItem { key: string; kind: string; title: string; pct: number | null; note: string; failed: boolean; detail?: string | string[]; actions: TaskAction[] }

const DISMISSED_FAILURES_KEY = "dismissed-playback-failures";
const FAILURE_KEEP_MS = 7 * 86400000;
const taskUi = { expanded: false, dismissed: new Set<number>(), pointerDown: false };

/** 끝난 지 7일 안이고 실패가 있는 재생 묶음. 닫은 묶음과 실패 없이 취소한 묶음은 뺀다. */
export function failedPlaybackBatches(batches: Batch[], dismissedIds: number[], now: number | string | Date): Batch[] {
  const dismissed = new Set(dismissedIds);
  const oldest = new Date(now).getTime() - FAILURE_KEEP_MS;
  return batches.filter((b) => !["queued", "running"].includes(b.status)
    && !dismissed.has(b.id)
    && new Date(b.finished_at ?? NaN).getTime() >= oldest
    && (b.failed_count > 0 || b.status === "failed"));
}

function dismissedFailureIds(): number[] {
  let saved: number[] = [];
  try {
    const parsed = JSON.parse(store.get(DISMISSED_FAILURES_KEY) ?? "null");
    if (Array.isArray(parsed)) saved = parsed;
  } catch { /* 깨진 값은 없는 것으로 본다 */ }
  return [...new Set([...saved, ...taskUi.dismissed])];  // 저장할 수 없는 환경에서도 이번 탭에서는 닫힌다
}

function dismissFailure(id: number): void {
  taskUi.dismissed.add(id);
  store.set(DISMISSED_FAILURES_KEY, JSON.stringify(dismissedFailureIds()));
  renderTaskPanel();
}

function playbackTask(a: BatchDetail): TaskItem {
  const done = (a.done_count || 0) + (a.skipped_count || 0);
  const wait = a.status === "queued" ? (a.queue_position != null ? `대기 ${a.queue_position}번째` : "대기 중") : "";
  return {
    key: "playback",
    kind: "playback",
    title: `강의 재생 ${done}/${a.total}개`,
    pct: a.total ? Math.round((done / a.total) * 100) : 0,
    note: a.cancel_requested ? "중지하는 중이에요"
      : [wait, "창을 닫아도 서버에서 계속돼요"].filter(Boolean).join(" · "),
    failed: false,
    actions: a.cancel_requested ? [] : [{ label: "재생 중지", cls: "btn btn-sm btn-danger", run: cancelActivePlayback }],
  };
}

function transcribeTask(): TaskItem {
  const job = tx.current;
  const waiting = tx.queue.length;
  const pct = job?.duration ? Math.min(100, Math.round((job.seconds / job.duration) * 100)) : null;
  const device = tx.device ? ` · ${tx.device === "webgpu" ? "GPU" : "CPU"}` : "";
  const note = [
    job ? `${pct === null ? `${fmtDuration(job.seconds || 0)} 처리` : `${pct}%`}${device}` : "",
    waiting ? `대기 ${waiting}개` : "",
    tx.modelPercent != null ? `Whisper 모델 받는 중 ${tx.modelPercent}% (처음 한 번만)` : "",
    "이 탭을 열어 두세요",
  ].filter(Boolean).join(" · ");
  return {
    key: "transcribe",
    kind: "transcribe",
    title: job ? `텍스트 변환: ${job.title}` : `텍스트 변환 대기 ${waiting}개`,
    pct,
    note,
    failed: false,
    actions: job ? [{ label: "변환 중지", cls: "btn btn-sm", run: cancelTranscription }] : [],
  };
}

function bulkTask(b: BulkStat): TaskItem {
  const done = b.done + b.failed;
  return {
    key: "bulk",
    kind: "bulk",
    title: `다운로드 ${done}/${b.total}개`,
    pct: b.total ? Math.round((done / b.total) * 100) : 0,
    note: b.direct ? "이 탭과 다운로드 창을 열어 두세요" : "이 탭을 열어 두세요",
    failed: false,
    actions: [{ label: "다운로드 중지", cls: "btn btn-sm", run: stopBulkDownload }],
  };
}

function failedBulkTask(b: BulkStat): TaskItem {
  return {
    key: "bulk-failed",
    kind: "bulk",
    title: `다운로드 실패 ${b.failedItems.length}개`,
    pct: null,
    note: "",
    failed: true,
    detail: b.errors,
    actions: [
      { label: "다시 시도", cls: "btn btn-sm", run: () => runBulkDownload(b.failedItems) },
      { label: "닫기", cls: "btn btn-sm btn-ghost", run: () => { state.bulk = null; renderTaskPanel(); } },
    ],
  };
}

function failedBatchTask(b: Batch): TaskItem {
  return {
    key: `playback-failed-${b.id}`,
    kind: "playback",
    title: b.failed_count > 0 ? `강의 재생 실패 ${b.failed_count}개` : "강의 재생 실패",
    pct: null,
    note: `${fmtDate(new Date(b.finished_at!))}에 끝났어요`,
    failed: true,
    detail: b.error || "",
    actions: [
      { label: "실패 사유 보기", cls: "btn btn-sm", run: () => { taskUi.expanded = false; renderTaskPanel(); navigate("playback"); } },
      { label: "닫기", cls: "btn btn-sm btn-ghost", run: () => dismissFailure(b.id) },
    ],
  };
}

function failedTranscribeTask(f: TxJob): TaskItem {
  return {
    key: `transcribe-failed-${f.key}`,
    kind: "transcribe",
    title: `텍스트 변환 실패: ${f.title}`,
    pct: null,
    note: "",
    failed: true,
    detail: f.error || "",
    actions: [
      { label: "다시 시도", cls: "btn btn-sm", run: () => enqueueTranscription([f]) },
      { label: "닫기", cls: "btn btn-sm btn-ghost", run: () => { tx.finished = tx.finished.filter((x) => x !== f); renderTaskPanel(); } },
    ],
  };
}

/** 진행 중인 작업을 먼저, 실패를 뒤에 둔다. */
function taskItems(): TaskItem[] {
  const failedBatches = failedPlaybackBatches(state.batches, dismissedFailureIds(), Date.now());
  return [
    ...(state.active ? [playbackTask(state.active)] : []),
    ...(tx.current || tx.queue.length ? [transcribeTask()] : []),
    ...(state.bulk?.running ? [bulkTask(state.bulk)] : []),
    ...failedBatches.map(failedBatchTask),
    ...(state.bulk && !state.bulk.running && state.bulk.failedItems?.length ? [failedBulkTask(state.bulk)] : []),
    ...tx.finished.filter((f) => f.status === "failed").map(failedTranscribeTask),
  ];
}

function taskSummary(items: TaskItem[]): string {
  const failed = items.filter((i) => i.failed).length;
  const running = items.length - failed;
  return [running ? `작업 ${running}개 진행 중` : "", failed ? `실패 ${failed}개` : ""].filter(Boolean).join(" · ");
}

function TaskRow({ item }: { item: TaskItem }) {
  const titleId = `task-title-${item.key}`;
  return (
    <li class={`tp-row${item.failed ? " bad" : ""}`}>
      <div class="tp-line">
        <p class="tp-title" id={titleId}>{item.title}</p>
        {item.actions.length > 0 && (
          <div class="tp-actions">
            {item.actions.map((a, i) => (
              <button key={a.label} type="button" class={a.cls} id={`task-action-${item.key}-${i}`} data-action-key={`${item.key}-${i}`}
                aria-describedby={titleId} onClick={() => a.run()}>{a.label}</button>
            ))}
          </div>
        )}
      </div>
      {!item.failed && <progress max={100} value={item.pct ?? undefined} aria-labelledby={titleId} />}
      {item.note && <p class="tp-note">{item.note}</p>}
      {([] as string[]).concat(item.detail || []).map((d, i) => <p key={i} class="tp-note">{d}</p>)}
    </li>
  );
}

const el = (id: string) => document.getElementById(id)!;

export function renderTaskPanel(): void {
  if (taskUi.pointerDown) return;  // 누르는 중에 버튼이 사라지면 클릭이 없어진다. 손을 떼면 다시 그린다.
  const panel = document.getElementById("task-panel");
  if (!panel) return;
  const bar = el("task-bar");
  const sheet = el("task-sheet");
  const focused = document.activeElement;
  const focusInSheet = focused !== sheet && sheet.contains(focused);  // 스크롤 영역 자체의 포커스는 그대로 둔다
  const focusOnBar = focused === bar;

  const items = state.token ? taskItems() : [];
  if (!items.length) taskUi.expanded = false;
  panel.hidden = !items.length;
  document.body.classList.toggle("has-task-bar", items.length > 0);

  const summary = taskSummary(items);
  if (el("task-summary").textContent !== summary) el("task-summary").textContent = summary;
  el("task-dot").hidden = !items.some((i) => i.failed);
  el("task-toggle").textContent = taskUi.expanded ? "접기" : "펼치기";
  bar.setAttribute("aria-expanded", String(taskUi.expanded));
  sheet.hidden = !taskUi.expanded;
  render(items.length ? <ul class="tp-list">{items.map((item) => <TaskRow key={item.key} item={item} />)}</ul> : null, sheet);

  const fallback = items.length ? bar : document.getElementById("view");
  const lost = focusInSheet && (!taskUi.expanded || !sheet.contains(document.activeElement));
  if (lost || (focusOnBar && !items.length)) fallback?.focus({ preventScroll: true });
}

function bindTaskPanel(): void {
  const panel = el("task-panel");
  const bar = el("task-bar");
  const sheet = el("task-sheet");
  bar.addEventListener("click", () => { taskUi.expanded = !taskUi.expanded; renderTaskPanel(); });
  panel.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !taskUi.expanded) return;
    taskUi.expanded = false;
    renderTaskPanel();
    bar.focus();
  });
  sheet.addEventListener("pointerdown", () => { taskUi.pointerDown = true; });
  const release = () => {
    if (!taskUi.pointerDown) return;
    taskUi.pointerDown = false;
    setTimeout(renderTaskPanel);  // 이번 입력의 click이 먼저 처리되도록 미룬다
  };
  window.addEventListener("pointerup", release);
  window.addEventListener("pointercancel", release);
}

bindTaskPanel();
