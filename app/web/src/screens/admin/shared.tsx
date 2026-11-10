/* 관리자 화면 공통: 이름표, 화면 조건(adminUi), 데이터 불러오기 hook, 표·페이지 넘김·상태·태그·권한 버튼.
 * 관리자 API 응답은 관리자 화면에서만 쓰므로 타입을 자세히 적지 않는다(app/api/routes_admin.py가 원본). */
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { type ApiError, api, fmtDate, pad, state } from "../../core";
import { confirmDialog, toast } from "../../ui";
import { PendingButton } from "../../ui/pending";

export type Row = any;

/* ---------- 이름표 ---------- */

export const ADMIN_TABS = [
  { key: "overview", label: "현황" },
  { key: "users", label: "사용자" },
  { key: "activity", label: "활동" },
  { key: "problems", label: "문제" },
];
export const ADMIN_PERIODS = [7, 30, 90];
export const ADMIN_METRICS: Record<string, string> = { playback_done: "재생 완료", logins: "로그인", downloads: "다운로드" };
export const ADMIN_PROBLEM_KIND: Record<string, { label: string; tone: string }> = {
  playback: { label: "재생 실패", tone: "bad" },
  batch: { label: "재생 묶음 오류", tone: "bad" },
  lms: { label: "LMS 문제", tone: "warn" },
  resolve: { label: "영상 주소 확보 실패", tone: "warn" },
};
/** 재생 묶음 범위(PlaybackBatch.scope)의 화면 이름. 묶음 문제 줄의 제목도 이 값이다. */
export const ADMIN_BATCH_SCOPE: Record<string, string> = { item: "한 개", module: "주차", course: "과목", all: "전체", auto: "자동", week: "이번 주" };
export const ADMIN_BATCH_STATUS: Record<string, { label: string; tone: string }> = {
  queued: { label: "대기", tone: "" }, running: { label: "진행 중", tone: "" }, done: { label: "완료", tone: "ok" },
  partial: { label: "일부 실패", tone: "bad" }, failed: { label: "실패", tone: "bad" }, cancelled: { label: "취소", tone: "" },
};
export const ADMIN_LMS_STATUS: Record<string, { label: string; tone: string }> = {
  ok: { label: "정상", tone: "ok" }, invalid: { label: "다시 로그인 필요", tone: "bad" }, error: { label: "최근 오류", tone: "warn" },
};
/** 사용 권한(User.access_status)의 화면 이름. 사용자 탭의 필터 순서이기도 하다. */
export const ADMIN_ACCESS: Record<string, { label: string; tone: string }> = {
  approved: { label: "승인됨", tone: "ok" }, pending: { label: "대기 중", tone: "warn" }, rejected: { label: "거절됨", tone: "bad" },
  revoked: { label: "회수됨", tone: "bad" }, none: { label: "요청 안 함", tone: "" },
};
export const ADMIN_CHANNEL: Record<string, string> = { discord: "Discord", telegram: "Telegram" };
export const ADMIN_PLAYBACK_STATUS: Record<string, string> = {
  queued: "재생 대기", playing: "재생 중", done: "재생 완료", failed: "재생 실패",
  skipped: "이미 완료", locked: "재생 잠김", cancelled: "재생 취소",
};

/* ---------- 화면 조건 ----------
 * nav: 화면을 새로 열 때마다 오른다(기다리던 검색 타이머가 다른 화면에서 실행되지 않게 한다).
 * users·activity·problems: 탭 조건(상세에 다녀와도 남는다). detail: 지금 보는 사용자 한 명의 요약과 탭별 조건(viewer가 다르면 버린다).
 */
export const adminUi = {
  nav: 0, days: 7, metric: "playback_done", problemCount: null as number | null, problemCountFor: null as string | null,
  searchTimer: 0 as ReturnType<typeof setTimeout> | 0,
  users: { q: "", lms_status: "", has_problem: false, access: "", sort: "activity", offset: 0 },
  detail: null as null | { viewer: string; id: string; summary: Row | null; tabs: Record<string, Row> },
  activity: { kind: "all", student_id: "", offset: 0 },
  problems: { kind: "all", student_id: "", course: "", offset: 0 },
};

