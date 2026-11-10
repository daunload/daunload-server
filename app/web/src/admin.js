import { $, KIND, api, esc, fmtDate, fmtDuration, fmtSize, pad, state } from "./core";
import { confirmDialog, icon, skeletonRows, toast, withPending } from "./ui";
import { navigate, viewHash } from "./app";
import { attendanceHtml } from "./screens/courses";
import { adminActivity, adminProblems } from "./admin-feeds";

/* ---------------- 관리자 ----------------
 * app.js 다음에 불러온다(state, api, esc, fmtDate, navigate, viewHash를 쓴다).
 * 탭 상태는 주소에 둔다: #admin/overview, #admin/users, #admin/users/{학번}[/{상세 탭}], #admin/activity, #admin/problems
 * 서버 문자열은 모두 esc()를 거친다.
 */

const ADMIN_TABS = [
  { key: "overview", label: "현황" },
  { key: "users", label: "사용자" },
  { key: "activity", label: "활동" },
  { key: "problems", label: "문제" },
];
const ADMIN_PERIODS = [7, 30, 90];
const ADMIN_METRICS = { playback_done: "재생 완료", logins: "로그인", downloads: "다운로드" };
export const ADMIN_PROBLEM_KIND = {
  playback: { label: "재생 실패", tone: "bad" },
  batch: { label: "재생 묶음 오류", tone: "bad" },
  lms: { label: "LMS 문제", tone: "warn" },
  resolve: { label: "영상 주소 확보 실패", tone: "warn" },
};
/** 재생 묶음 범위(PlaybackBatch.scope)의 화면 이름. 묶음 문제 줄의 제목도 이 값이다. */
const ADMIN_BATCH_SCOPE = { item: "한 개", module: "주차", course: "과목", all: "전체", auto: "자동", week: "이번 주" };
const ADMIN_BATCH_STATUS = {
  queued: { label: "대기", tone: "" }, running: { label: "진행 중", tone: "" }, done: { label: "완료", tone: "ok" },
  partial: { label: "일부 실패", tone: "bad" }, failed: { label: "실패", tone: "bad" }, cancelled: { label: "취소", tone: "" },
};
const ADMIN_LMS_STATUS = { ok: { label: "정상", tone: "ok" }, invalid: { label: "다시 로그인 필요", tone: "bad" }, error: { label: "최근 오류", tone: "warn" } };
/** 사용 권한(User.access_status)의 화면 이름. 사용자 탭의 필터 순서이기도 하다. */
const ADMIN_ACCESS = {
  approved: { label: "승인됨", tone: "ok" }, pending: { label: "대기 중", tone: "warn" }, rejected: { label: "거절됨", tone: "bad" },
  revoked: { label: "회수됨", tone: "bad" }, none: { label: "요청 안 함", tone: "" },
};
const ADMIN_CHANNEL = { discord: "Discord", telegram: "Telegram" };
const ADMIN_USER_SORTS = { activity: "최근 로그인순", created: "가입일순", student_id: "학번순" };
const ADMIN_USERS_LIMIT = 25;
const ADMIN_DETAIL_LIMIT = 20;
const ADMIN_DETAIL_TABS = [
  { key: "timeline", label: "전체 타임라인" }, { key: "courses", label: "과목·출석" }, { key: "todos", label: "할 일·과제" },
  { key: "playback", label: "재생" }, { key: "downloads", label: "다운로드" }, { key: "saved", label: "저장 영상" },
  { key: "transcripts", label: "전사" }, { key: "notifications", label: "알림" }, { key: "lms", label: "LMS" },
];

/**
 * nav: 화면을 그릴 때마다 오른다. seq: 같은 자리(key)의 요청 번호. 둘 중 하나라도 바뀌면 늦은 응답을 버린다.
 * users: 사용자 목록 조건(상세에 다녀와도 남는다). detail: 지금 보는 사용자 한 명의 요약과 탭별 조건(viewer가 다르면 버린다).
 */
export const adminUi = {
  nav: 0, seq: {}, days: 7, metric: "playback_done", problemCount: null, problemCountFor: null,
  users: { q: "", lms_status: "", has_problem: false, access: "", sort: "activity", offset: 0 },
  detail: null,
  activity: { kind: "all", student_id: "", offset: 0 },
  problems: { kind: "all", student_id: "", course: "", offset: 0 },
};

export const adminNumber = (n) => Number(n || 0).toLocaleString("ko-KR");
export const adminUserLink = (id) => `<a href="#admin/users/${encodeURIComponent(id)}">${esc(id)}</a>`;
export const adminTime = (iso) => iso ? `<time datetime="${esc(iso)}">${esc(fmtDate(new Date(iso)))}</time>` : "시각 기록 없음";

/**
 * 관리자 API를 부른다. 그 사이 화면이 바뀌었거나 같은 key로 새 요청을 보냈으면 응답을 버린다(render를 부르지 않고 오류도 던지지 않는다).
 * 아직 유효한 요청이 실패하면 오류를 던지므로 호출한 쪽이 adminState(el, "error")로 보여 준다.
 */
export async function adminRequest(path, render, key = "main") {
  const nav = adminUi.nav;
  const seq = (adminUi.seq[key] = (adminUi.seq[key] || 0) + 1);
  const current = () => nav === adminUi.nav && adminUi.seq[key] === seq && state.view === "admin" && !!state.me?.is_admin;
  let data;
  try {
    data = await api(path);
  } catch (err) {
    if (current()) throw err;
    return;
  }
  if (current()) render(data);
}

/** 로딩(스켈레톤), 빈 상태, 오류+재시도를 el 안에 그린다. */
export function adminState(el, kind, { message = "", retry, rows = 3 } = {}) {
  el.removeAttribute("aria-busy");
  if (kind === "loading") {
    el.setAttribute("aria-busy", "true");
    el.innerHTML = `${skeletonRows(rows)}<p class="sr-only" role="status">불러오는 중입니다.</p>`;
  } else if (kind === "empty") {
    el.innerHTML = `<p class="admin-empty">${esc(message)}</p>`;
  } else {
    el.innerHTML = `<div class="admin-error"><p role="alert"><strong>불러오지 못했어요</strong> ${esc(message)}</p>${retry ? '<button class="btn small" type="button">다시 시도</button>' : ""}</div>`;
    if (retry) $(".admin-error button", el).addEventListener("click", retry);
  }
  return el;
}

