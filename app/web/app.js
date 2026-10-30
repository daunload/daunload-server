"use strict";

/* ---------------- 공통 ---------------- */

const $ = (sel, root = document) => root.querySelector(sel);
const DAY = 86400000;
const KIND = { video: "영상", assignment: "과제", quiz: "퀴즈" };
const JOB_STATUS = {
  queued: "대기", playing: "재생 중", done: "완료", failed: "실패",
  skipped: "이미 완료", locked: "잠김", cancelled: "취소",
};
const BATCH_STATUS = {
  queued: "대기 중", running: "재생 중", done: "완료", partial: "일부 완료",
  failed: "실패", cancelled: "취소됨",
};
const GAP_SECONDS = 20; // 서버 기본 간격(10~30초)의 평균. 예상 시각 계산용

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 저장 불가 환경 */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* 저장 불가 환경 */ } },
};

const VIEW_NAMES = { dashboard: "학습 현황", todos: "할 일", assignments: "미제출 과제", calendar: "캘린더", courses: "과목", announcements: "공지", playback: "영상 재생", videos: "강의 영상", notifications: "알림", account: "계정", admin: "사용자 관리" };
const initialView = location.hash.slice(1) || "dashboard";

const state = {
  token: store.get("token"),
  dashboardPlan: null, dashboardError: null, dashboardLoading: false, playbackStarting: false,
  view: Object.hasOwn(VIEW_NAMES, initialView) ? initialView : "dashboard",
  me: null, cred: null, todos: [], courses: [], announcements: null,
  batches: [], active: null, plan: null, todoFilter: "all", noticeCourse: null,
  videoCourse: null, videoList: null, savedVideos: null,
  videoTab: "videos", materialList: null, notDownloadedOnly: false, bulk: null,
  assignments: null, assignmentFilter: "unsubmitted", todoSearch: "", todoDeadline: "all",
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function textOf(html) {
  const doc = new DOMParser().parseFromString(html || "", "text/html");
  doc.querySelectorAll("br, p, div, li").forEach((el) => el.append("\n"));
  return (doc.body.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
}

const pad = (n) => String(n).padStart(2, "0");
const fmtTime = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const fmtDate = (d) => `${d.getMonth() + 1}/${d.getDate()} ${fmtTime(d)}`;
const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

function dayName(d) {
  const diff = Math.round((startOfDay(d) - startOfDay(new Date())) / DAY);
  if (diff === 0) return "오늘";
  if (diff === 1) return "내일";
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEKDAY[d.getDay()]})`;
}

function fmtDuration(sec) {
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}시간 ${m % 60}분` : `${h}시간`;
}

function timeLeft(deadline) {
  const ms = deadline - Date.now();
  if (ms <= 0) return { text: "마감 지남", cls: "urgent" };
  const cls = ms < 3 * 3600e3 ? "urgent" : ms < DAY ? "soon" : "";
  const h = Math.floor(ms / 3600e3);
  const text = h >= 24 ? `${Math.floor(h / 24)}일 ${h % 24}시간 남음` : h >= 1 ? `${h}시간 ${Math.floor((ms % 3600e3) / 60e3)}분 남음` : `${Math.max(1, Math.floor(ms / 60e3))}분 남음`;
  return { text, cls };
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast show${bad ? " bad" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), 3200);
}

class ApiError extends Error {
  constructor(status, message, code) { super(message); this.status = status; this.code = code; }
}

async function api(path, { method = "GET", json, form } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = new URLSearchParams(form);
  const res = await fetch(path, { method, headers, body });
  if (res.status === 401 && state.token) { logout(); throw new ApiError(401, "다시 로그인하세요."); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail
      : detail?.message ? detail.message
      : Array.isArray(detail) ? detail.map((d) => d.msg).join(", ")
      : `요청 실패 (${res.status})`;
    throw new ApiError(res.status, msg, detail?.code);
  }
  return data;
}

/* ---------------- 로그인 ---------------- */

function showAuth() {
  $("#startup").hidden = true;
  $("#app").hidden = true;
  $("#auth").hidden = false;
}

$("#password-toggle").addEventListener("click", (e) => {
  const input = $("#password");
  const visible = input.type === "password";
  input.type = visible ? "text" : "password";
  e.currentTarget.textContent = visible ? "숨기기" : "표시";
  e.currentTarget.setAttribute("aria-pressed", String(visible));
});
$("#retry-boot").addEventListener("click", boot);
$("#startup-login").addEventListener("click", logout);

// 학번을 바꾸면 동의 단계는 처음부터 다시(이미 가입한 학번일 수 있으므로)
$("#login-form").student_id.addEventListener("input", () => {
  $("#consent-row").hidden = true;
  $("#auth-submit").textContent = "로그인";
  $("#auth-error").textContent = "";
});

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const btn = $("#auth-submit");
  const consentShown = !$("#consent-row").hidden;
  if (consentShown && !f.agree.checked) {
    $("#auth-error").textContent = "동의해야 시작할 수 있습니다.";
    return;
  }
  btn.disabled = true;
  btn.textContent = "학교 계정 확인 중";
  $("#auth-error").textContent = "";
  try {
    const form = { username: f.student_id.value.trim(), password: f.password.value };
    if (consentShown) form.agree_terms = "true";
    const res = await api("/auth/login", { method: "POST", form });
    state.token = res.access_token;
    store.set("token", state.token);
    f.reset();
    $("#consent-row").hidden = true;
    await boot();
  } catch (err) {
    if (err.code === "consent_required") {
      $("#consent-row").hidden = false;
      f.agree.focus();
    } else {
      $("#auth-error").textContent = err.message;
    }
  } finally {
    btn.disabled = false;
    btn.textContent = $("#consent-row").hidden ? "로그인" : "동의하고 시작하기";
  }
});

function logout() {
  $("#admin-menu").hidden = true;
  state.token = null;
  store.del("token");
  stopPolling();
  Object.assign(state, { me: null, cred: null, todos: [], courses: [], announcements: null,
    dashboardPlan: null, dashboardError: null, dashboardLoading: false, playbackStarting: false,
    batches: [], active: null, plan: null, planForm: null, planQuery: null,
    videoCourse: null, videoList: null, savedVideos: null, materialList: null, bulk: null,
  assignments: null, assignmentFilter: "unsubmitted", noticeCourse: null, todoSearch: "", todoFilter: "all", todoDeadline: "all" });
  $("#password").type = "password";
  $("#password-toggle").textContent = "표시";
  $("#password-toggle").setAttribute("aria-pressed", "false");
  showAuth();
}
$("#logout-btn").addEventListener("click", logout);

/* ---------------- 데이터 ---------------- */

async function loadCore() {
  const [me, cred, todos, courses, batches] = await Promise.all([
    api("/me"),
    api("/lms/credentials").catch((e) => (e.status === 404 ? null : Promise.reject(e))),
    api("/todos"),
    api("/courses"),
    api("/playback/batches?limit=10"),
  ]);
  Object.assign(state, { me, cred, todos, courses, batches });
  $("#admin-menu").hidden = !me.is_admin;
  const running = batches.find((b) => b.status === "queued" || b.status === "running");
  state.active = running ? await api(`/playback/batches/${running.id}`) : null;
  if (state.active) startPolling();
}

async function boot() {
  if (!state.token) return showAuth();
  $("#auth").hidden = true;
  $("#startup").hidden = false;
  $("#startup-message").textContent = "잠시만 기다려 주세요.";
  $("#retry-boot").hidden = true;
  $("#startup-login").hidden = true;
  try {
    await loadCore();
  } catch (err) {
    if (err.status !== 401) {
      $("#startup-message").textContent = `학습 현황을 불러오지 못했습니다. ${err.message}`;
      $("#retry-boot").hidden = false;
      $("#startup-login").hidden = false;
    }
    return;
  }
  $("#startup").hidden = true;
  $("#auth").hidden = true;
  $("#app").hidden = false;
  history.replaceState(null, "", `#${state.view}`);
  renderAll();
}

function renderAll() {
  renderSync();
  renderOverview();
  renderRail();
  renderView();
}

function renderOverview() {
  const now = new Date();
  $("#today-label").textContent = `${now.getMonth() + 1}월 ${now.getDate()}일 ${WEEKDAY[now.getDay()]}요일`;
  const soon = state.todos.filter((t) => { const ms = new Date(t.deadline) - now; return ms > 0 && ms <= DAY; }).length;
  const videos = state.todos.filter((t) => t.kind === "video");
  const stats = [
    ["24시간 내 마감", soon, "개", soon ? "먼저 확인해 주세요" : "급한 마감 없이 여유로워요", "urgent"],
    ["남은 할 일", state.todos.length, "개", "영상 · 과제 · 퀴즈", ""],
    ["미수강 영상", videos.length, "개", `총 ${fmtDuration(videos.reduce((sum, t) => sum + (t.duration || 0), 0))} 분량`, ""],
    ["수강 중인 과목", state.courses.length, "개", "과목별 출석을 확인하세요", ""],
  ];
  if (!state.cred?.last_synced_at) stats.forEach((stat) => { stat[1] = "—"; stat[3] = "첫 동기화를 기다리고 있어요"; });
  const shortcuts = ["soon", "all", "video", "courses"];
  $("#stats").innerHTML = stats.map(([label, value, unit, hint, cls], i) => `<button class="stat ${cls}" data-shortcut="${shortcuts[i]}" aria-label="${label} ${value}${unit} 보기"><span class="stat-label">${label}<span aria-hidden="true">↗</span></span><span class="stat-value"><strong>${value}</strong><span>${unit}</span></span><small>${hint}</small></button>`).join("");
}

