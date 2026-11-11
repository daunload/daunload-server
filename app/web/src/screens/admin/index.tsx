/* 관리자 화면(#admin). 관리자만 쓰므로 app.ts가 처음 열 때 import()로 받는다(별도 파일).
 * 탭 상태는 주소에 둔다: #admin/overview, #admin/users, #admin/users/{학번}[/{상세 탭}], #admin/activity, #admin/problems, #admin/videos, #admin/saved
 * 서버 문자열은 JSX가 글자로 넣는다(HTML로 해석하지 않는다).
 */
import { useState } from "preact/hooks";
import { navigate, viewHash } from "../../app";
import { state } from "../../core";
import { Icon } from "../../ui";
import { Empty, ViewHead } from "../../ui/layout";
import { mountScreen } from "../mount";
import { UserDetail } from "./detail";
import { Activity, Problems } from "./feeds";
import { Overview } from "./overview";
import { ADMIN_PERIODS, ADMIN_TABS, adminNumber, adminUi } from "./shared";
import { Users } from "./users";
import { SavedVideos } from "./saved-videos";
import { VideoUrls } from "./video-urls";

/** 관리자 화면을 새로 연다. path는 state.adminPath(`["users", "20201234"]` 등). */
export function viewAdmin(path: string[] = state.adminPath): void {
  adminUi.nav++;
  if (!state.me?.is_admin) {
    mountScreen("admin", <Empty role="alert">관리자 권한이 필요합니다.</Empty>);
    return;
  }
  if (!ADMIN_TABS.some((t) => t.key === path[0])) {
    path = ["overview"];
    state.adminPath = path;
    history.replaceState(null, "", viewHash());
  }
  current = path;
  drawAdmin();
}

let current: string[] = ["overview"];

/** 지금 화면을 다시 그린다(문제 건수가 바뀌었을 때 등). 불러오는 중인 요청은 그대로 둔다. */
function drawAdmin(): void {
  if (state.view !== "admin") return;
  mountScreen("admin", <Admin path={current} nav={adminUi.nav} />);
}

/** 문제 탭 이름의 건수. 조건 없이 센 네 종류의 합만 넣는다(현황의 재생 실패 카드와 다르다). */
export function setProblemCount(n: number): void {
  if (adminUi.problemCount === n && adminUi.problemCountFor === state.me?.student_id) return;
  adminUi.problemCount = n;
  adminUi.problemCountFor = state.me!.student_id;
  drawAdmin();
}

function TabLabel({ tab }: { tab: { key: string; label: string } }) {
  // 로그아웃 후 다른 관리자가 들어와도 앞 사람이 본 숫자를 보이지 않게 학번으로 묶는다
  if (tab.key !== "problems" || adminUi.problemCount == null || adminUi.problemCountFor !== state.me?.student_id) return <>{tab.label}</>;
  return <>{tab.label} <span class="admin-count">{adminNumber(adminUi.problemCount)}건</span></>;
}

/** 탭·하위 탭 링크는 주소를 바꾸고 새로 그린 같은 탭에 포커스를 둔다. 다른 링크(학번 등)는 본문으로 포커스를 옮긴다. */
function onLinkClick(e: MouseEvent): void {
  const link = (e.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#admin/"]');
  if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  const tabAttr = ["data-admin-tab", "data-admin-subtab"].find((a) => link.hasAttribute(a));
  navigate(link.getAttribute("href")!, true, { focus: !tabAttr });
  if (tabAttr) document.querySelector<HTMLElement>(`#view [${tabAttr}][aria-current='page']`)?.focus();
}

function PeriodActions({ onPeriod, onReload }: { onPeriod: () => void; onReload: () => void }) {
  return (
    <>
      <label class="admin-period">기간
        <select class="select w-auto min-w-0 flex-1" id="admin-days" value={String(adminUi.days)} onChange={(e) => { adminUi.days = Number(e.currentTarget.value); onPeriod(); }}>
          {ADMIN_PERIODS.map((d) => <option key={d} value={String(d)}>최근 {d}일</option>)}
        </select>
      </label>
      <ReloadButton onReload={onReload} />
    </>
  );
}

function ReloadButton({ onReload }: { onReload: () => void }) {
  return <button class="btn btn-sm" type="button" id="admin-reload" onClick={onReload}><Icon name="refresh" />다시 불러오기</button>;
}

function Admin({ path, nav }: { path: string[]; nav: number }) {
  const tab = path[0];
  const [again, setAgain] = useState(0);      // 다시 불러오기
  const [period, setPeriod] = useState(0);    // 기간을 바꿨다
  const [notFound, setNotFound] = useState(false);
  const reload = () => setAgain((n) => n + 1);
  const repick = () => setPeriod((n) => n + 1);
  const detail = tab === "users" && path.length > 1;

  let actions = null;
  if (tab === "overview" || tab === "activity" || tab === "problems") actions = <PeriodActions onPeriod={repick} onReload={reload} />;
  else if (detail && !notFound) {
    actions = <ReloadButton onReload={() => { adminUi.detail = null; reload(); document.getElementById("admin-reload")?.focus(); }} />;
  } else if (!detail) actions = <ReloadButton onReload={reload} />;

  return (
    <div class="admin" onClick={onLinkClick}>
      <ViewHead title="운영 현황"><div class="admin-actions" id="admin-actions">{actions}</div></ViewHead>
      <nav class="admin-tabs tabs tabs-box" aria-label="관리자 메뉴">
        {ADMIN_TABS.map((t) => (
          <a key={t.key} class="tab admin-tab" href={`#admin/${t.key}`} data-admin-tab={t.key} aria-current={t.key === tab ? "page" : undefined}><TabLabel tab={t} /></a>
        ))}
      </nav>
      <div class="admin-body" id="admin-body" key={`${nav}:${path.join("/")}`}>
        {tab === "overview" ? <Overview again={again} period={period} />
          : detail ? <UserDetail id={path[1]} tabKey={path[2]} again={again} onNotFound={() => setNotFound(true)} />
            : tab === "users" ? <Users again={again} />
              : tab === "activity" ? <Activity again={again} period={period} />
                : tab === "videos" ? <VideoUrls again={again} />
                  : tab === "saved" ? <SavedVideos again={again} />
                  : <Problems again={again} period={period} />}
      </div>
    </div>
  );
}

// 테스트(tests/test_admin_web.py)가 공통 조각을 직접 그려 본다.
export { h, render } from "preact";
export { Pager, Table, ErrorBox } from "./shared";