/**
 * 표 HTML. columns: [{ label, render(row) → 이미 이스케이프한 HTML, cls? }]. cls는 감싸는 div에 붙는다.
 * 760px 이하에서는 각 칸이 data-label을 머리글로 단 카드가 된다.
 */
export function adminTable(columns, rows, { caption = "", cls = "" } = {}) {
  const head = columns.map((c) => `<th scope="col"${c.cls ? ` class="${c.cls}"` : ""}>${esc(c.label)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((c) =>
    `<td data-label="${esc(c.label)}"${c.cls ? ` class="${c.cls}"` : ""}><div>${c.render(row)}</div></td>`).join("")}</tr>`).join("");
  return `<div class="admin-table-wrap${cls ? ` ${cls}` : ""}"><table class="admin-table">${caption ? `<caption class="sr-only">${esc(caption)}</caption>` : ""}
    <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/** 이전·다음 페이지 버튼. onPage(새 offset)를 부른다. DOM 요소를 돌려준다. */
export function adminPager(total, offset, limit, onPage) {
  const nav = document.createElement("nav");
  nav.className = "admin-pager";
  nav.setAttribute("aria-label", "페이지 이동");
  const end = Math.min(total, offset + limit);
  nav.innerHTML = `<button class="btn small" type="button" ${offset <= 0 ? "disabled" : ""}>이전</button>
    <span>${total ? `${offset + 1}–${end} / ${adminNumber(total)}건` : "0건"}</span>
    <button class="btn small" type="button" ${end >= total ? "disabled" : ""}>다음</button>`;
  const [prev, next] = nav.querySelectorAll("button");
  prev.addEventListener("click", () => onPage(Math.max(0, offset - limit)));
  next.addEventListener("click", () => onPage(offset + limit));
  return nav;
}

/** 문제 탭 이름의 건수를 바꾼다. 조건 없이 센 네 종류의 합만 넣는다(현황의 재생 실패 카드와 다르다). */
export function adminSetProblemCount(n) {
  adminUi.problemCount = n;
  adminUi.problemCountFor = state.me.student_id;
  const tab = $('#view .admin-tabs [data-admin-tab="problems"]');
  if (tab) tab.innerHTML = adminTabLabel(ADMIN_TABS[3]);
}

/** 머리의 기간 고르기와 다시 불러오기. 기간은 현황, 활동, 문제 탭이 함께 쓴다. */
export function adminPeriodActions(onPeriod, onReload) {
  $("#admin-actions").innerHTML = `<label class="admin-period">기간
      <select id="admin-days">${ADMIN_PERIODS.map((d) => `<option value="${d}"${d === adminUi.days ? " selected" : ""}>최근 ${d}일</option>`).join("")}</select></label>
    <button class="btn small" type="button" id="admin-reload">${icon("refresh")}다시 불러오기</button>`;
  $("#admin-days").addEventListener("change", (e) => { adminUi.days = Number(e.target.value); onPeriod(); });
  $("#admin-reload").addEventListener("click", onReload);
}

function adminTabLabel(tab) {
  // 로그아웃 후 다른 관리자가 들어와도 앞 사람이 본 숫자를 보이지 않게 학번으로 묶는다
  if (tab.key !== "problems" || adminUi.problemCount == null || adminUi.problemCountFor !== state.me?.student_id) return tab.label;
  return `${tab.label} <span class="admin-count">${adminNumber(adminUi.problemCount)}건</span>`;
}

/** 관리자 화면 뼈대. path는 state.adminPath(`["users", "20201234"]` 등). */
export function viewAdmin(path = state.adminPath) {
  adminUi.nav++;
  if (!state.me?.is_admin) {
    $("#view").innerHTML = `<div class="panel empty" role="alert">관리자 권한이 필요합니다.</div>`;
    return;
  }
  let tab = path[0];
  if (!ADMIN_TABS.some((t) => t.key === tab)) {
    tab = "overview";
    state.adminPath = ["overview"];
    history.replaceState(null, "", viewHash());
  }
  $("#view").innerHTML = `<div class="admin">
    <div class="view-head"><h1>운영 현황</h1><div class="admin-actions" id="admin-actions"></div></div>
    <nav class="admin-tabs" aria-label="관리자 메뉴">${ADMIN_TABS.map((t) =>
      `<a class="admin-tab" href="#admin/${t.key}" data-admin-tab="${t.key}"${t.key === tab ? ' aria-current="page"' : ""}>${adminTabLabel(t)}</a>`).join("")}</nav>
    <div class="admin-body" id="admin-body"></div></div>`;
  $("#view .admin").addEventListener("click", (e) => {
    const link = e.target.closest('a[href^="#admin/"]');
    if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    // 탭을 누르면 포커스를 새로 그린 같은 탭에 둔다. 다른 링크는 본문으로 옮긴다.
    const tabAttr = ["data-admin-tab", "data-admin-subtab"].find((a) => link.hasAttribute(a));
    navigate(link.getAttribute("href"), true, { focus: !tabAttr });
    if (tabAttr) $(`#view [${tabAttr}][aria-current='page']`)?.focus();
  });
  const body = $("#admin-body");
  if (tab === "overview") adminOverview(body);
  else if (tab === "users" && path.length > 1) adminUserDetail(body, path[1], path[2]);
  else if (tab === "users") adminUsers(body);
  else if (tab === "activity") adminActivity(body);  // admin-feeds.js
  else adminProblems(body);  // admin-feeds.js
}

/* ---------- 권한 ---------- */

const adminAccessTag = (status) => {
  const a = ADMIN_ACCESS[status] || { label: status, tone: "warn" };
  return adminTag(a.label, a.tone);
};

/** 상태별 버튼. 대기 중은 승인·거절, 승인됨은 회수, 나머지는 바로 승인. 관리자 줄에는 두지 않는다. */
function adminAccessButtons(id, status, isAdmin) {
  if (isAdmin) return "";
  const btn = (action, label, cls) => `<button class="btn small${cls}" type="button" data-access="${action}" data-id="${esc(id)}">${label}</button>`;
  let inner;
  if (status === "pending") inner = btn("approve", "승인", " primary") + btn("reject", "거절", " danger");
  else if (status === "approved") inner = btn("revoke", "권한 회수", " danger");
  else inner = btn("approve", "바로 승인", "");
  return `<div class="admin-acts">${inner}</div>`;
}

const ADMIN_ACCESS_DONE = { approve: (id) => `${id}을 승인했습니다`, reject: (id) => `${id}의 요청을 거절했습니다`, revoke: (id) => `${id}의 권한을 회수했습니다` };

