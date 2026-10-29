"use strict";

/* ---------------- 캘린더 ----------------
 * 월 달력에 해야 할 일(제출 전 과제·퀴즈, 들어야 할 강의)과 완료한 과제를 마감일에 표시한다.
 * 칸에는 상태 점만 두고, 날짜를 누르면 그날 항목을 아래에 보여 준다.
 * app.js보다 먼저 불러온다. app.js의 전역(state, api, esc, fmtTime, fmtDate, WEEKDAY, timeLeft, KIND,
 * safeUrl, needCred, startOfDay)은 함수가 실행될 때만 쓴다.
 */

const cal = { month: null, selected: null, assignments: null, error: null };
const CAL_STATUS = { todo: "해야 할 일", done: "완료", missed: "마감 지남" };

const dayKey = (d) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;

async function viewCalendar() {
  if (needCred()) return;
  const now = new Date();
  cal.month = cal.month || new Date(now.getFullYear(), now.getMonth(), 1);
  cal.selected = cal.selected || startOfDay(now);
  drawCalendar();
  try {
    cal.assignments = await api("/assignments?status=all");
    cal.error = null;
  } catch (err) {
    cal.error = err.message;
  }
  if (state.view === "calendar") drawCalendar();
}

/** 달력에 놓을 항목. 과제는 /assignments, 강의는 할 일 목록(state.todos)의 영상에서 가져온다. */
function calendarItems() {
  const items = [];
  for (const a of cal.assignments || []) {
    let status;
    let when = a.deadline;
    if (a.status === "submitted") { status = "done"; when = a.deadline || a.submitted_at; }
    else if (a.status === "overdue") status = "missed";
    else if (["open", "locked", "upcoming"].includes(a.status)) status = "todo";
    else continue; // 면제·상태 불명·마감 없음은 날짜에 놓을 수 없다
    if (!when) continue;
    items.push({ kind: a.kind === "quiz" ? "quiz" : "assignment", title: a.title, course: a.course_name, date: new Date(when),
      status, url: a.url, submittedAt: a.submitted_at, unlockAt: a.status === "upcoming" ? a.unlock_at : null });
  }
  for (const t of state.todos.filter((x) => x.kind === "video")) {
    items.push({ kind: "video", title: t.title, course: t.course_name, date: new Date(t.deadline), status: "todo", sub: t.module_title });
  }
  return items;
}

