/* 관리자 저장 영상 탭: 동기화 때 받아 둔 다운로드용 영상(saved_video_items)과 그 영상에 연결된 사용자(saved_videos).
 * #admin/saved: 과목 목록(과목별 영상 수·주소 상태·사용자 수). #admin/saved/{과목 ID}: 그 과목의 영상을 주차별로 묶어 LMS 순서로.
 * 학번·검색 조건은 두 화면이 함께 쓴다(adminUi.saved). 학번이 있으면 그 사용자에게 연결된 영상만 센다. */
import type { ComponentChildren } from "preact";
import { useRef, useState } from "preact/hooks";
import { fmtDuration, fmtSize } from "../../core";
import {
  adminNumber, adminUi, BackIcon, EmptyText, KindSeg, Loaded, Pager, type Row, Table, Tag, Time, UserLink, useAdmin, useRefocusKind,
} from "./shared";

const COURSE_LIMIT = 100;  // 한 과목의 영상은 대개 한 페이지에 다 들어간다
const STATUS: Record<string, string> = { all: "전체", saved: "주소 있음", pending: "확보 전", failed: "확보 실패" };

const coursePath = (id: number | string) => `#admin/saved/${encodeURIComponent(String(id))}`;

/** 학번·검색 조건(두 화면 공통). 바꾸면 첫 페이지부터 다시 부른다. */
function Filters({ onApply }: { onApply: () => void }) {
  const f = adminUi.saved;
  const nav = adminUi.nav;
  const formRef = useRef<HTMLFormElement>(null);
  const apply = () => {
    clearTimeout(adminUi.searchTimer);
    const field = (name: string) => formRef.current?.elements.namedItem(name) as HTMLInputElement | null;
    f.q = field("q")?.value ?? f.q;
    f.student_id = field("student_id")?.value ?? f.student_id;
    f.offset = 0;
    onApply();
  };
  const search = () => {
    clearTimeout(adminUi.searchTimer);
    adminUi.searchTimer = setTimeout(() => { if (nav === adminUi.nav) apply(); }, 300);
  };
  return (
    <form class="admin-filters" role="search" aria-label="저장 영상 찾기" ref={formRef} onSubmit={(e) => { e.preventDefault(); apply(); }}>
      <label class="admin-search">학번<input class="input" type="search" name="student_id" maxLength={32} autocomplete="off" defaultValue={f.student_id} onInput={search} /></label>
      <label class="admin-search">제목·과목 검색<input class="input" type="search" name="q" maxLength={100} autocomplete="off" defaultValue={f.q} onInput={search} /></label>
    </form>
  );
}

function filterParams(): URLSearchParams {
  const f = adminUi.saved;
  const params = new URLSearchParams();
  if (f.q.trim()) params.set("q", f.q.trim());
  if (f.student_id.trim()) params.set("student_id", f.student_id.trim());
  return params;
}

const narrowed = () => !!(adminUi.saved.q.trim() || adminUi.saved.student_id.trim());

function Summary({ s }: { s: Row }) {
  return (
    <p class="text-sm text-ink-2">
      영상 {adminNumber(s.items)}개 · 사용자 {adminNumber(s.users)}명 · 사용자 연결 {adminNumber(s.links)}개
      {s.items > s.videos && <> · 분반 등으로 같은 영상(content_id)이 {adminNumber(s.items - s.videos)}개 더 있어요</>}
    </p>
  );
}

/** 주소 상태 건수(0이면 빼고 보여 준다). */
function StatusCounts({ c }: { c: Row }) {
  const parts: ComponentChildren[] = [];
  if (c.saved) parts.push(<Tag key="s" label={`주소 있음 ${adminNumber(c.saved)}`} tone="ok" />);
  if (c.pending) parts.push(<Tag key="p" label={`확보 전 ${adminNumber(c.pending)}`} />);
  if (c.failed) parts.push(<Tag key="f" label={`확보 실패 ${adminNumber(c.failed)}`} tone="warn" />);
  return <div class="flex flex-wrap gap-1">{parts}</div>;
}