/**
 * root 안의 [data-access] 버튼을 처리한다. 결정이 끝나거나 다른 관리자가 먼저 처리해 409가 나면 reload()로 목록을 다시 불러온다.
 * 처리 중에는 같은 줄의 버튼을 모두 잠그고, 회수만 확인 창을 거친다.
 */
function adminBindAccess(root, reload) {
  root.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-access]");
    if (!btn || !root.contains(btn) || btn.disabled) return;
    const { access: action, id } = btn.dataset;
    if (action === "revoke") {
      const ok = await confirmDialog({
        title: `${id}의 사용 권한을 회수할까요?`,
        body: ["회수하면 이 사용자는 서비스를 쓸 수 없어요.", "대기 중이거나 진행 중인 재생도 멈춰요."],
        confirmLabel: "권한 회수", danger: true,
      });
      if (!ok) return;
    }
    const mates = [...btn.closest(".admin-acts").querySelectorAll("button")].filter((b) => b !== btn);
    mates.forEach((b) => { b.disabled = true; });
    try {
      const done = await withPending(btn, "처리 중", () => api(`${adminUserPath(id)}/access`, { method: "POST", json: { action } }));
      toast(ADMIN_ACCESS_DONE[action](id));
      if (done?.sync_queued === false) toast("동기화는 대기열에 넣지 못했어요. 사용자의 정보는 다음 로그인이나 동기화 때 불러와요");
    } catch (err) {
      if (err.status === 409) toast("이미 처리된 요청이라 최신 상태로 다시 불러왔어요");
      else toast(err.message, true);
      if (err.status !== 409) { mates.forEach((b) => { b.disabled = false; }); return; }  // 409는 상태가 바뀐 것이므로 목록을 새로 본다
    }
    reload();
  });
}

const ADMIN_PREVIOUS = { none: "처음 요청", rejected: "거절됨", revoked: "회수됨" };

/** 현황 맨 위의 권한 요청 표. 요청이 없으면 비운다. */
function adminAccessRequests(slot) {
  const load = () => adminRequest("/admin/access-requests", (d) => {
    if (!d.items.length) { slot.replaceChildren(); return; }
    slot.innerHTML = `<section class="panel admin-box admin-access" aria-labelledby="admin-access-title">
      <h2 id="admin-access-title">권한 요청 ${adminNumber(d.total)}건</h2>
      ${adminTable([
        { label: "학번", render: (r) => adminUserLink(r.student_id), cls: "admin-nowrap" },
        { label: "요청 시각", render: (r) => adminTime(r.requested_at), cls: "admin-nowrap" },
        { label: "이전 상태", render: (r) => adminTag(r.previous_status === "rejected" && r.reject_count ? `거절됨 ${adminNumber(r.reject_count)}회` : ADMIN_PREVIOUS[r.previous_status] || r.previous_status, r.previous_status === "none" ? "" : "bad") },
        { label: "처리", render: (r) => adminAccessButtons(r.student_id, "pending", false) },
      ], d.items, { caption: "권한 요청", cls: "admin-linked" })}</section>`;
  }, "access").catch((err) => adminState(slot, "error", { message: err.message, retry: load }));
  adminBindAccess(slot, load);
  load();
}

/* ---------- 현황 ---------- */

function adminOverview(body) {
  const load = () => {
    adminState(body, "loading", { rows: 4 });
    adminRequest(`/admin/overview?days=${adminUi.days}`, (data) => drawOverview(body, data), "overview")
      .catch((err) => adminState(body, "error", { message: err.message, retry: load }));
  };
  adminPeriodActions(load, load);
  load();
}

const ADMIN_PLAYBACK_STATUS = {
  queued: "재생 대기", playing: "재생 중", done: "재생 완료", failed: "재생 실패",
  skipped: "이미 완료", locked: "재생 잠김", cancelled: "재생 취소",
};

export function adminActivityLabel(item) {
  if (item.kind === "playback") return ADMIN_PLAYBACK_STATUS[item.status] || item.status;
  if (item.kind === "download") return `다운로드 누적 ${adminNumber(item.count)}회`;  // count는 기간 합계가 아니라 그 항목의 전체 누적이다
  if (item.kind === "transcript") return "전사 저장";
  return item.kind;
}

/** 문제 한 줄의 과목·영상. 묶음 줄의 제목은 범위 값이라 이름(전체 재생 묶음 등)으로 바꾼다. */
export function adminProblemTitle(p) {
  return adminCourseTitle(p.course_name, p.kind === "batch" ? `${ADMIN_BATCH_SCOPE[p.title] || p.title} 재생 묶음` : p.title);
}
export const adminProblemTag = (p) => {
  const kind = ADMIN_PROBLEM_KIND[p.kind] || { label: p.kind, tone: "warn" };
  return adminTag(kind.label, kind.tone);
};
export const adminProblemError = (p) => `${esc(p.error || "오류 내용 없음")}${p.not_run ? ` · 미실행 ${adminNumber(p.not_run)}개` : ""}`;