/* ---------------- 상단: 동기화 ---------------- */

function renderSync() {
  const el = $("#sync-status");
  const c = state.cred;
  el.classList.remove("bad");
  if (!c) { el.textContent = "LMS 연결 정보가 없습니다"; return; }
  if (c.status === "invalid") { el.textContent = "학교 비밀번호가 바뀌었습니다. 다시 로그인하세요."; el.classList.add("bad"); return; }
  const who = state.me?.student_id ? `${state.me.student_id} ` : "";
  el.textContent = c.last_synced_at ? `${who}마지막 동기화 ${fmtDate(new Date(c.last_synced_at))}` : `${who}첫 동기화를 기다리는 중`;
  if (c.status === "error") { el.textContent += " (최근 동기화 실패)"; el.classList.add("bad"); }
}

$("#sync-btn").addEventListener("click", async () => {
  const btn = $("#sync-btn");
  const before = state.cred?.last_synced_at;
  btn.disabled = true;
  btn.textContent = "동기화 중";
  try {
    await api("/sync", { method: "POST" });
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const cred = await api("/lms/credentials");
      if (cred.last_synced_at !== before || cred.status !== "ok") { state.cred = cred; break; }
    }
    await loadCore();
    state.announcements = null;
    state.assignments = null;
    renderAll();
    const updated = state.cred?.last_synced_at && state.cred.last_synced_at !== before;
    toast(state.cred?.status !== "ok" ? "동기화하지 못했습니다. 계정 상태를 확인하세요."
      : updated ? "최신 학습 현황으로 업데이트했어요." : "동기화가 지연되고 있어요. 잠시 후 다시 확인해 주세요.", state.cred?.status !== "ok");
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "지금 동기화";
  }
});

/* ---------------- 마감 레일 ---------------- */

/** 진행 중인 묶음(없으면 미리보기 계획)의 항목별 예상 종료 시각과 전체 구간 */
function playbackTimeline() {
  if (state.active) {
    const now = Date.now();
    let cursor = now;
    const ends = new Map();
    for (const j of state.active.jobs) {
      if (j.status !== "queued" && j.status !== "playing") continue;
      const remaining = j.status === "playing" ? Math.max(0, j.duration - j.progress_seconds) : j.duration;
      cursor = j.expected_end ? new Date(j.expected_end).getTime() : cursor + (remaining + GAP_SECONDS) * 1000;
      ends.set(j.module_item_id, cursor);
    }
    return { kind: "run", start: now, end: cursor, ends };
  }
  if (state.plan && state.plan.items.length) {
    const ends = new Map(state.plan.items.map((i) => [i.module_item_id, new Date(i.expected_end).getTime()]));
    return {
      kind: "plan",
      start: new Date(state.plan.items[0].expected_start).getTime(),
      end: new Date(state.plan.expected_end).getTime(),
      ends,
    };
  }
  return null;
}

function renderRail() {
  const rail = $("#rail");
  const today = startOfDay(new Date());
  const timeline = playbackTimeline();
  const days = [...Array(7)].map((_, i) => new Date(today.getTime() + i * DAY));
  const end = days[6].getTime() + DAY;

  const cols = days.map((d, i) => {
    const items = state.todos
      .map((t) => ({ ...t, at: new Date(t.deadline) }))
      .filter((t) => t.at >= d && t.at < new Date(d.getTime() + DAY))
      .sort((a, b) => a.at - b.at || a.title.localeCompare(b.title, "ko"));
    const chips = items.map((t) => {
      const expected = timeline?.ends.get(t.lms_item_id);
      const risk = t.kind === "video" && expected && expected > t.at.getTime();
      const soon = t.at - Date.now() < DAY;
      const cls = `chip ${t.kind}${risk ? " risk" : soon ? " soon" : ""}`;
      const tip = `${KIND[t.kind]} ${t.title} (${t.course_name})${risk ? " — 재생이 마감 전에 끝나지 않을 수 있음" : ""}`;
      const url = safeUrl(t.url);
      const content = `<time>${fmtTime(t.at)} · ${KIND[t.kind] || "할 일"}</time><span>${esc(t.title)}</span><small>${esc(t.course_name)}</small>${risk ? '<small class="risk-label">예상 재생 마감 초과</small>' : ""}`;
      return url ? `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener" aria-label="${esc(tip)} (새 탭)">${content}</a>` : `<div class="${cls}">${content}</div>`;
    }).join("");
    const weekend = d.getDay() === 0 || d.getDay() === 6;
    return `<div class="day${i === 0 ? " today" : ""}${weekend ? " weekend" : ""}">
      <div class="day-label"><span>${i === 0 ? "오늘" : WEEKDAY[d.getDay()]}</span><b>${d.getDate()}</b></div>
      <div class="chips">${chips || '<span class="day-empty">마감 없음</span>'}</div></div>`;
  }).join("");

  let band = "";
  let note = "";
  if (timeline) {
    const span = end - today.getTime();
    const left = Math.max(0, (timeline.start - today.getTime()) / span) * 100;
    const right = Math.min(1, (timeline.end - today.getTime()) / span) * 100;
    band = `<div class="band${timeline.kind === "plan" ? " plan" : ""}" style="left:${left}%;width:${Math.max(0.5, right - left)}%"></div>`;
    const risky = state.todos.filter((t) => t.kind === "video" && timeline.ends.get(t.lms_item_id) > new Date(t.deadline).getTime()).length;
    const finish = fmtDate(new Date(timeline.end));
    if (timeline.kind === "run") {
      const a = state.active;
      note = `영상 재생 중: ${a.done_count + a.skipped_count}/${a.total}개 끝남, ${finish}쯤 모두 끝납니다.`;
    } else {
      note = `미리보기한 재생 계획대로라면 ${finish}쯤 끝납니다.`;
    }
    if (risky) note += ` 영상 ${risky}개는 마감 전에 끝나지 않습니다.`;
  }
  rail.innerHTML = `<div class="rail-inner">${cols}${band}</div>`;
  let noteEl = $(".band-note");
  if (!noteEl) {
    noteEl = document.createElement("p");
    noteEl.className = "band-note";
    rail.after(noteEl);
  }
  noteEl.textContent = note || (state.todos.some((t) => new Date(t.deadline) >= today && new Date(t.deadline).getTime() < end) ? "" : "앞으로 7일 안에 마감되는 할 일이 없습니다.");
  noteEl.classList.toggle("risk", /마감 전에 끝나지 않습니다/.test(note));
}

/* ---------------- 화면 전환 ---------------- */

function closeMenu() {
  $(".navigation").classList.remove("expanded");
  $("#menu-toggle").setAttribute("aria-expanded", "false");
}
function navigate(view, push = true) {
  state.view = Object.hasOwn(VIEW_NAMES, view) ? view : "todos";
  store.set("view", state.view);
  if (state.view === "videos") state.savedVideos = null;  // 동기화로 새로 저장된 주소 반영
  if (push && location.hash !== `#${state.view}`) history.pushState(null, "", `#${state.view}`);
  closeMenu();
  renderView();
  $("#view").focus({ preventScroll: true });
  $("#view").scrollIntoView({ block: "start" });
}
window.addEventListener("popstate", () => {
  if (!$("#app").hidden) navigate(location.hash.slice(1) || "todos", false);
});
$("#menu-toggle").addEventListener("click", () => {
  const open = $(".navigation").classList.toggle("expanded");
  $("#menu-toggle").setAttribute("aria-expanded", String(open));
});
$(".navigation").addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $(".navigation").classList.contains("expanded")) { closeMenu(); $("#menu-toggle").focus(); }
});
document.querySelectorAll(".side button").forEach((b) => b.addEventListener("click", () => navigate(b.dataset.view)));
$(".skip-link").addEventListener("click", (e) => {
  e.preventDefault();
  const target = $("#app").hidden ? $("#login-form").student_id : $("#view");
  target.focus();
  target.scrollIntoView({ block: "start" });
});
$(".home-link").addEventListener("click", (e) => { e.preventDefault(); navigate("dashboard"); });
$("#stats").addEventListener("click", (e) => {
  const key = e.target.closest("[data-shortcut]")?.dataset.shortcut;
  if (!key) return;
  if (state.view === "dashboard") return;
  if (key === "courses") return navigate("courses");
  state.todoSearch = "";
  state.todoFilter = key === "video" ? "video" : "all";
  state.todoDeadline = key === "soon" ? "soon" : "all";
  navigate("todos");
});
function setCalendar(open) {
  $("#calendar-content").hidden = !open;
  $("#calendar-toggle").setAttribute("aria-expanded", String(open));
  $("#calendar-toggle").textContent = open ? "일정 접기" : "일정 펼치기";
}
setCalendar(false);
$("#calendar-toggle").addEventListener("click", () => setCalendar($("#calendar-content").hidden));

