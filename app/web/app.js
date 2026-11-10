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

const PRIVACY_NOTICE_KEY = "privacy-notice-2026-11-access";
const VIEW_NAMES = { dashboard: "학습 현황", courses: "과목", playback: "강의 재생", videos: "내려받기·텍스트 변환", notifications: "알림", account: "계정", admin: "사용자 관리" };
/** 주소의 해시를 화면 이름과 하위 경로로 나눈다. `#admin/users/2020%201234` → { view: "admin", path: ["users", "2020 1234"] } */
function parseHash(hash) {
  const parts = String(hash || "").replace(/^#/, "").split("/").filter(Boolean).map((part) => {
    try { return decodeURIComponent(part); } catch { return part; }
  });
  return { view: parts[0] || "", path: parts.slice(1) };
}
const initialView = parseHash(location.hash);

const state = {
  token: store.get("token"),
  dashboardPlan: null, dashboardError: null, dashboardLoading: false, playbackStarting: false,
  view: Object.hasOwn(VIEW_NAMES, initialView.view) ? initialView.view : "dashboard",
  adminPath: initialView.view === "admin" && initialView.path.length ? initialView.path : ["overview"],
  me: null, cred: null, todos: [], courses: [],
  batches: [], active: null, plan: null,
  videoCourse: null, videoList: null, savedVideos: null,
  videoTab: "videos", materialList: null, notDownloadedOnly: false, bulk: null,
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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

/** 마감 없는 할 일(영상)은 어떤 시각보다 뒤로 보낸다. new Date(null)은 1970년이 되므로 직접 쓰지 않는다. */
const dueTime = (t) => t.deadline ? new Date(t.deadline).getTime() : Infinity;
const dueText = (iso) => iso ? `마감 ${fmtDate(new Date(iso))}` : "마감 없음";

class ApiError extends Error {
  constructor(status, message, code) { super(message); this.status = status; this.code = code; }
}

async function api(path, { method = "GET", json, form } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = new URLSearchParams(form);
  const sentToken = state.token;
  const res = await fetch(path, { method, headers, body, ...(path.startsWith("/playback/") ? { cache: "no-store" } : {}) });
  const renewed = res.headers.get("X-Access-Token");
  if (renewed && sentToken && state.token === sentToken) {  // 그 사이 로그아웃·계정 전환했으면 버린다
    state.token = renewed;
    store.set("token", renewed);
  }
  if (res.status === 401 && state.token) { logout(); throw new ApiError(401, "다시 로그인하세요."); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail
      : detail?.message ? detail.message
      : Array.isArray(detail) ? detail.map((d) => d.msg).join(", ")
      : `요청 실패 (${res.status})`;
    if (res.status === 403 && detail?.code === "ACCESS_REQUIRED") showAccessScreen();  // 열어 둔 탭에서 권한이 회수된 경우
    throw new ApiError(res.status, msg, detail?.code);
  }
  return data;
}

/* ---------------- 로그인 ---------------- */

function showAuth() {
  $("#startup").hidden = true;
  $("#app").hidden = true;
  $("#access").hidden = true;
  $("#auth").hidden = false;
  preloadAuthMoods();
}

// 로그인 화면의 뚱이 표정. 실제 안내와 오류는 폼이 맡고, 여기는 분위기만 바꾼다.
const AUTH_MOODS = {
  idle: ["blank", "아유… 하기 싫어…"],
  password: ["think", "비밀번호가… 뭐였더라…"],
  consent: ["think", "이건 읽어 보고 가자"],
  submitting: ["steam", "머리에서 김 나는 중…"],
  error: ["burnt", "아유… 터졌다"],
};
function setAuthMood(mood) {
  const [image, caption] = AUTH_MOODS[mood];
  $("#auth-mood").src = `/patrick-${image}.webp`;
  $("#auth-caption").textContent = caption;
}
/** 표정 사진은 로그인 화면에서만 쓴다. 로그인한 사용자는 받지 않고, 로그인 화면이 뜬 뒤 한가할 때 받아 둔다. */
let authMoodsPreloaded = false;
function preloadAuthMoods() {
  if (authMoodsPreloaded) return;
  authMoodsPreloaded = true;
  whenIdle(() => new Set(Object.values(AUTH_MOODS).map(([image]) => image)).forEach((image) => { new Image().src = `/patrick-${image}.webp`; }));
}
$("#password").addEventListener("focus", () => setAuthMood("password"));
$("#password").addEventListener("blur", () => { if (!$("#auth-error").textContent) setAuthMood("idle"); });

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
  setAuthMood("idle");
});

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const btn = $("#auth-submit");
  const consentShown = !$("#consent-row").hidden;
  if (consentShown && !f.agree.checked) {
    $("#auth-error").textContent = "동의해야 시작할 수 있습니다.";
    setAuthMood("error");
    return;
  }
  btn.disabled = true;
  btn.textContent = "학교 계정 확인 중";
  $("#auth-error").textContent = "";
  setAuthMood("submitting");
  try {
    const form = { username: f.student_id.value.trim(), password: f.password.value };
    if (consentShown) form.agree_terms = "true";
    const res = await api("/auth/login", { method: "POST", form });
    state.token = res.access_token;
    store.set("token", state.token);
    f.reset();
    $("#consent-row").hidden = true;
    setAuthMood("idle");
    await boot();
  } catch (err) {
    if (err.code === "consent_required") {
      $("#consent-row").hidden = false;
      setAuthMood("consent");
      f.agree.focus();
    } else {
      $("#auth-error").textContent = err.message;
      setAuthMood("error");
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
  stopBulkDownload();
  tx.queue = [];  // cancelTranscription은 지금 도는 작업만 멈추므로, 대기열을 먼저 비워 다음 작업이 시작되지 않게 한다
  tx.epoch++;  // 취소된 작업이 나중에 끝나도 다음 사용자의 기록에 남지 않게 한다
  cancelTranscription();
  tx.finished = [];
  Object.assign(state, { me: null, cred: null, todos: [], courses: [],
    dashboardPlan: null, dashboardError: null, dashboardLoading: false, playbackStarting: false,
    batches: [], active: null, plan: null, planForm: null, planQuery: null,
    videoCourse: null, videoList: null, savedVideos: null, materialList: null, bulk: null });
  closeConfirmDialog();
  $("#transcript-dialog")?.close();
  clearToasts();
  state.extensionMissing = null;
  renderTaskPanel();
  $("#password").type = "password";
  $("#password-toggle").textContent = "표시";
  $("#password-toggle").setAttribute("aria-pressed", "false");
  showAuth();
}
/** 사용자가 직접 로그아웃할 때: 서버에서도 토큰을 폐기한다. api()는 401이면 logout()을 다시 부르므로 쓰지 않는다. */
function serverLogout() {
  const token = state.token;
  if (token) fetch("/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
  logout();
}
$("#logout-btn").addEventListener("click", serverLogout);

/* ---------------- 데이터 ---------------- */

async function loadCore(knownMe) {
  const [me, cred, todos, courses, batches] = await Promise.all([
    knownMe ?? api("/me"),
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
  renderTaskPanel();
}

async function boot() {
  if (!state.token) return showAuth();
  $("#auth").hidden = true;
  $("#access").hidden = true;
  $("#startup").hidden = false;
  $("#startup-message").textContent = "잠시만 기다려 주세요.";
  $("#startup-photo").src = "/patrick-steam.webp";
  $("#retry-boot").hidden = true;
  $("#startup-login").hidden = true;
  try {
    const me = await api("/me");
    if (me.access_status !== "approved") return showAccessScreen(me);  // 승인 전에는 다른 API를 부르지 않는다
    await loadCore(me);
  } catch (err) {
    if (err.status !== 401 && err.code !== "ACCESS_REQUIRED") {
      $("#startup-message").textContent = `학습 현황을 불러오지 못했습니다. ${err.message}`;
      $("#startup-photo").src = "/patrick-burnt.webp";
      $("#retry-boot").hidden = false;
      $("#startup-login").hidden = false;
    }
    return;
  }
  $("#startup").hidden = true;
  $("#auth").hidden = true;
  $("#app").hidden = false;
  history.replaceState(null, "", viewHash());
  renderAll();
  $("#privacy-notice").hidden = store.get(PRIVACY_NOTICE_KEY) === "1";
}

$("#privacy-notice-close").addEventListener("click", () => {
  store.set(PRIVACY_NOTICE_KEY, "1");
  $("#privacy-notice").hidden = true;
});

function renderAll() {
  renderSync();
  renderView();
}

/* ---------------- 상단: 동기화 ---------------- */

function renderSync() {
  const el = $("#sync-status");
  const c = state.cred;
  el.classList.remove("bad");
  if (!c) { el.textContent = "LMS 연결 정보가 없습니다"; return; }
  if (c.status === "invalid") { el.textContent = "학교 로그인이 만료되었습니다. 다시 로그인하세요."; el.classList.add("bad"); return; }
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
    renderAll();
    const updated = state.cred?.last_synced_at && state.cred.last_synced_at !== before;
    toast(state.cred?.status !== "ok" ? "동기화하지 못했습니다. 계정 상태를 확인하세요."
      : updated ? "최신 학습 현황으로 업데이트했어요." : "동기화가 지연되고 있어요. 잠시 후 다시 확인해 주세요.", state.cred?.status !== "ok");
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "LMS 동기화";
  }
});

/* ---------------- 화면 전환 ---------------- */

function closeMenu() {
  $(".navigation").classList.remove("expanded");
  $("#menu-toggle").setAttribute("aria-expanded", "false");
}
/** 지금 화면의 주소. 관리자 화면만 하위 경로(`#admin/overview`)를 붙인다. */
function viewHash() {
  if (state.view !== "admin") return `#${state.view}`;
  return `#admin/${state.adminPath.map(encodeURIComponent).join("/")}`;
}
/** target은 화면 이름이나 해시(`admin/users`). focus가 false면 포커스를 옮기지 않는다(관리자 탭 이동). */
function navigate(target, push = true, { focus = true } = {}) {
  const { view, path } = parseHash(target);
  state.view = Object.hasOwn(VIEW_NAMES, view) ? view : "dashboard";  // 없어진 화면(#todos 등)은 홈으로
  if (state.view === "admin") state.adminPath = path.length ? path : ["overview"];
  store.set("view", state.view);
  if (state.view === "videos") state.savedVideos = null;  // 동기화로 새로 저장된 주소 반영
  if (push && location.hash !== viewHash()) history.pushState(null, "", viewHash());
  else if (!push && location.hash !== viewHash()) history.replaceState(null, "", viewHash());  // #admin → #admin/overview, 없어진 #todos → #dashboard
  closeMenu();
  renderView();
  if (!focus) return;
  $("#view").focus({ preventScroll: true });
  $("#view").scrollIntoView({ block: "start" });
}
window.addEventListener("popstate", () => {
  if (!$("#app").hidden) navigate(location.hash || "dashboard", false);
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
function renderView() {
  $(".workspace").classList.toggle("dashboard-workspace", state.view === "dashboard");
  $(".workspace").classList.toggle("admin-workspace", state.view === "admin");
  $("#current-view").textContent = VIEW_NAMES[state.view] || "학습 현황";
  document.title = `${VIEW_NAMES[state.view] || "학습 현황"} · 아유 하기싫어`;
  document.querySelectorAll(".side button").forEach((b) => {
    const selected = b.dataset.view === state.view;
    b.classList.toggle("on", selected);
    if (selected) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  const views = { dashboard: viewDashboard, courses: viewCourses, playback: viewPlayback, videos: viewVideos, notifications: viewNotifications, account: viewAccount, admin: renderAdminView };
  (views[state.view] || viewDashboard)();
}

/** viewAdmin은 admin.js에 있다. 관리자만 쓰므로 처음 열 때 불러온다. */
function renderAdminView() {
  if (typeof viewAdmin === "function") return viewAdmin(state.adminPath);
  $("#view").innerHTML = `<div class="panel" aria-busy="true">${skeletonRows(4)}<p class="sr-only" role="status">불러오는 중입니다.</p></div>`;
  loadScripts("admin").then(
    () => { if (state.view === "admin") viewAdmin(state.adminPath); },
    (err) => { if (state.view === "admin") viewError(err.message, renderAdminView); },
  );
}

function viewError(message, retry) {
  $("#view").innerHTML = `<div class="panel empty" role="alert"><strong>불러오지 못했어요</strong><p>${esc(message)}</p><button class="btn" id="retry-view">다시 시도</button></div>`;
  $("#retry-view").addEventListener("click", retry);
}

function needCred() {
  if (state.cred) return false;
  $("#view").innerHTML = `<div class="panel empty"><p>LMS 연결 정보가 없습니다. 로그아웃한 뒤 다시 로그인하세요.</p></div>`;
  return true;
}

/* ---------------- 학습 현황(첫 화면) ----------------
 * 이번 주 할 일만 짧게 보여 주고, 버튼 하나로 끝낼 수 있게 한다. 내용은 week.js가 그린다.
 */
function viewDashboard() {
  if (needCred()) return;
  // 이번 주 할 일과 재생 가능한 강의 전체를 한 카드에서 탭으로 나눠 보여 준다(week.js).
  $("#view").innerHTML = `<div id="week-panel"></div>`;
  loadDashboardPlayback();
  if (typeof renderWeek === "function") renderWeek();
}

async function loadDashboardPlayback() {
  const root = $("#week-panel");
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

// Playback time includes confirmed completion and the current video's reported time.
function jobProgressPercent(job) {
  if (["done", "skipped"].includes(job.status)) return 100;
  if (job.status !== "playing" || !(job.duration > 0)) return 0;
  return Math.min(100, Math.max(0, (Number(job.progress_seconds) || 0) / job.duration * 100));
}

/** 재생이 정상이면 진행률이 말해 주므로 아무것도 쓰지 않는다. 확인이 필요할 때만 한 줄을 보인다. */
function playbackActivityHtml(job) {
  const updated = job.progress_updated_at ? new Date(job.progress_updated_at) : null;
  if (!updated || !Number.isFinite(updated.getTime())) return '<p class="muted small-text">재생 위치를 확인하는 중이에요</p>';
  const age = Math.max(0, Math.floor((Date.now() - updated.getTime()) / 1000));
  if (age <= 45) return "";
  return `<p class="warning">재생 확인 필요 · 45초 넘게 진행 보고가 없어요 (마지막 ${esc(updated.toLocaleTimeString("ko-KR", { hour12: false }))})</p>`;
}

function playbackPosition(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}분 ${value % 60}초`;
}

function playbackOverallPercent(jobs) {
  const duration = jobs.reduce((sum, job) => sum + Math.max(0, job.duration || 0), 0);
  const played = jobs.reduce((sum, job) => sum + Math.max(0, job.duration || 0) * jobProgressPercent(job) / 100, 0);
  return duration > 0 ? Math.floor(played / duration * 100) : 0;
}

/** 재생 가능한 강의 목록은 첫 화면 카드(week.js)가 함께 그린다. */
function drawDashboardPlayback() {
  if (typeof drawWeek === "function") drawWeek();
}

/* 학칙 확인 창은 처음 한 번만 띄운다. 동의한 뒤에는 버튼 한 번으로 바로 재생하고, 경고는 버튼 아래 글로 계속 보인다. */
const PLAYBACK_CONSENT_KEY = "playback-consent-v1";
const playbackConsented = () => store.get(PLAYBACK_CONSENT_KEY) === "1";

async function confirmDashboardPlayback() {
  if (state.playbackStarting || state.active || !state.dashboardPlan?.items.length) return;
  if (!playbackConsented()) {
    if (!(await confirmPlayback(state.dashboardPlan, "전체"))) return;
    store.set(PLAYBACK_CONSENT_KEY, "1");
  }
  await startDashboardPlayback();
}

async function startDashboardPlayback() {
  if (state.playbackStarting || state.active || !state.dashboardPlan?.items.length) return;
  const token = state.token;
  state.playbackStarting = true;
  drawDashboardPlayback();
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
    }
  }
}

function safeUrl(value) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}

/* ---------------- 과목 ---------------- */

/** 주차별 출석 칸. 과목 화면과 관리자 상세가 같이 쓴다. 기록이 없으면 empty 문구를 쓴다. */
function attendanceHtml(attendance, empty = "출석 기록이 아직 없습니다.") {
  const weeks = (attendance || []).filter((w) => w.statuses?.length);
  if (!weeks.length) return `<p class="muted small-text">${esc(empty)}</p>`;
  return `<div class="attend" aria-label="주차별 출석">${weeks.map((w) => `<div class="week"><div class="cells">${w.statuses
    .map((s) => `<i class="cell ${esc(s)}" title="${esc(w.week)}주차 ${({ attendance: "출석", late: "지각", absent: "결석" })[s] || "기록 없음"}"></i>`).join("")}</div><small>${esc(w.week)}</small></div>`).join("")}</div>`;
}

function viewCourses() {
  if (needCred()) return;
  const rows = state.courses.map((c) => {
    const grid = attendanceHtml(c.attendance);
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
    <div class="panel">${rows || `<div class="empty"><p>과목이 없습니다. ‘LMS 동기화’를 눌러 불러오세요.</p></div>`}</div>
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

/* ---------------- 강의 재생 ---------------- */

function viewPlayback() {
  if (needCred()) return;
  const v = $("#view");
  const helpFocused = document.activeElement?.closest?.(".playback-help") != null;  // 5초마다 다시 그려도 포커스를 잃지 않는다
  v.innerHTML = `<div class="view-head"><h1>강의 재생</h1></div>
    ${state.active ? activeBatchHtml(state.active) : planFormHtml()}
    ${historyHtml()}`;
  const help = v.querySelector(".playback-help");
  help?.addEventListener("toggle", () => { state.playbackHelpOpen = help.open; });
  if (helpFocused) help?.querySelector("summary")?.focus();
  document.querySelectorAll(".playback-failure").forEach((details) => {
    details.addEventListener("toggle", () => {
      if (details.open && !details.dataset.loaded) loadPlaybackFailure(details);
    });
  });
  if (!state.active) bindPlanForm();
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
      <div class="row"><button class="btn">재생 계획 보기</button>
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
      viewPlayback();
    } catch (err) { toast(err.message, true); }
  });
  $("#start-batch")?.addEventListener("click", startBatch);
  $("#clear-plan")?.addEventListener("click", () => { state.plan = null; viewPlayback(); });
}

function planHtml(plan) {
  if (!plan.items.length) return `<div class="panel empty"><p>이 범위에는 재생할 영상이 없습니다.</p></div>`;
  const steps = plan.items.map((i) => `<li class="${i.at_risk ? "risk" : ""}">
      <span class="num">${i.position + 1}</span>
      <span><span class="title">${esc(i.title)}</span><br>
        <span class="meta row small-text"><span>${esc(i.course_name)} ${esc(i.module_title)}</span><span>${fmtDuration(i.duration)}</span>
        <span>예상 종료 ${fmtDate(new Date(i.expected_end))}</span><span>${dueText(i.deadline)}</span></span></span>
      <span class="status ${i.at_risk ? "failed" : "queued"}">${i.at_risk ? "마감 초과" : ""}</span></li>`).join("");
  const warn = plan.warnings.length
    ? `<p class="warning">${plan.warnings.length}개 영상은 순서대로 재생하면 마감 전에 끝나지 않습니다. 마감이 이른 과목부터 재생하니, 필요하면 범위를 줄이세요.</p>` : "";
  return `<div class="panel">
    <div class="panel-body"><div class="summary-line">
      <span><b>${plan.items.length}</b>개 영상</span><span><b>${fmtDuration(plan.total_seconds)}</b>예상 소요</span>
      <span><b>${fmtDate(new Date(plan.expected_end))}</b>예상 종료</span></div><p class="muted small-text">현재 대기열을 반영한 예상입니다. 새 요청·재시도에 따라 달라집니다.</p>${warn}<p class="warning">${esc(PLAYBACK_WARNING)}</p></div>
    <ol class="steps">${steps}</ol>
    <div class="actions"><button class="btn primary" id="start-batch">${plan.items.length}개 재생 시작</button>
      <button class="btn ghost" id="clear-plan">계획 닫기</button></div></div>`;
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
    viewPlayback();
  } catch (err) { toast(err.message, true); }
}

const playbackCourseName = (job) => state.courses.find((course) => course.lms_course_id === job.lms_course_id)?.name || "";

/** 지금 강의 한 개가 화면의 주인공이다. 진행률과 제목은 여기서만 크게 보여 준다. */
function playbackHeroHtml(b, jobs, lateIds) {
  const current = jobs.find((job) => job.status === "playing");
  const focus = current || (b.cancel_requested ? null : jobs.find((job) => job.status === "queued"));
  const waiting = b.status === "queued" && b.queue_position != null
    ? `다음 영상 대기 ${b.queue_position}번째 · 약 ${fmtDuration(b.estimated_wait_seconds || 0)} 후 시작` : "다음 강의를 기다리는 중";
  const stateLabel = b.cancel_requested ? "재생 중지 요청됨" : current ? "지금 재생 중" : waiting;
  const finished = (b.done_count || 0) + (b.skipped_count || 0);
  const percent = current ? Math.floor(jobProgressPercent(current)) : 0;
  const extras = [b.failed_count ? `실패 ${b.failed_count}` : "", b.skipped_count ? `이미 완료 ${b.skipped_count}` : "",
    b.expected_end ? `예상 종료 ${fmtDate(new Date(b.expected_end))}` : ""].filter(Boolean).join(" · ");
  return `<section class="panel playback-hero" aria-label="지금 재생 상황">
    <div class="panel-body">
      <p class="playback-state" role="status">${stateLabel}</p>
      <h2>${focus ? esc(focus.title) : b.cancel_requested ? "예정된 강의는 재생하지 않아요" : "재생을 준비하고 있어요"}</h2>
      ${focus ? `<p class="meta">${[esc(playbackCourseName(focus)), current
        ? `${playbackPosition(Math.min(Math.max(0, current.progress_seconds || 0), Math.max(0, current.duration || 0)))} / ${playbackPosition(current.duration)}`
        : fmtDuration(focus.duration), dueText(focus.deadline)].filter(Boolean).join(" · ")}</p>` : ""}
      ${current ? `<p class="playback-percent"><strong>${percent}%</strong></p>
        <progress value="${jobProgressPercent(current)}" max="100" aria-label="현재 강의 재생 진행률"></progress>${playbackActivityHtml(current)}` : ""}
      ${focus && lateIds.has(focus.module_item_id) ? '<p class="warning">마감 전에 끝나지 않을 예정이에요</p>' : ""}
      ${focus?.attempts > 1 ? `<p class="meta">${focus.attempts}번째 시도</p>` : ""}
      ${focus?.error ? `<p class="warning">${esc(focus.error)}</p>` : ""}
      ${b.deadline_at_risk ? '<p class="warning">대기 시간 때문에 일부 강의가 마감 전에 끝나지 않을 수 있어요</p>' : ""}
    </div>
    <div class="panel-body playback-overall">
      ${b.total > 1 ? `<p><strong>전체 진행률 ${playbackOverallPercent(jobs)}%</strong> · ${finished}/${b.total}개 완료</p>
        <progress value="${playbackOverallPercent(jobs)}" max="100" aria-label="전체 재생 진행률"></progress>` : ""}
      ${extras ? `<p class="meta">${extras}</p>` : ""}
      <p class="meta">창을 닫아도 서버에서 계속돼요</p>
      <details class="playback-help" ${state.playbackHelpOpen ? "open" : ""}><summary>재생 방식 안내</summary>
        <ul>
          <li>사용자별로 강의를 한 개씩 번갈아 재생해요.</li>
          <li>예상 시간은 현재 대기열 기준이라 새 요청이나 재시도에 따라 달라져요.</li>
          <li>전체 진행률은 영상 길이 기준이고 이미 완료된 강의도 포함해요.</li>
          <li>학습 완료는 LMS에서 확인한 뒤 반영돼요.</li>
        </ul></details>
    </div>
  </section>`;
}

/** 강의가 2개 이상일 때만 목록을 둔다. 진행률은 위에서 보여 주므로 여기서는 순서와 상태만 쓴다. */
function playbackQueueHtml(b, jobs, lateIds) {
  if (b.total < 2 || !jobs.length) return "";
  const manyCourses = new Set(jobs.map((job) => job.lms_course_id)).size > 1;
  const nextId = b.cancel_requested ? null : jobs.find((job) => job.status === "queued")?.module_item_id;
  return `<section class="panel playback-queue" aria-labelledby="playback-queue-title">
    <h2 class="panel-title" id="playback-queue-title">재생 목록 ${b.total}개</h2>
    <ol class="steps">${jobs.map((j) => {
      const late = lateIds.has(j.module_item_id);
      const isNext = j.module_item_id === nextId;
      const meta = [manyCourses ? esc(playbackCourseName(j)) : "", fmtDuration(j.duration), dueText(j.deadline),
        j.status === "queued" && !b.cancel_requested && j.expected_start ? `예상 시작 ${fmtDate(new Date(j.expected_start))}` : "",
        late ? "마감 전에 끝나지 않을 예정" : "", j.attempts > 1 ? `${j.attempts}번 시도` : "", j.error ? esc(j.error) : ""];
      return `<li class="${[late ? "risk" : "", j.status === "playing" ? "now" : ""].filter(Boolean).join(" ")}"><span class="num">${j.position + 1}</span>
        <span><span class="title">${esc(j.title)}</span><span class="meta row small-text">${meta.filter(Boolean).map((m) => `<span>${m}</span>`).join("")}</span></span>
        <span class="status ${esc(j.status)}">${isNext ? "다음 재생" : JOB_STATUS[j.status] || esc(j.status)}</span></li>`;
    }).join("")}</ol>
  </section>`;
}

/** 진행 중인 묶음의 강의별 예상 종료 시각. 마감 전에 끝나지 않을 강의를 표시하는 데 쓴다. */
function playbackEnds(b) {
  let cursor = Date.now();
  const ends = new Map();
  for (const j of b.jobs || []) {
    if (j.status !== "queued" && j.status !== "playing") continue;
    const remaining = j.status === "playing" ? Math.max(0, j.duration - j.progress_seconds) : j.duration;
    cursor = j.expected_end ? new Date(j.expected_end).getTime() : cursor + (remaining + GAP_SECONDS) * 1000;
    ends.set(j.module_item_id, cursor);
  }
  return ends;
}

function activeBatchHtml(b) {
  const ends = playbackEnds(b);
  const jobs = [...(b.jobs || [])].sort((x, y) => x.position - y.position);
  const lateIds = new Set(jobs.filter((j) => ends.get(j.module_item_id) > dueTime(j)).map((j) => j.module_item_id));
  return playbackHeroHtml(b, jobs, lateIds) + playbackQueueHtml(b, jobs, lateIds);
}

/** 확인 창을 거쳐 진행 중인 묶음을 멈춘다. 창이 열린 동안 묶음이 끝나거나 바뀌었으면 아무것도 보내지 않는다. */
async function cancelActivePlayback() {
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
      const detail = await api(`/playback/batches/${id}`);
      if (state.active?.id === id) state.active = detail;
    } catch { /* 중지는 접수됐다. 진행 상황은 다음 폴링에서 다시 조회한다. */ }
    if (state.active?.id === id && !["queued", "running"].includes(state.active.status)) await finishActive();
  } catch (err) { toast(err.message, true); }
  if (state.token) redrawPlayback();
}

function historyHtml() {
  const past = state.batches.filter((b) => !["queued", "running"].includes(b.status));
  if (!past.length) return "";
  const scopeName = { item: "영상 하나", module: "주차", course: "과목", all: "모든 과목", auto: "자동 수강" };
  const counts = (b) => [["완료", b.done_count], ["실패", b.failed_count], ["이미 완료", b.skipped_count]]
    .filter(([, n]) => n).map(([name, n]) => `<span>${name} ${n}</span>`).join("");
  return `<section class="panel playback-history" aria-labelledby="playback-history-title">
    <h2 class="panel-title" id="playback-history-title">지난 재생</h2>
    <ul class="list">${past.map((b) => {
      const failed = b.failed_count || b.status === "failed" || b.status === "partial";
      return `<li>
      <span class="history-head"><span class="title">${scopeName[b.scope] || esc(b.scope)} ${b.total}개</span>
        <span class="status ${esc(b.status)}">${BATCH_STATUS[b.status] || esc(b.status)}</span></span>
      <span class="meta row">${counts(b)}<span>${fmtDate(new Date(b.created_at))}</span>${!failed && b.error ? `<span>${esc(b.error)}</span>` : ""}</span>
      ${failed ? `<details class="playback-failure" data-batch-id="${b.id}"><summary>실패한 영상과 오류 사유 보기</summary><div class="playback-failure-content" aria-live="polite"></div></details>` : ""}</li>`;
    }).join("")}</ul>
  </section>`;
}

async function loadPlaybackFailure(details) {
  const content = details.querySelector(".playback-failure-content");
  if (details.dataset.loading) return;
  details.dataset.loading = "true";
  content.innerHTML = '<p role="status">실패 내용을 불러오는 중입니다.</p>';
  try {
    const batch = await api(`/playback/batches/${details.dataset.batchId}`);
    if (!details.isConnected) return;
    const failed = batch.jobs.filter((job) => ["failed", "locked"].includes(job.status));
    content.innerHTML = `${batch.error ? `<p>${esc(batch.error)}</p>` : ""}
      ${failed.length ? `<ul>${failed.map((job) => `<li><strong>${esc(job.title)}</strong>
        <p>${esc(job.error || batch.error || "오류 사유가 기록되지 않았습니다. 서버의 재생 워커 로그를 확인해야 합니다.")}</p>
        <p class="muted small-text">시도 ${job.attempts}회 · 마지막 재생 위치 ${fmtDuration(job.progress_seconds || 0)} / ${fmtDuration(job.duration)}</p></li>`).join("")}</ul>`
        : '<p>영상별 실패 기록이 없습니다.</p>'}`;
    details.dataset.loaded = "true";
  } catch (err) {
    if (!details.isConnected) return;
    content.innerHTML = `<p role="alert">${esc(err.message)}</p><button class="btn">다시 시도</button>`;
    content.querySelector("button").addEventListener("click", () => loadPlaybackFailure(details));
  } finally {
    delete details.dataset.loading;
  }
}

let pollTimer = null;
function redrawPlayback() {
  if (state.view === "playback") viewPlayback();
  if (state.view === "dashboard" && typeof drawWeek === "function") drawWeek();
  renderTaskPanel();
}
function startPolling() {
  renderTaskPanel();
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    if (!state.active) return stopPolling();
    try {
      state.active = await api(`/playback/batches/${state.active.id}`);
      if (!["queued", "running"].includes(state.active.status)) await finishActive();
      redrawPlayback();
    } catch { /* 일시적 오류는 다음 주기에 다시 시도 */ }
  }, 5000);
}
function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

async function finishActive() {
  const b = state.active;
  stopPolling();
  toast(`강의 재생 ${BATCH_STATUS[b.status]}: 완료 ${b.done_count}, 실패 ${b.failed_count}`, b.status === "failed");
  state.active = null;
  renderTaskPanel();
  [state.todos, state.batches] = await Promise.all([api("/todos"), api("/playback/batches?limit=10")]);
  renderTaskPanel();
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
    $("#view").innerHTML = `<div class="view-head"><h1>내려받기·텍스트 변환</h1></div><div class="panel empty"><p>과목이 없습니다. ‘LMS 동기화’를 눌러 불러오세요.</p></div>`;
    return;
  }
  const allIds = [...courses.map((c) => c.lms_course_id), ...pastCourses.map(([id]) => id)];
  if (!state.videoCourse || !allIds.includes(state.videoCourse)) state.videoCourse = allIds[0];
  const opt = (id, name) => `<option value="${id}" ${id === state.videoCourse ? "selected" : ""}>${esc(name)}</option>`;
  const options = courses.map((c) => opt(c.lms_course_id, c.name)).join("")
    + (pastCourses.length ? `<optgroup label="지난 과목 (저장된 영상)">${pastCourses.map(([id, name]) => opt(id, name)).join("")}</optgroup>` : "");
  const isPast = !courses.some((c) => c.lms_course_id === state.videoCourse);
  if (isPast) state.videoTab = "videos";  // 지난 과목은 수업자료를 LMS에서 볼 수 없다
  const phone = isPhoneDevice();
  detectExtension();
  $("#view").innerHTML = `
    <div class="view-head"><h1>내려받기·텍스트 변환</h1>
      <label>과목<select id="video-course">${options}</select></label></div>
    <div class="tabs" role="tablist">
      <button role="tab" data-tab="videos" aria-selected="${state.videoTab === "videos"}">영상</button>
      <button role="tab" data-tab="materials" aria-selected="${state.videoTab === "materials"}" ${isPast ? 'disabled aria-describedby="tabs-reason"' : ""}>수업자료</button>
      <label class="check inline"><input type="checkbox" id="not-downloaded" ${state.notDownloadedOnly ? "checked" : ""}><span>안 받은 것만</span></label>
    </div>
    ${isPast ? '<p class="tabs-reason" id="tabs-reason">지난 과목은 수업자료를 볼 수 없어요</p>' : ""}
    ${phone ? `<p class="notice-inline" role="note">${PHONE_NOTICE}</p>` : ""}
    <details class="notice-inline notice-fold"><summary>개인 복습용이에요. 공유하지 마세요</summary><p>본인이 수강하는 강의와 자료를 개인 복습용으로 받는 기능입니다. 강의 영상과 자료는 교수와 학교의 저작물이므로 다른 사람에게 공유하거나 올리지 마세요. 파일은 서버에 저장하지 않고 학교에서 받는 대로 바로 전달합니다. 동기화 때 영상 주소를 저장해 두므로 시청기간이 끝난 영상도 받을 수 있지만, 학교 서버가 주소를 막거나 영상을 지우면 받을 수 없습니다.</p></details>
    <div class="panel" id="video-list" aria-busy="true"><p class="sr-only" role="status">LMS에서 목록을 불러오는 중</p>${skeletonRows(4)}</div>`;
  $("#video-course").addEventListener("change", (e) => {
    state.videoCourse = Number(e.target.value); state.videoList = null; state.materialList = null; viewVideos();
  });
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { state.videoTab = b.dataset.tab; viewVideos(); }));
  $("#not-downloaded").addEventListener("change", (e) => { state.notDownloadedOnly = e.target.checked; renderCurrentTab(); });

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

const PHONE_NOTICE = "PC 크롬에서 받을 수 있어요";

function renderCurrentTab() {
  if (state.videoTab === "materials") renderMaterialList(); else renderVideoList();
}

/** 주차 전체 받기. 받을 수 있는 항목이 2개 이상일 때만 보인다. 1개뿐이면 그 줄의 버튼과 같은 동작이다. */
function bulkButton(items, title, index) {
  if (items.length < 2) return "";
  const label = `전체 내려받기 (${items.length})`;
  const name = `aria-label="${esc(title)} ${label}" title="${label}"`;
  const face = `${icon("download")}전체 ${items.length}`;
  if (isPhoneDevice()) return `<button class="btn small link" data-bulk="${index}" ${name} disabled>${face}</button>`;  // 이유는 화면 위쪽에 한 번만 알린다
  return (state.videoTab !== "materials" || bulkDownloadSupported())
    ? `<button class="btn small link" data-bulk="${index}" ${name} ${state.bulk?.running ? "disabled" : ""}>${face}</button>`
    : `<button class="btn small link" ${name} disabled>${face}</button><span class="reason">폴더에 바로 저장하는 기능은 데스크톱 Chrome·Edge에서만 지원해요</span>`;
}

/** 여러 개 받기를 시작한다. 진행은 하단 패널에 나오고, 실패한 항목만 다시 시도할 수 있다. */
async function runBulkDownload(items) {
  const token = state.token;
  try {
    await startBulkDownload(items, (stat) => {
      if (state.token !== token) return;  // 로그아웃 뒤에 늦게 온 갱신은 버린다
      state.bulk = stat;
      try {
        if (!stat.running) {
          if (stat.stopped) toast(`${stat.done}/${stat.total}개 받고 중지했어요`);
          else if (!stat.failedItems.length) toast(`${stat.done}개를 모두 받았어요`);
          if (!stat.failedItems.length) state.bulk = null;
          // 다른 화면으로 옮겼거나 목록을 다시 불러오는 중이면 덮어쓰지 않는다
          if (state.view === "videos" && (state.videoTab === "materials" ? state.materialList : state.videoList)) renderCurrentTab();
        }
      } finally {
        renderTaskPanel();
      }
    });
  } catch (err) {
    if (err.name !== "AbortError") reportDownloadError(err);  // 폴더 선택 취소는 조용히
  }
}

/** 확장프로그램이 없으면 설치 안내를 펼쳐 문구를 그 안에 보이고, 그 밖의 오류는 토스트로 알린다. */
function reportDownloadError(err) {
  if (err.code !== "EXTENSION_MISSING") { toast(err.message, true); return; }
  state.extensionMissing = err.message;
  if (state.view !== "videos" || !state.videoList) { state.extensionMissing = null; toast(err.message, true); return; }
  renderCurrentTab();
  const summary = document.querySelector("#video-list details summary");
  if (!summary) { state.extensionMissing = null; toast(err.message, true); return; }  // 안내가 그려지지 않는 화면(빈 목록·휴대폰)에서도 오류는 보인다
  summary.scrollIntoView({ block: "center" });
  summary.focus({ preventScroll: true });
}

function wireBulk(box, groups) {
  box.querySelectorAll("[data-bulk]").forEach((b) => b.addEventListener("click", () => runBulkDownload(groups[Number(b.dataset.bulk)])));
}

function groupBy(items, key, title) {
  const groups = new Map();
  for (const v of items) {
    if (!groups.has(v[key])) groups.set(v[key], { title: v[title], items: [] });
    groups.get(v[key]).items.push(v);
  }
  return [...groups.values()];
}

/** 받았는지는 내려받기 아이콘의 모양과 색으로 보여 준다. 이 글자는 보조기기용이다. */
const doneBadge = (at) => at ? '<span class="received sr-only">받음</span>' : "";
const downloadFace = (at) => iconOnly(at ? "download-done" : "download", "내려받기");
const downloadTip = (at) => at ? `${esc(fmtDate(new Date(at)))}에 받음. 누르면 다시 받아요` : "내려받기";

const EXTENSION_STORE_URL = "https://chromewebstore.google.com/detail/ocnolcfmmkkphhkdnmblcdmakaciinda";

/** 설치 안내. 확장프로그램이 응답하기 전까지는 펼쳐 두어 처음 온 사람이 바로 설치할 수 있게 하고, 설치된 사람에게는 접어 둔다. */
function installGuide() {
  const ready = state.extensionReady && !state.extensionMissing;
  const open = state.extensionMissing || (!ready && !state.guideDismissed);
  return `<details class="guide${ready ? "" : " needs-install"}" ${open ? "open" : ""}>
    <summary><span class="guide-title">${icon("puzzle")}${ready ? "크롬 확장프로그램 안내" : "크롬 확장프로그램을 설치해 주세요"}</span></summary>
    <div class="guide-body">${state.extensionMissing ? `<p class="warning" role="alert">${esc(state.extensionMissing)}</p>` : ""}
    <ol class="install-steps">
      <li><strong>웹 스토어에서 설치</strong><span>‘Chrome에 추가’를 누르세요.</span>
        <a class="btn primary" href="${EXTENSION_STORE_URL}" target="_blank" rel="noopener">Chrome 웹 스토어 열기${icon("external")}</a></li>
      <li><strong>이 페이지 새로고침</strong><span>설치한 뒤 한 번만 하면 돼요.</span>
        <button class="btn" type="button" data-guide-reload>${icon("refresh")}새로고침</button></li>
      <li><strong>내려받기</strong><span>처음 열린 창에서 저장 폴더를 고르세요. 진행 상황과 취소도 그 창에서 볼 수 있어요.</span></li>
    </ol>
    <p class="guide-note">데스크톱 크롬에서만 쓸 수 있어요. 저장이 끝날 때까지 다운로드 창과 이 탭을 열어 두세요.</p></div></details>`;
}

function wireInstallGuide(box) {
  const guide = box.querySelector(".guide");
  if (!guide) return;
  guide.addEventListener("toggle", () => { state.guideDismissed = !guide.open; });
  guide.querySelector("[data-guide-reload]").addEventListener("click", () => location.reload());
}

/** 확장프로그램이 있으면 응답이 바로 오므로 목록보다 먼저 결과를 안다. 없으면 안내를 그대로 둘 뿐 기다리게 하지 않는다. */
function detectExtension() {
  if (state.extensionReady || isPhoneDevice()) return;
  extensionRequest("ping").then(() => {
    state.extensionReady = true;
    const guide = document.querySelector("#video-list .guide");
    if (!guide || state.extensionMissing) return;
    guide.insertAdjacentHTML("beforebegin", installGuide());
    guide.remove();
    wireInstallGuide($("#video-list"));
  }).catch(() => {});
}

const VIDEO_STATUS = {
  done: ["check-circle", "수강 완료"],
  todo: ["circle", "미수강"],
  expired: ["clock", "시청기간 종료"],
  locked: ["lock", "아직 열리지 않음"],
};
const videoStatus = (v) => v.available ? (v.completed ? "done" : "todo") : v.savedRow ? "expired" : "locked";

/** 글자를 뺀 아이콘의 뜻을 목록 위에 한 번만 적는다. 터치 화면에는 title이 없어서 필요하다. 지금 목록에 있는 것만 보인다. */
function iconLegend(items, phone) {
  const has = (test) => items.some(test);
  const rows = [
    !phone && has((v) => v.linkPath && !v.downloaded_at) && ["download", "내려받기"],
    has((v) => v.downloaded_at) && ["download-done", "받음"],
    has((v) => v.mediaPath && !v.transcript_id) && ["text-convert", "텍스트로 변환"],
    has((v) => v.transcript_id) && ["text-view", "텍스트 보기"],
    ...Object.entries(VIDEO_STATUS).map(([key, row]) => has((v) => videoStatus(v) === key) && row),
  ].filter(Boolean);
  return `<ul class="icon-legend" aria-label="아이콘 설명">${rows.map(([name, label]) => `<li>${icon(name)}${label}</li>`).join("")}</ul>`;
}

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
  const phone = isPhoneDevice();
  for (const v of items) {
    v.courseName = course?.name || v.savedRow?.course_name || "";
    v.downloaded_at = v.downloaded_at || v.savedRow?.downloaded_at || null;
    v.transcript_id = v.transcript_id ?? v.savedRow?.transcript_id ?? null;
    v.linkPath = v.available ? `/courses/${courseId}/videos/${v.module_item_id}/direct-download`
      : v.savedRow?.saved ? `/saved-videos/${v.savedRow.id}/direct-download` : null;
    v.mediaPath = phone ? null : v.linkPath;  // 오디오는 PC의 확장 프로그램이 뽑는다
  }
  if (state.notDownloadedOnly) items = items.filter((v) => !v.downloaded_at);
  const box = $("#video-list");
  box.removeAttribute("aria-busy");
  if (!items.length) {
    box.innerHTML = `<div class="empty"><p>${state.notDownloadedOnly ? "아직 받지 않은 영상이 없습니다." : "이 과목에는 내려받을 수 있는 강의 영상이 없습니다."}</p></div>`;
    return;
  }
  const groups = groupBy(items, "module_id", "module_title");
  const status = (v) => {
    const key = videoStatus(v);
    const [name, label] = VIDEO_STATUS[key];
    const full = v.savedRow?.saved ? `${label}, 주소 저장됨` : label;
    return `<span class="video-status ${key}" role="img" aria-label="${full}" title="${full}">${icon(name)}</span>`;
  };
  const thumb = (v) => v.thumbnail_url
    ? `<img class="thumb" src="${esc(v.thumbnail_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : "";
  const button = (v) => v.linkPath && !phone
    ? `<button class="btn small ${v.downloaded_at ? "ghost" : "soft"}" data-link="${esc(v.linkPath)}" title="${downloadTip(v.downloaded_at)}">${downloadFace(v.downloaded_at)}</button>`
    : `<button class="btn small ghost" disabled>${downloadFace(v.downloaded_at)}</button>`;
  // 내려받을 수 없는 이유는 마우스를 올리지 않아도 보이게 글자로 둔다. 휴대폰은 화면 위쪽 안내 하나로 갈음한다.
  const reason = (v) => v.linkPath || phone ? "" : `<span class="reason">${esc(v.savedRow?.resolve_error || "지금은 내려받을 수 없어요")}</span>`;
  const bulkGroups = groups.map((g) => g.items.filter((v) => v.linkPath).map((v) => ({
    linkPath: v.linkPath, label: v.title, onDone: () => { v.downloaded_at = new Date().toISOString(); markDownloaded(v); } })));
  const guide = phone ? "" : installGuide();
  box.innerHTML = guide + iconLegend(items, phone) + groups.map((g, i) => `<div class="group-label row"><span class="group-title">${esc(g.title)}</span>
      <span class="group-actions">${bulkButton(bulkGroups[i], g.title, i)}${typeof transcribeGroupButton === "function" ? transcribeGroupButton(g.items, g.title) : ""}</span></div>
    <ul class="list">${g.items.map((v) => `
    <li class="video-row">
      ${status(v)}${thumb(v)}
      <span class="video-info"><span class="title">${esc(v.title)}</span><br>
        <span class="meta row"><span>${v.duration ? fmtDuration(v.duration) : ""}</span><span>${fmtSize(v.size)}</span>${doneBadge(v.downloaded_at)}</span>${reason(v)}</span>
      <span class="row-actions">${typeof transcribeButton === "function" ? transcribeButton(v, courseId) : ""}${button(v)}</span>
    </li>`).join("")}</ul>`).join("");
  box.querySelectorAll("[data-link]").forEach((b) => b.addEventListener("click", async () => {
    if (await downloadVideo(b, b.dataset.link)) {
      const v = items.find((x) => x.linkPath === b.dataset.link);
      if (v) { v.downloaded_at = new Date().toISOString(); markDownloaded(v); }
      renderVideoList();
    }
  }));
  wireInstallGuide(box);
  wireBulk(box, bulkGroups);
  if (typeof wireTranscribe === "function") wireTranscribe(box, items, courseId);
}

function markDownloaded(v) {
  // 다시 그려도 표시가 남도록 원본 목록에도 반영한다
  const src = state.videoList?.items.find((x) => x.module_item_id === v.module_item_id);
  if (src) src.downloaded_at = v.downloaded_at;
  if (v.savedRow) v.savedRow.downloaded_at = v.downloaded_at;
}

function renderMaterialList() {
  const courseId = state.materialList.courseId;
  let items = state.materialList.items.map((m) => ({ ...m, linkPath: `/courses/${courseId}/materials/${encodeURIComponent(m.key)}/download-link` }));
  if (state.notDownloadedOnly) items = items.filter((m) => !m.downloaded_at);
  const box = $("#video-list");
  box.removeAttribute("aria-busy");
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
  box.innerHTML = groups.map((g, i) => `<div class="group-label row"><span class="group-title">${esc(g.title)}</span><span class="group-actions">${bulkButton(bulkGroups[i], g.title, i)}</span></div>
    <ul class="list">${g.items.map((m) => `
    <li class="video-row">
      <span class="video-info"><span class="title">${esc(m.file_name)}</span><br>
        <span class="meta row"><span>${esc(m.title)}</span><span>${fmtSize(m.size)}</span>
          ${m.kind === "canvas_file" ? "<span>위키 첨부</span>" : ""}${doneBadge(m.downloaded_at)}</span></span>
      <span class="row-actions"><button class="btn small ${m.downloaded_at ? "ghost" : "soft"}" data-link="${esc(m.linkPath)}" title="${downloadTip(m.downloaded_at)}">${downloadFace(m.downloaded_at)}</button></span>
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
  const token = state.token;
  const face = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = iconOnly("loader", "준비 중");
  try {
    if (linkPath.endsWith("/direct-download")) {
      await requireDownloadExtension();
      btn.innerHTML = iconOnly("loader", "다운로드 중");
      await downloadDirectVideo({ linkPath }, new AbortController().signal);
      if (state.token !== token) return false;  // 로그아웃 뒤에 늦게 온 결과는 알리지 않는다
      toast("영상 저장이 완료되었습니다.");
      return true;
    }
    const link = await api(linkPath, { method: "POST" });
    if (state.token !== token) return false;
    const a = document.createElement("a");
    a.href = link.url;
    a.download = link.file_name;
    document.body.append(a);
    a.click();
    a.remove();
    toast(`${link.file_name} 내려받기를 시작했습니다. 브라우저 다운로드 목록에서 진행 상황을 볼 수 있습니다.`);
    return true;
  } catch (err) {
    if (state.token === token) reportDownloadError(err);
    return false;
  } finally {
    btn.disabled = false;
    btn.innerHTML = face;
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
    : `<div class="empty"><p>알림 받을 곳을 추가하면 마감 24시간·3시간 전, 재생 결과를 보내 드립니다.</p></div>`;
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
    try {
      await withPending(form.querySelector("button:not([type=button])"), "처리 중", () => api("/notifications/channels", { method: "POST", json: body }));
      toast("알림 받을 곳을 추가했습니다"); viewNotifications();
    } catch (err) { toast(err.message, true); }
  });
  $("#view").querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
    try { await withPending(b, "처리 중", () => api(`/notifications/channels/${b.dataset.test}/test`, { method: "POST" })); toast("테스트 메시지를 보냈습니다"); }
    catch (err) { toast(err.message, true); }
  }));
  $("#view").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "이 알림 채널을 삭제할까요?", body: ["삭제하면 이 채널로 더 이상 알림을 보내지 않아요."],
      confirmLabel: "알림 채널 삭제", danger: true });
    if (!ok) return;
    try { await withPending(b, "처리 중", () => api(`/notifications/channels/${b.dataset.del}`, { method: "DELETE" })); toast("삭제했습니다"); viewNotifications(); }
    catch (err) { toast(err.message, true); }
  }));
}

