"use strict";

/* ---------------- 하단 진행 패널 ----------------
 * 강의 재생과 텍스트 변환의 진행과 실패를 한곳(#task-panel)에 모은다. 상태가 바뀔 때마다 renderTaskPanel()을 부른다.
 * ui.js 다음에 불러오며, app.js의 state, store, esc, fmtDate, fmtDuration, navigate와 transcribe.js의 tx는
 * 함수를 부를 때만 읽는다.
 *
 * TaskItem: { key, kind, title, pct: number|null, note, failed, detail?, actions: [{ label, cls, run }] }
 */

const DISMISSED_FAILURES_KEY = "dismissed-playback-failures";
const FAILURE_KEEP_MS = 7 * 86400000;
const taskUi = { expanded: false, actions: new Map(), dismissed: new Set(), sheetHtml: "", pointerDown: false };

/** 끝난 지 7일 안이고 실패가 있는 재생 묶음. 닫은 묶음과 실패 없이 취소한 묶음은 뺀다. */
function failedPlaybackBatches(batches, dismissedIds, now) {
  const dismissed = new Set(dismissedIds);
  const oldest = new Date(now).getTime() - FAILURE_KEEP_MS;
  return batches.filter((b) => !["queued", "running"].includes(b.status)
    && !dismissed.has(b.id)
    && new Date(b.finished_at ?? NaN).getTime() >= oldest
    && (b.failed_count > 0 || b.status === "failed"));
}

function dismissedFailureIds() {
  let saved = [];
  try {
    const parsed = JSON.parse(store.get(DISMISSED_FAILURES_KEY));
    if (Array.isArray(parsed)) saved = parsed;
  } catch { /* 깨진 값은 없는 것으로 본다 */ }
  return [...new Set([...saved, ...taskUi.dismissed])];  // 저장할 수 없는 환경에서도 이번 탭에서는 닫힌다
}

function dismissFailure(id) {
  taskUi.dismissed.add(id);
  store.set(DISMISSED_FAILURES_KEY, JSON.stringify(dismissedFailureIds()));
  renderTaskPanel();
}

function playbackTask(a) {
  const done = (a.done_count || 0) + (a.skipped_count || 0);
  const wait = a.status === "queued" ? (a.queue_position != null ? `대기 ${a.queue_position}번째` : "대기 중") : "";
  return {
    key: "playback",
    kind: "playback",
    title: `강의 재생 ${done}/${a.total}개`,
    pct: a.total ? Math.round((done / a.total) * 100) : 0,
    note: a.cancel_requested ? "중지하는 중이에요. 지금 재생 중인 강의가 끝나는 대로 멈춰요."
      : [wait, "창을 닫아도 서버에서 계속돼요"].filter(Boolean).join(" · "),
    failed: false,
    actions: a.cancel_requested ? [] : [{ label: "재생 중지", cls: "btn small danger", run: cancelActivePlayback }],
  };
}

function transcribeTask() {
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
    actions: job ? [{ label: "변환 중지", cls: "btn small", run: cancelTranscription }] : [],
  };
}

function failedBatchTask(b) {
  return {
    key: `playback-failed-${b.id}`,
    kind: "playback",
    title: b.failed_count > 0 ? `강의 재생 실패 ${b.failed_count}개` : "강의 재생 실패",
    pct: null,
    note: `${fmtDate(new Date(b.finished_at))}에 끝났어요`,
    failed: true,
    detail: b.error || "",
    actions: [
      { label: "실패 사유 보기", cls: "btn small", run: () => { taskUi.expanded = false; renderTaskPanel(); navigate("playback"); } },
      { label: "닫기", cls: "btn small ghost", run: () => dismissFailure(b.id) },
    ],
  };
}

function failedTranscribeTask(f) {
  return {
    key: `transcribe-failed-${f.key}`,
    kind: "transcribe",
    title: `텍스트 변환 실패: ${f.title}`,
    pct: null,
    note: "",
    failed: true,
    detail: f.error || "",
    actions: [
      { label: "다시 시도", cls: "btn small", run: () => enqueueTranscription([f]) },
      { label: "닫기", cls: "btn small ghost", run: () => { tx.finished = tx.finished.filter((x) => x !== f); renderTaskPanel(); } },
    ],
  };
}

