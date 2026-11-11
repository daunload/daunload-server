/* 관리자 사용자 탭: 학번 검색, LMS 상태·정렬·문제 있음 조건, 권한 필터, 사용자 표(권한 버튼), 페이지 넘김. */
import { useRef, useState } from "preact/hooks";
import { adminNumber, AccessButtons, AccessTag, ADMIN_ACCESS, ADMIN_CHANNEL, ADMIN_LMS_STATUS, adminUi, EmptyText, LmsTag, Loaded, Pager, type Row, Table, Time, UserLink, useAdmin } from "./shared";

const LIMIT = 25;
const SORTS: Record<string, string> = { activity: "최근 로그인순", created: "가입일순", student_id: "학번순" };

export function Users({ again }: { again: number }) {
  adminUi.detail = null;
  const f = adminUi.users;
  const nav = adminUi.nav;
  const [, redraw] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  const params = new URLSearchParams({ sort: f.sort, offset: String(f.offset), limit: String(LIMIT) });
  if (f.q.trim()) params.set("q", f.q.trim());
  if (f.lms_status) params.set("lms_status", f.lms_status);
  if (f.has_problem) params.set("has_problem", "true");
  if (f.access) params.set("access", f.access);
  const [load, reload] = useAdmin(`/admin/users?${params}`, again);

  /** 폼의 지금 값으로 첫 페이지부터 다시 찾는다. */
  const apply = () => {
    clearTimeout(adminUi.searchTimer);
    const form = formRef.current!;
    const field = (name: string) => form.elements.namedItem(name) as HTMLInputElement;
    f.q = field("q").value;
    f.lms_status = field("lms_status").value;
    f.sort = field("sort").value;
    f.has_problem = field("has_problem").checked;
    f.offset = 0;
    redraw((n) => n + 1);
  };
  const search = () => {
    clearTimeout(adminUi.searchTimer);
    adminUi.searchTimer = setTimeout(() => { if (nav === adminUi.nav) apply(); }, 300);
  };
  // 학번 링크가 실제 컨트롤이고, 줄의 다른 곳을 눌러도 그 링크를 누른 것으로 친다
  const rowClick = (e: MouseEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest("a, button") || String(getSelection())) return;
    target.closest("tbody tr")?.querySelector<HTMLAnchorElement>('a[href^="#admin/users/"]')?.click();
  };

  return (
    <>
      <form class="admin-filters" role="search" aria-label="사용자 찾기" ref={formRef}
        onSubmit={(e) => { e.preventDefault(); apply(); }}
        onChange={(e) => { if ((e.target as HTMLInputElement).name !== "q") apply(); }}>
        <label class="admin-search">학번 검색<input class="input" type="search" name="q" maxLength={32} autocomplete="off" defaultValue={f.q} onInput={search} /></label>
        <label>LMS 상태<select class="select" name="lms_status" value={f.lms_status}>
          <option value="">전체</option>{Object.entries(ADMIN_LMS_STATUS).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
        </select></label>
        <label>정렬<select class="select" name="sort" value={f.sort}>{Object.entries(SORTS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select></label>
        <label class="flex min-h-touch cursor-pointer items-center gap-2 font-normal text-ink"><input type="checkbox" class="checkbox" name="has_problem" defaultChecked={f.has_problem} /><span>문제 있음만</span></label>
      </form>
      <div class="admin-seg admin-access-filter" role="group" aria-label="권한">
        {[["", "전체"], ...Object.entries(ADMIN_ACCESS).map(([k, a]) => [k, a.label])].map(([k, label]) => (
          <button key={k} class="btn btn-sm px-2.5" type="button" data-access-filter={k} aria-pressed={f.access === k}
            onClick={() => { f.access = k; f.offset = 0; redraw((n) => n + 1); }}>{label}</button>
        ))}
      </div>
      <section class="card admin-box" aria-labelledby="admin-users-title">
        <div class="admin-box-head"><h2 id="admin-users-title" tabIndex={-1} ref={titleRef}>사용자 <span class="admin-count" id="admin-user-count" role="status">
          {load.kind === "ok" ? `${adminNumber(load.data.total)}명` : ""}</span></h2></div>
        <div id="admin-user-list" onClick={rowClick}>
          <Loaded load={load} retry={reload} rows={4}>
            {(d: Row) => (
              <>
                {d.users.length ? (
                  <Table caption="사용자 목록" cls="admin-users" rows={d.users} columns={[
                    { label: "학번", render: (u) => <UserLink id={u.student_id} />, cls: "admin-nowrap" },
                    { label: "권한", render: (u) => <AccessTag status={u.access_status} /> },
                    { label: "최근 로그인", render: (u) => <Time iso={u.last_login_at} none="로그인 기록 없음" />, cls: "admin-nowrap" },
                    { label: "LMS", render: (u) => <LmsTag status={u.lms_status} /> },
                    { label: "과목", render: (u) => adminNumber(u.course_count) },
                    { label: "재생 (완료/실패)", render: (u) => <>{adminNumber(u.playback_done)} / {u.playback_failed ? <span class="admin-bad">{adminNumber(u.playback_failed)}</span> : "0"}</> },
                    { label: "다운로드", render: (u) => adminNumber(u.download_count) },
                    { label: "전사", render: (u) => adminNumber(u.transcript_count) },
                    { label: "알림", render: (u) => (u.channels.length ? u.channels.map((c: string) => ADMIN_CHANNEL[c] || c).join(", ") : "없음") },
                    { label: "권한 관리", render: (u) => (u.is_admin ? "관리자 계정" : <AccessButtons id={u.student_id} status={u.access_status} isAdmin={false} reload={reload} />) },
                  ]} />
                ) : <EmptyText>조건에 맞는 사용자가 없습니다.</EmptyText>}
                {(d.total > LIMIT || f.offset > 0) && (
                  <Pager total={d.total} offset={f.offset} limit={LIMIT} onPage={(o) => { f.offset = o; redraw((n) => n + 1); titleRef.current?.focus(); }} />
                )}
              </>
            )}
          </Loaded>
        </div>
      </section>
    </>
  );
}
