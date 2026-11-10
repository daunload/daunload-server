import { $, esc, state } from "./core";
import { ADMIN_PROBLEM_KIND, adminActivityLabel, adminCourseTitle, adminJobTag, adminNumber, adminPager, adminPeriodActions, adminProblemError, adminProblemTag, adminProblemTitle, adminRequest, adminSetProblemCount, adminState, adminTable, adminTag, adminTime, adminUi, adminUserLink, adminUserPath } from "./admin";

/* ---------------- 관리자: 활동과 문제 탭 ----------------
 * admin.js 다음에 불러온다(adminUi, adminRequest, adminState, adminTable, adminPager, adminPeriodActions 등을 쓴다).
 * 기간은 현황과 같은 adminUi.days를 쓰고, 나머지 조건은 탭마다 adminUi에 남겨 상세에 다녀와도 유지한다.
 */

const ADMIN_FEED_LIMIT = 20;
const ADMIN_ACTIVITY_KINDS = { all: "전체", playback: "재생", download: "다운로드", transcript: "전사" };

/**
 * 활동·문제 탭의 공통 틀: 학번 검색(+ extra 조건), 종류 버튼(건수), 표, 페이지 이동.
 * cfg: { f: 탭 조건, key, title, path, kinds: {키: 이름}, items: 응답의 목록 이름, extra: 조건 HTML,
 *        params(): 추가 쿼리, table(rows): 표 HTML, empty: [조건 없을 때, 조건 있을 때] 문구,
 *        filtered(): 학번 말고 걸린 조건이 있는지, onStudent(): 학번이 바뀐 뒤, onChange(control): extra 조건이 바뀐 뒤,
 *        loaded(d, 종류 합): 그린 뒤 }
 * { form, load }를 돌려준다.
 */
function adminFeed(body, cfg) {
  const { f, key } = cfg;
  const nav = adminUi.nav;
  body.innerHTML = `
    <form class="admin-filters" role="search" aria-label="${esc(cfg.title)} 조건">
      <label class="admin-search">학번<input type="search" name="student_id" maxlength="32" autocomplete="off" value="${esc(f.student_id)}"></label>
      ${cfg.extra || ""}
    </form>
    <section class="panel admin-box" aria-labelledby="admin-${key}-title">
      <div class="admin-box-head"><h2 id="admin-${key}-title" tabindex="-1">${esc(cfg.title)} <span class="admin-count" role="status"></span></h2></div>
      <div class="admin-paged"></div>
    </section>`;
  const form = $(".admin-filters", body);
  const el = $(".admin-paged", body);
  const title = $(`#admin-${key}-title`, body);
  let refocus = false;
  const load = () => {
    const q = new URLSearchParams({ days: adminUi.days, offset: f.offset, limit: ADMIN_FEED_LIMIT, ...cfg.params?.() });
    if (f.kind !== "all") q.set("kind", f.kind);
    if (f.student_id.trim()) q.set("student_id", f.student_id.trim());
    adminState(el, "loading");
    adminRequest(`${cfg.path}?${q}`, (d) => {
      el.removeAttribute("aria-busy");
      $(".admin-count", title).textContent = `${adminNumber(d.total)}건`;
      const all = Object.values(d.counts).reduce((a, b) => a + b, 0);
      const seg = `<div class="admin-seg" role="group" aria-label="종류">${Object.entries(cfg.kinds).map(([k, label]) =>
        `<button class="btn small" type="button" data-kind="${k}" aria-pressed="${k === f.kind}">${esc(label)} ${adminNumber(k === "all" ? all : d.counts[k] || 0)}</button>`).join("")}</div>`;
      const rows = d[cfg.items];
      const narrowed = f.kind !== "all" || !!f.student_id.trim() || !!cfg.filtered?.();
      el.innerHTML = seg + (rows.length ? cfg.table(rows) : `<p class="admin-empty">${esc(cfg.empty[narrowed ? 1 : 0])}</p>`);
      if (d.total > ADMIN_FEED_LIMIT || f.offset) {
        el.append(adminPager(d.total, f.offset, ADMIN_FEED_LIMIT, (o) => { f.offset = o; load(); title.focus(); }));
      }
      // 종류를 바꾸면 다시 그린 같은 버튼으로 포커스를 돌린다
      if (refocus) $(`[data-kind="${f.kind}"]`, el)?.focus();
      refocus = false;
      cfg.loaded?.(d, all);
    }, key).catch((err) => adminState(el, "error", { message: err.message, retry: load }));
  };
  // 다른 조건을 바꿀 때도 입력 중인 학번을 함께 반영한다(기다리던 검색은 취소한다)
  const reset = () => {
    clearTimeout(adminUi.searchTimer);
    if (form.student_id.value !== f.student_id) {
      f.student_id = form.student_id.value;
      cfg.onStudent?.();
    }
    f.offset = 0;
    load();
  };
  el.addEventListener("click", (e) => {
    const k = e.target.closest("[data-kind]")?.dataset.kind;
    if (!k || k === f.kind) return;
    f.kind = k;
    refocus = true;
    reset();
  });
  form.addEventListener("submit", (e) => { e.preventDefault(); reset(); });
  form.addEventListener("change", (e) => { if (e.target.name !== "student_id") { cfg.onChange?.(e.target); reset(); } });
  form.student_id.addEventListener("input", () => {
    clearTimeout(adminUi.searchTimer);
    adminUi.searchTimer = setTimeout(() => { if (nav === adminUi.nav) reset(); }, 300);
  });
  adminPeriodActions(reset, load);
  load();
  return { form, load };
}