function renderView() {
  $(".overview").hidden = state.view !== "todos";
  $(".workspace").classList.toggle("dashboard-workspace", state.view === "dashboard");
  $("#stats").hidden = state.view === "dashboard";
  $(".rail-wrap").hidden = !["todos", "playback"].includes(state.view);
  $("#current-view").textContent = VIEW_NAMES[state.view] || "할 일";
  document.title = `${VIEW_NAMES[state.view] || "할 일"} · 숭실 LMS`;
  document.querySelectorAll(".side button").forEach((b) => {
    const selected = b.dataset.view === state.view;
    b.classList.toggle("on", selected);
    if (selected) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  const views = { dashboard: viewDashboard, todos: viewTodos, assignments: viewAssignments, calendar: () => viewCalendar(), // calendar.js에 있다. 불러오지 못해도 다른 화면은 동작하도록 실행할 때 찾는다
    courses: viewCourses, announcements: viewAnnouncements, playback: viewPlayback, videos: viewVideos, notifications: viewNotifications, account: viewAccount, admin: viewAdmin };
  (views[state.view] || viewTodos)();
}

function viewError(message, retry) {
  $("#view").innerHTML = `<div class="panel empty" role="alert"><strong>불러오지 못했어요</strong><p>${esc(message)}</p><button class="btn" id="retry-view">다시 시도</button></div>`;
  $("#retry-view").addEventListener("click", retry);
}

function needCred() {
  if (state.cred) return false;
  $("#view").innerHTML = `<div class="panel empty"><p>LMS 연결 정보가 없습니다. 로그아웃한 뒤 학교 비밀번호로 다시 로그인하세요.</p></div>`;
  return true;
}

/* ---------------- 학습 현황(첫 화면) ----------------
 * 이번 주 할 일만 짧게 보여 주고, 버튼 하나로 끝낼 수 있게 한다. 내용은 week.js가 그린다.
 */
function viewDashboard() {
  if (needCred()) return;
  $("#view").innerHTML = `<section id="dashboard-playback" class="panel" aria-label="재생 가능한 강의"></section><div id="week-panel"></div>`;
  loadDashboardPlayback();
  if (typeof renderWeek === "function") renderWeek();
}

async function loadDashboardPlayback() {
  const root = $("#dashboard-playback");
  if (!root) return;
  const token = state.token;
  const valid = () => root.isConnected && state.token === token && state.view === "dashboard";
  state.dashboardLoading = true;
  state.dashboardError = null;
  drawDashboardPlayback();
  try {
    const plan = await api("/playback/batches/preview?scope=all");
    if (valid()) state.dashboardPlan = plan;
  } catch (err) {
    if (valid()) state.dashboardError = err.message;
  } finally {
    if (valid()) {
      state.dashboardLoading = false;
      drawDashboardPlayback();
    }
  }
}

function drawDashboardPlayback() {
  const root = $("#dashboard-playback");
  if (!root) return;
  const plan = state.dashboardPlan;
  const items = plan?.items || [];
  const batch = state.active;
  const jobs = batch?.jobs || [];
  const done = (batch?.done_count || 0) + (batch?.skipped_count || 0);
  const progress = batch ? `<div class="dashboard-playback-progress" role="status">
    <strong>${batch.status === "queued" ? "재생 대기 중" : "강의 재생 중"} · ${done}/${batch.total}개 완료</strong>
    <progress value="${done}" max="${batch.total || 1}" aria-label="전체 강의 재생 진행률"></progress>
    <p>${batch.status === "queued" && batch.queue_position != null ? `대기 ${batch.queue_position}번째 · ` : ""}창을 닫아도 서버에서 계속 재생합니다.</p>
    <button class="btn" id="dashboard-playback-details">재생 현황 보기</button></div>` : "";
  const list = items.length ? `<ol class="dashboard-lecture-list">${items.map((item) => {
    const job = jobs.find((j) => j.module_item_id === item.module_item_id && j.lms_course_id === item.lms_course_id);
    return `<li><div><h3>${esc(item.title)}</h3><p>${esc(item.course_name)} · ${esc(item.module_title)} · ${fmtDuration(item.duration)}</p>
      <p>마감 ${fmtDate(new Date(item.deadline))}${item.at_risk ? " · 마감 전 완료가 어려울 수 있습니다" : ""}</p></div>
      ${job ? `<span class="status ${esc(job.status)}">${esc(JOB_STATUS[job.status] || job.status)}</span>` : ""}</li>`;
  }).join("")}</ol>` : `<p class="dashboard-playback-note">현재 재생 가능한 미수강 강의가 없습니다.</p>`;
  root.innerHTML = `<div class="panel-body">
    <div class="view-head"><h2>재생 가능한 강의${plan && !state.dashboardLoading ? ` ${items.length}개` : ""}</h2>
      <button class="btn" id="dashboard-playback-refresh" ${state.dashboardLoading || state.playbackStarting ? "disabled" : ""}>목록 새로고침</button></div>
    <p class="dashboard-playback-note">전체 과목에서 지금 열려 있고 마감 전인 미수강 강의입니다. 마지막 동기화 기준이며, 재생 직전에 완료 여부를 다시 확인합니다.</p>
    ${progress}
    ${state.dashboardLoading ? '<p role="status">강의 목록을 불러오는 중입니다.</p>'
      : state.dashboardError ? `<div role="alert"><p>${esc(state.dashboardError)}</p><button class="btn" id="dashboard-playback-retry">다시 시도</button></div>`
      : `${list}${!batch ? `<button class="btn primary block" id="dashboard-play-all" ${!items.length || state.playbackStarting ? "disabled" : ""}>
          ${state.playbackStarting ? "재생 요청 중…" : `모두 재생 (${items.length}개)`}</button>
        ${items.length ? `<p class="dashboard-playback-note">총 ${fmtDuration(items.reduce((sum, item) => sum + item.duration, 0))} 분량 · 1배속으로 순서대로 재생하고 LMS 완료 여부를 확인합니다.</p>` : ""}` : ""}`}
  </div>`;
  $("#dashboard-playback-refresh")?.addEventListener("click", loadDashboardPlayback);
  $("#dashboard-playback-retry")?.addEventListener("click", loadDashboardPlayback);
  $("#dashboard-playback-details")?.addEventListener("click", () => navigate("playback"));
  $("#dashboard-play-all")?.addEventListener("click", startDashboardPlayback);
}

async function startDashboardPlayback() {
  if (state.playbackStarting || state.active || !state.dashboardPlan?.items.length) return;
  const token = state.token;
  state.playbackStarting = true;
  drawDashboardPlayback();
  if (typeof drawWeek === "function") drawWeek();
  try {
    const batch = await api("/playback/batches", { method: "POST", json: { scope: "all" } });
    if (state.token !== token) return;
    // Keep the accepted queue entry even if the subsequent detail request fails.
    state.active = { ...batch, jobs: [] };
    startPolling();
    toast(`강의 ${batch.total}개를 재생 대기열에 등록했습니다`);
    try {
      const detail = await api(`/playback/batches/${batch.id}`);
      if (state.token === token && state.active?.id === batch.id) state.active = detail;
    } catch { /* 진행 상황은 다음 폴링에서 다시 조회한다. */ }
  } catch (err) {
    if (state.token !== token) return;
    toast(err.message, true);
    if (err.status === 409) {
      try { await loadCore(); } catch (refreshError) { toast(refreshError.message, true); }
    }
    if (err.status === 404) await loadDashboardPlayback();
  } finally {
    if (state.token === token) {
      state.playbackStarting = false;
      drawDashboardPlayback();
      if (typeof drawWeek === "function") drawWeek();
    }
  }
}

/* ---------------- 할 일 ---------------- */

function viewTodos() {
  if (needCred()) return;
  const f = state.todoFilter;
  const counts = (k) => state.todos.filter((t) => k === "all" || t.kind === k).length;
  const filters = ["all", "video", "assignment", "quiz"].map((k) =>
    `<button data-f="${k}" aria-pressed="${f === k}" class="${f === k ? "on" : ""}">${k === "all" ? "전체" : KIND[k]} ${counts(k)}</button>`).join("");
  $("#view").innerHTML = `
    <div class="view-head"><h2>나의 할 일 <span class="result-count" id="todo-count"></span></h2><span class="muted small-text">마감이 가까운 순서예요</span></div>
    <div class="todo-toolbar"><label class="search-field"><span>할 일 검색</span><input id="todo-search" type="search" placeholder="할 일 또는 과목 검색" value="${esc(state.todoSearch)}"></label>
      <div class="filters" aria-label="할 일 종류">${filters}</div></div>
    <div class="row"><label class="check"><input type="checkbox" id="todo-soon" ${state.todoDeadline === "soon" ? "checked" : ""}><span>24시간 내 마감만 보기</span></label></div>
    <span class="sr-only" id="todo-result-status" role="status" aria-atomic="true"></span><div class="panel" id="todo-results"></div>`;
  renderTodoResults();
  $("#view .filters").addEventListener("click", (e) => {
    const k = e.target.closest("button")?.dataset.f;
    if (k) { state.todoFilter = k; viewTodos(); $(`#view [data-f="${k}"]`).focus(); }
  });
  $("#todo-search").addEventListener("input", (e) => { state.todoSearch = e.target.value; renderTodoResults(); });
  $("#todo-soon").addEventListener("change", (e) => { state.todoDeadline = e.target.checked ? "soon" : "all"; renderTodoResults(); });
}

function renderTodoResults() {
  const query = state.todoSearch.trim().toLocaleLowerCase();
  const todos = state.todos.filter((t) => {
    const ms = new Date(t.deadline) - Date.now();
    return (state.todoFilter === "all" || t.kind === state.todoFilter)
      && (!query || `${t.title} ${t.course_name}`.toLocaleLowerCase().includes(query))
      && (state.todoDeadline !== "soon" || (ms > 0 && ms <= DAY));
  }).sort((a, b) => new Date(a.deadline) - new Date(b.deadline) || a.title.localeCompare(b.title, "ko"));
  const groups = new Map();
  for (const t of todos) {
    const d = new Date(t.deadline);
    const key = startOfDay(d).getTime();
    if (!groups.has(key)) groups.set(key, { label: dayName(d), items: [] });
    groups.get(key).items.push(t);
  }
  $("#todo-count").textContent = `${todos.length}개`;
  $("#todo-result-status").textContent = `조건에 맞는 할 일 ${todos.length}개`;
  const filtered = query || state.todoFilter !== "all" || state.todoDeadline !== "all";
  $("#todo-results").innerHTML = groups.size
    ? [...groups.values()].map((g) => `<div class="group-label">${esc(g.label)} · ${g.items.length}개</div><ul class="list">${g.items.map(todoItem).join("")}</ul>`).join("")
    : `<div class="empty"><strong>${filtered ? "조건에 맞는 할 일이 없어요" : "남은 할 일이 없어요"}</strong><p>${filtered ? "검색어나 필터를 바꿔 다른 할 일을 확인해 보세요." : state.cred?.last_synced_at ? "새로운 할 일이 생기면 이곳에 모아 보여드릴게요." : "첫 동기화가 끝나면 과목과 할 일이 표시됩니다. 잠시 후 지금 동기화를 눌러 주세요."}</p>${filtered ? '<button class="btn" id="reset-todos">전체 할 일 보기</button>' : ""}</div>`;
  $("#reset-todos")?.addEventListener("click", () => { state.todoSearch = ""; state.todoFilter = "all"; state.todoDeadline = "all"; viewTodos(); $("#todo-search").focus(); });
}

function todoItem(t) {
  const d = new Date(t.deadline);
  const left = timeLeft(d);
  const where = t.kind === "video" ? [t.module_title, t.duration && fmtDuration(t.duration)].filter(Boolean).join(" · ") : "";
  const url = safeUrl(t.url);
  const title = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(t.title)} <span class="todo-link" aria-label="새 탭에서 열기">↗</span></a>` : esc(t.title);
  return `<li><div class="todo-title-row">
    <span class="title"><span class="kind ${esc(t.kind)}">${KIND[t.kind] || "할 일"}</span>${title}</span>
    <span class="left ${left.cls}">${left.text}</span></div>
    <span class="meta row"><span>${esc(t.course_name)}</span>${where ? `<span>${esc(where)}</span>` : ""}<span>마감 ${fmtDate(d)}</span></span>
  </li>`;
}

function safeUrl(value) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}

/* ---------------- 미제출 과제 ---------------- */

const ASSIGNMENT_STATUS = {
  open: "제출 가능", locked: "잠김", overdue: "마감 지남", upcoming: "열리기 전", no_due: "마감 없음",
};

function daysAgo(d) {
  const h = Math.floor((Date.now() - d) / 3600e3);
  return h >= 24 ? `${Math.floor(h / 24)}일 지남` : `${Math.max(1, h)}시간 지남`;
}

async function viewAssignments() {
  if (needCred()) return;
  if (!state.assignments) {
    $("#view").innerHTML = `<div class="view-head"><h1>미제출 과제</h1></div><div class="panel empty"><p>불러오는 중</p></div>`;
    try { state.assignments = await api("/assignments"); } catch (err) { toast(err.message, true); return; }
    if (state.view !== "assignments") return;
  }
  const all = state.assignments;
  const f = state.assignmentFilter;
  const count = (k) => all.filter((a) => k === "unsubmitted" || a.status === k || (k === "open" && a.status === "locked")).length;
  const filters = ["unsubmitted", "open", "overdue", "upcoming", "no_due"]
    .filter((k) => k === "unsubmitted" || count(k))
    .map((k) => `<button data-f="${k}" class="${f === k ? "on" : ""}">${k === "unsubmitted" ? "전체" : ASSIGNMENT_STATUS[k]} ${count(k)}</button>`).join("");
  const list = all.filter((a) => f === "unsubmitted" || a.status === f || (f === "open" && a.status === "locked"));

  const groups = new Map();
  for (const a of list) {
    const g = a.status === "locked" ? "open" : a.status;
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(a);
  }
  const body = groups.size
    ? [...groups.entries()].map(([g, items]) => `<div class="group-label">${ASSIGNMENT_STATUS[g]} ${items.length}</div>
        <ul class="list">${items.map(assignmentItem).join("")}</ul>`).join("")
    : `<div class="empty"><p>${f === "unsubmitted" ? "미제출 과제가 없습니다." : "해당하는 과제가 없습니다."}</p></div>`;

  $("#view").innerHTML = `
    <div class="view-head"><h1>미제출 과제</h1><div class="filters">${filters}</div></div>
    <div class="panel">${body}</div>
    <p class="muted small-text">마지막 동기화 기준입니다. 방금 제출했다면 지금 동기화를 누르세요. 제출 여부를 확인할 수 없는 과제는 넣지 않았습니다.</p>`;
  $("#view .filters").addEventListener("click", (e) => {
    const k = e.target.closest("button")?.dataset.f;
    if (k) { state.assignmentFilter = k; viewAssignments(); }
  });
}

function assignmentItem(a) {
  const kind = a.kind === "quiz" ? "quiz" : "assignment";
  const title = a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>` : esc(a.title);
  const meta = [`<span>${esc(a.course_name)}</span>`];
  if (a.group_name) meta.push(`<span>${esc(a.group_name)}</span>`);
  const deadline = a.deadline && new Date(a.deadline);
  if (a.status === "open" || a.status === "locked") {
    const left = timeLeft(deadline);
    meta.push(`<span>마감 ${fmtDate(deadline)}</span><span class="left ${left.cls}">${left.text}</span>`);
    if (a.status === "locked") meta.push(`<span>LMS에서 잠겨 있음</span>`);
  } else if (a.status === "overdue") {
    meta.push(`<span>마감 ${fmtDate(deadline)}</span><span class="left urgent">${daysAgo(deadline)}</span>`);
    const lock = a.lock_at && new Date(a.lock_at);
    if (lock && lock > Date.now()) meta.push(`<span>LMS 잠금 ${fmtDate(lock)} (그전까지 지각 제출이 열려 있을 수 있음)</span>`);
  } else if (a.status === "upcoming") {
    meta.push(`<span>${a.unlock_at ? `${fmtDate(new Date(a.unlock_at))}에 열림` : ""}</span>${deadline ? `<span>마감 ${fmtDate(deadline)}</span>` : ""}`);
  }
  if (a.points_possible) meta.push(`<span>${a.points_possible}점</span>`);
  return `<li><span class="title"><span class="kind ${kind}">${KIND[kind]}</span>${title}</span>
    <span class="meta row">${meta.join("")}</span></li>`;
}

/* ---------------- 과목 ---------------- */

function viewCourses() {
  if (needCred()) return;
  const rows = state.courses.map((c) => {
    const weeks = (c.attendance || []).filter((w) => w.statuses.length);
    const grid = weeks.length
      ? `<div class="attend" aria-label="주차별 출석">${weeks.map((w) => `<div class="week"><div class="cells">${w.statuses
          .map((s) => `<i class="cell ${esc(s)}" title="${w.week}주차 ${({ attendance: "출석", late: "지각", absent: "결석" })[s] || "기록 없음"}"></i>`).join("")}</div><small>${w.week}</small></div>`).join("")}</div>`
      : `<p class="muted small-text">출석 기록이 아직 없습니다.</p>`;
    const pending = state.todos.filter((t) => t.lms_course_id === c.lms_course_id && t.kind === "video").length;
    return `<div class="course">
      <div class="course-head"><h3>${esc(c.name)}</h3><span class="muted small-text">${esc(c.professors || "")}</span>
        <label class="toggle"><input type="checkbox" data-course="${c.lms_course_id}" ${c.auto_play ? "checked" : ""}>자동 수강</label></div>
      ${grid}
      <p class="muted small-text">남은 영상 ${pending}개</p>
    </div>`;
  }).join("");
  $("#view").innerHTML = `
    <div class="view-head"><h1>과목</h1></div>
    <p class="notice-inline">자동 수강을 켜면 동기화할 때마다 새로 열린 영상을 순서대로 재생합니다. 직접 시청하지 않은 수강은 학칙상 문제가 될 수 있으니 확인 후 켜세요.</p>
    <div class="panel">${rows || `<div class="empty"><p>과목이 없습니다. 지금 동기화를 눌러 불러오세요.</p></div>`}</div>
    <p class="muted small-text">초록 출석, 노랑 지각, 빨강 결석, 회색 기록 없음</p>`;
  $("#view").querySelectorAll("input[data-course]").forEach((el) => el.addEventListener("change", onAutoPlay));
}

async function onAutoPlay(e) {
  const input = e.currentTarget;
  const id = Number(input.dataset.course);
  input.disabled = true;
  try {
    const row = await api(`/courses/${id}/auto-play`, { method: "PUT", json: { enabled: input.checked } });
    state.courses = state.courses.map((c) => (c.lms_course_id === id ? row : c));
    toast(input.checked ? `${row.name} 자동 수강을 켰습니다` : `${row.name} 자동 수강을 껐습니다`);
  } catch (err) {
    input.checked = !input.checked;
    toast(err.message, true);
  } finally { input.disabled = false; }
}

/* ---------------- 공지 ---------------- */

async function viewAnnouncements() {
  if (needCred()) return;
  if (!state.announcements) {
    $("#view").innerHTML = `<div class="view-head"><h1>공지</h1></div><div class="panel empty"><p>불러오는 중</p></div>`;
    try { state.announcements = await api("/announcements?limit=200"); } catch (err) { if (state.view === "announcements") viewError(err.message, viewAnnouncements); return; }
    if (state.view !== "announcements") return;
  }
  const sel = state.noticeCourse;
  const list = state.announcements.filter((a) => !sel || a.lms_course_id === sel);
  const courseIds = [...new Set(state.announcements.map((a) => a.lms_course_id))];
  const nameOf = (id) => state.announcements.find((a) => a.lms_course_id === id)?.course_name || id;
  const filters = [`<button data-c="" class="${sel ? "" : "on"}">전체</button>`,
    ...courseIds.map((id) => `<button data-c="${id}" class="${sel === id ? "on" : ""}">${esc(nameOf(id))}</button>`)].join("");
  const items = list.map((a) => {
    const files = (a.attachments || []).map((f) => `<a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.name)}</a>`).join("<br>");
    return `<details class="notice"><summary><span class="title">${esc(a.title)}</span>
      <span class="meta row"><span>${esc(a.course_name)}</span>${a.author ? `<span>${esc(a.author)}</span>` : ""}${a.posted_at ? `<span>${fmtDate(new Date(a.posted_at))}</span>` : ""}</span></summary>
      <div class="notice-body">${esc(textOf(a.message))}${files ? `<p>${files}</p>` : ""}${a.url ? `<p><a href="${esc(a.url)}" target="_blank" rel="noopener">LMS에서 보기</a></p>` : ""}</div></details>`;
  }).join("");
  $("#view").innerHTML = `
    <div class="view-head"><h1>공지</h1></div>
    <div class="filters">${filters}</div>
    <div class="panel">${items || `<div class="empty"><p>공지가 없습니다.</p></div>`}</div>`;
  $("#view .filters").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.noticeCourse = b.dataset.c ? Number(b.dataset.c) : null;
    viewAnnouncements();
  });
}