function drawOverview(body, d) {
  body.removeAttribute("aria-busy");
  adminSetProblemCount(d.problem_count);
  const rate = d.failure_rate == null ? "-" : `${(d.failure_rate * 100).toFixed(1)}%`;
  const kpi = (label, value, sub, cls = "") =>
    `<div class="panel admin-kpi${cls}"><span class="admin-kpi-label">${label}</span><strong>${value}</strong><span class="admin-kpi-sub">${sub}</span></div>`;
  const problems = d.recent_problems.length ? adminTable([
    { label: "시각", render: (p) => adminTime(p.occurred_at), cls: "admin-nowrap" },
    { label: "사용자", render: (p) => adminUserLink(p.student_id) },
    { label: "과목 · 영상", render: adminProblemTitle },
    { label: "오류", render: (p) => `${adminProblemTag(p)} ${adminProblemError(p)}` },
  ], d.recent_problems, { caption: "확인이 필요한 문제", cls: "admin-linked" }) : '<p class="admin-empty">확인할 문제가 없습니다.</p>';
  body.innerHTML = `
    <div class="admin-access-slot"></div>
    <section class="admin-kpis" aria-label="주요 지표 (최근 ${adminNumber(d.days)}일)">
      ${kpi("활동 사용자", adminNumber(d.active_users), `전체 ${adminNumber(d.total_users)}명`)}
      ${kpi("재생한 강의", adminNumber(d.playback_done), `${fmtDuration(d.playback_seconds)}${d.playback_auto ? ` · 자동 ${adminNumber(d.playback_auto)}개` : ""}`)}
      ${kpi("다운로드 기록", adminNumber(d.downloads), "완료 보고 또는 링크 발급 횟수")}
      ${kpi("재생 실패", adminNumber(d.playback_failed), `실패율 ${rate}`, d.playback_failed ? " bad" : "")}
    </section>
    <div class="admin-grid">
      <section class="panel admin-box admin-chart" aria-labelledby="admin-chart-title">
        <div class="admin-box-head"><h2 id="admin-chart-title"></h2>
          <div class="admin-seg" role="group" aria-label="그래프 항목">${Object.entries(ADMIN_METRICS).map(([key, label]) =>
            `<button class="btn small" type="button" data-metric="${key}">${label}</button>`).join("")}</div></div>
        <div class="admin-bars${d.series.length > 31 ? " dense" : ""}" aria-hidden="true"></div>
        <p class="admin-caption"></p>
        <details class="admin-alt"><summary>표로 보기</summary>${adminTable([
          { label: "날짜", render: (s) => esc(s.day) },
          ...Object.entries(ADMIN_METRICS).map(([key, label]) => ({ label, render: (s) => adminNumber(s[key]) })),
        ], d.series, { caption: "일별 재생 완료, 로그인, 다운로드" })}</details>
      </section>
      <section class="panel admin-box" aria-labelledby="admin-feed-title">
        <h2 id="admin-feed-title">최근 활동</h2>
        ${d.recent_activity.length ? `<ul class="admin-feed">${d.recent_activity.map((a) => `<li>
          <span class="admin-feed-text">${adminUserLink(a.student_id)} · ${esc(a.title)}</span>
          <span class="admin-feed-meta"><span class="admin-tag${a.kind === "playback" && ["failed", "locked"].includes(a.status) ? " bad" : ""}">${esc(adminActivityLabel(a))}</span>${adminTime(a.recorded_at)}</span></li>`).join("")}</ul>`
          : '<p class="admin-empty admin-feed">기간 안에 활동이 없습니다.</p>'}
      </section>
    </div>
    <section class="panel admin-box admin-problems" aria-labelledby="admin-problems-title">
      <div class="admin-box-head"><h2 id="admin-problems-title">확인이 필요한 문제</h2>
        <a class="admin-more" href="#admin/problems">문제 ${adminNumber(d.problem_count)}건 모두 보기</a></div>
      ${problems}
    </section>`;
  if (d.pending_count) adminAccessRequests($(".admin-access-slot", body));
  const chart = $(".admin-chart", body);
  chart.querySelectorAll("[data-metric]").forEach((b) => b.addEventListener("click", () => {
    adminUi.metric = b.dataset.metric;
    drawBars(chart, d);
  }));
  drawBars(chart, d);
}