/* ---------- 불러오기 ---------- */

export type Load<T> = { kind: "loading" } | { kind: "error"; message: string; status?: number } | { kind: "ok"; data: T };

const stillAdmin = () => state.view === "admin" && !!state.me?.is_admin;

/**
 * path를 불러온다(null이면 부르지 않는다). path나 again이 바뀌면 다시 부른다.
 * 그 사이 path가 바뀌었거나, 화면이 사라졌거나, 관리자가 아니게 됐으면 늦게 온 응답은 버린다(그리지도, 오류를 띄우지도 않는다).
 * [상태, 다시 불러오기]를 돌려준다.
 */
export function useAdmin<T = Row>(path: string | null, again: unknown = 0): [Load<T>, () => void] {
  const [load, setLoad] = useState<Load<T>>({ kind: "loading" });
  const [nonce, setNonce] = useState(0);
  const seq = useRef(0);
  useEffect(() => {
    if (!path) return;
    const mine = ++seq.current;
    const current = () => mine === seq.current && stillAdmin();
    setLoad({ kind: "loading" });
    api<T>(path).then(
      (data) => { if (current()) setLoad({ kind: "ok", data }); },
      (err: ApiError) => { if (current()) setLoad({ kind: "error", message: err.message, status: err.status }); },
    );
    return () => { seq.current++; };
  }, [path, again, nonce]);
  return [load, () => setNonce((n) => n + 1)];
}

/* ---------- 작은 조각 ---------- */

export const adminNumber = (n: unknown) => Number(n || 0).toLocaleString("ko-KR");
export const adminUserPath = (id: string) => `/admin/users/${encodeURIComponent(id)}`;

export function Tag({ label, tone = "" }: { label: string; tone?: string }) {
  return <span class={`admin-tag${tone ? ` ${tone}` : ""}`}>{label}</span>;
}

export function Time({ iso, none = "시각 기록 없음" }: { iso?: string | null; none?: string }) {
  return iso ? <time dateTime={iso}>{fmtDate(new Date(iso))}</time> : <>{none}</>;
}

/** 날짜만(2026/03/04). 가입일·동의일처럼 해가 중요한 곳에 쓴다. */
export function Day({ iso }: { iso?: string | null }) {
  if (!iso) return <>기록 없음</>;
  const d = new Date(iso);
  return <time dateTime={iso}>{d.getFullYear()}/{pad(d.getMonth() + 1)}/{pad(d.getDate())}</time>;
}

export function UserLink({ id }: { id: string }) {
  return <a href={`#admin/users/${encodeURIComponent(id)}`}>{id}</a>;
}

export const courseTitle = (course: string | null | undefined, title: string) => (course ? `${course} · ${title}` : title);

export function LmsTag({ status, prefix = "" }: { status?: string | null; prefix?: string }) {
  if (!status) return <Tag label={`${prefix}연결 안 함`} />;
  const s = ADMIN_LMS_STATUS[status] || { label: status, tone: "warn" };
  return <Tag label={prefix + s.label} tone={s.tone} />;
}

export function AccessTag({ status }: { status: string }) {
  const a = ADMIN_ACCESS[status] || { label: status, tone: "warn" };
  return <Tag label={a.label} tone={a.tone} />;
}

/** 재생 job 상태. 실패했지만 시작한 적이 없으면(앞 영상 실패로 멈춘 묶음 등) "미실행"이다. */
export function JobTag({ job }: { job: Row }) {
  if (job.status === "failed" && !job.started_at) return <Tag label="미실행" />;
  const tone = ["failed", "locked"].includes(job.status) ? "bad" : job.status === "done" ? "ok" : "";
  return <Tag label={ADMIN_PLAYBACK_STATUS[job.status] || job.status} tone={tone} />;
}