/* ---------------- 영상 재생 ---------------- */

function viewPlayback() {
  if (needCred()) return;
  const v = $("#view");
  v.innerHTML = `<div class="view-head"><h1>영상 재생</h1></div>
    ${state.active ? activeBatchHtml(state.active) : planFormHtml()}
    ${historyHtml()}`;
  if (state.active) {
    $("#cancel-batch")?.addEventListener("click", cancelBatch);
  } else {
    bindPlanForm();
  }
}

function videoTodos() { return state.todos.filter((t) => t.kind === "video"); }

function planFormHtml() {
  const videos = videoTodos();
  if (!videos.length) return `<div class="panel empty"><p>기간 안에 남은 영상이 없습니다.</p></div>`;
  const courses = [...new Map(videos.map((t) => [t.lms_course_id, t.course_name])).entries()];
  const plan = state.plan;
  return `<div class="panel"><form class="panel-body" id="plan-form">
      <div class="row">
        <label>범위<select name="scope">
          <option value="module">주차 하나</option><option value="course">과목 하나</option><option value="all">모든 과목</option></select></label>
        <label>과목<select name="course">${courses.map(([id, n]) => `<option value="${id}">${esc(n)}</option>`).join("")}</select></label>
        <label>주차<select name="module"></select></label>
      </div>
      <div class="row"><button class="btn">미리보기</button>
        <span class="muted small-text">1배속으로 처음부터 끝까지 재생하고, 영상마다 LMS에서 완료로 바뀌었는지 확인합니다.</span></div>
    </form></div>
    ${plan ? planHtml(plan) : ""}`;
}