/** 일별 막대. 값은 각 막대의 title과 아래 표에 있다. */
function drawBars(chart, d) {
  const key = adminUi.metric;
  const label = ADMIN_METRICS[key];
  const values = d.series.map((s) => s[key] || 0);
  const max = Math.max(0, ...values);
  const n = d.series.length;
  const step = n <= 7 ? 1 : n <= 31 ? 5 : 15;  // 마지막 날부터 거꾸로 셀 때 이 간격마다 날짜를 쓴다
  $("#admin-chart-title", chart).textContent = `일별 ${label}`;
  chart.querySelectorAll("[data-metric]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.metric === key)));
  $(".admin-bars", chart).innerHTML = d.series.map((s, i) => {
    const [y, m, day] = s.day.split("-").map(Number);
    const date = new Date(y, m - 1, day);
    const text = `${date.getMonth() + 1}/${date.getDate()}`;
    const v = values[i];
    const pct = max ? Math.max(v ? 2 : 0, Math.round((v / max) * 100)) : 0;
    return `<div class="admin-bar" title="${text} ${label} ${adminNumber(v)}건">
      <span class="admin-fill" style="height:${pct}%"></span><span class="admin-day">${(n - 1 - i) % step === 0 ? text : ""}</span></div>`;
  }).join("");
  const since = d.tracking_since ? ` · 로그인·다운로드 추이는 ${esc(d.tracking_since)}부터 집계` : " · 로그인·다운로드 기록은 아직 없습니다";
  $(".admin-caption", chart).innerHTML = `최대 ${adminNumber(max)}건${since}`;
}

/* ---------- 사용자 ---------- */

const ADMIN_BACK_ICON = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M15 5l-7 7 7 7"/></svg>';
const ADMIN_PLAYBACK_FILTER = ["failed", "locked", "done", "queued", "playing", "skipped", "cancelled"];  // API가 받는 job 상태 순서
const ADMIN_SUBMISSION = { unsubmitted: "미제출", submitted: "제출함", excused: "면제", unknown: "확인 불가" };
const ADMIN_DOWNLOAD_KIND = { video: "영상", commons_file: "강의 자료", canvas_file: "첨부 파일" };

export const adminTag = (label, tone = "") => `<span class="admin-tag${tone ? ` ${tone}` : ""}">${esc(label)}</span>`;
const adminWhen = (iso, none = "-") => iso ? adminTime(iso) : esc(none);
/** 날짜만(2026/03/04). 가입일·동의일처럼 해가 중요한 곳에 쓴다. */
function adminDay(iso) {
  if (!iso) return "기록 없음";
  const d = new Date(iso);
  return `<time datetime="${esc(iso)}">${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}</time>`;
}
function adminLmsTag(status, prefix = "") {
  if (!status) return adminTag(`${prefix}연결 안 함`);
  const s = ADMIN_LMS_STATUS[status] || { label: status, tone: "warn" };
  return adminTag(prefix + s.label, s.tone);
}
/** 재생 job 상태. 실패했지만 시작한 적이 없으면(앞 영상 실패로 멈춘 묶음 등) "미실행"이다. */
export function adminJobTag(job) {
  if (job.status === "failed" && !job.started_at) return adminTag("미실행");
  const tone = ["failed", "locked"].includes(job.status) ? "bad" : job.status === "done" ? "ok" : "";
  return adminTag(ADMIN_PLAYBACK_STATUS[job.status] || job.status, tone);
}
export const adminUserPath = (id) => `/admin/users/${encodeURIComponent(id)}`;
const adminSubtabHref = (id, key) => `#admin/users/${encodeURIComponent(id)}/${key}`;
export const adminCourseTitle = (course, title) => esc(course ? `${course} · ${title}` : title);

function adminUsers(body) {
  adminUi.detail = null;
  const f = adminUi.users;
  const nav = adminUi.nav;
  $("#admin-actions").innerHTML = `<button class="btn small" type="button" id="admin-reload">${icon("refresh")}다시 불러오기</button>`;
  body.innerHTML = `
    <form class="admin-filters" role="search" aria-label="사용자 찾기">
      <label class="admin-search">학번 검색<input type="search" name="q" maxlength="32" autocomplete="off" value="${esc(f.q)}"></label>
      <label>LMS 상태<select name="lms_status"><option value="">전체</option>${Object.entries(ADMIN_LMS_STATUS).map(([k, s]) =>
        `<option value="${k}"${f.lms_status === k ? " selected" : ""}>${s.label}</option>`).join("")}</select></label>
      <label>정렬<select name="sort">${Object.entries(ADMIN_USER_SORTS).map(([k, label]) =>
        `<option value="${k}"${f.sort === k ? " selected" : ""}>${label}</option>`).join("")}</select></label>
      <label class="check"><input type="checkbox" name="has_problem"${f.has_problem ? " checked" : ""}><span>문제 있음만</span></label>
    </form>
    <div class="admin-seg admin-access-filter" role="group" aria-label="권한">${[["", "전체"], ...Object.entries(ADMIN_ACCESS).map(([k, a]) => [k, a.label])].map(([k, label]) =>
      `<button class="btn small" type="button" data-access-filter="${k}" aria-pressed="${f.access === k}">${label}</button>`).join("")}</div>
    <section class="panel admin-box" aria-labelledby="admin-users-title">
      <div class="admin-box-head"><h2 id="admin-users-title" tabindex="-1">사용자 <span class="admin-count" id="admin-user-count" role="status"></span></h2></div>
      <div id="admin-user-list"></div>
    </section>`;
  const form = $(".admin-filters", body);
  const list = $("#admin-user-list");
  const load = () => {
    const params = new URLSearchParams({ sort: f.sort, offset: f.offset, limit: ADMIN_USERS_LIMIT });
    if (f.q.trim()) params.set("q", f.q.trim());
    if (f.lms_status) params.set("lms_status", f.lms_status);
    if (f.has_problem) params.set("has_problem", "true");
    if (f.access) params.set("access", f.access);
    adminState(list, "loading", { rows: 4 });
    adminRequest(`/admin/users?${params}`, (d) => drawUsers(list, d, load), "users")
      .catch((err) => adminState(list, "error", { message: err.message, retry: load }));
  };
  const apply = () => {
    clearTimeout(adminUi.searchTimer);
    f.q = form.q.value;
    f.lms_status = form.lms_status.value;
    f.sort = form.sort.value;
    f.has_problem = form.has_problem.checked;
    f.offset = 0;
    load();
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); apply(); });
  form.addEventListener("change", (e) => { if (e.target.name !== "q") apply(); });
  form.q.addEventListener("input", () => {
    clearTimeout(adminUi.searchTimer);
    adminUi.searchTimer = setTimeout(() => { if (nav === adminUi.nav) apply(); }, 300);
  });
  $(".admin-access-filter", body).addEventListener("click", (e) => {
    const b = e.target.closest("[data-access-filter]");
    if (!b) return;
    f.access = b.dataset.accessFilter;
    f.offset = 0;
    body.querySelectorAll("[data-access-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    load();
  });
  adminBindAccess(list, load);
  $("#admin-reload").addEventListener("click", load);
  // 학번 링크가 실제 컨트롤이고, 줄의 다른 곳을 눌러도 그 링크를 누른 것으로 친다
  list.addEventListener("click", (e) => {
    if (e.target.closest("a, button") || String(getSelection())) return;
    e.target.closest("tbody tr")?.querySelector('a[href^="#admin/users/"]')?.click();
  });
  load();
}

function drawUsers(list, d, load) {
  const f = adminUi.users;
  list.removeAttribute("aria-busy");
  $("#admin-user-count").textContent = `${adminNumber(d.total)}명`;
  if (!d.users.length) {
    list.innerHTML = '<p class="admin-empty">조건에 맞는 사용자가 없습니다.</p>';
  } else {
    list.innerHTML = adminTable([
      { label: "학번", render: (u) => adminUserLink(u.student_id), cls: "admin-nowrap" },
      { label: "권한", render: (u) => adminAccessTag(u.access_status) },
      { label: "최근 로그인", render: (u) => adminWhen(u.last_login_at, "로그인 기록 없음"), cls: "admin-nowrap" },
      { label: "LMS", render: (u) => adminLmsTag(u.lms_status) },
      { label: "과목", render: (u) => adminNumber(u.course_count) },
      { label: "재생 (완료/실패)", render: (u) =>
        `${adminNumber(u.playback_done)} / ${u.playback_failed ? `<span class="admin-bad">${adminNumber(u.playback_failed)}</span>` : "0"}` },
      { label: "다운로드", render: (u) => adminNumber(u.download_count) },
      { label: "전사", render: (u) => adminNumber(u.transcript_count) },
      { label: "알림", render: (u) => u.channels.length ? esc(u.channels.map((c) => ADMIN_CHANNEL[c] || c).join(", ")) : "없음" },
      { label: "권한 관리", render: (u) => adminAccessButtons(u.student_id, u.access_status, u.is_admin) || "관리자 계정" },
    ], d.users, { caption: "사용자 목록", cls: "admin-users" });
  }
  if (d.total > ADMIN_USERS_LIMIT || f.offset) {
    list.append(adminPager(d.total, f.offset, ADMIN_USERS_LIMIT, (o) => { f.offset = o; load(); $("#admin-users-title").focus(); }));
  }
}

/* ---------- 사용자 상세 ---------- */