function drawCalendar() {
  const view = document.getElementById("view");
  if (!view || state.view !== "calendar") return;
  const month = cal.month;
  const items = calendarItems();
  const byDay = new Map();
  for (const it of items) {
    const k = dayKey(it.date);
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k).push(it);
  }

  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const offset = first.getDay(); // 일요일 시작
  const rows = Math.ceil((offset + days) / 7);
  const todayKey = dayKey(new Date());
  const selectedKey = dayKey(cal.selected);
  const cells = [];
  for (let i = 0; i < rows * 7; i++) {
    const d = new Date(month.getFullYear(), month.getMonth(), 1 - offset + i);
    const k = dayKey(d);
    const list = byDay.get(k) || [];
    const count = (s) => list.filter((x) => x.status === s).length;
    const dots = ["todo", "missed", "done"].filter((s) => count(s)).map((s) => `<i class="${s}"></i>`).join("");
    const label = `${d.getMonth() + 1}월 ${d.getDate()}일${list.length ? `, ${["todo", "missed", "done"].filter((s) => count(s)).map((s) => `${CAL_STATUS[s]} ${count(s)}개`).join(", ")}` : ""}`;
    const cls = ["cal-day", d.getMonth() !== month.getMonth() ? "out" : "", k === todayKey ? "today" : "", k === selectedKey ? "selected" : "",
      d.getDay() === 0 ? "sun" : ""].filter(Boolean).join(" ");
    cells.push(`<button class="${cls}" data-day="${d.getFullYear()}-${d.getMonth()}-${d.getDate()}" aria-label="${label}" aria-pressed="${k === selectedKey}">
      <span class="cal-num">${d.getDate()}</span><span class="cal-dots">${dots}</span></button>`);
  }

  const inMonth = items.filter((x) => x.date.getFullYear() === month.getFullYear() && x.date.getMonth() === month.getMonth());
  const sum = (s) => inMonth.filter((x) => x.status === s).length;
  const summary = cal.error
    ? `<p class="cal-summary bad">과제 정보를 불러오지 못했어요. 강의만 표시합니다.</p>`
    : !cal.assignments ? `<p class="cal-summary">불러오는 중</p>`
    : `<p class="cal-summary"><span><i class="todo"></i>해야 할 일 ${sum("todo")}</span><span><i class="done"></i>완료 ${sum("done")}</span>${sum("missed") ? `<span><i class="missed"></i>마감 지남 ${sum("missed")}</span>` : ""}</p>`;

  const sel = cal.selected;
  const order = { todo: 0, missed: 1, done: 2 };
  const dayItems = (byDay.get(selectedKey) || []).sort((a, b) => order[a.status] - order[b.status] || a.date - b.date);

  view.innerHTML = `
    <section class="cal panel" aria-label="캘린더">
      <div class="cal-head">
        <button class="btn ghost small cal-nav" id="cal-prev" aria-label="이전 달">‹</button>
        <h2 id="cal-title">${month.getFullYear()}년 ${month.getMonth() + 1}월</h2>
        <button class="btn ghost small cal-nav" id="cal-next" aria-label="다음 달">›</button>
        <button class="btn small cal-today" id="cal-today">오늘</button>
      </div>
      ${summary}
      <div class="cal-grid">
        ${WEEKDAY.map((w, i) => `<span class="cal-wd${i === 0 ? " sun" : ""}" aria-hidden="true">${w}</span>`).join("")}
        ${cells.join("")}
      </div>
    </section>
    <section class="panel cal-list" aria-labelledby="cal-day-title">
      <h3 id="cal-day-title">${sel.getMonth() + 1}월 ${sel.getDate()}일 ${WEEKDAY[sel.getDay()]}요일</h3>
      ${dayItems.length ? `<ul>${dayItems.map(calItem).join("")}</ul>` : `<p class="cal-empty">이날은 할 일이 없어요.</p>`}
    </section>`;

  const move = (n) => { cal.month = new Date(month.getFullYear(), month.getMonth() + n, 1); drawCalendar(); };
  document.getElementById("cal-prev").addEventListener("click", () => move(-1));
  document.getElementById("cal-next").addEventListener("click", () => move(1));
  document.getElementById("cal-today").addEventListener("click", () => {
    const now = new Date();
    cal.month = new Date(now.getFullYear(), now.getMonth(), 1);
    cal.selected = startOfDay(now);
    drawCalendar();
  });
  view.querySelectorAll(".cal-day").forEach((b) => b.addEventListener("click", () => {
    const [y, m, d] = b.dataset.day.split("-").map(Number);
    cal.selected = new Date(y, m, d);
    if (m !== month.getMonth() || y !== month.getFullYear()) cal.month = new Date(y, m, 1);
    drawCalendar();
    document.querySelector(`.cal-day[data-day="${y}-${m}-${d}"]`)?.focus();
  }));
}

function calItem(it) {
  const url = safeUrl(it.url);
  let side;
  if (it.status === "done") {
    side = `<span class="cal-status done">완료</span>${it.submittedAt ? `<span class="cal-when">${fmtDate(new Date(it.submittedAt))} 제출</span>` : ""}`;
  } else if (it.status === "missed") {
    side = `<span class="cal-status missed">마감 지남</span><span class="cal-when">${fmtTime(it.date)} 마감</span>`;
  } else if (it.unlockAt) {
    side = `<span class="cal-status">열리기 전</span><span class="cal-when">${fmtDate(new Date(it.unlockAt))}에 열림</span>`;
  } else {
    const left = timeLeft(it.date);
    side = `<span class="cal-status left ${left.cls}">${left.text}</span><span class="cal-when">${fmtTime(it.date)} 마감</span>`;
  }
  const title = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(it.title)}</a>` : esc(it.title);
  const kindName = { video: "강의", assignment: "과제", quiz: "퀴즈" }[it.kind];
  return `<li class="cal-item${it.status === "done" ? " is-done" : ""}">
    <span class="kind ${it.kind}">${kindName}</span>
    <div class="cal-item-main"><p class="task-title">${title}</p><p class="task-sub">${esc(it.course)}${it.sub ? ` · ${esc(it.sub)}` : ""}</p></div>
    <div class="cal-item-side">${side}</div></li>`;
}
