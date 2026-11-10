"use strict";

/* ---------------- 첫 화면: 이번 주 할 일 ----------------
 * 이번 주(월~일, 한국 시간)에 남은 강의와 과제만 짧게 보여 주고, 버튼 하나로 끝낸다.
 *  - 강의가 있으면: "이번 주 강의 N개 재생" → 확인 창 → 순서대로 재생하고 학습 완료까지 확인
 *  - 과제만 남았으면: 마감이 가장 빠른 과제의 제출 화면을 연다(과제는 직접 제출해야 한다)
 * app.js의 전역(state, api, esc, fmtTime, fmtDuration, dayName, WEEKDAY, toast, startPolling, navigate)을 쓴다.
 */

const week = { data: null, error: null, lastActive: null, busy: false, tab: null, open: new Set() };

async function renderWeek() {
  const el = document.getElementById("week-panel");
  if (!el) return;
  if (!week.data && !week.error) el.innerHTML = `<section class="today"><p class="today-sub">불러오는 중… 나도 하기 싫어…</p></section>`;
  try {
    week.data = await api("/week");
    week.error = null;
  } catch (err) {
    week.error = err.message;
  }
  week.lastActive = state.active?.id ?? null;
  drawWeek();
}

function whenText(iso) {
  const d = new Date(iso);
  const name = dayName(d); // 오늘 / 내일 / 10월 10일 (토)
  const short = name.length <= 2 ? name : `${d.getMonth() + 1}/${d.getDate()}(${WEEKDAY[d.getDay()]})`;
  return `${short} ${fmtTime(d)}`;
}

/** 이번 주 할 일. 강의는 과목별로 묶고 과제·퀴즈는 한 줄씩 둔다. */
const byDue = (a, b) => dueTime(a) - dueTime(b);

function assignmentTasks(list) {
  return (list || []).map((a) => ({ kind: a.kind === "quiz" ? "quiz" : "assignment", title: a.title, course: a.course_name,
    deadline: a.deadline, url: a.url, status: a.status, upcoming: a.status === "upcoming" ? a.unlock_at : null }));
}

function weekTasks(d) {
  return [
    ...d.videos.map((v) => ({ kind: "video", title: v.title, course: v.course_name, courseId: v.lms_course_id, deadline: v.deadline,
      id: v.lms_item_id, duration: v.duration, module: v.module_title })),
    ...assignmentTasks(d.assignments),
  ].sort(byDue);
}

/** 전체 과목에서 지금 재생할 수 있는 강의(마감이 다음 주 이후이거나 없는 것 포함). */
function allLectures() {
  return (state.dashboardPlan?.items || []).map((i) => ({ kind: "video", title: i.title, course: i.course_name, courseId: i.lms_course_id,
    deadline: i.deadline, id: i.module_item_id, duration: i.duration, module: i.module_title, atRisk: i.at_risk }));
}

/** 같은 과목의 강의가 2개 이상이면 한 줄로 묶는다. 줄 순서는 가장 빠른 마감순이다. */
function groupRows(tasks) {
  const groups = new Map();
  const rows = [];
  for (const t of tasks) {
    if (t.kind !== "video") { rows.push(t); continue; }
    const key = String(t.courseId ?? t.course);
    if (!groups.has(key)) { const g = { kind: "group", key, course: t.course, items: [] }; groups.set(key, g); rows.push(g); }
    groups.get(key).items.push(t);
  }
  return rows.map((r) => (r.kind === "group" && r.items.length === 1 ? r.items[0] : r))
    .map((r) => (r.kind === "group" ? { ...r, deadline: r.items.find((i) => i.deadline)?.deadline ?? null } : r))
    .sort((a, b) => dueTime(a) - dueTime(b));
}

const jobFor = (t) => state.active?.jobs?.find((j) => j.module_item_id === t.id && (t.courseId == null || j.lms_course_id === t.courseId));