function adminUserDetail(body, id, tabKey) {
  let tab = tabKey || "timeline";
  if (!ADMIN_DETAIL_TABS.some((t) => t.key === tab)) {
    tab = "timeline";
    state.adminPath = ["users", id];
    history.replaceState(null, "", viewHash());
  }
  const viewer = state.me.student_id;
  if (adminUi.detail?.viewer !== viewer || adminUi.detail.id !== id) adminUi.detail = { viewer, id, summary: null, tabs: {} };
  const detail = adminUi.detail;
  const st = (detail.tabs[tab] ||= { offset: 0, offsets: {} });
  const label = ADMIN_DETAIL_TABS.find((t) => t.key === tab).label;
  $("#admin-actions").innerHTML = `<button class="btn small" type="button" id="admin-reload">${icon("refresh")}다시 불러오기</button>`;
  body.innerHTML = `
    <a class="admin-back" href="#admin/users">${ADMIN_BACK_ICON}사용자 목록</a>
    <section class="admin-user-head" aria-label="사용자 요약"></section>
    <nav class="admin-subtabs" aria-label="사용자 기록">${ADMIN_DETAIL_TABS.map((t) =>
      `<a class="admin-tab" href="${adminSubtabHref(id, t.key)}" data-admin-subtab="${t.key}"${t.key === tab ? ' aria-current="page"' : ""}>${t.label}</a>`).join("")}</nav>
    <section class="panel admin-box admin-user-tab" aria-label="${esc(label)}"></section>`;
  const head = $(".admin-user-head", body);
  const box = $(".admin-user-tab", body);
  $("#admin-reload").addEventListener("click", () => {
    adminUi.detail = null;
    viewAdmin();
    $("#admin-reload")?.focus();
  });
  const loadSummary = () => {
    adminState(head, "loading", { rows: 2 });
    if (tab === "lms") adminState(box, "loading");
    adminRequest(`${adminUserPath(id)}/summary`, (s) => {
      detail.summary = s;
      drawUserHead(head, s);
      if (tab === "lms") drawUserLms(box, s);
    }, "summary").catch((err) => {
      if (err.status === 404) return adminNotFound(body);
      adminState(head, "error", { message: err.message, retry: loadSummary });
      if (tab === "lms") adminState(box, "error", { message: err.message, retry: loadSummary });
    });
  };
  adminBindAccess(head, () => { detail.summary = null; loadSummary(); });
  if (detail.summary) {
    drawUserHead(head, detail.summary);
    if (tab === "lms") drawUserLms(box, detail.summary);
  } else {
    loadSummary();
  }
  if (tab !== "lms") ADMIN_TAB_VIEWS[tab](box, id, st);
}

function adminNotFound(body) {
  $("#admin-actions").innerHTML = "";
  body.innerHTML = `<div class="panel admin-box" role="alert"><p><strong>사용자를 찾을 수 없습니다.</strong> 학번을 확인하세요.</p>
    <a class="admin-more" href="#admin/users">사용자 목록으로</a></div>`;
}

function drawUserHead(head, s) {
  head.removeAttribute("aria-busy");
  const lms = s.lms;
  const kpi = (label, value, cls = "") =>
    `<div class="panel admin-kpi${cls}"><span class="admin-kpi-label">${label}</span><strong>${adminNumber(value)}</strong></div>`;
  head.innerHTML = `
    <div class="admin-who"><h2>${esc(s.student_id)}</h2>${s.is_admin ? adminTag("관리자", "warn") : adminTag("일반 사용자")}${adminAccessTag(s.access_status)}${adminAccessButtons(s.student_id, s.access_status, s.is_admin)}</div>
    <p class="admin-sub">가입 ${adminDay(s.created_at)} · 로그인 ${adminNumber(s.login_count)}회 · 최근 로그인 ${adminWhen(s.last_login_at, "기록 없음")} · 이용 동의 ${adminDay(s.consent_at)}</p>
    <p class="admin-sub">${adminLmsTag(lms?.status, "LMS ")}${lms ? ` 동기화 ${adminWhen(lms.last_synced_at, "기록 없음")}` : ""}
      ${lms?.last_error ? `<span class="admin-err">마지막 오류: ${esc(lms.last_error)}</span>` : ""}</p>
    <div class="admin-kpis admin-sum">
      ${kpi("수강 과목", s.counts.courses)}${kpi("남은 할 일", s.counts.todos)}${kpi("재생 완료", s.counts.playback_done)}
      ${kpi("재생 실패", s.counts.playback_failed, s.counts.playback_failed ? " bad" : "")}${kpi("다운로드", s.counts.downloads)}
    </div>`;
}

function drawUserLms(box, s) {
  box.removeAttribute("aria-busy");
  const l = s.lms;
  if (!l) { box.innerHTML = '<p class="admin-empty">LMS 계정을 연결하지 않았습니다.</p>'; return; }
  const row = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
  box.innerHTML = `<h3 class="sr-only">LMS</h3><dl class="admin-dl">
    ${row("상태", adminLmsTag(l.status))}
    ${row("마지막 오류", l.last_error ? `<span class="admin-err">${esc(l.last_error)}</span> ${adminWhen(l.last_error_at, "")}` : "없음")}
    ${row("마지막 로그인", adminWhen(l.last_login_at, "기록 없음"))}
    ${row("마지막 동기화", adminWhen(l.last_synced_at, "기록 없음"))}
    ${row("다음 동기화", adminWhen(l.next_sync_at, "예정 없음"))}</dl>`;
}

/**
 * 상세 탭의 한 목록을 페이지 단위로 그린다. el 안을 draw(data)의 HTML로 채우고, 넘칠 때만 페이지 버튼을 붙인다.
 * st.offsets[key]에 지금 위치를 둔다(같은 사용자의 다른 탭에 다녀와도 남는다). params()는 추가 조건, drawn()은 그린 뒤에 부른다.
 * 다시 불러오는 함수를 돌려준다.
 */
function adminPaged(el, path, st, key, draw, { params = () => ({}), drawn } = {}) {
  const load = () => {
    const offset = st.offsets[key] || 0;
    const q = new URLSearchParams({ ...params(), offset, limit: ADMIN_DETAIL_LIMIT });
    adminState(el, "loading");
    adminRequest(`${path}?${q}`, (d) => {
      el.removeAttribute("aria-busy");
      el.innerHTML = draw(d);
      if (d.total > ADMIN_DETAIL_LIMIT || offset) {
        el.append(adminPager(d.total, offset, ADMIN_DETAIL_LIMIT, (o) => { st.offsets[key] = o; load(); }));
      }
      drawn?.();
    }, key).catch((err) => adminState(el, "error", { message: err.message, retry: load }));
  };
  load();
  return load;
}

const adminEmptyRows = (rows, message, html) => rows.length ? html() : `<p class="admin-empty">${esc(message)}</p>`;