export function activityLabel(item: Row): string {
  if (item.kind === "playback") return ADMIN_PLAYBACK_STATUS[item.status] || item.status;
  if (item.kind === "download") return `다운로드 누적 ${adminNumber(item.count)}회`;  // count는 기간 합계가 아니라 그 항목의 전체 누적이다
  if (item.kind === "transcript") return "전사 저장";
  return item.kind;
}

/** 문제 한 줄의 과목·영상. 묶음 줄의 제목은 범위 값이라 이름(전체 재생 묶음 등)으로 바꾼다. */
export const problemTitle = (p: Row) => courseTitle(p.course_name, p.kind === "batch" ? `${ADMIN_BATCH_SCOPE[p.title] || p.title} 재생 묶음` : p.title);

export function ProblemTag({ p }: { p: Row }) {
  const kind = ADMIN_PROBLEM_KIND[p.kind] || { label: p.kind, tone: "warn" };
  return <Tag label={kind.label} tone={kind.tone} />;
}

export const problemError = (p: Row) => `${p.error || "오류 내용 없음"}${p.not_run ? ` · 미실행 ${adminNumber(p.not_run)}개` : ""}`;

/* ---------- 표 · 페이지 넘김 · 상태 ---------- */

export interface Column { label: string; render: (row: Row) => ComponentChildren; cls?: string }

