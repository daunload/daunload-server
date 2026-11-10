"use strict";

/* ---------------- 홈: 남은 할 일 전부 ----------------
 * 들어오자마자 재생할 강의와 낼 과제를 한 화면에서 보고, 버튼 한 번으로 끝낸다.
 *  - 강의가 있으면: "강의 N개 모두 재생" → 순서대로 재생하고 학습 완료까지 확인(학칙 확인 창은 처음 한 번만)
 *  - 과제만 남았으면: 마감이 가장 빠른 과제의 제출 화면을 연다(과제는 직접 제출해야 한다)
 * 강의는 /playback/batches/preview?scope=all(app.js가 state.dashboardPlan에 둔다),
 * 과제는 /week의 assignments + later_assignments + late_assignments에서 가져온다.
 * app.js의 전역(state, api, esc, fmtTime, fmtDuration, dayName, WEEKDAY, dueTime, safeUrl, icon, ...)을 쓴다.
 */

const week = { data: null, error: null, lastActive: null, open: new Set() };

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

/** 마감순. 이미 마감이 지난 지각 제출 과제는 가장 급하므로 맨 앞에 둔다. */
const byDue = (a, b) => (b.late === true) - (a.late === true) || dueTime(a) - dueTime(b);
const dueShort = (iso) => (iso ? whenText(iso) : "마감 없음");

/** 지금 재생할 수 있는 강의 전부(마감이 다음 주 이후이거나 없는 것 포함). */
function lectures() {
  return (state.dashboardPlan?.items || []).map((i) => ({ kind: "video", title: i.title, course: i.course_name, courseId: i.lms_course_id,
    deadline: i.deadline, id: i.module_item_id, duration: i.duration, module: i.module_title, atRisk: i.at_risk }));
}