/* ---------------- 계정 ---------------- */

function viewAccount() {
  const c = state.cred;
  const statusName = { ok: "정상", invalid: "다시 로그인 필요", error: "최근 오류" };
  $("#view").innerHTML = `
    <div class="view-head"><h1>계정</h1></div>
    <div class="panel"><div class="panel-body">
      <div class="summary-line"><span><b>${esc(state.me?.student_id || "")}</b>학번</span>
        ${c ? `<span><b>${statusName[c.status] || esc(c.status)}</b>LMS 연결</span>` : ""}</div>
      ${c ? `<p class="muted small-text">마지막 LMS 로그인 ${c.last_login_at ? fmtDate(new Date(c.last_login_at)) : "없음"},
        다음 자동 동기화 ${c.next_sync_at ? fmtDate(new Date(c.next_sync_at)) : "예정 없음"}</p>` : ""}
      ${c?.last_error ? `<p class="error">${esc(c.last_error)}</p>` : ""}
      <p class="small-text">학습 연결이 만료되면 등록한 개인 토큰, 학교 로그인 정보 순으로 복구합니다. 모두 만료되면 다시 로그인하거나 새 토큰을 등록하세요. 학교 비밀번호는 저장하지 않습니다.</p>
    </div></div>
    <div class="panel"><div class="panel-body">
      <h3>개인 액세스 토큰</h3>
      <p class="small-text">${c?.personal_token_registered
        ? (c.personal_token_status === "invalid"
          ? (c.personal_token_managed ? "자동 발급한 토큰이 만료되거나 폐기됐습니다. 다시 로그인하면 새로 발급합니다." : "만료되거나 폐기된 토큰입니다. 새 토큰을 등록하세요.")
          : (c.personal_token_managed ? "로그인할 때 서버가 자동 발급해 관리합니다 · 학습 연결 자동 복구에 사용합니다. 제거해도 Canvas 설정 > 승인된 통합의 ‘SSU LMS 자동 수강’ 토큰은 직접 삭제해야 합니다." : "등록됨 · 학습 연결 자동 복구에 사용합니다."))
        : "로그인하면 서버가 토큰을 자동 발급합니다. 직접 발급한 토큰을 등록할 수도 있습니다."}
        ${c?.personal_token_registered_at ? `등록일 ${fmtDate(new Date(c.personal_token_registered_at))}` : ""}</p>
      <p class="muted small-text" id="personal-token-help"><a href="https://canvas.ssu.ac.kr/profile/settings" target="_blank" rel="noopener noreferrer">Canvas 설정에서 토큰 발급</a> 후 붙여넣으세요. 토큰은 서버에 암호화해 보관합니다. 만료·폐기 시 새 토큰이 필요합니다.</p>
      <form id="personal-token-form">
        <label for="personal-token">개인 액세스 토큰</label>
        <input id="personal-token" type="password" required maxlength="4096" autocomplete="off" spellcheck="false" aria-describedby="personal-token-help personal-token-error">
        <p id="personal-token-error" class="error small-text" role="alert" tabindex="-1"></p>
        <div class="actions"><button class="btn primary" type="submit">${c?.personal_token_registered ? "토큰 교체" : "토큰 등록"}</button>
        ${c?.personal_token_registered ? '<button class="btn ghost" type="button" id="remove-personal-token">저장된 토큰 제거</button>' : ""}</div>
      </form>
      <p class="muted small-text">여기서 제거해도 학교의 토큰과 현재 학습 세션은 유지됩니다. 토큰 자체를 폐기하려면 Canvas 설정에서 삭제하세요.</p>
    </div></div>
    <div class="panel"><div class="panel-body">
      <h3>계정 삭제</h3>
      <p class="muted small-text">저장된 학교 로그인 세션과 동기화한 과목·할 일, 재생 기록, 알림 설정을 모두 지웁니다. 되돌릴 수 없습니다.</p>
      <div><button class="btn danger" id="delete-me">계정 삭제</button></div>
    </div></div>`;
  const tokenForm = $("#personal-token-form");
  async function updatePersonalToken(method) {
    const input = $("#personal-token", tokenForm);
    const error = $("#personal-token-error", tokenForm);
    const submit = tokenForm.querySelector('[type="submit"]');
    const buttons = [...tokenForm.querySelectorAll("button")];
    const label = submit.textContent;
    let body = method === "PUT" ? { access_token: input.value.trim() } : undefined;
    input.value = "";
    error.textContent = "";
    buttons.forEach(b => { b.disabled = true; });
    input.disabled = true;
    submit.textContent = method === "PUT" ? "학교 연결 확인 중…" : "제거 중…";
    try {
      state.cred = await api("/lms/access-token", { method, ...(body ? { json: body } : {}) });
      if (state.view === "account") viewAccount();
      toast(method === "PUT" ? "토큰을 등록하고 학습 연결을 확인했습니다" : "저장된 개인 토큰을 제거했습니다");
    } catch (err) {
      error.textContent = err.message;
      if (tokenForm.isConnected) error.focus();
    } finally {
      body = undefined;
      buttons.forEach(b => { b.disabled = false; });
      input.disabled = false;
      submit.textContent = label;
    }
  }
  tokenForm.addEventListener("submit", e => { e.preventDefault(); updatePersonalToken("PUT"); });
  $("#remove-personal-token")?.addEventListener("click", () => updatePersonalToken("DELETE"));
  $("#delete-me").addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: "계정을 삭제할까요?",
      body: ["저장된 학교 로그인 세션과 동기화한 과목·할 일, 재생 기록, 알림 설정을 모두 지웁니다. 되돌릴 수 없습니다.",
        "서버가 발급한 Canvas 토큰은 Canvas 설정 > 승인된 통합에서 ‘SSU LMS 자동 수강’ 항목을 직접 삭제해 주세요."],
      requireText: state.me.student_id, requireLabel: "확인을 위해 학번을 입력하세요",
      confirmLabel: "계정 삭제", danger: true,
    });
    if (!ok) return;
    try {
      await withPending($("#delete-me"), "처리 중", () => api("/me", { method: "DELETE" }));
      logout();
      toast("계정과 저장된 정보를 모두 지웠습니다");
    } catch (err) { toast(err.message, true); }
  });
}

/* ---------------- 시작 ---------------- */

setInterval(() => { if (!$("#app").hidden && state.view === "dashboard" && !document.activeElement.closest("#week-panel") && typeof drawWeek === "function") drawWeek(); }, 60000); // 오늘 날짜·마감 표시 갱신
// 모든 스크립트가 올라온 뒤에 시작한다: /me가 빨리 답해도 access.js 같은 뒤쪽 스크립트의 함수가 있어야 한다
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
