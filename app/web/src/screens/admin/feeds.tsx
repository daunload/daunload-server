/* 관리자 활동·문제 탭. 두 탭은 같은 틀(학번 검색 + 종류 버튼 + 표 + 페이지 넘김)을 쓴다.
 * 기간은 현황과 같은 adminUi.days를 쓰고, 나머지 조건은 탭마다 adminUi에 남겨 상세에 다녀와도 유지한다. */
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { state } from "../../core";
import { setProblemCount } from "./index";
import {
  ADMIN_PROBLEM_KIND, EmptyText, JobTag, KindSeg, Loaded, Pager, ProblemTag, type Row, Table, Tag, Time, UserLink,
  activityLabel, adminNumber, adminUi, adminUserPath, courseTitle, problemError, problemTitle, useAdmin, useRefocusKind,
} from "./shared";

const LIMIT = 20;

interface FeedFilter { kind: string; student_id: string; offset: number; course?: string }

interface FeedConfig {
  f: FeedFilter; name: string; title: string; path: string; items: string; kinds: Record<string, string>;
  empty: [string, string];                  // [조건 없을 때, 조건 있을 때]
  extra?: () => ComponentChildren;          // 학번 옆의 추가 조건(지금 조건으로 그릴 때마다 만든다)
  params?: () => Record<string, string>;
  filtered?: () => boolean;                 // 학번 말고 걸린 조건이 있는지
  onStudent?: () => void;                   // 학번이 바뀐 뒤
  onChange?: (control: HTMLSelectElement) => void;  // 추가 조건이 바뀐 뒤
  table: (rows: Row[]) => ComponentChildren;
  loaded?: (d: Row, all: number) => void;
  again: number; period: number;
}

function Feed(cfg: FeedConfig) {
  const { f, name } = cfg;
  const nav = adminUi.nav;
  const [, redraw] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const q = new URLSearchParams({ days: String(adminUi.days), offset: String(f.offset), limit: String(LIMIT), ...cfg.params?.() });
  if (f.kind !== "all") q.set("kind", f.kind);
  if (f.student_id.trim()) q.set("student_id", f.student_id.trim());
  const [load, reload] = useAdmin(`${cfg.path}?${q}`, cfg.again);
  const refocus = useRefocusKind(listRef, f.kind, load.kind === "ok");

  /** 다른 조건을 바꿀 때도 입력 중인 학번을 함께 반영한다(기다리던 검색은 취소한다). */
  const reset = () => {
    clearTimeout(adminUi.searchTimer);
    const input = formRef.current?.elements.namedItem("student_id") as HTMLInputElement | null;
    if (input && input.value !== f.student_id) {
      f.student_id = input.value;
      cfg.onStudent?.();
    }
    f.offset = 0;
    redraw((n) => n + 1);
  };
  const firstPeriod = useRef(cfg.period);
  useEffect(() => { if (cfg.period !== firstPeriod.current) { firstPeriod.current = cfg.period; reset(); } }, [cfg.period]);

  const all = load.kind === "ok" ? Object.values(load.data.counts as Record<string, number>).reduce((a, b) => a + b, 0) : 0;
  useEffect(() => { if (load.kind === "ok") cfg.loaded?.(load.data, all); }, [load]);

  return (
    <>
      <form class="admin-filters" role="search" aria-label={`${cfg.title} 조건`} ref={formRef}
        onSubmit={(e) => { e.preventDefault(); reset(); }}
        onChange={(e) => { const t = e.target as HTMLSelectElement; if (t.name !== "student_id") { cfg.onChange?.(t); reset(); } }}>
        <label class="admin-search">학번<input class="input" type="search" name="student_id" maxLength={32} autocomplete="off" defaultValue={f.student_id}
          onInput={() => { clearTimeout(adminUi.searchTimer); adminUi.searchTimer = setTimeout(() => { if (nav === adminUi.nav) reset(); }, 300); }} /></label>
        {cfg.extra?.()}
      </form>
      <section class="card admin-box" aria-labelledby={`admin-${name}-title`}>
        <div class="admin-box-head"><h2 id={`admin-${name}-title`} tabIndex={-1} ref={titleRef}>{cfg.title} <span class="admin-count" role="status">
          {load.kind === "ok" ? `${adminNumber(load.data.total)}건` : ""}</span></h2></div>
        <div class="contents" ref={listRef}>
          <Loaded load={load} retry={reload} cls="admin-paged">
            {(d: Row) => {
              const rows = d[cfg.items];
              const narrowed = f.kind !== "all" || !!f.student_id.trim() || !!cfg.filtered?.();
              return (
                <>
                  <KindSeg kinds={cfg.kinds} counts={d.counts} current={f.kind} label="종류" onPick={(k) => { f.kind = k; refocus(); reset(); }} />
                  {rows.length ? cfg.table(rows) : <EmptyText>{cfg.empty[narrowed ? 1 : 0]}</EmptyText>}
                  {(d.total > LIMIT || f.offset > 0) && (
                    <Pager total={d.total} offset={f.offset} limit={LIMIT} onPage={(o) => { f.offset = o; redraw((n) => n + 1); titleRef.current?.focus(); }} />
                  )}
                </>
              );
            }}
          </Loaded>
        </div>
      </section>
    </>
  );
}