const ADMIN_TAB_VIEWS = {
  timeline(box, id, st) {
    st.kind ||= "all";
    box.innerHTML = '<div class="admin-paged"></div>';
    const el = $(".admin-paged", box);
    const kinds = { all: "전체", playback: "재생", download: "다운로드", transcript: "전사" };
    let reload;
    let refocus = false;
    el.addEventListener("click", (e) => {
      const k = e.target.closest("[data-kind]")?.dataset.kind;
      if (!k || k === st.kind) return;
      st.kind = k;
      st.offsets.timeline = 0;
      refocus = true;
      reload();
    });
    reload = adminPaged(el, `${adminUserPath(id)}/activities`, st, "timeline", (d) => {
      const all = Object.values(d.counts).reduce((a, b) => a + b, 0);
      const seg = `<div class="admin-seg" role="group" aria-label="활동 종류">${Object.entries(kinds).map(([k, label]) =>
        `<button class="btn small" type="button" data-kind="${k}" aria-pressed="${k === st.kind}">${label} ${adminNumber(k === "all" ? all : d.counts[k])}</button>`).join("")}</div>`;
      return seg + adminEmptyRows(d.activities, "기록된 활동이 없습니다.", () => adminTable([
        { label: "시각", render: (a) => adminTime(a.recorded_at), cls: "admin-nowrap" },
        { label: "종류", render: (a) => a.kind === "playback" ? adminJobTag(a) : adminTag(adminActivityLabel(a)) },
        { label: "과목 · 제목", render: (a) => adminCourseTitle(a.course_name, a.title) },
      ], d.activities, { caption: "전체 타임라인" }));
    }, {
      params: () => st.kind === "all" ? {} : { kind: st.kind },
      // 종류를 바꾸면 다시 그린 같은 버튼으로 포커스를 돌린다
      drawn: () => { if (refocus) $(`[data-kind="${st.kind}"]`, el)?.focus(); refocus = false; },
    });
  },

  courses(box, id, st) {
    adminPaged(box, `${adminUserPath(id)}/courses`, st, "courses", (d) => adminEmptyRows(d.items, "과목이 없습니다.", () => adminTable([
      { label: "과목", render: (c) => esc(c.name) },
      { label: "교수", render: (c) => esc(c.professors || "-") },
      { label: "학기", render: (c) => c.active ? adminTag("현재 학기", "ok") : adminTag("지난 학기") },
      { label: "자동 재생", render: (c) => c.auto_play ? "켜짐" : "꺼짐" },
      { label: "주차별 출석", render: (c) => attendanceHtml(c.attendance, "출석 기록 없음") },
    ], d.items, { caption: "과목과 출석" })));
  },

  todos(box, id, st) {
    box.innerHTML = `<section aria-labelledby="admin-todos-title"><h3 id="admin-todos-title">할 일</h3><div class="admin-paged" data-part="todos"></div></section>
      <section aria-labelledby="admin-assign-title"><h3 id="admin-assign-title">과제</h3><div class="admin-paged" data-part="assignments"></div></section>`;
    adminPaged($('[data-part="todos"]', box), `${adminUserPath(id)}/todos`, st, "todos", (d) => adminEmptyRows(d.items, "할 일이 없습니다.", () => adminTable([
      { label: "종류", render: (t) => esc(KIND[t.kind] || t.kind) },
      { label: "과목 · 제목", render: (t) => adminCourseTitle(t.course_name, t.title) },
      { label: "마감", render: (t) => adminWhen(t.deadline, "마감 없음"), cls: "admin-nowrap" },
      // active가 false면 동기화에서 사라졌다는 뜻일 뿐이라 완료로 쓰지 않는다
      { label: "상태", render: (t) => t.active ? adminTag("남아 있음") : adminTag("목록에서 사라짐(완료 또는 마감)") },
    ], d.items, { caption: "할 일" })));
    adminPaged($('[data-part="assignments"]', box), `${adminUserPath(id)}/assignments`, st, "assignments", (d) => adminEmptyRows(d.items, "과제가 없습니다.", () => adminTable([
      { label: "종류", render: (a) => esc(KIND[a.kind] || a.kind) },
      { label: "과목 · 제목", render: (a) => adminCourseTitle(a.course_name, a.title) },
      { label: "제출 상태", render: (a) => adminTag(ADMIN_SUBMISSION[a.submission_state] || a.submission_state, a.submission_state === "submitted" ? "ok" : "") },
      { label: "제출 시각", render: (a) => adminWhen(a.submitted_at), cls: "admin-nowrap" },
      { label: "마감", render: (a) => adminWhen(a.deadline, "마감 없음"), cls: "admin-nowrap" },
      { label: "미제출 여부", render: (a) => a.missing ? adminTag("마감 지나 미제출", "bad") : "-" },
    ], d.items, { caption: "과제" })));
  },

  playback(box, id, st) {
    st.status ??= ""; st.course ??= ""; st.days ??= "";
    const opt = (v, label, cur) => `<option value="${esc(v)}"${String(cur) === String(v) ? " selected" : ""}>${esc(label)}</option>`;
    box.innerHTML = `<form class="admin-filters" aria-label="재생 기록 조건">
        <label>상태<select name="status">${opt("", "전체", st.status)}${ADMIN_PLAYBACK_FILTER.map((k) => opt(k, ADMIN_PLAYBACK_STATUS[k], st.status)).join("")}</select></label>
        <label>과목<select name="course">${opt("", "전체", st.course)}</select></label>
        <label>기간<select name="days">${opt("", "전체 기간", st.days)}${ADMIN_PERIODS.map((d) => opt(d, `최근 ${d}일`, st.days)).join("")}</select></label>
      </form><div class="admin-paged"></div>`;
    const form = $("form", box);
    const courses = new Map();
    let reload;
    form.addEventListener("submit", (e) => e.preventDefault());
    form.addEventListener("change", () => {
      st.status = form.status.value; st.course = form.course.value; st.days = form.days.value;
      st.offsets.playback = 0;
      reload();
    });
    const draw = (d) => adminEmptyRows(d.items, "조건에 맞는 재생 기록이 없습니다.", () => d.items.map((b) => adminBatch(b, courses)).join(""));
    reload = adminPaged($(".admin-paged", box), `${adminUserPath(id)}/playback`, st, "playback", draw, { params: () => {
      const p = {};
      if (st.status) p.status = st.status;
      if (st.course) p.lms_course_id = st.course;
      if (st.days) p.days = st.days;
      return p;
    } });
    // 과목 필터와 묶음의 과목 이름용. 실패해도 재생 기록은 그대로 보인다.
    adminRequest(`${adminUserPath(id)}/courses?limit=100`, (d) => {
      d.items.forEach((c) => courses.set(c.lms_course_id, c.name));
      form.course.innerHTML = opt("", "전체", st.course) + d.items.map((c) => opt(c.lms_course_id, c.name, st.course)).join("");
      box.querySelectorAll(".admin-batch [data-course]").forEach((el) => { el.textContent = courses.get(Number(el.dataset.course)) || el.textContent; });
    }, "playback-courses").catch(() => {});
  },

  downloads(box, id, st) {
    adminPaged(box, `${adminUserPath(id)}/downloads`, st, "downloads", (d) => adminEmptyRows(d.items, "다운로드 기록이 없습니다.", () => adminTable([
      { label: "제목", render: (r) => esc(r.title) },
      { label: "종류", render: (r) => esc(ADMIN_DOWNLOAD_KIND[r.kind] || r.kind) },
      { label: "횟수", render: (r) => `${adminNumber(r.count)}회` },
      { label: "처음", render: (r) => adminTime(r.first_at), cls: "admin-nowrap" },
      { label: "마지막", render: (r) => adminTime(r.last_at), cls: "admin-nowrap" },
    ], d.items, { caption: "다운로드" })));
  },

  saved(box, id, st) {
    adminPaged(box, `${adminUserPath(id)}/saved-videos`, st, "saved", (d) => adminEmptyRows(d.items, "저장한 영상이 없습니다.", () => adminTable([
      { label: "영상", render: (v) => `${esc(v.title)}<div class="admin-sub">${esc(v.course_name)} · ${esc(v.module_title)}</div>` },
      { label: "파일명", render: (v) => esc(v.file_name) },
      { label: "길이 · 용량", render: (v) => [v.duration ? fmtDuration(v.duration) : "", fmtSize(v.size)].filter(Boolean).join(" · ") || "-" },
      // 주소 자체는 API가 주지 않는다. 있는지만 보여 준다.
      { label: "주소", render: (v) => `${v.has_media_url ? adminTag("주소 있음", "ok") : adminTag("주소 없음", "warn")}${v.resolve_error ? `<div class="admin-err">${esc(v.resolve_error)}</div>` : ""}` },
      { label: "확보 시각", render: (v) => v.resolved_at ? adminTime(v.resolved_at) : v.resolve_failed_at ? `실패 ${adminTime(v.resolve_failed_at)}` : "-", cls: "admin-nowrap" },
    ], d.items, { caption: "저장 영상" })));
  },

  transcripts(box, id, st) {
    adminPaged(box, `${adminUserPath(id)}/transcripts`, st, "transcripts", (d) => adminEmptyRows(d.items, "전사 기록이 없습니다.", () => adminTable([
      { label: "과목 · 제목", render: (t) => adminCourseTitle(t.course_name, t.title) },
      { label: "모델", render: (t) => esc(t.model) },
      { label: "길이", render: (t) => t.duration ? fmtDuration(t.duration) : "-" },
      { label: "생성", render: (t) => adminTime(t.created_at), cls: "admin-nowrap" },
      { label: "수정", render: (t) => adminTime(t.updated_at), cls: "admin-nowrap" },
    ], d.items, { caption: "전사" })));
  },

  notifications(box, id, st) {
    adminPaged(box, `${adminUserPath(id)}/notifications`, st, "notifications", (d) => `
      <h3>알림 채널</h3>${adminEmptyRows(d.channels, "등록한 알림 채널이 없습니다.", () => adminTable([
        { label: "종류", render: (c) => esc(ADMIN_CHANNEL[c.kind] || c.kind) },
        { label: "사용", render: (c) => c.enabled ? adminTag("사용 중", "ok") : adminTag("꺼짐") },
        { label: "등록일", render: (c) => adminDay(c.created_at) },
      ], d.channels, { caption: "알림 채널" }))}
      <h3>발송 기록 <span class="admin-count">${adminNumber(d.total)}건</span></h3>${adminEmptyRows(d.items, "발송 기록이 없습니다.", () => adminTable([
        { label: "알림 키", render: (n) => `<code>${esc(n.key)}</code>` },
        { label: "시각", render: (n) => adminTime(n.created_at), cls: "admin-nowrap" },
      ], d.items, { caption: "알림 발송 기록" }))}`);
  },
};