/** 진행 중인 작업을 먼저, 실패를 뒤에 둔다. */
function taskItems() {
  const failedBatches = failedPlaybackBatches(state.batches, dismissedFailureIds(), Date.now());
  return [
    ...(state.active ? [playbackTask(state.active)] : []),
    ...(tx.current || tx.queue.length ? [transcribeTask()] : []),
    ...failedBatches.map(failedBatchTask),
    ...tx.finished.filter((f) => f.status === "failed").map(failedTranscribeTask),
  ];
}

function taskSummary(items) {
  const failed = items.filter((i) => i.failed).length;
  const running = items.length - failed;
  return [running ? `작업 ${running}개 진행 중` : "", failed ? `실패 ${failed}개` : ""].filter(Boolean).join(" · ");
}

function taskRowHtml(item) {
  const titleId = `task-title-${item.key}`;
  const buttons = item.actions.map((a, i) => {
    const key = `${item.key}-${i}`;
    return `<button type="button" class="${a.cls}" id="task-action-${esc(key)}" data-action-key="${esc(key)}" aria-describedby="${esc(titleId)}">${esc(a.label)}</button>`;
  }).join("");
  return `<li class="tp-row${item.failed ? " bad" : ""}">
    <div class="tp-line"><p class="tp-title" id="${esc(titleId)}">${esc(item.title)}</p>${buttons ? `<div class="tp-actions">${buttons}</div>` : ""}</div>
    ${item.failed ? "" : `<progress max="100" ${item.pct === null ? "" : `value="${item.pct}"`} aria-labelledby="${esc(titleId)}"></progress>`}
    ${item.note ? `<p class="tp-note">${esc(item.note)}</p>` : ""}
    ${item.detail ? `<p class="tp-note">${esc(item.detail)}</p>` : ""}
  </li>`;
}

function renderTaskPanel() {
  if (taskUi.pointerDown) return;  // 누르는 중에 버튼을 바꾸면 클릭이 사라진다. 손을 떼면 다시 그린다.
  const panel = document.getElementById("task-panel");
  if (!panel) return;
  const bar = document.getElementById("task-bar");
  const sheet = document.getElementById("task-sheet");
  const focused = document.activeElement;
  const focusInSheet = focused !== sheet && sheet.contains(focused);  // 스크롤 영역 자체의 포커스는 그대로 둔다
  const focusKey = focused?.dataset?.actionKey;
  const focusOnBar = focused === bar;

  const items = state.token ? taskItems() : [];
  if (!items.length) taskUi.expanded = false;
  panel.hidden = !items.length;
  document.body.classList.toggle("has-task-bar", items.length > 0);

  const summary = taskSummary(items);
  const summaryEl = document.getElementById("task-summary");
  if (summaryEl.textContent !== summary) summaryEl.textContent = summary;
  document.getElementById("task-dot").hidden = !items.some((i) => i.failed);
  document.getElementById("task-toggle").textContent = taskUi.expanded ? "접기" : "펼치기";
  bar.setAttribute("aria-expanded", String(taskUi.expanded));
  sheet.hidden = !taskUi.expanded;

  taskUi.actions = new Map(items.flatMap((item) => item.actions.map((a, i) => [`${item.key}-${i}`, a.run])));
  const html = items.length ? `<ul class="tp-list">${items.map(taskRowHtml).join("")}</ul>` : "";
  if (html !== taskUi.sheetHtml) {
    const scroll = sheet.scrollTop;
    sheet.innerHTML = html;
    sheet.scrollTop = scroll;
    taskUi.sheetHtml = html;
  }

  const fallback = items.length ? bar : document.getElementById("view");
  if (focusInSheet) {
    const same = taskUi.expanded ? [...sheet.querySelectorAll("[data-action-key]")].find((b) => b.dataset.actionKey === focusKey) : null;
    if (same !== document.activeElement) (same || fallback)?.focus({ preventScroll: true });
  } else if (focusOnBar && !items.length) {
    fallback?.focus({ preventScroll: true });
  }
}

function bindTaskPanel() {
  const panel = document.getElementById("task-panel");
  const bar = document.getElementById("task-bar");
  const sheet = document.getElementById("task-sheet");
  bar.addEventListener("click", () => { taskUi.expanded = !taskUi.expanded; renderTaskPanel(); });
  sheet.addEventListener("click", (e) => {
    const key = e.target.closest("[data-action-key]")?.dataset.actionKey;
    if (key) taskUi.actions.get(key)?.();
  });
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
