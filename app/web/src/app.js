import { $, PRIVACY_NOTICE_KEY, VIEW_NAMES, api, esc, fmtDate, parseHash, setApiHandlers, state, store } from "./core";
import { whenIdle } from "./assets";
import { clearToasts, closeConfirmDialog, skeletonRows, toast } from "./ui";
import { renderTaskPanel } from "./tasks";
import { stopBulkDownload } from "./downloads";
import { cancelTranscription, tx } from "./transcribe";
import { showAccessScreen } from "./access";
import { drawWeek, loadDashboardPlayback, renderWeek } from "./screens/home";
import { startPolling, stopPolling, viewPlayback } from "./screens/playback";
import { unmountScreen } from "./screens/mount";
import { viewCourses } from "./screens/courses";
import { viewNotifications } from "./screens/notifications";
import { viewAccount } from "./screens/account";
import { viewVideos } from "./screens/videos";

/* ---------------- 로그인 ---------------- */

// api()가 401이면 로그아웃, 권한이 회수됐으면 승인 대기 화면으로 보낸다
setApiHandlers({ unauthorized: logout, accessRequired: () => showAccessScreen() });

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

export function logout() {
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
export function serverLogout() {
  const token = state.token;
  if (token) fetch("/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
  logout();
}
$("#logout-btn").addEventListener("click", serverLogout);

/* ---------------- 데이터 ---------------- */

export async function loadCore(knownMe) {
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

export async function boot() {
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
export function viewHash() {
  if (state.view !== "admin") return `#${state.view}`;
  return `#admin/${state.adminPath.map(encodeURIComponent).join("/")}`;
}
/** target은 화면 이름이나 해시(`admin/users`). focus가 false면 포커스를 옮기지 않는다(관리자 탭 이동). */
export function navigate(target, push = true, { focus = true } = {}) {
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
  unmountScreen();  // 이전 Preact 화면을 정리한다(같은 화면을 다시 그릴 때도 처음부터 그린다)
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

/** 관리자 화면(admin.js)은 관리자만 쓰므로 처음 열 때 따로 받는다(Vite가 별도 파일로 나눈다). */
let adminModule = null;
function renderAdminView() {
  if (adminModule) return adminModule.viewAdmin(state.adminPath);
  $("#view").innerHTML = `<div class="panel" aria-busy="true">${skeletonRows(4)}<p class="sr-only" role="status">불러오는 중입니다.</p></div>`;
  import("./admin").then(
    (mod) => { adminModule = mod; if (state.view === "admin") mod.viewAdmin(state.adminPath); },
    () => { if (state.view === "admin") viewError("화면을 불러오지 못했어요. 네트워크를 확인하고 다시 시도하세요.", renderAdminView); },
  );
}

export function viewError(message, retry) {
  $("#view").innerHTML = `<div class="panel empty" role="alert"><strong>불러오지 못했어요</strong><p>${esc(message)}</p><button class="btn" id="retry-view">다시 시도</button></div>`;
  $("#retry-view").addEventListener("click", retry);
}

export function needCred() {
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
  renderWeek();
}

/* 옮긴 화면: 홈(screens/home.tsx), 강의 재생(screens/playback.tsx), 과목(screens/courses.tsx),
 * 알림(screens/notifications.tsx), 계정(screens/account.tsx), 내려받기·텍스트 변환(screens/videos.tsx). */

/* ---------------- 시작 ---------------- */

setInterval(() => { if (!$("#app").hidden && state.view === "dashboard" && !document.activeElement.closest("#week-panel") ) drawWeek(); }, 60000); // 오늘 날짜·마감 표시 갱신