/** 760px 이하에서는 각 칸이 data-label을 머리글로 단 카드가 된다. cls는 감싸는 div에 붙는다. */
export function Table({ columns, rows, caption = "", cls = "" }: { columns: Column[]; rows: Row[]; caption?: string; cls?: string }) {
  return (
    <div class={`admin-table-wrap${cls ? ` ${cls}` : ""}`}>
      <table class="admin-table">
        {caption && <caption class="sr-only">{caption}</caption>}
        <thead><tr>{columns.map((c) => <th key={c.label} scope="col" class={c.cls}>{c.label}</th>)}</tr></thead>
        <tbody>{rows.map((row, i) => <tr key={i}>{columns.map((c) => <td key={c.label} data-label={c.label} class={c.cls}><div>{c.render(row)}</div></td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

/** 이전·다음 페이지. onPage(새 offset)를 부른다. */
export function Pager({ total, offset, limit, onPage }: { total: number; offset: number; limit: number; onPage: (offset: number) => void }) {
  const end = Math.min(total, offset + limit);
  return (
    <nav class="admin-pager" aria-label="페이지 이동">
      <button class="btn small" type="button" disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - limit))}>이전</button>
      <span>{total ? `${offset + 1}–${end} / ${adminNumber(total)}건` : "0건"}</span>
      <button class="btn small" type="button" disabled={end >= total} onClick={() => onPage(offset + limit)}>다음</button>
    </nav>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <>
      <div class="skeleton-list" aria-hidden="true">{Array.from({ length: rows }, (_, i) => <div key={i} class="skeleton" />)}</div>
      <p class="sr-only" role="status">불러오는 중입니다.</p>
    </>
  );
}

export function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div class="admin-error">
      <p role="alert"><strong>불러오지 못했어요</strong> {message}</p>
      {retry && <button class="btn small" type="button" onClick={retry}>다시 시도</button>}
    </div>
  );
}

export const EmptyText = ({ children }: { children: ComponentChildren }) => <p class="admin-empty">{children}</p>;

/**
 * 불러오는 동안 스켈레톤(aria-busy), 실패하면 오류와 다시 시도, 성공하면 children(data)을 그리는 칸.
 * as/cls/attrs로 감싸는 요소를 정한다(표가 들어가는 .admin-paged, 머리 요약 section 등).
 */
export function Loaded<T>({ load, retry, rows, children, as: Tag = "div", cls, attrs }: {
  load: Load<T>; retry: () => void; rows?: number; children: (data: T) => ComponentChildren;
  as?: "div" | "section"; cls?: string; attrs?: Record<string, string>;
}) {
  return (
    <Tag class={cls} aria-busy={load.kind === "loading" ? "true" : undefined} {...attrs}>
      {load.kind === "loading" ? <Skeleton rows={rows} /> : load.kind === "error" ? <ErrorBox message={load.message} retry={retry} /> : children(load.data)}
    </Tag>
  );
}

/* ---------- 권한 버튼 ---------- */

const ACCESS_DONE: Record<string, (id: string) => string> = {
  approve: (id) => `${id}을 승인했습니다`, reject: (id) => `${id}의 요청을 거절했습니다`, revoke: (id) => `${id}의 권한을 회수했습니다`,
};

/**
 * 상태별 버튼: 대기 중은 승인·거절, 승인됨은 회수, 나머지는 바로 승인. 관리자 줄에는 두지 않는다(null).
 * 처리 중에는 같은 줄의 버튼을 모두 잠그고, 회수만 확인 창을 거친다.
 * 끝나거나 다른 관리자가 먼저 처리해 409가 나면 reload()로 목록을 새로 본다.
 */
export function AccessButtons({ id, status, isAdmin, reload }: { id: string; status: string; isAdmin: boolean; reload: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  if (isAdmin) return null;
  const act = async (action: string) => {
    if (action === "revoke") {
      const ok = await confirmDialog({
        title: `${id}의 사용 권한을 회수할까요?`,
        body: ["회수하면 이 사용자는 서비스를 쓸 수 없어요.", "대기 중이거나 진행 중인 재생도 멈춰요."],
        confirmLabel: "권한 회수", danger: true,
      });
      if (!ok) return;
    }
    setBusy(action);
    try {
      const done = await api<{ sync_queued?: boolean }>(`${adminUserPath(id)}/access`, { method: "POST", json: { action } });
      toast(ACCESS_DONE[action](id));
      if (done?.sync_queued === false) toast("동기화는 대기열에 넣지 못했어요. 사용자의 정보는 다음 로그인이나 동기화 때 불러와요");
    } catch (err) {
      const e = err as ApiError;
      if (e.status === 409) toast("이미 처리된 요청이라 최신 상태로 다시 불러왔어요");
      else { toast(e.message, true); setBusy(null); return; }  // 409는 상태가 바뀐 것이므로 목록을 새로 본다
    }
    setBusy(null);
    reload();
  };
  const btn = (action: string, label: string, cls: string) => (
    <PendingButton key={action} class={`btn small${cls}`} type="button" data-access={action} data-id={id}
      busy={busy === action} disabled={busy !== null} onClick={() => act(action)}>{label}</PendingButton>
  );
  return (
    <div class="admin-acts">
      {status === "pending" ? [btn("approve", "승인", " primary"), btn("reject", "거절", " danger")]
        : status === "approved" ? btn("revoke", "권한 회수", " danger")
          : btn("approve", "바로 승인", "")}
    </div>
  );
}

/* ---------- 종류 버튼(건수) ---------- */

/** 종류 고르기. 고른 버튼은 다시 그린 뒤에도 포커스를 지킨다(refocus가 true일 때). */
export function KindSeg({ kinds, counts, current, label, onPick }: {
  kinds: Record<string, string>; counts: Record<string, number>; current: string; label: string; onPick: (kind: string) => void;
}) {
  const all = Object.values(counts).reduce((a, b) => a + b, 0);
  return (
    <div class="admin-seg" role="group" aria-label={label}>
      {Object.entries(kinds).map(([k, name]) => (
        <button key={k} class="btn small" type="button" data-kind={k} aria-pressed={k === current} onClick={() => { if (k !== current) onPick(k); }}>
          {name} {adminNumber(k === "all" ? all : counts[k] || 0)}
        </button>
      ))}
    </div>
  );
}

/** 바로 앞에서 고른 종류 버튼으로 포커스를 돌린다(목록을 다시 그리면 버튼이 새로 생긴다). */
export function useRefocusKind(root: { current: HTMLElement | null }, kind: string, ready: boolean): () => void {
  const pending = useRef(false);
  useEffect(() => {
    if (ready && pending.current) {
      root.current?.querySelector<HTMLElement>(`[data-kind="${kind}"]`)?.focus();
      pending.current = false;
    }
  });
  return () => { pending.current = true; };
}