/* ---------- 과목 목록 ---------- */

function CourseList({ again }: { again: number }) {
  const [, redraw] = useState(0);
  const [load, reload] = useAdmin(`/admin/saved-videos/courses?${filterParams()}`, again);
  // 과목 이름 링크가 실제 컨트롤이고, 줄의 다른 곳을 눌러도 그 링크를 누른 것으로 친다
  const rowClick = (e: MouseEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest("a, button") || String(getSelection())) return;
    target.closest("tbody tr")?.querySelector<HTMLAnchorElement>('a[href^="#admin/saved/"]')?.click();
  };
  return (
    <>
      <Filters onApply={() => redraw((n) => n + 1)} />
      <section class="card admin-box" aria-labelledby="admin-saved-title">
        <div class="admin-box-head"><h2 id="admin-saved-title" tabIndex={-1}>저장 영상 · 과목별 <span class="admin-count" role="status">
          {load.kind === "ok" ? `${adminNumber(load.data.courses.length)}과목` : ""}</span></h2></div>
        <Loaded load={load} retry={reload} cls="admin-paged">
          {(d: Row) => (
            <>
              <Summary s={d.summary} />
              {d.courses.length ? (
                <div onClick={rowClick}>
                  <Table caption="과목별 저장 영상" cls="admin-saved-courses admin-linked" rows={d.courses} columns={[
                    { label: "과목", render: (c) => <a href={coursePath(c.lms_course_id)}>{c.course_name}</a> },
                    { label: "영상", render: (c) => `${adminNumber(c.items)}개`, cls: "admin-nowrap" },
                    { label: "주소 상태", render: (c) => <StatusCounts c={c} /> },
                    { label: "사용자", render: (c) => `${adminNumber(c.users)}명`, cls: "admin-nowrap" },
                    { label: "마지막 저장", render: (c) => <Time iso={c.last_at} />, cls: "admin-nowrap" },
                  ]} />
                </div>
              ) : <EmptyText>{narrowed() ? "조건에 맞는 저장 영상이 없습니다." : "저장된 영상이 없습니다."}</EmptyText>}
            </>
          )}
        </Loaded>
      </section>
    </>
  );
}

/* ---------- 한 과목: 주차별 ---------- */

function Holders({ v }: { v: Row }) {
  const ids: string[] = v.students || [];
  if (!v.holders) return <>연결 없음</>;
  return (
    <>
      <span class="admin-nowrap">{adminNumber(v.holders)}명</span>
      <div class="admin-sub">{ids.map((id, i) => <span key={id}>{i > 0 && ", "}<UserLink id={id} /></span>)}{v.holders > ids.length && ` 외 ${adminNumber(v.holders - ids.length)}명`}</div>
    </>
  );
}

function StatusCell({ v }: { v: Row }) {
  if (v.media_url) return <><Tag label="주소 있음" tone="ok" /><code class="mt-1 block wrap-anywhere text-xs">{v.media_url}</code></>;
  if (v.resolve_error) return <><Tag label="확보 실패" tone="warn" /><div class="admin-err">{v.resolve_error}</div></>;
  return <Tag label="확보 전" />;
}

/** 이어진 같은 주차끼리 묶는다(서버가 주차 → 항목 순서로 준다). */
function byWeek(items: Row[]): { title: string; items: Row[] }[] {
  const groups: { title: string; items: Row[] }[] = [];
  for (const v of items) {
    const last = groups[groups.length - 1];
    if (last && last.title === v.module_title) last.items.push(v);
    else groups.push({ title: v.module_title, items: [v] });
  }
  return groups;
}

