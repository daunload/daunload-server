/* 관리자 저장 영상 탭: 전체 사용자의 saved_videos(동기화 때 받아 둔 다운로드용 영상 주소).
 * 같은 영상도 사용자마다 한 줄이라 요약에 서로 다른 영상 수와 중복 줄 수를, 줄마다 같은 영상을 가진 사용자 수를 보여 준다. */
import { useRef, useState } from "preact/hooks";
import { fmtDuration, fmtSize } from "../../core";
import { adminNumber, adminUi, EmptyText, KindSeg, Loaded, Pager, type Row, Table, Tag, Time, UserLink, useAdmin, useRefocusKind } from "./shared";

const LIMIT = 20;
const STATUS: Record<string, string> = { all: "전체", saved: "주소 있음", pending: "확보 전", failed: "확보 실패" };

function StatusCell({ v }: { v: Row }) {
  if (v.media_url) return <><Tag label="주소 있음" tone="ok" /><div class="admin-sub"><Time iso={v.resolved_at} none="" /></div></>;
  if (v.resolve_error) return <><Tag label="확보 실패" tone="warn" /><div class="admin-err">{v.resolve_error}</div></>;
  return <Tag label="확보 전" />;
}

export function SavedVideos({ again }: { again: number }) {
  adminUi.detail = null;
  const f = adminUi.saved;
  const nav = adminUi.nav;
  const [, redraw] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const params = new URLSearchParams({ offset: String(f.offset), limit: String(LIMIT) });
  if (f.q.trim()) params.set("q", f.q.trim());
  if (f.student_id.trim()) params.set("student_id", f.student_id.trim());
  if (f.status !== "all") params.set("status", f.status);
  const [load, reload] = useAdmin(`/admin/saved-videos?${params}`, again);
  const refocus = useRefocusKind(listRef, f.status, load.kind === "ok");

  /** 폼의 지금 값으로 첫 페이지부터 다시 찾는다. */
  const apply = () => {
    clearTimeout(adminUi.searchTimer);
    const field = (name: string) => formRef.current?.elements.namedItem(name) as HTMLInputElement | null;
    f.q = field("q")?.value ?? f.q;
    f.student_id = field("student_id")?.value ?? f.student_id;
    f.offset = 0;
    redraw((n) => n + 1);
  };
  const search = () => {
    clearTimeout(adminUi.searchTimer);
    adminUi.searchTimer = setTimeout(() => { if (nav === adminUi.nav) apply(); }, 300);
  };

  return (
    <>
      <form class="admin-filters" role="search" aria-label="저장 영상 찾기" ref={formRef} onSubmit={(e) => { e.preventDefault(); apply(); }}>
        <label class="admin-search">학번<input class="input" type="search" name="student_id" maxLength={32} autocomplete="off" defaultValue={f.student_id} onInput={search} /></label>
        <label class="admin-search">제목·과목 검색<input class="input" type="search" name="q" maxLength={100} autocomplete="off" defaultValue={f.q} onInput={search} /></label>
      </form>
      <section class="card admin-box" aria-labelledby="admin-saved-title">
        <div class="admin-box-head"><h2 id="admin-saved-title" tabIndex={-1} ref={titleRef}>저장 영상 <span class="admin-count" role="status">
          {load.kind === "ok" ? `${adminNumber(load.data.total)}줄` : ""}</span></h2></div>
        <div class="contents" ref={listRef}>
          <Loaded load={load} retry={reload} cls="admin-paged">
            {(d: Row) => {
              const sum = d.summary;
              return (
                <>
                  <p class="admin-saved-summary text-sm text-ink-2">
                    {adminNumber(sum.rows)}줄 · 서로 다른 영상 {adminNumber(sum.videos)}개 · 사용자 {adminNumber(sum.users)}명
                    {sum.rows > sum.videos && <> · 같은 영상이 여러 사용자에게 저장된 중복 {adminNumber(sum.rows - sum.videos)}줄</>}
                  </p>
                  <KindSeg kinds={STATUS} counts={d.counts} current={f.status} label="주소 상태"
                    onPick={(k) => { f.status = k; f.offset = 0; refocus(); redraw((n) => n + 1); }} />
                  {d.items.length ? (
                    <Table caption="저장 영상 목록" cls="admin-saved-videos" rows={d.items} columns={[
                      { label: "저장", render: (v) => <Time iso={v.created_at} />, cls: "admin-nowrap" },
                      { label: "사용자", render: (v) => <UserLink id={v.student_id} />, cls: "admin-nowrap" },
                      { label: "과목 · 제목", render: (v) => <>{v.course_name} · {v.title}<div class="admin-sub">{v.module_title} · content_id <code>{v.content_id}</code></div></>, cls: "narrow:min-w-44" },
                      { label: "같은 영상", render: (v) => `${adminNumber(v.holders)}명`, cls: "admin-nowrap" },
                      { label: "길이 · 용량", render: (v) => [v.duration ? fmtDuration(v.duration) : "", fmtSize(v.size)].filter(Boolean).join(" · ") || "-", cls: "admin-nowrap" },
                      { label: "주소", render: (v) => <><StatusCell v={v} />{v.media_url && <code class="mt-1 block wrap-anywhere text-xs">{v.media_url}</code>}</> },
                    ]} />
                  ) : <EmptyText>{f.q.trim() || f.student_id.trim() || f.status !== "all" ? "조건에 맞는 저장 영상이 없습니다." : "저장된 영상이 없습니다."}</EmptyText>}
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