function adminBatch(b, courses) {
  const status = ADMIN_BATCH_STATUS[b.status] || { label: b.status, tone: "" };
  const course = b.lms_course_id != null ? ` · <span data-course="${esc(b.lms_course_id)}">${esc(courses.get(b.lms_course_id) || `과목 ${b.lms_course_id}`)}</span>` : "";
  const jobs = b.jobs.length ? adminTable([
    { label: "영상", render: (j) => `${esc(j.title)}${j.error ? `<div class="admin-err">${esc(j.error)}</div>` : ""}` },
    { label: "길이", render: (j) => fmtDuration(j.duration), cls: "admin-nowrap" },
    { label: "상태", render: (j) => adminJobTag(j) },
    { label: "시도", render: (j) => `${adminNumber(j.attempts)}회` },
    { label: "진행", render: (j) => `${fmtDuration(j.progress_seconds || 0)} / ${fmtDuration(j.duration)}`, cls: "admin-nowrap" },
    { label: "시작", render: (j) => adminWhen(j.started_at), cls: "admin-nowrap" },
    { label: "종료", render: (j) => adminWhen(j.finished_at), cls: "admin-nowrap" },
  ], b.jobs, { caption: `${ADMIN_BATCH_SCOPE[b.scope] || b.scope} 재생 묶음의 영상` }) : '<p class="admin-empty">묶음에 영상이 없습니다.</p>';
  return `<article class="admin-batch">
    <div class="admin-batch-head"><h3>${esc(ADMIN_BATCH_SCOPE[b.scope] || b.scope)} 재생${course}</h3>${adminTag(status.label, status.tone)}</div>
    <p class="admin-sub">완료 ${adminNumber(b.done_count)} · 실패 ${adminNumber(b.failed_count)} · 건너뜀 ${adminNumber(b.skipped_count)} / 전체 ${adminNumber(b.total)}
      · 생성 ${adminTime(b.created_at)} · 시작 ${adminWhen(b.started_at, "기록 없음")} · 종료 ${adminWhen(b.finished_at, "기록 없음")}</p>
    ${b.error ? `<p class="admin-err">${esc(b.error)}</p>` : ""}
    ${jobs}</article>`;
}