function jobBadge(job) {
  if (!job) return "";
  const pct = job.duration ? Math.min(100, Math.round((job.progress_seconds / job.duration) * 100)) : 0;
  return { playing: `<span class="task-state on">듣는 중 ${pct}%</span>`, done: '<span class="task-state done">완료</span>',
    skipped: '<span class="task-state done">완료</span>', failed: '<span class="task-state bad">실패</span>',
    locked: '<span class="task-state bad">잠김</span>', queued: '<span class="task-state">대기</span>' }[job.status] || "";
}

/** 묶음 줄의 상태 한 개: 듣는 중 > 실패 > 모두 완료 > 일부 완료 */
function groupBadge(items) {
  const jobs = items.map(jobFor).filter(Boolean);
  if (!jobs.length) return "";
  const playing = jobs.find((j) => j.status === "playing");
  if (playing) return jobBadge(playing);
  const failed = jobs.filter((j) => ["failed", "locked"].includes(j.status)).length;
  if (failed) return `<span class="task-state bad">실패 ${failed}</span>`;
  const done = jobs.filter((j) => ["done", "skipped"].includes(j.status)).length;
  if (done === items.length) return '<span class="task-state done">완료</span>';
  return done ? `<span class="task-state">${done}/${items.length} 완료</span>` : '<span class="task-state">대기</span>';
}

const dueShort = (iso) => (iso ? whenText(iso) : "마감 없음");

function groupRow(g, tab) {
  const key = `${tab}:${g.key}`;
  const seconds = g.items.reduce((s, i) => s + (i.duration || 0), 0);
  const lines = g.items.map((i) => `<li><div class="task-main"><p class="task-title">${esc(i.title)}</p>
      <p class="task-sub">${[i.module && esc(i.module), i.duration ? fmtDuration(i.duration) : "", i.atRisk ? "마감 전 완료가 어려울 수 있어요" : ""].filter(Boolean).join(" · ")}</p></div>
      <div class="task-side"><span class="task-due">${dueShort(i.deadline)}</span>${jobBadge(jobFor(i))}</div></li>`).join("");
  return `<li class="task task-group"><details data-key="${esc(key)}" ${week.open.has(key) ? "open" : ""}>
    <summary><div class="task-main"><p class="task-title">${esc(g.course)}</p>
      <p class="task-sub">강의 ${g.items.length}개${seconds ? ` · ${fmtDuration(seconds)}` : ""}</p></div>
      <div class="task-side"><span class="task-due">${dueShort(g.deadline)}</span>${groupBadge(g.items)}</div>
      <span class="chevron" aria-hidden="true"></span></summary>
    <ol class="task-sublist">${lines}</ol></details></li>`;
}

function listHtml(tasks, tab) {
  return `<ul class="task-list" id="week-list-${tab}">${groupRows(tasks).map((r) => (r.kind === "group" ? groupRow(r, tab) : taskRow(r))).join("")}</ul>`;
}