/* ---------- 활동 ---------- */

export function adminActivity(body) {
  adminUi.detail = null;
  adminFeed(body, {
    f: adminUi.activity, key: "activity", title: "활동", path: "/admin/activities", items: "activities",
    kinds: ADMIN_ACTIVITY_KINDS, empty: ["기간 안에 활동이 없습니다.", "조건에 맞는 활동이 없습니다."],
    table: (rows) => adminTable([
      { label: "시각", render: (a) => adminTime(a.recorded_at), cls: "admin-nowrap" },
      { label: "사용자", render: (a) => adminUserLink(a.student_id), cls: "admin-nowrap" },
      { label: "종류", render: (a) => a.kind === "playback" ? adminJobTag(a) : adminTag(adminActivityLabel(a)) },
      { label: "과목 · 제목", render: (a) => adminCourseTitle(a.course_name, a.title) },
    ], rows, { caption: "전체 사용자의 활동", cls: "admin-activities admin-linked" }),
  });
}

/* ---------- 문제 ---------- */

export function adminProblems(body) {
  adminUi.detail = null;
  const f = adminUi.problems;
  const kinds = { all: "전체", ...Object.fromEntries(Object.entries(ADMIN_PROBLEM_KIND).map(([k, v]) => [k, v.label])) };
  const viewer = state.me.student_id;
  const { form } = adminFeed(body, {
    f, key: "problems", title: "문제", path: "/admin/problems", items: "problems", kinds,
    empty: ["확인할 문제가 없습니다.", "조건에 맞는 문제가 없습니다."],
    // 과목은 사람마다 따로 저장돼 있어 학번을 고른 뒤 그 사람의 과목에서 고른다
    extra: `<label>과목<select name="course" disabled aria-describedby="admin-course-hint"></select></label>
      <p class="admin-hint" id="admin-course-hint">학번을 입력하면 그 사용자의 과목으로 거를 수 있습니다.</p>`,
    params: () => f.course && f.student_id.trim() ? { lms_course_id: f.course } : {},
    filtered: () => !!f.course,
    onStudent: () => { f.course = ""; loadCourses(); },
    onChange: (control) => { if (control.name === "course") f.course = control.value; },
    table: (rows) => adminTable([
      { label: "시각", render: (p) => adminTime(p.occurred_at), cls: "admin-nowrap" },
      { label: "사용자", render: (p) => adminUserLink(p.student_id), cls: "admin-nowrap" },
      { label: "과목 · 영상", render: adminProblemTitle },
      { label: "종류", render: adminProblemTag },
      { label: "오류 내용", render: adminProblemError },
    ], rows, { caption: "확인이 필요한 문제", cls: "admin-problem-list admin-linked" }),
    // 탭 이름의 건수는 학번·과목 조건이 없을 때의 합이다(종류 버튼은 건수를 바꾸지 않는다)
    loaded: (d, all) => { if (!f.student_id.trim() && !f.course) adminSetProblemCount(all); },
  });
  const select = form.course;
  const fill = (courses) => {
    select.disabled = !courses;
    $("#admin-course-hint").hidden = !!courses;
    select.innerHTML = `<option value="">전체</option>${(courses || []).map((c) =>
      `<option value="${esc(c.lms_course_id)}"${String(c.lms_course_id) === f.course ? " selected" : ""}>${esc(c.name)}</option>`).join("")}`;
  };
  function loadCourses() {
    fill(null);
    const id = f.student_id.trim();
    if (!id) return;
    adminRequest(`${adminUserPath(id)}/courses?limit=100`, (d) => {
      if (state.me.student_id === viewer && f.student_id.trim() === id) fill(d.items);
    }, "problem-courses").catch(() => {});  // 없는 학번이면 과목 없이 둔다
  }
  loadCourses();
}
