/* 관리자 현황 탭: 권한 요청, 주요 지표 4개, 일별 막대그래프(표로 보기), 최근 활동, 확인이 필요한 문제. */
import { useEffect, useState } from "preact/hooks";
import { fmtDuration } from "../../core";
import { setProblemCount } from "./index";
import {
  ADMIN_METRICS, AccessButtons, EmptyText, ErrorBox, ProblemTag, Skeleton, Table, Tag, Time, UserLink, activityLabel,
  adminNumber, adminUi, problemError, problemTitle, type Row, useAdmin,
} from "./shared";

const PREVIOUS: Record<string, string> = { none: "처음 요청", rejected: "거절됨", revoked: "회수됨" };

/** 현황 맨 위의 권한 요청 표. 요청이 없으면 비운다. */
function AccessRequests() {
  const [load, reload] = useAdmin("/admin/access-requests");
  if (load.kind === "loading") return null;
  if (load.kind === "error") return <ErrorBox message={load.message} retry={reload} />;
  const d = load.data;
  if (!d.items.length) return null;
  return (
    <section class="panel admin-box admin-access border-amber" aria-labelledby="admin-access-title">
      <h2 id="admin-access-title">권한 요청 {adminNumber(d.total)}건</h2>
      <Table caption="권한 요청" cls="admin-linked" rows={d.items} columns={[
        { label: "학번", render: (r) => <UserLink id={r.student_id} />, cls: "admin-nowrap" },
        { label: "요청 시각", render: (r) => <Time iso={r.requested_at} />, cls: "admin-nowrap" },
        { label: "이전 상태", render: (r) => <Tag label={r.previous_status === "rejected" && r.reject_count ? `거절됨 ${adminNumber(r.reject_count)}회` : PREVIOUS[r.previous_status] || r.previous_status}
          tone={r.previous_status === "none" ? "" : "bad"} /> },
        { label: "처리", render: (r) => <AccessButtons id={r.student_id} status="pending" isAdmin={false} reload={reload} /> },
      ]} />
    </section>
  );
}

function Kpi({ label, value, sub, bad }: { label: string; value: string; sub: string; bad?: boolean }) {
  return <div class={`panel admin-kpi${bad ? " bad" : ""}`}><span class="admin-kpi-label">{label}</span><strong>{value}</strong><span class="admin-kpi-sub">{sub}</span></div>;
}

/** 일별 막대. 값은 각 막대의 title과 아래 표에 있다. */
function Chart({ d }: { d: Row }) {
  const [metric, setMetric] = useState(adminUi.metric);
  const label = ADMIN_METRICS[metric];
  const values: number[] = d.series.map((s: Row) => s[metric] || 0);
  const max = Math.max(0, ...values);
  const n = d.series.length;
  const step = n <= 7 ? 1 : n <= 31 ? 5 : 15;  // 마지막 날부터 거꾸로 셀 때 이 간격마다 날짜를 쓴다
  const since = d.tracking_since ? ` · 로그인·다운로드 추이는 ${d.tracking_since}부터 집계` : " · 로그인·다운로드 기록은 아직 없습니다";
  return (
    <section class="panel admin-box admin-chart" aria-labelledby="admin-chart-title">
      <div class="admin-box-head"><h2 id="admin-chart-title">일별 {label}</h2>
        <div class="admin-seg" role="group" aria-label="그래프 항목">
          {Object.entries(ADMIN_METRICS).map(([key, name]) => (
            <button key={key} class="btn btn-sm px-2.5" type="button" data-metric={key} aria-pressed={key === metric}
              onClick={() => { adminUi.metric = key; setMetric(key); }}>{name}</button>
          ))}
        </div>
      </div>
      <div class={`admin-bars${n > 31 ? " dense" : ""}`} aria-hidden="true">
        {d.series.map((s: Row, i: number) => {
          const [y, m, day] = s.day.split("-").map(Number);
          const date = new Date(y, m - 1, day);
          const text = `${date.getMonth() + 1}/${date.getDate()}`;
          const v = values[i];
          const pct = max ? Math.max(v ? 2 : 0, Math.round((v / max) * 100)) : 0;
          return (
            <div key={s.day} class="admin-bar" title={`${text} ${label} ${adminNumber(v)}건`}>
              <span class="admin-fill" style={{ height: `${pct}%` }} /><span class="admin-day">{(n - 1 - i) % step === 0 ? text : ""}</span>
            </div>
          );
        })}
      </div>
      <p class="admin-caption">최대 {adminNumber(max)}건{since}</p>
      <details class="admin-alt"><summary>표로 보기</summary>
        <Table caption="일별 재생 완료, 로그인, 다운로드" rows={d.series} columns={[
          { label: "날짜", render: (s) => s.day },
          ...Object.entries(ADMIN_METRICS).map(([key, name]) => ({ label: name, render: (s: Row) => adminNumber(s[key]) })),
        ]} />
      </details>
    </section>
  );
}