function bindPlanForm() {
  const form = $("#plan-form");
  if (!form) return;
  const saved = state.planForm || {};
  const syncModules = () => {
    const course = Number(form.course.value);
    const mods = [...new Map(videoTodos().filter((t) => t.lms_course_id === course).map((t) => [t.module_id, t.module_title])).entries()];
    form.module.innerHTML = mods.map(([id, n]) => `<option value="${id}">${esc(n || `모듈 ${id}`)}</option>`).join("");
    form.module.disabled = form.scope.value !== "module";
    form.course.disabled = form.scope.value === "all";
  };
  if (saved.scope) form.scope.value = saved.scope;
  if (saved.course) form.course.value = saved.course;
  syncModules();
  if (saved.module) form.module.value = saved.module;
  form.scope.addEventListener("change", syncModules);
  form.course.addEventListener("change", syncModules);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    state.planForm = { scope: form.scope.value, course: form.course.value, module: form.module.value };
    const q = new URLSearchParams({ scope: form.scope.value });
    if (form.scope.value !== "all") q.set("course_id", form.course.value);
    if (form.scope.value === "module") q.set("module_id", form.module.value);
    try {
      state.plan = await api(`/playback/batches/preview?${q}`);
      state.planQuery = Object.fromEntries(q);
      renderRail();
      viewPlayback();
    } catch (err) { toast(err.message, true); }
  });
  $("#start-batch")?.addEventListener("click", startBatch);
  $("#clear-plan")?.addEventListener("click", () => { state.plan = null; renderRail(); viewPlayback(); });
}

function planHtml(plan) {
  if (!plan.items.length) return `<div class="panel empty"><p>이 범위에는 재생할 영상이 없습니다.</p></div>`;
  const steps = plan.items.map((i) => `<li class="${i.at_risk ? "risk" : ""}">
      <span class="num">${i.position + 1}</span>
      <span><span class="title">${esc(i.title)}</span><br>
        <span class="meta row small-text"><span>${esc(i.course_name)} ${esc(i.module_title)}</span><span>${fmtDuration(i.duration)}</span>
        <span>예상 종료 ${fmtDate(new Date(i.expected_end))}</span><span>마감 ${fmtDate(new Date(i.deadline))}</span></span></span>
      <span class="status ${i.at_risk ? "failed" : "queued"}">${i.at_risk ? "마감 초과" : ""}</span></li>`).join("");
  const warn = plan.warnings.length
    ? `<p class="warning">${plan.warnings.length}개 영상은 순서대로 재생하면 마감 전에 끝나지 않습니다. 마감이 이른 과목부터 재생하니, 필요하면 범위를 줄이세요.</p>` : "";
  return `<div class="panel">
    <div class="panel-body"><div class="summary-line">
      <span><b>${plan.items.length}</b>개 영상</span><span><b>${fmtDuration(plan.total_seconds)}</b>예상 소요</span>
      <span><b>${fmtDate(new Date(plan.expected_end))}</b>예상 종료</span></div><p class="muted small-text">현재 대기열을 반영한 예상입니다. 새 요청·재시도에 따라 달라집니다.</p>${warn}</div>
    <ol class="steps">${steps}</ol>
    <div class="actions"><button class="btn primary" id="start-batch">${plan.items.length}개 영상 재생 시작</button>
      <button class="btn ghost" id="clear-plan">미리보기 닫기</button></div></div>`;
}

async function startBatch() {
  const q = state.planQuery || {};
  const body = { scope: q.scope, course_id: q.course_id ? Number(q.course_id) : null, module_id: q.module_id ? Number(q.module_id) : null };
  try {
    const batch = await api("/playback/batches", { method: "POST", json: body });
    state.plan = null;
    state.active = await api(`/playback/batches/${batch.id}`);
    state.batches = await api("/playback/batches?limit=10");
    toast(`영상 ${batch.total}개 재생을 시작했습니다`);
    startPolling();
    renderRail();
    viewPlayback();
  } catch (err) { toast(err.message, true); }
}

function activeBatchHtml(b) {
  const ends = playbackTimeline()?.ends || new Map();
  const steps = b.jobs.map((j) => {
    const late = ends.get(j.module_item_id) > new Date(j.deadline).getTime();
    const pct = j.status === "playing" ? Math.min(100, (j.progress_seconds / j.duration) * 100) : j.status === "done" ? 100 : 0;
    return `<li class="${late ? "risk" : ""}"><span class="num">${j.position + 1}</span>
      <span><span class="title">${esc(j.title)}</span>
        <span class="meta row small-text"><span>${fmtDuration(j.duration)}</span><span>마감 ${fmtDate(new Date(j.deadline))}</span>
        ${late ? "<span>마감 전에 끝나지 않을 예정</span>" : ""}${j.attempts > 1 ? `<span>${j.attempts}번 시도</span>` : ""}${j.error ? `<span>${esc(j.error)}</span>` : ""}</span>
        ${j.status === "playing" ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ""}</span>
      <span class="status ${j.status}">${JOB_STATUS[j.status] || j.status}</span></li>`;
  }).join("");
  const cancelling = b.cancel_requested;
  return `<div class="panel">
    <div class="panel-body">
      <div class="summary-line"><span><b>${BATCH_STATUS[b.status]}</b></span>
        <span><b>${b.done_count}</b>완료</span><span><b>${b.failed_count}</b>실패</span><span><b>${b.skipped_count}</b>이미 완료</span>
        <span><b>${b.total}</b>전체</span></div>
      <p class="muted small-text" role="status">${b.status === "queued" && b.queue_position != null
        ? `다음 영상 대기 ${b.queue_position}번째 · 약 ${fmtDuration(b.estimated_wait_seconds || 0)} 후 시작` : "현재 영상을 재생하고 있습니다."}
        ${b.expected_end ? ` · 묶음 예상 종료 ${fmtDate(new Date(b.expected_end))}` : ""}</p>
      ${b.deadline_at_risk ? '<p class="status failed">대기 시간으로 인해 일부 영상이 마감 전에 끝나지 않을 수 있습니다.</p>' : ""}
      <p class="muted small-text">사용자별 영상 한 개씩 번갈아 재생합니다. 예상 시간은 현재 대기열 기준이며 새 요청·재시도에 따라 달라집니다.</p>
      <p class="muted small-text">재생은 서버에서 계속됩니다. 이 창을 닫아도 멈추지 않습니다.</p>
    </div>
    <ol class="steps">${steps}</ol>
    <div class="actions"><button class="btn danger" id="cancel-batch" ${cancelling ? "disabled" : ""}>${cancelling ? "재생 중지 요청됨" : "재생 중지"}</button></div>
  </div>`;
}

