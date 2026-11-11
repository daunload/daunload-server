/* 관리자 영상 주소 탭: 재생 워커가 실제로 튼 영상 주소(영상마다 한 줄). 제목·과목·content_id 검색, 표, 페이지 넘김.
 * 기록 전용이라 기간 조건 없이 마지막 재생 시도가 최근인 순서로 보여 준다. */
import { useRef, useState } from "preact/hooks";
import { adminNumber, adminUi, EmptyText, Loaded, Pager, type Row, Table, Time, UserLink, useAdmin } from "./shared";

const LIMIT = 20;

function Who({ id }: { id: string | null }) {
  return id ? <UserLink id={id} /> : <>삭제됨</>;
}

export function VideoUrls({ again }: { again: number }) {
  adminUi.detail = null;
  const f = adminUi.videos;
  const nav = adminUi.nav;
  const [, redraw] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  const params = new URLSearchParams({ offset: String(f.offset), limit: String(LIMIT) });
  if (f.q.trim()) params.set("q", f.q.trim());
  const [load, reload] = useAdmin(`/admin/video-urls?${params}`, again);

  const apply = () => {
    clearTimeout(adminUi.searchTimer);
    f.q = inputRef.current?.value ?? f.q;
    f.offset = 0;
    redraw((n) => n + 1);
  };

  return (
    <>
      <form class="admin-filters" role="search" aria-label="영상 주소 찾기" onSubmit={(e) => { e.preventDefault(); apply(); }}>
        <label class="admin-search">제목·과목 검색<input class="input" type="search" name="q" maxLength={100} autocomplete="off" defaultValue={f.q} ref={inputRef}
          onInput={() => { clearTimeout(adminUi.searchTimer); adminUi.searchTimer = setTimeout(() => { if (nav === adminUi.nav) apply(); }, 300); }} /></label>
        <p class="admin-hint">재생을 시도할 때 플레이어가 실제로 불러온 주소예요. 같은 영상은 한 줄로 합치고 시도 횟수만 늘려요.</p>
      </form>
      <section class="card admin-box" aria-labelledby="admin-videos-title">
        <div class="admin-box-head"><h2 id="admin-videos-title" tabIndex={-1} ref={titleRef}>영상 주소 <span class="admin-count" role="status">
          {load.kind === "ok" ? `${adminNumber(load.data.total)}개` : ""}</span></h2></div>
        <Loaded load={load} retry={reload} cls="admin-paged">
          {(d: Row) => (
            <>
              {d.items.length ? (
                <Table caption="재생한 영상 주소" cls="admin-video-urls" rows={d.items} columns={[
                  { label: "마지막 시도", render: (v) => <><Time iso={v.last_seen_at} /><div class="admin-sub">처음 <Time iso={v.first_seen_at} /></div></>, cls: "admin-nowrap" },
                  { label: "과목 · 제목", render: (v) => <>{v.course_name} · {v.title}<div class="admin-sub">content_id <code>{v.content_id}</code></div></>, cls: "narrow:min-w-44" },
                  { label: "영상 주소", render: (v) => <code class="wrap-anywhere text-xs">{v.media_url}</code> },
                  { label: "시도", render: (v) => `${adminNumber(v.attempts)}회`, cls: "admin-nowrap" },
                  { label: "사용자", render: (v) => <>처음 <Who id={v.first_student_id} /><div>마지막 <Who id={v.last_student_id} /></div></>, cls: "admin-nowrap" },
                ]} />
              ) : <EmptyText>{f.q.trim() ? "조건에 맞는 영상 주소가 없습니다." : "아직 기록된 영상 주소가 없습니다."}</EmptyText>}
              {(d.total > LIMIT || f.offset > 0) && (
                <Pager total={d.total} offset={f.offset} limit={LIMIT} onPage={(o) => { f.offset = o; redraw((n) => n + 1); titleRef.current?.focus(); }} />
              )}
            </>
          )}
        </Loaded>
      </section>
    </>
  );
}