/** 아직 낼 수 있는 과제·퀴즈 전부. 지각 제출 과제는 LMS가 잠그는 시각까지를 보여 준다. */
function assignments(d) {
  const map = (list, late = false) => (list || []).map((a) => ({ kind: a.kind === "quiz" ? "quiz" : "assignment", title: a.title,
    course: a.course_name, deadline: late ? a.lock_at : a.deadline, url: a.url, status: a.status, late,
    upcoming: a.status === "upcoming" ? a.unlock_at : null }));
  return [...map(d.late_assignments, true), ...map(d.assignments), ...map(d.later_assignments)].sort(byDue);
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
    .sort(byDue);
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

function groupRow(g) {
  const seconds = g.items.reduce((s, i) => s + (i.duration || 0), 0);
  const lines = g.items.map((i) => `<li><div class="task-main"><p class="task-title">${esc(i.title)}</p>
      <p class="task-sub">${[i.module && esc(i.module), i.duration ? fmtDuration(i.duration) : "", i.atRisk ? "마감 전 완료가 어려울 수 있어요" : ""].filter(Boolean).join(" · ")}</p></div>
      <div class="task-side"><span class="task-due">${dueShort(i.deadline)}</span>${jobBadge(jobFor(i))}</div></li>`).join("");
  return `<li class="task task-group"><details data-key="${esc(g.key)}" ${week.open.has(g.key) ? "open" : ""}>
    <summary><div class="task-main"><p class="task-title">${esc(g.course)}</p>
      <p class="task-sub">강의 ${g.items.length}개${seconds ? ` · ${fmtDuration(seconds)}` : ""}</p></div>
      <div class="task-side"><span class="task-due">${dueShort(g.deadline)}</span>${groupBadge(g.items)}</div>
      <span class="chevron" aria-hidden="true"></span></summary>
    <ol class="task-sublist">${lines}</ol></details></li>`;
}

function taskRow(t) {
  const label = { video: "강의", assignment: "과제", quiz: "퀴즈" }[t.kind];
  const job = t.kind === "video" ? jobFor(t) : null;
  let side = jobBadge(job);
  if (!job && t.kind !== "video" && safeUrl(t.url) && t.status !== "upcoming") {
    side = `<a class="btn small" href="${esc(safeUrl(t.url))}" target="_blank" rel="noopener" aria-label="${esc(t.title)} LMS에서 제출 (새 탭)">LMS에서 제출 ${icon("external")}</a>`;
  }
  const sub = t.upcoming ? `${label} · ${whenText(t.upcoming)}에 열려요`
    : `${label} · ${esc(t.course)}${t.kind === "video" && t.duration ? ` · ${fmtDuration(t.duration)}` : ""}`;
  const due = t.late ? `<span class="task-due late">지각 제출 ${dueShort(t.deadline)}까지</span>` : `<span class="task-due">${dueShort(t.deadline)}</span>`;
  return `<li class="task"><div class="task-main"><p class="task-title">${esc(t.title)}</p>
    <p class="task-sub">${sub}</p></div>
    <div class="task-side">${due}${side}</div></li>`;
}

function drawWeek() {
  const el = document.getElementById("week-panel");
  if (!el) return;
  if (week.error) {
    el.innerHTML = `<section class="today"><h1>할 일을 불러오지 못했어요</h1><p class="today-sub">${esc(week.error)}</p>
      <div class="cta"><button class="btn primary block" id="week-retry">다시 불러오기</button></div></section>`;
    document.getElementById("week-retry").addEventListener("click", renderWeek);
    return;
  }
  const d = week.data;
  if (!d) return;

  const videos = lectures();
  const work = assignments(d);
  const remaining = [...videos, ...work].sort(byDue);
  const synced = !!state.cred?.last_synced_at;
  const planPending = !state.dashboardPlan && !state.dashboardError;

  const now = new Date();
  const today = `${now.getMonth() + 1}월 ${now.getDate()}일 ${WEEKDAY[now.getDay()]}요일`;
  let head, photo;
  if (!synced) {
    head = `<h1 id="week-heading">첫 동기화를 기다리고 있어요</h1><p class="today-sub">학교 LMS에서 과목과 할 일을 가져오는 중이에요.</p>`;
    photo = "steam";
  } else if (remaining.length) {
    const first = remaining.find((t) => t.deadline && !t.late);  // 지각 제출 과제의 잠금 시각은 '마감'이 아니다
    const parts = [videos.length && `강의 ${videos.length}개`, work.length && `과제·퀴즈 ${work.length}개`].filter(Boolean).join(" · ");
    head = `<h1 id="week-heading">아유… 할 일이 ${remaining.length}개 남았어요</h1>
      <p class="today-sub">${parts}${first ? ` · 가장 빠른 마감 ${whenText(first.deadline)}` : ""}</p>`;
    photo = "think";
  } else if (state.dashboardError) {
    head = `<h1 id="week-heading">남은 강의를 확인하지 못했어요</h1><p class="today-sub">낼 과제는 없어요. 아래에서 다시 시도해 주세요.</p>`;
    photo = "blank";
  } else if (planPending) {
    head = `<h1 id="week-heading">남은 강의를 확인하고 있어요</h1><p class="today-sub">잠깐만… 나도 하기 싫어…</p>`;
    photo = "think";
  } else {
    head = `<h1 id="week-heading">이번 주 할 일을 모두 끝냈어요. 누워도 됨</h1><p class="today-sub">새 강의나 과제가 열리면 여기에 알려 드릴게요.</p>`;
    photo = "drool";
  }

  let body = "";
  if (state.dashboardError) body += `<div class="notice-inline" role="alert"><p>${esc(state.dashboardError)}</p><button class="btn" id="dashboard-playback-retry">다시 시도</button></div>`;
  if (remaining.length) body += `<ul class="task-list">${groupRows(remaining).map((r) => (r.kind === "group" ? groupRow(r) : taskRow(r))).join("")}</ul>`;
  else if (state.dashboardLoading && !state.dashboardPlan) body += `<div class="task-list" aria-busy="true"><p class="sr-only" role="status">강의 목록을 불러오는 중이에요</p>${skeletonRows(3)}</div>`;

  el.innerHTML = `
    <section class="today" aria-labelledby="week-heading">
      <div class="today-head">
        <div class="today-title">
          <p class="today-date">${today}</p>
          ${head}
        </div>
        <img class="today-photo" src="/patrick-${photo}.jpg" alt="" width="128" height="128">
      </div>
      <div class="cta">${ctaHtml(videos, work)}</div>
      ${body}
    </section>`;
  bindWeek();
}

/** 주요 동작 하나: 재생 중이면 진행 요약, 강의가 있으면 전부 재생, 과제만 있으면 가장 급한 과제 제출 */
function ctaHtml(videos, work) {
  const a = state.active;
  if (state.playbackStarting) return `<button class="btn primary block" disabled>재생 요청 중…</button>`;
  if (a) {
    return `<div class="cta-progress">
      <strong>강의 재생 중 ${a.done_count + a.skipped_count}/${a.total}개</strong>
      <button class="btn" id="week-details">재생 현황 보기</button></div>`;
  }
  if (videos.length) {
    const risky = videos.filter((v) => v.atRisk).length;
    const seconds = videos.reduce((s, v) => s + (v.duration || 0), 0);
    return `<button class="btn primary block" id="dashboard-play-all" ${state.dashboardLoading ? "disabled" : ""}>강의 ${videos.length}개 모두 재생</button>
      <p class="cta-note">${fmtDuration(seconds)} 분량 · 마감 빠른 순서로 1배속 재생${work.length ? " · 과제·퀴즈는 직접 제출해야 해요" : ""}</p>
      ${risky ? `<p class="cta-note warning-text">${risky}개는 마감 전에 다 듣지 못할 수 있어요</p>` : ""}
      ${playbackConsented() ? `<p class="cta-note">${esc(PLAYBACK_WARNING)}</p>` : ""}`;
  }
  const next = work.find((x) => safeUrl(x.url) && x.status !== "upcoming");
  if (next) {
    return `<a class="btn primary block" href="${esc(safeUrl(next.url))}" target="_blank" rel="noopener">${esc(next.title)} LMS에서 제출 ${icon("external")}</a>
      <p class="cta-note">학교 LMS에서 제출하면 다음 동기화 때 목록에서 빠져요</p>`;
  }
  return "";
}

function bindWeek() {
  const on = (id, fn) => document.getElementById(id)?.addEventListener("click", fn);
  on("week-details", () => navigate("playback"));
  on("dashboard-play-all", confirmDashboardPlayback);
  on("dashboard-playback-retry", loadDashboardPlayback);
  // 5초마다 다시 그려도 펼친 과목은 펼친 채로 둔다.
  document.querySelectorAll("#week-panel details[data-key]").forEach((el) => el.addEventListener("toggle", () => {
    if (el.open) week.open.add(el.dataset.key); else week.open.delete(el.dataset.key);
  }));
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
  if (!document.getElementById("week-panel")) return;
  const activeId = state.active?.id ?? null;
  if (week.lastActive !== null && activeId === null) { renderWeek(); loadDashboardPlayback(); }
  else if (activeId !== null) { week.lastActive = activeId; redrawKeepingFocus(); }
}, 5000);

// app.js가 먼저 첫 화면을 그렸다면 여기서 채운다.
if (document.getElementById("week-panel")) renderWeek();