async function cancelBatch() {
  try {
    await api(`/playback/batches/${state.active.id}`, { method: "DELETE" });
    state.active = await api(`/playback/batches/${state.active.id}`);
    toast("재생 중지를 요청했습니다. 지금 영상이 정리되면 멈춥니다.");
    if (!["queued", "running"].includes(state.active.status)) await finishActive();
    viewPlayback();
  } catch (err) { toast(err.message, true); }
}

function historyHtml() {
  const past = state.batches.filter((b) => !["queued", "running"].includes(b.status));
  if (!past.length) return "";
  const scopeName = { item: "영상 하나", module: "주차", course: "과목", all: "모든 과목", auto: "자동 수강" };
  return `<h3>지난 재생</h3><div class="panel"><ul class="list">${past.map((b) => `<li>
    <span class="title">${scopeName[b.scope] || b.scope} ${b.total}개, ${BATCH_STATUS[b.status]}</span>
    <span class="meta row"><span>완료 ${b.done_count}</span><span>실패 ${b.failed_count}</span><span>이미 완료 ${b.skipped_count}</span>
      <span>${fmtDate(new Date(b.created_at))}</span>${b.error ? `<span>${esc(b.error)}</span>` : ""}</span></li>`).join("")}</ul></div>`;
}

let pollTimer = null;
function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    if (!state.active) return stopPolling();
    try {
      state.active = await api(`/playback/batches/${state.active.id}`);
      if (!["queued", "running"].includes(state.active.status)) await finishActive();
      renderRail();
      if (state.view === "playback") viewPlayback();
      if (state.view === "dashboard" && typeof drawWeek === "function") drawWeek();
    } catch { /* 일시적 오류는 다음 주기에 다시 시도 */ }
  }, 5000);
}
function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

async function finishActive() {
  const b = state.active;
  stopPolling();
  toast(`영상 재생 ${BATCH_STATUS[b.status]}: 완료 ${b.done_count}, 실패 ${b.failed_count}`, b.status === "failed");
  state.active = null;
  [state.todos, state.batches] = await Promise.all([api("/todos"), api("/playback/batches?limit=10")]);
  if (state.view === "dashboard") {
    loadDashboardPlayback();
    if (typeof renderWeek === "function") renderWeek();
  }
}

/* ---------------- 강의 영상 내려받기 ---------------- */

const fmtSize = (n) => (!n ? "" : n >= 1e9 ? `${(n / 1e9).toFixed(1)}GB` : `${Math.round(n / 1e6)}MB`);

async function viewVideos() {
  if (needCred()) return;
  if (!state.savedVideos) {
    try { state.savedVideos = await api("/saved-videos"); }
    catch (err) { if (state.view === "videos") viewError(err.message, viewVideos); return; }
    if (state.view !== "videos") return;
  }
  const courses = state.courses;
  // 지난 학기 등 지금 목록에 없지만 저장된 영상이 있는 과목
  const pastCourses = [...new Map(state.savedVideos
    .filter((v) => !courses.some((c) => c.lms_course_id === v.lms_course_id))
    .map((v) => [v.lms_course_id, v.course_name])).entries()];
  if (!courses.length && !pastCourses.length) {
    $("#view").innerHTML = `<div class="view-head"><h1>강의 영상</h1></div><div class="panel empty"><p>과목이 없습니다. 지금 동기화를 눌러 불러오세요.</p></div>`;
    return;
  }
  const allIds = [...courses.map((c) => c.lms_course_id), ...pastCourses.map(([id]) => id)];
  if (!state.videoCourse || !allIds.includes(state.videoCourse)) state.videoCourse = allIds[0];
  const opt = (id, name) => `<option value="${id}" ${id === state.videoCourse ? "selected" : ""}>${esc(name)}</option>`;
  const options = courses.map((c) => opt(c.lms_course_id, c.name)).join("")
    + (pastCourses.length ? `<optgroup label="지난 과목 (저장된 영상)">${pastCourses.map(([id, name]) => opt(id, name)).join("")}</optgroup>` : "");
  const isPast = !courses.some((c) => c.lms_course_id === state.videoCourse);
  if (isPast) state.videoTab = "videos";  // 지난 과목은 수업자료를 LMS에서 볼 수 없다
  $("#view").innerHTML = `
    <div class="view-head"><h1>강의 영상</h1>
      <label>과목<select id="video-course">${options}</select></label></div>
    <div class="tabs" role="tablist">
      <button role="tab" data-tab="videos" aria-selected="${state.videoTab === "videos"}">영상</button>
      <button role="tab" data-tab="materials" aria-selected="${state.videoTab === "materials"}" ${isPast ? "disabled" : ""}>수업자료</button>
      <label class="check inline"><input type="checkbox" id="not-downloaded" ${state.notDownloadedOnly ? "checked" : ""}><span>안 받은 것만</span></label>
    </div>
    <p class="notice-inline">본인이 수강하는 강의와 자료를 개인 복습용으로 받는 기능입니다. 강의 영상과 자료는 교수와 학교의 저작물이므로 다른 사람에게 공유하거나 올리지 마세요. 파일은 서버에 저장하지 않고 학교에서 받는 대로 바로 전달합니다. 동기화 때 영상 주소를 저장해 두므로 시청기간이 끝난 영상도 받을 수 있지만, 학교 서버가 주소를 막거나 영상을 지우면 받을 수 없습니다.</p>
    <p class="bulk-status" id="bulk-status" role="status"></p>
    <div class="panel" id="video-list"><div class="empty"><p>LMS에서 목록을 불러오는 중</p></div></div>`;
  $("#video-course").addEventListener("change", (e) => {
    state.videoCourse = Number(e.target.value); state.videoList = null; state.materialList = null; viewVideos();
  });
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { state.videoTab = b.dataset.tab; viewVideos(); }));
  $("#not-downloaded").addEventListener("change", (e) => { state.notDownloadedOnly = e.target.checked; renderCurrentTab(); });
  renderBulkStatus();

  const courseId = state.videoCourse;
  if (isPast) {  // 지난 과목: 저장된 영상만
    state.videoList = { courseId, items: [] };
    renderVideoList();
    return;
  }
  const key = state.videoTab === "videos" ? "videoList" : "materialList";
  const path = state.videoTab === "videos" ? `/courses/${courseId}/videos` : `/courses/${courseId}/materials`;
  if (!state[key] || state[key].courseId !== courseId) {
    try {
      state[key] = { courseId, items: await api(path) };
    } catch (err) {
      if (state.view === "videos" && state.videoCourse === courseId) viewError(err.message, viewVideos);
      return;
    }
  }
  if (state.view !== "videos" || state.videoCourse !== courseId) return;
  renderCurrentTab();
}

function renderCurrentTab() {
  if (state.videoTab === "materials") renderMaterialList(); else renderVideoList();
}

