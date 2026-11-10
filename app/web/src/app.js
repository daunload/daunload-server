import { $, PRIVACY_NOTICE_KEY, VIEW_NAMES, api, esc, fmtDate, fmtDuration, fmtSize, parseHash, setApiHandlers, state, store } from "./core";
import { whenIdle } from "./assets";
import { clearToasts, closeConfirmDialog, icon, iconOnly, isPhoneDevice, skeletonRows, toast } from "./ui";
import { renderTaskPanel } from "./tasks";
import { bulkDownloadSupported, downloadDirectVideo, requireDownloadExtension, startBulkDownload, stopBulkDownload } from "./downloads";
import { ext } from "./extension";
import { cancelTranscription, transcribeButton, transcribeGroupButton, tx, wireTranscribe } from "./transcribe";
import { showAccessScreen } from "./access";
import { drawWeek, loadDashboardPlayback, renderWeek } from "./screens/home";
import { startPolling, stopPolling, viewPlayback } from "./screens/playback";
import { unmountScreen } from "./screens/mount";
import { viewCourses } from "./screens/courses";
import { viewNotifications } from "./screens/notifications";
import { viewAccount } from "./screens/account";

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
 * 알림(screens/notifications.tsx), 계정(screens/account.tsx). */

/* ---------------- 강의 영상 내려받기 ---------------- */

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
export async function runBulkDownload(items) {
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
export function reportDownloadError(err) {
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
  ext.request("ping").then(() => {
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

export function renderVideoList() {
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
      <span class="group-actions">${bulkButton(bulkGroups[i], g.title, i)}${transcribeGroupButton(g.items, g.title)}</span></div>
    <ul class="list">${g.items.map((v) => `
    <li class="video-row">
      ${status(v)}${thumb(v)}
      <span class="video-info"><span class="title">${esc(v.title)}</span><br>
        <span class="meta row"><span>${v.duration ? fmtDuration(v.duration) : ""}</span><span>${fmtSize(v.size)}</span>${doneBadge(v.downloaded_at)}</span>${reason(v)}</span>
      <span class="row-actions">${transcribeButton(v, courseId)}${button(v)}</span>
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
  wireTranscribe(box, items, courseId);
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

/* ---------------- 시작 ---------------- */

setInterval(() => { if (!$("#app").hidden && state.view === "dashboard" && !document.activeElement.closest("#week-panel") ) drawWeek(); }, 60000); // 오늘 날짜·마감 표시 갱신
