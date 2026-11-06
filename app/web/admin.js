"use strict";

/* ---------------- 관리자 ----------------
 * app.js 다음에 불러온다(state, api, esc, fmtDate, navigate, viewHash를 쓴다).
 * 탭 상태는 주소에 둔다: #admin/overview, #admin/users, #admin/users/{학번}, #admin/activity, #admin/problems
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
const ADMIN_PROBLEM_KIND = {
  playback: { label: "재생 실패", tone: "bad" },
  batch: { label: "재생 묶음 오류", tone: "bad" },
  lms: { label: "LMS 문제", tone: "warn" },
  resolve: { label: "영상 주소 확보 실패", tone: "warn" },
};
/** nav: 화면을 그릴 때마다 오른다. seq: 같은 자리(key)의 요청 번호. 둘 중 하나라도 바뀌면 늦은 응답을 버린다. */
const adminUi = { nav: 0, seq: {}, days: 7, metric: "playback_done", problemCount: null, problemCountFor: null };

const adminNumber = (n) => Number(n || 0).toLocaleString("ko-KR");
const adminUserLink = (id) => `<a href="#admin/users/${encodeURIComponent(id)}">${esc(id)}</a>`;
const adminTime = (iso) => iso ? `<time datetime="${esc(iso)}">${esc(fmtDate(new Date(iso)))}</time>` : "시각 기록 없음";

/**
 * 관리자 API를 부른다. 그 사이 화면이 바뀌었거나 같은 key로 새 요청을 보냈으면 응답을 버린다(render를 부르지 않고 오류도 던지지 않는다).
 * 아직 유효한 요청이 실패하면 오류를 던지므로 호출한 쪽이 adminState(el, "error")로 보여 준다.
 */
async function adminRequest(path, render, key = "main") {
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
function adminState(el, kind, { message = "", retry, rows = 3 } = {}) {
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
 * 표 HTML. columns: [{ label, render(row) → 이미 이스케이프한 HTML, cls? }].
 * 760px 이하에서는 각 칸이 data-label을 머리글로 단 카드가 된다.
 */
function adminTable(columns, rows, { caption = "" } = {}) {
  const head = columns.map((c) => `<th scope="col"${c.cls ? ` class="${c.cls}"` : ""}>${esc(c.label)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((c) =>
    `<td data-label="${esc(c.label)}"${c.cls ? ` class="${c.cls}"` : ""}><div>${c.render(row)}</div></td>`).join("")}</tr>`).join("");
  return `<div class="admin-table-wrap"><table class="admin-table">${caption ? `<caption class="sr-only">${esc(caption)}</caption>` : ""}
    <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/** 이전·다음 페이지 버튼. onPage(새 offset)를 부른다. DOM 요소를 돌려준다. */
function adminPager(total, offset, limit, onPage) {
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

function adminTabLabel(tab) {
  // 로그아웃 후 다른 관리자가 들어와도 앞 사람이 본 숫자를 보이지 않게 학번으로 묶는다
  if (tab.key !== "problems" || adminUi.problemCount == null || adminUi.problemCountFor !== state.me?.student_id) return tab.label;
  return `${tab.label} <span class="admin-count">${adminNumber(adminUi.problemCount)}건</span>`;
}

/** 관리자 화면 뼈대. path는 state.adminPath(`["users", "20201234"]` 등). */
function viewAdmin(path = state.adminPath) {
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
    <div class="view-head"><h1>사용자 관리</h1><div class="admin-actions" id="admin-actions"></div></div>
    <nav class="admin-tabs" aria-label="관리자 메뉴">${ADMIN_TABS.map((t) =>
      `<a class="admin-tab" href="#admin/${t.key}" data-admin-tab="${t.key}"${t.key === tab ? ' aria-current="page"' : ""}>${adminTabLabel(t)}</a>`).join("")}</nav>
    <div class="admin-body" id="admin-body"></div></div>`;
  $("#view .admin").addEventListener("click", (e) => {
    const link = e.target.closest('a[href^="#admin/"]');
    if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const isTab = link.hasAttribute("data-admin-tab");
    navigate(link.getAttribute("href"), true, { focus: !isTab });
    if (isTab) $("#view .admin-tabs [aria-current='page']")?.focus();
  });
  const body = $("#admin-body");
  if (tab === "overview") adminOverview(body);
  else adminState(body, "empty", { message: "이 탭은 준비 중입니다." });
}

/* ---------- 현황 ---------- */

function adminOverview(body) {
  $("#admin-actions").innerHTML = `<label class="admin-period">기간
      <select id="admin-days">${ADMIN_PERIODS.map((d) => `<option value="${d}"${d === adminUi.days ? " selected" : ""}>최근 ${d}일</option>`).join("")}</select></label>
    <button class="btn small" type="button" id="admin-reload">${icon("refresh")}다시 불러오기</button>`;
  const load = () => {
    adminState(body, "loading", { rows: 4 });
    adminRequest(`/admin/overview?days=${adminUi.days}`, (data) => drawOverview(body, data), "overview")
      .catch((err) => adminState(body, "error", { message: err.message, retry: load }));
  };
  $("#admin-days").addEventListener("change", (e) => { adminUi.days = Number(e.target.value); load(); });
  $("#admin-reload").addEventListener("click", load);
  load();
}

const ADMIN_PLAYBACK_STATUS = {
  queued: "재생 대기", playing: "재생 중", done: "재생 완료", failed: "재생 실패",
  skipped: "이미 완료", locked: "재생 잠김", cancelled: "재생 취소",
};

function adminActivityLabel(item) {
  if (item.kind === "playback") return ADMIN_PLAYBACK_STATUS[item.status] || item.status;
  if (item.kind === "download") return `다운로드 ${adminNumber(item.count)}회`;
  if (item.kind === "transcript") return "전사 저장";
  return item.kind;
}

function drawOverview(body, d) {
  body.removeAttribute("aria-busy");
  adminUi.problemCount = d.problem_count;
  adminUi.problemCountFor = state.me.student_id;
  const problemTab = $('#view .admin-tabs [data-admin-tab="problems"]');
  if (problemTab) problemTab.innerHTML = adminTabLabel(ADMIN_TABS[3]);
  const rate = d.failure_rate == null ? "-" : `${(d.failure_rate * 100).toFixed(1)}%`;
  const kpi = (label, value, sub, cls = "") =>
    `<div class="panel admin-kpi${cls}"><span class="admin-kpi-label">${label}</span><strong>${value}</strong><span class="admin-kpi-sub">${sub}</span></div>`;
  const problems = d.recent_problems.length ? adminTable([
    { label: "시각", render: (p) => adminTime(p.occurred_at), cls: "admin-nowrap" },
    { label: "사용자", render: (p) => adminUserLink(p.student_id) },
    { label: "과목 · 영상", render: (p) => esc(p.course_name ? `${p.course_name} · ${p.title}` : p.title) },
    { label: "오류", render: (p) => {
      const kind = ADMIN_PROBLEM_KIND[p.kind] || { label: p.kind, tone: "warn" };
      return `<span class="admin-tag ${kind.tone}">${esc(kind.label)}</span> ${esc(p.error || "오류 내용 없음")}${p.not_run ? ` · 미실행 ${adminNumber(p.not_run)}개` : ""}`;
    } },
  ], d.recent_problems, { caption: "확인이 필요한 문제" }) : '<p class="admin-empty">확인할 문제가 없습니다.</p>';
  body.innerHTML = `
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