/* ---------- 활동 ---------- */

const ACTIVITY_KINDS = { all: "전체", playback: "재생", download: "다운로드", transcript: "전사" };

export function Activity({ again, period }: { again: number; period: number }) {
  adminUi.detail = null;
  return (
    <Feed f={adminUi.activity} name="activity" title="활동" path="/admin/activities" items="activities" kinds={ACTIVITY_KINDS}
      empty={["기간 안에 활동이 없습니다.", "조건에 맞는 활동이 없습니다."]} again={again} period={period}
      table={(rows) => (
        <Table caption="전체 사용자의 활동" cls="admin-activities admin-linked" rows={rows} columns={[
          { label: "시각", render: (a) => <Time iso={a.recorded_at} />, cls: "admin-nowrap" },
          { label: "사용자", render: (a) => <UserLink id={a.student_id} />, cls: "admin-nowrap" },
          { label: "종류", render: (a) => (a.kind === "playback" ? <JobTag job={a} /> : <Tag label={activityLabel(a)} />) },
          { label: "과목 · 제목", render: (a) => courseTitle(a.course_name, a.title) },
        ]} />
      )} />
  );
}

/* ---------- 문제 ---------- */

const PROBLEM_KINDS = { all: "전체", ...Object.fromEntries(Object.entries(ADMIN_PROBLEM_KIND).map(([k, v]) => [k, v.label])) };

/** 과목은 사람마다 따로 저장돼 있어 학번을 고른 뒤 그 사람의 과목에서 고른다. 없는 학번이면 과목 없이 둔다. */
function CourseFilter({ studentId }: { studentId: string }) {
  const f = adminUi.problems;
  const viewer = state.me?.student_id;
  const [load] = useAdmin(studentId ? `${adminUserPath(studentId)}/courses?limit=100` : null);
  const courses = studentId && load.kind === "ok" && state.me?.student_id === viewer ? load.data.items as Row[] : null;
  return (
    <>
      <label>과목<select class="select" name="course" disabled={!courses} aria-describedby="admin-course-hint" value={f.course}>
        <option value="">전체</option>
        {(courses || []).map((c) => <option key={c.lms_course_id} value={String(c.lms_course_id)}>{c.name}</option>)}
      </select></label>
      <p class="admin-hint" id="admin-course-hint" hidden={!!courses}>학번을 입력하면 그 사용자의 과목으로 거를 수 있습니다.</p>
    </>
  );
}

export function Problems({ again, period }: { again: number; period: number }) {
  adminUi.detail = null;
  const f = adminUi.problems;
  return (
    <Feed f={f} name="problems" title="문제" path="/admin/problems" items="problems" kinds={PROBLEM_KINDS}
      empty={["확인할 문제가 없습니다.", "조건에 맞는 문제가 없습니다."]} again={again} period={period}
      extra={() => <CourseFilter studentId={f.student_id.trim()} />}
      params={(): Record<string, string> => (f.course && f.student_id.trim() ? { lms_course_id: f.course } : {})}
      filtered={() => !!f.course}
      onStudent={() => { f.course = ""; }}
      onChange={(control) => { if (control.name === "course") f.course = control.value; }}
      // 탭 이름의 건수는 학번·과목 조건이 없을 때의 합이다(종류 버튼은 건수를 바꾸지 않는다)
      loaded={(_d, all) => { if (!f.student_id.trim() && !f.course) setProblemCount(all); }}
      table={(rows) => (
        <Table caption="확인이 필요한 문제" cls="admin-problem-list admin-linked" rows={rows} columns={[
          { label: "시각", render: (p) => <Time iso={p.occurred_at} />, cls: "admin-nowrap" },
          { label: "사용자", render: (p) => <UserLink id={p.student_id} />, cls: "admin-nowrap" },
          { label: "과목 · 영상", render: problemTitle },
          { label: "종류", render: (p) => <ProblemTag p={p} /> },
          { label: "오류 내용", render: problemError },
        ]} />
      )} />
  );
}