function OverviewBody({ d }: { d: Row }) {
  useEffect(() => setProblemCount(d.problem_count), [d]);
  const rate = d.failure_rate == null ? "-" : `${(d.failure_rate * 100).toFixed(1)}%`;
  return (
    <>
      <div class="admin-access-slot">{d.pending_count ? <AccessRequests /> : null}</div>
      <section class="admin-kpis" aria-label={`주요 지표 (최근 ${adminNumber(d.days)}일)`}>
        <Kpi label="활동 사용자" value={adminNumber(d.active_users)} sub={`전체 ${adminNumber(d.total_users)}명`} />
        <Kpi label="재생한 강의" value={adminNumber(d.playback_done)} sub={`${fmtDuration(d.playback_seconds)}${d.playback_auto ? ` · 자동 ${adminNumber(d.playback_auto)}개` : ""}`} />
        <Kpi label="다운로드 기록" value={adminNumber(d.downloads)} sub="완료 보고 또는 링크 발급 횟수" />
        <Kpi label="재생 실패" value={adminNumber(d.playback_failed)} sub={`실패율 ${rate}`} bad={!!d.playback_failed} />
      </section>
      <div class="admin-grid">
        <Chart d={d} />
        <section class="panel admin-box" aria-labelledby="admin-feed-title">
          <h2 id="admin-feed-title">최근 활동</h2>
          {d.recent_activity.length ? (
            <ul class="admin-feed">
              {d.recent_activity.map((a: Row, i: number) => (
                <li key={i}>
                  <span class="admin-feed-text"><UserLink id={a.student_id} /> · {a.title}</span>
                  <span class="admin-feed-meta">
                    <span class={`admin-tag${a.kind === "playback" && ["failed", "locked"].includes(a.status) ? " bad" : ""}`}>{activityLabel(a)}</span>
                    <Time iso={a.recorded_at} />
                  </span>
                </li>
              ))}
            </ul>
          ) : <p class="admin-empty admin-feed">기간 안에 활동이 없습니다.</p>}
        </section>
      </div>
      <section class="panel admin-box admin-problems" aria-labelledby="admin-problems-title">
        <div class="admin-box-head"><h2 id="admin-problems-title">확인이 필요한 문제</h2>
          <a class="admin-more" href="#admin/problems">문제 {adminNumber(d.problem_count)}건 모두 보기</a></div>
        {d.recent_problems.length ? (
          <Table caption="확인이 필요한 문제" cls="admin-linked" rows={d.recent_problems} columns={[
            { label: "시각", render: (p) => <Time iso={p.occurred_at} />, cls: "admin-nowrap" },
            { label: "사용자", render: (p) => <UserLink id={p.student_id} /> },
            { label: "과목 · 영상", render: problemTitle },
            { label: "오류", render: (p) => <><ProblemTag p={p} /> {problemError(p)}</> },
          ]} />
        ) : <EmptyText>확인할 문제가 없습니다.</EmptyText>}
      </section>
    </>
  );
}

export function Overview({ again, period }: { again: number; period: number }) {
  const [load, reload] = useAdmin(`/admin/overview?days=${adminUi.days}`, `${again}:${period}`);
  if (load.kind === "loading") return <div aria-busy="true"><Skeleton rows={4} /></div>;
  if (load.kind === "error") return <ErrorBox message={load.message} retry={reload} />;
  return <OverviewBody d={load.data} />;
}