function drawWeek() {
  const el = document.getElementById("week-panel");
  if (!el) return;
  if (week.error) {
    el.innerHTML = `<section class="today"><h1>이번 주 할 일을 불러오지 못했어요</h1><p class="today-sub">${esc(week.error)}</p>
      <div class="cta"><button class="btn primary block" id="week-retry">다시 불러오기</button></div></section>`;
    document.getElementById("week-retry").addEventListener("click", renderWeek);
    return;
  }
  const d = week.data;
  if (!d) return;

  const tasks = weekTasks(d);
  const all = allLectures();
  const planReady = !!state.dashboardPlan && !state.dashboardLoading;
  const planPending = !state.dashboardPlan && !state.dashboardError;
  const weekIds = new Set(d.videos.map((v) => `${v.lms_course_id}:${v.lms_item_id}`));
  const laterLectures = all.filter((v) => !weekIds.has(`${v.courseId}:${v.id}`));
  const laterAssignments = assignmentTasks(d.later_assignments);
  const laterCount = laterLectures.length + laterAssignments.length;
  // 남은 할 일 전체: 재생할 수 있는 강의 전부 + 아직 낼 수 있는 과제·퀴즈 전부(이번 주 + 다음 주 이후·마감 없음)
  const remainingAssignments = [...assignmentTasks(d.assignments), ...laterAssignments];
  const remaining = [...all, ...remainingAssignments].sort(byDue);
  const synced = !!state.cred?.last_synced_at;

  // 탭은 이번 주 밖의 할 일이 있을 때만 둔다. 고르지 않았으면 이번 주 쪽을 먼저 보인다.
  const showTabs = synced && tasks.length > 0 && laterCount > 0;
  const tab = showTabs ? (week.tab || "week") : tasks.length ? "week" : "all";

  const now = new Date();
  const today = `${now.getMonth() + 1}월 ${now.getDate()}일 ${WEEKDAY[now.getDay()]}요일`;
  let head, photo;
  if (!synced) {
    head = `<h1 id="week-heading">첫 동기화를 기다리고 있어요</h1><p class="today-sub">학교 LMS에서 과목과 할 일을 가져오는 중이에요.</p>`;
    photo = "steam";
  } else if (tasks.length) {
    head = `<h1 id="week-heading">아유… 이번 주 할 일이 ${tasks.length}개 남았어요</h1>
      <p class="today-sub">가장 빠른 마감은 ${whenText(tasks[0].deadline)}이에요${laterCount ? ` · 다음 주 이후 할 일 ${laterCount}개 더 있어요` : ""}</p>`;
    photo = "think";
  } else if (remaining.length) {
    // 이번 주 마감만 없을 뿐 들을 강의나 낼 과제가 남았다. '누워도 됨'이라고 하지 않는다.
    const first = remaining.find((t) => t.deadline);
    const parts = [all.length && `강의 ${all.length}개`, remainingAssignments.length && `과제·퀴즈 ${remainingAssignments.length}개`].filter(Boolean).join(" · ");
    head = `<h1 id="week-heading">이번 주 마감은 끝! 그래도 남은 할 일이 ${remaining.length}개 있어요</h1>
      <p class="today-sub">${parts} · ${first ? `가장 빠른 마감은 ${whenText(first.deadline)}이에요` : "모두 마감이 정해지지 않았어요"}</p>`;
    photo = "think";
  } else if (state.dashboardError) {
    head = `<h1 id="week-heading">남은 강의를 확인하지 못했어요</h1><p class="today-sub">이번 주 마감인 할 일은 없어요. 아래에서 다시 시도해 주세요.</p>`;
    photo = "blank";
  } else if (planPending) {
    head = `<h1 id="week-heading">남은 강의를 확인하고 있어요</h1><p class="today-sub">잠깐만… 나도 하기 싫어…</p>`;
    photo = "think";
  } else {
    head = `<h1 id="week-heading">이번 주 할 일을 모두 끝냈어요. 누워도 됨</h1><p class="today-sub">새 강의나 과제가 열리면 여기에 알려 드릴게요.</p>`;
    photo = "drool";
  }

  const tabBtn = (id, label, n) => `<button type="button" role="tab" id="week-tab-${id}" data-tab="${id}" aria-controls="week-tabpanel"
    aria-selected="${tab === id}" tabindex="${tab === id ? 0 : -1}" class="${tab === id ? "on" : ""}">${label} <b>${n}</b></button>`;
  const tabs = showTabs ? `<div class="filters week-tabs" role="tablist" aria-label="보기 범위">
      ${tabBtn("week", "이번 주 할 일", tasks.length)}${tabBtn("all", "남은 할 일 전체", remaining.length)}</div>` : "";

  let body = "";
  if (tab === "week") body = tasks.length ? listHtml(tasks, "week") : "";
  else if (state.dashboardLoading && !state.dashboardPlan) body = `<div class="task-list" aria-busy="true"><p class="sr-only" role="status">강의 목록을 불러오는 중이에요</p>${skeletonRows(3)}</div>`;
  else {
    if (state.dashboardError) body = `<div class="notice-inline" role="alert"><p>${esc(state.dashboardError)}</p><button class="btn" id="dashboard-playback-retry">다시 시도</button></div>`;
    if (remaining.length) body += listHtml(remaining, "all");
  }

  el.innerHTML = `
    <section class="today" aria-labelledby="week-heading">
      <div class="today-head">
        <div class="today-title">
          <p class="today-date">${today}</p>
          ${head}
        </div>
        <img class="today-photo" src="/patrick-${photo}.jpg" alt="" width="128" height="128">
      </div>
      <div class="cta">${tab === "all" ? allCtaHtml(all, planReady, remainingAssignments) : ctaHtml(d)}</div>
      ${tabs}
      <div id="week-tabpanel" ${showTabs ? `role="tabpanel" aria-labelledby="week-tab-${tab}"` : ""}>${body}</div>
      <p class="today-links">
        ${d.missed.length ? `<button class="btn link" id="week-missed">이번 주에 놓친 과제 ${d.missed.length}개</button>` : ""}
        <button class="btn link" id="week-all">전체 할 일 보기</button>
        ${tab === "all" ? `<button class="btn link" id="dashboard-playback-refresh" ${state.dashboardLoading || state.playbackStarting ? "disabled" : ""}>목록 다시 불러오기</button>` : ""}
      </p>
    </section>`;
  bindWeek();
}

