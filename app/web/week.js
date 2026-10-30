"use strict";

/* ---------------- 첫 화면: 이번 주 할 일 ----------------
 * 이번 주(월~일, 한국 시간)에 남은 강의와 과제만 짧게 보여 주고, 버튼 하나로 끝낸다.
 *  - 강의가 있으면: "강의 N개 한 번에 듣기" → 확인 → 순서대로 재생하고 학습 완료까지 확인
 *  - 과제만 남았으면: 마감이 가장 빠른 과제의 제출 화면을 연다(과제는 직접 제출해야 한다)
 * app.js의 전역(state, api, esc, fmtTime, fmtDuration, dayName, WEEKDAY, toast, startPolling, navigate)을 쓴다.
 */

const week = { data: null, plan: null, error: null, confirming: false, lastActive: null, busy: false };

async function renderWeek() {
  const el = document.getElementById("week-panel");
  if (!el) return;
  if (!week.data && !week.error) el.innerHTML = `<section class="today"><p class="today-sub">불러오는 중</p></section>`;
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

function drawWeek() {
  drawDashboardPlayback();
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

  const tasks = [
    ...d.videos.map((v) => ({ kind: "video", title: v.title, course: v.course_name, deadline: v.deadline, id: v.lms_item_id })),
    ...d.assignments.map((a) => ({ kind: a.kind === "quiz" ? "quiz" : "assignment", title: a.title, course: a.course_name,
      deadline: a.deadline, url: a.url, upcoming: a.status === "upcoming" ? a.unlock_at : null })),
  ].sort((a, b) => new Date(a.deadline) - new Date(b.deadline));

  const now = new Date();
  const today = `${now.getMonth() + 1}월 ${now.getDate()}일 ${WEEKDAY[now.getDay()]}요일`;
  const head = !state.cred?.last_synced_at
    ? `<h1 id="week-heading">첫 동기화를 기다리고 있어요</h1><p class="today-sub">학교 LMS에서 과목과 할 일을 가져오는 중이에요.</p>`
    : tasks.length
      ? `<h1 id="week-heading">이번 주 할 일이 ${tasks.length}개 남았어요</h1><p class="today-sub">가장 빠른 마감은 ${whenText(tasks[0].deadline)}이에요</p>`
      : `<h1 id="week-heading">이번 주 할 일을 모두 끝냈어요</h1><p class="today-sub">새 강의나 과제가 열리면 여기에 알려 드릴게요.</p>`;

  el.innerHTML = `
    <section class="today" aria-labelledby="week-heading">
      <p class="today-date">${today}</p>
      ${head}
      ${tasks.length ? `<ul class="task-list">${tasks.map(taskRow).join("")}</ul>` : ""}
      <div class="cta">${ctaHtml(d)}</div>
      <p class="today-links">
        ${d.missed.length ? `<button class="btn link" id="week-missed">이번 주에 놓친 과제 ${d.missed.length}개</button>` : ""}
        <button class="btn link" id="week-all">전체 할 일 보기</button>
      </p>
    </section>`;
  bindWeek(tasks);
}

function taskRow(t) {
  const label = { video: "강의", assignment: "과제", quiz: "퀴즈" }[t.kind];
  const job = t.kind === "video" ? state.active?.jobs?.find((j) => j.module_item_id === t.id) : null;
  let side = "";
  if (job) {
    const pct = job.duration ? Math.min(100, Math.round((job.progress_seconds / job.duration) * 100)) : 0;
    side = { playing: `<span class="task-state on">듣는 중 ${pct}%</span>`, done: '<span class="task-state done">완료</span>',
      skipped: '<span class="task-state done">완료</span>', failed: '<span class="task-state bad">실패</span>',
      locked: '<span class="task-state bad">잠김</span>', queued: '<span class="task-state">대기</span>' }[job.status] || "";
  } else if (t.kind !== "video" && safeUrl(t.url) && !t.upcoming) {
    side = `<a class="btn small" href="${esc(safeUrl(t.url))}" target="_blank" rel="noopener" aria-label="${esc(t.title)} 제출하기 (새 탭)">제출하기</a>`;
  }
  const sub = t.upcoming ? `${label} · ${whenText(t.upcoming)}에 열려요` : `${label} · ${esc(t.course)}`;
  return `<li class="task"><div class="task-main"><p class="task-title">${esc(t.title)}</p>
    <p class="task-sub">${sub}</p></div>
    <div class="task-side"><span class="task-due">${whenText(t.deadline)}</span>${side}</div></li>`;
}

function ctaHtml(d) {
  const a = state.active;
  if (state.playbackStarting) return `<button class="btn primary block" disabled>재생 요청 중…</button>`;
  if (a) {
    const done = a.done_count + a.skipped_count;
    return `<div class="cta-progress">
      <div class="row"><strong>강의 듣는 중</strong><span class="today-sub">${done}/${a.total}개 완료</span></div>
      <progress value="${done}" max="${a.total || 1}" aria-label="강의 재생 진행률"></progress>
      <p class="today-sub">창을 닫아도 계속 들어요.${a.cancel_requested ? " 중지하는 중이에요." : ""}</p>
      ${a.cancel_requested ? "" : '<button class="btn ghost block" id="week-stop">중지</button>'}</div>`;
  }
  if (week.confirming && week.plan) {
    const p = week.plan;
    const risky = p.items.filter((i) => i.at_risk).length;
    return `<div class="cta-confirm">
      <p class="confirm-title">강의 ${p.items.length}개를 순서대로 끝까지 들을게요</p>
      <p class="today-sub">1배속 · 약 ${fmtDuration(p.total_seconds)}${p.expected_end ? ` · ${fmtTime(new Date(p.expected_end))}쯤 끝나요` : ""}</p>
      ${risky ? `<p class="warning">마감 전에 끝나지 않는 강의가 ${risky}개 있어요</p>` : ""}
      <p class="confirm-note">직접 시청하지 않은 수강은 학칙상 문제가 될 수 있어요.</p>
      <button class="btn primary block" id="week-start">시작하기</button>
      <button class="btn ghost block" id="week-cancel">취소</button></div>`;
  }
  if (d.videos.length) {
    const hasAssignments = d.assignments.length > 0;
    return `<button class="btn primary block" id="week-play">강의 ${d.videos.length}개 한 번에 듣기</button>
      <p class="cta-note">${fmtDuration(d.video_seconds)} 분량${hasAssignments ? " · 과제·퀴즈는 직접 제출해야 해요" : ""}</p>`;
  }
  const next = d.assignments.find((x) => safeUrl(x.url) && x.status !== "upcoming");
  if (next) {
    return `<a class="btn primary block" href="${esc(safeUrl(next.url))}" target="_blank" rel="noopener">${esc(next.title)} 제출하러 가기</a>
      <p class="cta-note">학교 LMS에서 제출하면 다음 동기화 때 목록에서 빠져요</p>`;
  }
  return "";
}

function bindWeek() {
  const on = (id, fn) => document.getElementById(id)?.addEventListener("click", fn);
  on("week-all", () => navigate("todos"));
  on("week-missed", () => { state.assignmentFilter = "overdue"; navigate("assignments"); });
  on("week-cancel", () => { week.confirming = false; week.plan = null; drawWeek(); });
  on("week-play", async (e) => {
    e.currentTarget.disabled = true;
    try {
      week.plan = await api("/playback/batches/preview?scope=week");
      week.confirming = true;
    } catch (err) {
      toast(err.message, true);
    }
    drawWeek();
  });
  on("week-start", async (e) => {
    e.currentTarget.disabled = true;
    try {
      const batch = await api("/playback/batches", { method: "POST", json: { scope: "week" } });
      state.active = await api(`/playback/batches/${batch.id}`);
      week.confirming = false;
      week.plan = null;
      week.lastActive = batch.id;
      toast(`강의 ${batch.total}개를 듣기 시작했어요`);
      startPolling();
    } catch (err) {
      toast(err.message, true);
    }
    drawWeek();
  });
  on("week-stop", async (e) => {
    e.currentTarget.disabled = true;
    try {
      await api(`/playback/batches/${state.active.id}`, { method: "DELETE" });
      state.active = await api(`/playback/batches/${state.active.id}`);
      if (!["queued", "running"].includes(state.active.status)) state.active = null;
      toast("지금 듣는 강의가 끝나는 대로 멈출게요");
    } catch (err) {
      toast(err.message, true);
    }
    drawWeek();
  });
}

// 재생 진행률을 갱신하고(app.js 폴링이 state.active를 갱신), 끝나면 목록을 새로 불러온다.
setInterval(() => {
  if (!document.getElementById("week-panel") || week.confirming) return;
  const activeId = state.active?.id ?? null;
  if (week.lastActive !== null && activeId === null) renderWeek();
  else if (activeId !== null) { week.lastActive = activeId; drawWeek(); }
}, 5000);

// app.js가 먼저 첫 화면을 그렸다면 여기서 채운다.
if (document.getElementById("week-panel")) renderWeek();