function CourseVideos({ courseId, again }: { courseId: string; again: number }) {
  const f = adminUi.saved;
  if (f.course !== courseId) { f.course = courseId; f.status = "all"; f.offset = 0; }  // 다른 과목으로 오면 상태·페이지는 처음부터
  const [, redraw] = useState(0);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const params = filterParams();
  params.set("lms_course_id", courseId);
  params.set("offset", String(f.offset));
  params.set("limit", String(COURSE_LIMIT));
  if (f.status !== "all") params.set("status", f.status);
  const [load, reload] = useAdmin(/^\d+$/.test(courseId) ? `/admin/saved-videos?${params}` : null, again);
  const refocus = useRefocusKind(listRef, f.status, load.kind === "ok");
  const name = load.kind === "ok" && load.data.items.length ? load.data.items[0].course_name : `과목 ${courseId}`;

  if (!/^\d+$/.test(courseId)) {
    return <div class="card admin-box" role="alert"><p><strong>과목을 찾을 수 없습니다.</strong></p><a class="admin-more" href="#admin/saved">과목 목록으로</a></div>;
  }
  return (
    <>
      <a class="admin-back" href="#admin/saved"><BackIcon />과목 목록</a>
      <Filters onApply={() => redraw((n) => n + 1)} />
      <section class="card admin-box" aria-labelledby="admin-saved-course-title">
        <div class="admin-box-head"><h2 id="admin-saved-course-title" tabIndex={-1} ref={titleRef}>{name} <span class="admin-count" role="status">
          {load.kind === "ok" ? `${adminNumber(load.data.total)}개` : ""}</span></h2></div>
        <div class="contents" ref={listRef}>
          <Loaded load={load} retry={reload} cls="admin-paged">
            {(d: Row) => (
              <>
                <Summary s={d.summary} />
                <KindSeg kinds={STATUS} counts={d.counts} current={f.status} label="주소 상태"
                  onPick={(k) => { f.status = k; f.offset = 0; refocus(); redraw((n) => n + 1); }} />
                {d.items.length ? byWeek(d.items).map((g) => (
                  <section key={g.title} class="admin-saved-week grid gap-1 border-t border-line pt-3 first-of-type:border-t-0 first-of-type:pt-0" aria-label={g.title || "주차 없음"}>
                    <h3 class="text-base font-bold">{g.title || "주차 없음"} <span class="admin-count">{adminNumber(g.items.length)}개</span></h3>
                    <Table caption={`${g.title} 저장 영상`} cls="admin-saved-videos [&_table]:phone:table-fixed" rows={g.items} columns={[
                      { label: "제목", render: (v) => <>{v.title}<div class="admin-sub">content_id <code>{v.content_id}</code> · <Time iso={v.created_at} /> 저장</div></>, cls: "phone:w-[30%]" },
                      { label: "사용자", render: (v) => <Holders v={v} />, cls: "phone:w-[20%]" },
                      { label: "길이 · 용량", render: (v) => [v.duration ? fmtDuration(v.duration) : "", fmtSize(v.size)].filter(Boolean).join(" · ") || "-", cls: "phone:w-[8.5rem]" },
                      { label: "주소", render: (v) => <StatusCell v={v} /> },
                    ]} />
                  </section>
                )) : <EmptyText>{narrowed() || f.status !== "all" ? "조건에 맞는 영상이 없습니다." : "이 과목에 저장된 영상이 없습니다."}</EmptyText>}
                {(d.total > COURSE_LIMIT || f.offset > 0) && (
                  <Pager total={d.total} offset={f.offset} limit={COURSE_LIMIT} onPage={(o) => { f.offset = o; redraw((n) => n + 1); titleRef.current?.focus(); }} />
                )}
              </>
            )}
          </Loaded>
        </div>
      </section>
    </>
  );
}

export function SavedVideos({ again, courseId }: { again: number; courseId?: string }) {
  adminUi.detail = null;
  return courseId ? <CourseVideos courseId={courseId} again={again} /> : <CourseList again={again} />;
}