/** '재생 가능한 강의 전체' 쪽의 주요 동작. 재생 중이면 이번 주 쪽과 같은 진행 요약을 보인다. */
function allCtaHtml(all, ready, assignments) {
  if (state.playbackStarting || state.active) return ctaHtml(week.data);
  if (ready && all.length) {
    return `<button class="btn primary block" id="dashboard-play-all">전체 강의 ${all.length}개 재생</button>
      <p class="cta-note">총 ${fmtDuration(all.reduce((s, i) => s + (i.duration || 0), 0))} 분량 · 1배속으로 순서대로 재생하고 LMS 완료 여부를 확인해요${assignments.length ? " · 과제·퀴즈는 직접 제출해야 해요" : ""}</p>`;
  }
  return submitCta(assignments);
}

/** 강의가 없을 때의 주요 동작: 마감이 가장 빠른, 지금 낼 수 있는 과제의 제출 화면을 연다. */
function submitCta(assignments) {
  const next = [...assignments].sort(byDue).find((x) => safeUrl(x.url) && x.status !== "upcoming");
  if (!next) return "";
  return `<a class="btn primary block" href="${esc(safeUrl(next.url))}" target="_blank" rel="noopener">${esc(next.title)} LMS에서 제출 ${icon("external")}</a>
    <p class="cta-note">학교 LMS에서 제출하면 다음 동기화 때 목록에서 빠져요</p>`;
}

function taskRow(t) {
  const label = { video: "강의", assignment: "과제", quiz: "퀴즈" }[t.kind];
  const job = t.kind === "video" ? jobFor(t) : null;
  let side = jobBadge(job);
  if (!job && t.kind !== "video" && safeUrl(t.url) && !t.upcoming) {
    side = `<a class="btn small" href="${esc(safeUrl(t.url))}" target="_blank" rel="noopener" aria-label="${esc(t.title)} LMS에서 제출 (새 탭)">LMS에서 제출 ${icon("external")}</a>`;
  }
  const sub = t.upcoming ? `${label} · ${whenText(t.upcoming)}에 열려요`
    : `${label} · ${esc(t.course)}${t.kind === "video" && t.duration ? ` · ${fmtDuration(t.duration)}` : ""}`;
  return `<li class="task"><div class="task-main"><p class="task-title">${esc(t.title)}</p>
    <p class="task-sub">${sub}</p></div>
    <div class="task-side"><span class="task-due">${dueShort(t.deadline)}</span>${side}</div></li>`;
}