function renderBulkStatus() {
  const box = $("#bulk-status");
  if (!box) return;
  const b = state.bulk;
  if (!b) { box.innerHTML = ""; return; }
  const text = b.running ? `여러 개 받는 중: ${b.done + b.failed}/${b.total} (실패 ${b.failed})`
    : b.stopped ? `중지했습니다: ${b.done}/${b.total} 완료` : `다 받았습니다: ${b.done}/${b.total} 완료, 실패 ${b.failed}`;
  box.innerHTML = `${esc(text)} ${b.running ? '<button class="btn small" id="bulk-stop">중지</button>' : ""}
    ${b.errors?.length ? `<details><summary>실패 항목</summary><ul>${b.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></details>` : ""}`;
  $("#bulk-stop")?.addEventListener("click", stopBulkDownload);
}

function bulkButton(items) {
  const label = items.length ? `전체 받기 (${items.length})` : "전체 받기";
  return bulkDownloadSupported()
    ? `<button class="btn small" data-bulk ${items.length && !state.bulk?.running ? "" : "disabled"}>${label}</button>`
    : `<button class="btn small" disabled title="폴더에 바로 저장하는 기능은 데스크톱 Chrome·Edge에서만 지원합니다.">${label}</button>`;
}

function wireBulk(box, groups) {
  box.querySelectorAll("[data-bulk]").forEach((b, i) => b.addEventListener("click", async () => {
    try {
      await startBulkDownload(groups[i], (stat) => { state.bulk = stat; renderBulkStatus(); if (!stat.running) renderCurrentTab(); });
    } catch (err) {
      if (err.name !== "AbortError") toast(err.message, true);  // 폴더 선택 취소는 조용히
    }
  }));
}

function groupBy(items, key, title) {
  const groups = new Map();
  for (const v of items) {
    if (!groups.has(v[key])) groups.set(v[key], { title: v[title], items: [] });
    groups.get(v[key]).items.push(v);
  }
  return [...groups.values()];
}

const doneBadge = (at) => at ? `<span class="badge done" title="${esc(fmtDate(new Date(at)))}에 받음">✓ 받음</span>` : "";

function renderVideoList() {
  const courseId = state.videoList.courseId;
  const course = state.courses.find((c) => c.lms_course_id === courseId);
  const saved = new Map(state.savedVideos.filter((v) => v.lms_course_id === courseId).map((v) => [v.module_item_id, v]));
  // LMS 목록을 기준으로 하고, LMS에서 사라진 저장 영상을 뒤에 붙인다
  let items = state.videoList.items.map((v) => ({ ...v, savedRow: saved.get(v.module_item_id) }));
  const live = new Set(items.map((v) => v.module_item_id));
  for (const s of saved.values()) {
    if (!live.has(s.module_item_id)) items.push({ ...s, module_id: `saved-${s.module_title}`, available: false, completed: false, savedRow: s });
  }
  for (const v of items) {
    v.courseName = course?.name || v.savedRow?.course_name || "";
    v.downloaded_at = v.downloaded_at || v.savedRow?.downloaded_at || null;
    v.transcript_id = v.transcript_id ?? v.savedRow?.transcript_id ?? null;
    v.linkPath = v.available ? `/courses/${courseId}/videos/${v.module_item_id}/download-link`
      : v.savedRow?.saved ? `/saved-videos/${v.savedRow.id}/download-link` : null;
    v.audioPath = v.available ? `/courses/${courseId}/videos/${v.module_item_id}/audio-link`
      : v.savedRow?.saved ? `/saved-videos/${v.savedRow.id}/audio-link` : null;
  }
  if (state.notDownloadedOnly) items = items.filter((v) => !v.downloaded_at);
  const box = $("#video-list");
  if (!items.length) {
    box.innerHTML = `<div class="empty"><p>${state.notDownloadedOnly ? "아직 받지 않은 영상이 없습니다." : "이 과목에는 내려받을 수 있는 강의 영상이 없습니다."}</p></div>`;
    return;
  }
  const groups = groupBy(items, "module_id", "module_title");
  const status = (v) => v.available ? (v.completed ? "수강 완료" : "미수강")
    : v.savedRow ? "시청기간 종료" : "아직 열리지 않음";
  const thumb = (v) => v.thumbnail_url
    ? `<img class="thumb" src="${esc(v.thumbnail_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : "";
  const button = (v) => v.linkPath
    ? `<button class="btn small" data-link="${esc(v.linkPath)}">내려받기</button>`
    : `<button class="btn small" disabled title="${esc(v.savedRow?.resolve_error || "")}">내려받기</button>`;
  const bulkGroups = groups.map((g) => g.items.filter((v) => v.linkPath).map((v) => ({
    linkPath: v.linkPath, label: v.title, onDone: () => { v.downloaded_at = new Date().toISOString(); markDownloaded(v); } })));
  box.innerHTML = groups.map((g, i) => `<div class="group-label row">${esc(g.title)}
      <span class="group-actions">${bulkButton(bulkGroups[i])}${typeof transcribeGroupButton === "function" ? transcribeGroupButton(g.items) : ""}</span></div>
    <ul class="list">${g.items.map((v) => `
    <li class="video-row">
      ${thumb(v)}
      <span class="video-info"><span class="title">${esc(v.title)}</span><br>
        <span class="meta row"><span>${v.duration ? fmtDuration(v.duration) : ""}</span><span>${fmtSize(v.size)}</span>
          <span>${status(v)}</span>${v.savedRow?.saved ? "<span>주소 저장됨</span>" : ""}${doneBadge(v.downloaded_at)}</span></span>
      <span class="row-actions">${typeof transcribeButton === "function" ? transcribeButton(v, courseId) : ""}${button(v)}</span>
    </li>`).join("")}</ul>`).join("");
  box.querySelectorAll("[data-link]").forEach((b) => b.addEventListener("click", async () => {
    if (await downloadVideo(b, b.dataset.link)) {
      const v = items.find((x) => x.linkPath === b.dataset.link);
      if (v) { v.downloaded_at = new Date().toISOString(); markDownloaded(v); }
      renderVideoList();
    }
  }));
  wireBulk(box, bulkGroups);
  if (typeof wireTranscribe === "function") wireTranscribe(box, items, courseId);
}

function markDownloaded(v) {
  // 다시 그려도 표시가 남도록 원본 목록에도 반영한다
  const src = state.videoList.items.find((x) => x.module_item_id === v.module_item_id);
  if (src) src.downloaded_at = v.downloaded_at;
  if (v.savedRow) v.savedRow.downloaded_at = v.downloaded_at;
}

function renderMaterialList() {
  const courseId = state.materialList.courseId;
  let items = state.materialList.items.map((m) => ({ ...m, linkPath: `/courses/${courseId}/materials/${encodeURIComponent(m.key)}/download-link` }));
  if (state.notDownloadedOnly) items = items.filter((m) => !m.downloaded_at);
  const box = $("#video-list");
  if (!items.length) {
    box.innerHTML = `<div class="empty"><p>${state.notDownloadedOnly ? "아직 받지 않은 자료가 없습니다." : "이 과목에는 받을 수 있는 수업자료가 없습니다."}</p></div>`;
    return;
  }
  const groups = groupBy(items, "module_id", "module_title");
  const markMaterial = (key) => {
    const src = state.materialList.items.find((x) => x.key === key);
    if (src) src.downloaded_at = new Date().toISOString();
  };
  const bulkGroups = groups.map((g) => g.items.map((m) => ({ linkPath: m.linkPath, label: m.file_name, onDone: () => markMaterial(m.key) })));
  box.innerHTML = groups.map((g, i) => `<div class="group-label row">${esc(g.title)}<span class="group-actions">${bulkButton(bulkGroups[i])}</span></div>
    <ul class="list">${g.items.map((m) => `
    <li class="video-row">
      <span class="video-info"><span class="title">${esc(m.file_name)}</span><br>
        <span class="meta row"><span>${esc(m.title)}</span><span>${fmtSize(m.size)}</span>
          <span>${m.kind === "canvas_file" ? "위키 첨부" : "자료"}</span>${doneBadge(m.downloaded_at)}</span></span>
      <button class="btn small" data-link="${esc(m.linkPath)}">내려받기</button>
    </li>`).join("")}</ul>`).join("");
  box.querySelectorAll("[data-link]").forEach((b) => b.addEventListener("click", async () => {
    if (await downloadVideo(b, b.dataset.link)) {
      const m = items.find((x) => x.linkPath === b.dataset.link);
      if (m) markMaterial(m.key);
      renderMaterialList();
    }
  }));
  wireBulk(box, bulkGroups);
}

async function downloadVideo(btn, linkPath) {
  btn.disabled = true;
  btn.textContent = "준비 중";
  try {
    const link = await api(linkPath, { method: "POST" });
    const a = document.createElement("a");
    a.href = link.url;
    a.download = link.file_name;
    document.body.append(a);
    a.click();
    a.remove();
    toast(`${link.file_name} 내려받기를 시작했습니다. 브라우저 다운로드 목록에서 진행 상황을 볼 수 있습니다.`);
    return true;
  } catch (err) {
    toast(err.message, true);
    return false;
  } finally {
    btn.disabled = false;
    btn.textContent = "내려받기";
  }
}

/* ---------------- 알림 ---------------- */

async function viewNotifications() {
  $("#view").innerHTML = '<div class="panel empty" role="status">알림 설정을 불러오는 중입니다.</div>';
  let channels = [];
  try { channels = await api("/notifications/channels"); }
  catch (err) { if (state.view === "notifications") viewError(err.message, viewNotifications); return; }
  if (state.view !== "notifications") return;
  const name = { discord: "Discord", telegram: "Telegram" };
  const list = channels.length
    ? `<ul class="list">${channels.map((c) => `<li class="row"><span class="title">${name[c.kind] || c.kind}</span>
        <span class="meta">${fmtDate(new Date(c.created_at))} 등록</span><span style="margin-left:auto" class="row">
        <button class="btn small" data-test="${c.id}">테스트 메시지 보내기</button>
        <button class="btn small danger" data-del="${c.id}">삭제</button></span></li>`).join("")}</ul>`
    : `<div class="empty"><p>알림 받을 곳을 추가하면 새 공지, 마감 24시간·3시간 전, 재생 결과를 보내 드립니다.</p></div>`;
  $("#view").innerHTML = `
    <div class="view-head"><h1>알림</h1></div>
    <div class="panel">${list}</div>
    <div class="panel"><form class="panel-body" id="channel-form">
      <h3>알림 받을 곳 추가</h3>
      <label>종류<select name="kind"><option value="discord">Discord 웹훅</option><option value="telegram">Telegram 봇</option></select></label>
      <label data-for="discord">웹훅 URL<input name="webhook_url" placeholder="https://discord.com/api/webhooks/..."></label>
      <label data-for="telegram" hidden>봇 토큰<input name="bot_token"></label>
      <label data-for="telegram" hidden>채팅 ID<input name="chat_id"></label>
      <div><button class="btn primary">추가하기</button></div>
    </form></div>`;
  const form = $("#channel-form");
  const toggle = () => form.querySelectorAll("[data-for]").forEach((el) => (el.hidden = el.dataset.for !== form.kind.value));
  form.kind.addEventListener("change", toggle);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { kind: form.kind.value, webhook_url: form.webhook_url.value || null, bot_token: form.bot_token.value || null, chat_id: form.chat_id.value || null };
    try { await api("/notifications/channels", { method: "POST", json: body }); toast("알림 받을 곳을 추가했습니다"); viewNotifications(); }
    catch (err) { toast(err.message, true); }
  });
  $("#view").querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/notifications/channels/${b.dataset.test}/test`, { method: "POST" }); toast("테스트 메시지를 보냈습니다"); }
    catch (err) { toast(err.message, true); }
  }));
  $("#view").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/notifications/channels/${b.dataset.del}`, { method: "DELETE" }); toast("삭제했습니다"); viewNotifications(); }
    catch (err) { toast(err.message, true); }
  }));
}

/* ---------------- 관리자 ---------------- */

async function viewAdmin(query = "", offset = 0) {
  if (!state.me?.is_admin) {
    $("#view").innerHTML = `<div class="panel empty" role="alert">관리자 권한이 필요합니다.</div>`;
    return;
  }
  $("#view").innerHTML = `
    <div class="view-head"><h1>사용자 관리</h1><button class="btn" id="admin-refresh">새로고침</button></div>
    <p class="admin-note">사이트 로그인에 성공한 기록입니다. 현재 접속 여부를 나타내지는 않습니다. 기존 계정의 기록은 기능 추가 후 로그인부터 집계됩니다.</p>
    <form id="admin-search" class="admin-search">
      <label for="admin-query">학번 검색</label>
      <input id="admin-query" type="search" maxlength="32" value="${esc(query)}" placeholder="학번 전체 또는 일부">
      <button class="btn" type="submit">검색</button>
    </form>
    <div id="admin-content" aria-live="polite"><p class="panel empty" role="status">사용자 정보를 불러오는 중입니다.</p></div>`;
  const root = $("#admin-content");
  $("#admin-search").addEventListener("submit", (e) => {
    e.preventDefault();
    viewAdmin($("#admin-query").value.trim());
  });
  $("#admin-refresh").addEventListener("click", () => viewAdmin(query, offset));
  try {
    const data = await api(`/admin/users?${new URLSearchParams({ q: query, offset, limit: 25 })}`);
    if (!root.isConnected || state.view !== "admin" || !state.me?.is_admin) return;
    const date = (value) => value ? esc(new Date(value).toLocaleString("ko-KR")) : "기록 없음";
    const statuses = { ok: "정상", invalid: "재로그인 필요", error: "오류" };
    root.innerHTML = `<p class="admin-note">${query ? "검색 결과" : "전체 사용자"} ${data.total}명 · 최근 로그인 순</p>
      ${data.users.length ? `<ul class="admin-users">${data.users.map((u) => `
        <li class="panel panel-body"><h2>${esc(u.student_id)}</h2><dl class="admin-details">
          <div><dt>최근 사이트 로그인</dt><dd>${date(u.last_login_at)}</dd></div>
          <div><dt>가입일</dt><dd>${date(u.created_at)}</dd></div>
          <div><dt>로그인 횟수</dt><dd>${esc(u.login_count)}회</dd></div>
          <div><dt>LMS 연결</dt><dd>${esc(statuses[u.lms_status] || "연결 정보 없음")}</dd></div>
        </dl><details class="admin-settings" data-student-id="${esc(u.student_id)}">
          <summary>${esc(u.student_id)} 설정 보기</summary>
          <div class="admin-settings-content" aria-live="polite"></div>
        </details></li>`).join("")}</ul>` : `<p class="panel empty">해당하는 사용자가 없습니다.</p>`}
      <div class="admin-pagination"><button class="btn" id="admin-prev" ${offset === 0 ? "disabled" : ""}>이전</button>
        <span>${data.total ? `${offset + 1}–${offset + data.users.length} / ${data.total}명` : "0명"}</span>
        <button class="btn" id="admin-next" ${offset + 25 >= data.total ? "disabled" : ""}>다음</button></div>`;
    root.querySelectorAll(".admin-settings").forEach((details) => {
      details.addEventListener("toggle", () => {
        if (details.open && !details.dataset.loaded && !details.dataset.loading) loadAdminSettings(details);
      });
    });
    $("#admin-prev").addEventListener("click", () => viewAdmin(query, Math.max(0, offset - 25)));
    $("#admin-next").addEventListener("click", () => viewAdmin(query, offset + 25));
  } catch (err) {
    if (!root.isConnected || state.view !== "admin" || !state.me?.is_admin) return;
    root.innerHTML = `<div class="panel empty" role="alert"><p>${esc(err.message)}</p><button class="btn" id="admin-retry">다시 시도</button></div>`;
    $("#admin-retry").addEventListener("click", () => viewAdmin(query, offset));
  }
}

async function loadAdminSettings(details) {
  const panel = $(".admin-settings-content", details);
  details.dataset.loading = "true";
  panel.innerHTML = '<p role="status">설정을 불러오는 중입니다.</p>';
  try {
    const data = await api(`/admin/users/${encodeURIComponent(details.dataset.studentId)}/settings`);
    if (!details.isConnected || state.view !== "admin" || !state.me?.is_admin) return;
    const date = (value) => value ? esc(new Date(value).toLocaleString("ko-KR")) : "기록 없음";
    const status = { ok: "정상", invalid: "재로그인 필요", error: "오류" };
    const kinds = { discord: "Discord", telegram: "Telegram" };
    panel.innerHTML = `
      <section><h3>계정 및 LMS 연결</h3><dl class="admin-details">
        <div><dt>권한</dt><dd>${data.is_admin ? "관리자" : "일반 사용자"}</dd></div>
        <div><dt>이용 동의</dt><dd>${date(data.consent_at)}</dd></div>
        <div><dt>LMS 연결 상태</dt><dd>${esc(status[data.lms?.status] || "연결 정보 없음")}</dd></div>
        <div><dt>마지막 LMS 로그인</dt><dd>${date(data.lms?.last_login_at)}</dd></div>
        <div><dt>마지막 동기화</dt><dd>${date(data.lms?.last_synced_at)}</dd></div>
        <div><dt>다음 동기화 예정</dt><dd>${date(data.lms?.next_sync_at)}</dd></div>
      </dl></section>
      <section><h3>과목별 자동 재생</h3>
        ${data.courses.length ? `<ul class="admin-setting-list">${data.courses.map((c) => `<li>
          <span>${esc(c.name)}${c.active ? "" : " · 비활성 과목"}</span>
          <strong>자동 재생 ${c.auto_play ? "켜짐" : "꺼짐"}</strong>
        </li>`).join("")}</ul>` : "<p>동기화된 과목이 없습니다.</p>"}
      </section>
      <section><h3>알림 설정</h3>
        ${data.channels.length ? `<ul class="admin-setting-list">${data.channels.map((c) => `<li>
          <span>${esc(kinds[c.kind] || c.kind)} · 등록 ${date(c.created_at)}</span>
          <strong>${c.enabled ? "사용 중" : "사용 안 함"}</strong>
        </li>`).join("")}</ul>` : "<p>등록된 알림 채널이 없습니다.</p>"}
      </section>`;
    details.dataset.loaded = "true";
  } catch (err) {
    if (!details.isConnected || state.view !== "admin" || !state.me?.is_admin) return;
    panel.innerHTML = `<p role="alert">${esc(err.message)}</p><button class="btn admin-settings-retry">다시 시도</button>`;
    $(".admin-settings-retry", panel).addEventListener("click", () => loadAdminSettings(details));
  } finally {
    delete details.dataset.loading;
  }
}

/* ---------------- 계정 ---------------- */

function viewAccount() {
  const c = state.cred;
  const statusName = { ok: "정상", invalid: "비밀번호가 바뀜", error: "최근 오류" };
  $("#view").innerHTML = `
    <div class="view-head"><h1>계정</h1></div>
    <div class="panel"><div class="panel-body">
      <div class="summary-line"><span><b>${esc(state.me?.student_id || "")}</b>학번</span>
        ${c ? `<span><b>${statusName[c.status] || esc(c.status)}</b>LMS 연결</span>` : ""}</div>
      ${c ? `<p class="muted small-text">마지막 LMS 로그인 ${c.last_login_at ? fmtDate(new Date(c.last_login_at)) : "없음"},
        다음 자동 동기화 ${c.next_sync_at ? fmtDate(new Date(c.next_sync_at)) : "예정 없음"}</p>` : ""}
      ${c?.last_error ? `<p class="error">${esc(c.last_error)}</p>` : ""}
      <p class="small-text">학교 비밀번호를 바꿨다면 로그아웃한 뒤 새 비밀번호로 다시 로그인하세요. 저장된 정보가 함께 바뀝니다.</p>
    </div></div>
    <div class="panel"><div class="panel-body">
      <h3>계정 삭제</h3>
      <p class="muted small-text">저장된 학교 비밀번호와 동기화한 과목·할 일·공지, 재생 기록, 알림 설정을 모두 지웁니다. 되돌릴 수 없습니다.</p>
      <div><button class="btn danger" id="delete-me">계정 삭제</button></div>
    </div></div>`;
  const del = $("#delete-me");
  del.addEventListener("click", async () => {
    if (!del.dataset.armed) {
      del.dataset.armed = "1";
      del.textContent = "한 번 더 누르면 삭제합니다";
      return;
    }
    try {
      await api("/me", { method: "DELETE" });
      toast("계정과 저장된 정보를 모두 지웠습니다");
      logout();
    } catch (err) { toast(err.message, true); }
  });
}

/* ---------------- 시작 ---------------- */

setInterval(() => { if (!$("#app").hidden && !document.activeElement.closest("#stats, #rail, #todo-results")) { renderOverview(); renderRail(); if (state.view === "todos" && $("#todo-results")) renderTodoResults(); if (state.view === "dashboard" && typeof drawWeek === "function") drawWeek(); } }, 60000); // 남은 시간·오늘 표시 갱신
boot();