function ctaHtml(d) {
  const a = state.active;
  if (state.playbackStarting) return `<button class="btn primary block" disabled>재생 요청 중…</button>`;
  if (a) {
    return `<div class="cta-progress">
      <strong>강의 재생 중 ${a.done_count + a.skipped_count}/${a.total}개</strong>
      <button class="btn" id="week-details">재생 현황 보기</button></div>`;
  }
  if (d.videos.length) {
    const hasAssignments = d.assignments.length > 0;
    return `<button class="btn primary block" id="week-play">이번 주 강의 ${d.videos.length}개 재생</button>
      <p class="cta-note">${fmtDuration(d.video_seconds)} 분량${hasAssignments ? " · 과제·퀴즈는 직접 제출해야 해요" : ""}</p>`;
  }
  return submitCta(assignmentTasks(d.assignments));
}

function bindWeek() {
  const on = (id, fn) => document.getElementById(id)?.addEventListener("click", fn);
  on("week-all", () => navigate("todos"));
  on("dashboard-play-all", confirmDashboardPlayback);
  on("dashboard-playback-refresh", loadDashboardPlayback);
  on("dashboard-playback-retry", loadDashboardPlayback);
  // 5초마다 다시 그려도 펼친 과목은 펼친 채로 둔다.
  document.querySelectorAll("#week-panel details[data-key]").forEach((el) => el.addEventListener("toggle", () => {
    if (el.open) week.open.add(el.dataset.key); else week.open.delete(el.dataset.key);
  }));
  const tabs = [...document.querySelectorAll("#week-panel [role=tab]")];
  const pick = (t) => { week.tab = t.dataset.tab; drawWeek(); document.getElementById(`week-tab-${week.tab}`)?.focus(); };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => pick(t));
    t.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (step) { e.preventDefault(); pick(tabs[(i + step + tabs.length) % tabs.length]); }
    });
  });
  on("week-details", () => navigate("playback"));
  on("week-missed", () => { state.assignmentFilter = "overdue"; navigate("assignments"); });
  on("week-play", async (e) => {
    if (week.busy) return;
    const token = state.token;
    const btn = e.currentTarget;
    week.busy = true;
    btn.disabled = true;
    try {
      const plan = await api("/playback/batches/preview?scope=week");
      if (state.token !== token) return;  // 미리보기를 기다리는 사이 로그아웃했다
      btn.disabled = false;
      btn.focus();
      if (!(await confirmPlayback(plan, "이번 주"))) return;
      if (state.token !== token) return;
      btn.disabled = true;
      const batch = await api("/playback/batches", { method: "POST", json: { scope: "week" } });
      state.active = await api(`/playback/batches/${batch.id}`);
      week.lastActive = batch.id;
      toast(`강의 ${batch.total}개를 듣기 시작했어요`);
      startPolling();
    } catch (err) {
      if (state.token === token) {
        toast(err.message, true);
        if (err.status === 409) {
          try { await loadCore(); } catch (refreshError) { toast(refreshError.message, true); }
          renderWeek();
        }
      }
    } finally {
      week.busy = false;
    }
    drawWeek();
  });
}

/** 폴링으로 다시 그릴 때 키보드 포커스가 맨 위로 튀지 않게 같은 요소로 돌려준다. */
function redrawKeepingFocus() {
  const a = document.activeElement;
  const sel = a?.closest?.("#week-panel") && (a.id ? `#${a.id}` : a.matches("summary") ? `details[data-key="${CSS.escape(a.parentElement.dataset.key)}"] > summary` : null);
  drawWeek();
  if (sel) document.querySelector(`#week-panel ${sel}`)?.focus();
}

// 재생 진행률을 갱신하고(app.js 폴링이 state.active를 갱신), 끝나면 목록을 새로 불러온다.
setInterval(() => {
  if (!document.getElementById("week-panel") || week.busy) return;
  const activeId = state.active?.id ?? null;
  if (week.lastActive !== null && activeId === null) { renderWeek(); loadDashboardPlayback(); }
  else if (activeId !== null) { week.lastActive = activeId; redrawKeepingFocus(); }
}, 5000);

// app.js가 먼저 첫 화면을 그렸다면 여기서 채운다.
if (document.getElementById("week-panel")) renderWeek();
