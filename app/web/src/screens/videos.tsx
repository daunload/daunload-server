/* 내려받기·텍스트 변환 화면(#videos).
 * - 과목을 고르고 영상·수업자료 탭을 오간다. 지난 과목(지금 목록에 없지만 저장된 영상이 있는 과목)은 저장된 영상만 보인다.
 * - 영상은 확장 프로그램이 학교 CDN에서 고른 폴더로 바로 저장한다. 수업자료는 브라우저가 받는다(여러 개는 폴더에 하나씩).
 * - 줄마다 아이콘 버튼 2개까지(텍스트 변환/보기 + 내려받기), 뜻은 목록 위 범례에 한 번만 적는다.
 * - 데이터: state.savedVideos(/saved-videos), state.videoList(/courses/{id}/videos), state.materialList(/courses/{id}/materials)
 *
 * renderVideoList()·renderCurrentTab()은 지금 상태로 다시 그린다(목록을 다시 받지 않는다). viewVideos()는 필요한 목록을 받아 온다.
 */
import { Fragment } from "preact";
import { useState } from "preact/hooks";
import { needCred, viewError } from "../app";
import { api, fmtDate, fmtDuration, fmtSize, state } from "../core";
import { type BulkItem, type BulkStat, bulkDownloadSupported, downloadDirectVideo, requireDownloadExtension, startBulkDownload } from "../downloads";
import { ext } from "../extension";
import { renderTaskPanel } from "../tasks";
import { Icon, IconOnly, isPhoneDevice, toast } from "../ui";
import { Empty, ViewHead } from "../ui/layout";
import { mountScreen } from "./mount";
import { TranscribeButton, TranscribeGroupButton } from "./transcript";

const PHONE_NOTICE = "PC 크롬에서 받을 수 있어요";
const EXTENSION_STORE_URL = "https://chromewebstore.google.com/detail/ocnolcfmmkkphhkdnmblcdmakaciinda";

/* 서버가 주는 값 + 화면이 붙이는 값(courseName, linkPath, mediaPath, savedRow). */
interface SavedVideo {
  id: number; lms_course_id: number; course_name: string; module_item_id: number; module_title: string; title: string;
  duration?: number | null; size?: number | null; saved: boolean; resolve_error?: string | null;
  downloaded_at?: string | null; transcript_id?: number | null;
}
interface VideoItem {
  module_item_id: number; module_id: number | string; module_title: string; title: string; duration?: number | null; size?: number | null;
  thumbnail_url?: string | null; available: boolean; completed: boolean; downloaded_at?: string | null; transcript_id?: number | null;
  savedRow?: SavedVideo; courseName: string; linkPath: string | null; mediaPath: string | null;
}
interface MaterialItem {
  key: string; module_id: number | string; module_title: string; title: string; file_name: string; size?: number | null; kind: string;
  downloaded_at?: string | null; linkPath: string;
}

/* ---------- 불러오기 ---------- */

export async function viewVideos(): Promise<void> {
  if (needCred()) return;
  if (!state.savedVideos) {
    try { state.savedVideos = await api<SavedVideo[]>("/saved-videos"); }
    catch (err) { if (state.view === "videos") viewError((err as Error).message, viewVideos); return; }
    if (state.view !== "videos") return;
  }
  const { allIds } = courseOptions();
  if (!allIds.length) { mountScreen("videos", <NoCourses />); return; }
  if (!state.videoCourse || !allIds.includes(state.videoCourse)) state.videoCourse = allIds[0];
  const { isPast } = courseOptions();
  if (isPast) state.videoTab = "videos";  // 지난 과목은 수업자료를 LMS에서 볼 수 없다
  detectExtension();
  const courseId = state.videoCourse!;
  if (isPast) {  // 지난 과목: 저장된 영상만
    state.videoList = { courseId, items: [] };
    renderCurrentTab();
    return;
  }
  const key = state.videoTab === "videos" ? "videoList" : "materialList";
  const path = state.videoTab === "videos" ? `/courses/${courseId}/videos` : `/courses/${courseId}/materials`;
  if (!state[key] || state[key].courseId !== courseId) {
    drawVideos(true);
    try {
      state[key] = { courseId, items: await api(path) };
    } catch (err) {
      if (state.view === "videos" && state.videoCourse === courseId) viewError((err as Error).message, viewVideos);
      return;
    }
  }
  if (state.view !== "videos" || state.videoCourse !== courseId) return;
  renderCurrentTab();
}

/** 지금 상태로 다시 그린다. 목록을 아직 받지 않았으면 자리표시를 보인다. */
function drawVideos(loading = false): void {
  if (state.view !== "videos" || !state.savedVideos) return;
  mountScreen("videos", <Videos loading={loading} />);
}

export const renderCurrentTab = () => drawVideos();
export const renderVideoList = () => drawVideos();

function courseOptions() {
  const courses = state.courses;
  const saved: SavedVideo[] = state.savedVideos || [];
  const pastCourses = [...new Map(saved
    .filter((v) => !courses.some((c) => c.lms_course_id === v.lms_course_id))
    .map((v) => [v.lms_course_id, v.course_name])).entries()];
  const allIds = [...courses.map((c) => c.lms_course_id), ...pastCourses.map(([id]) => id)];
  const isPast = !courses.some((c) => c.lms_course_id === state.videoCourse);
  return { courses, pastCourses, allIds, isPast };
}

/* ---------- 내려받기 ---------- */

/** 여러 개 받기를 시작한다. 진행은 하단 패널에 나오고, 실패한 항목만 다시 시도할 수 있다. */
export async function runBulkDownload(items: BulkItem[]): Promise<void> {
  const token = state.token;
  try {
    await startBulkDownload(items, (stat: BulkStat) => {
      if (state.token !== token) return;  // 로그아웃 뒤에 늦게 온 갱신은 버린다
      state.bulk = stat;
      try {
        if (!stat.running) {
          if (stat.stopped) toast(`${stat.done}/${stat.total}개 받고 중지했어요`);
          else if (!stat.failedItems.length) toast(`${stat.done}개를 모두 받았어요`);
          if (!stat.failedItems.length) state.bulk = null;
          // 다른 화면으로 옮겼거나 목록을 다시 불러오는 중이면 덮어쓰지 않는다
          if (state.view === "videos" && (state.videoTab === "materials" ? state.materialList : state.videoList)) renderCurrentTab();
        }
      } finally {
        renderTaskPanel();
      }
    });
  } catch (err) {
    if ((err as Error).name !== "AbortError") reportDownloadError(err as Error & { code?: string });  // 폴더 선택 취소는 조용히
  }
}

/** 확장프로그램이 없으면 설치 안내를 펼쳐 문구를 그 안에 보이고, 그 밖의 오류는 토스트로 알린다. */
export function reportDownloadError(err: Error & { code?: string }): void {
  if (err.code !== "EXTENSION_MISSING") { toast(err.message, true); return; }
  state.extensionMissing = err.message;
  if (state.view !== "videos" || !state.videoList) { state.extensionMissing = null; toast(err.message, true); return; }
  renderCurrentTab();
  const summary = document.querySelector<HTMLElement>("#video-list details summary");
  if (!summary) { state.extensionMissing = null; toast(err.message, true); return; }  // 안내가 그려지지 않는 화면(빈 목록·휴대폰)에서도 오류는 보인다
  summary.scrollIntoView({ block: "center" });
  summary.focus({ preventScroll: true });
}

/** 한 개 받기. 영상은 확장 프로그램으로, 자료는 브라우저 다운로드로. phase로 버튼 문구(준비 중 → 다운로드 중)를 바꾼다. */
async function downloadOne(linkPath: string, phase: (label: string) => void): Promise<boolean> {
  const token = state.token;
  try {
    if (linkPath.endsWith("/direct-download")) {
      await requireDownloadExtension();
      phase("다운로드 중");
      await downloadDirectVideo({ linkPath }, new AbortController().signal);
      if (state.token !== token) return false;  // 로그아웃 뒤에 늦게 온 결과는 알리지 않는다
      toast("영상 저장이 완료되었습니다.");
      return true;
    }
    const link = await api<{ url: string; file_name: string }>(linkPath, { method: "POST" });
    if (state.token !== token) return false;
    const a = document.createElement("a");
    a.href = link.url;
    a.download = link.file_name;
    document.body.append(a);
    a.click();
    a.remove();
    toast(`${link.file_name} 내려받기를 시작했습니다. 브라우저 다운로드 목록에서 진행 상황을 볼 수 있습니다.`);
    return true;
  } catch (err) {
    if (state.token === token) reportDownloadError(err as Error & { code?: string });
    return false;
  }
}

/** 확장프로그램이 있으면 응답이 바로 오므로 목록보다 먼저 결과를 안다. 없으면 안내를 그대로 둘 뿐 기다리게 하지 않는다. */
function detectExtension(): void {
  if (state.extensionReady || isPhoneDevice()) return;
  ext.request("ping").then(() => {
    state.extensionReady = true;
    if (!state.extensionMissing && document.querySelector("#video-list .guide")) drawVideos();
  }).catch(() => {});
}

/* ---------- 조각 ---------- */

const downloadTip = (at?: string | null) => (at ? `${fmtDate(new Date(at))}에 받음. 누르면 다시 받아요` : "내려받기");

/** 줄의 내려받기 버튼. 받는 동안은 잠그고 준비 중·다운로드 중을 보인다. 받았는지는 아이콘 모양과 색으로 보인다. */
function DownloadButton({ linkPath, at, onDone }: { linkPath: string; at?: string | null; onDone: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const click = async () => {
    setBusy("준비 중");
    const ok = await downloadOne(linkPath, setBusy);
    setBusy(null);
    if (ok) onDone();
  };
  return (
    <button class={`btn btn-icon ${at ? "btn-ghost" : "btn-soft"}`} data-link={linkPath} title={downloadTip(at)} disabled={!!busy} onClick={click}>
      {busy ? <IconOnly name="loader" label={busy} /> : <IconOnly name={at ? "download-done" : "download"} label="내려받기" />}
    </button>
  );
}

/** 받았다는 글자는 보조기기용이다(눈에는 아이콘 모양·색으로 보인다). */
const DoneBadge = ({ at }: { at?: string | null }) => (at ? <span class="received sr-only">받음</span> : null);

/** 주차 전체 받기. 받을 수 있는 항목이 2개 이상일 때만 보인다. 1개뿐이면 그 줄의 버튼과 같은 동작이다. */
function BulkButton({ items, title }: { items: BulkItem[]; title: string }) {
  if (items.length < 2) return null;
  const label = `전체 내려받기 (${items.length})`;
  const face = <><Icon name="download" />전체 {items.length}</>;
  const common = { class: "btn btn-link gap-1 px-2 font-semibold whitespace-nowrap", "aria-label": `${title} ${label}`, title: label };
  if (isPhoneDevice()) return <button {...common} data-bulk="" disabled>{face}</button>;  // 이유는 화면 위쪽에 한 번만 알린다
  if (state.videoTab === "materials" && !bulkDownloadSupported()) {
    return <><button {...common} disabled>{face}</button><span class="reason">폴더에 바로 저장하는 기능은 데스크톱 Chrome·Edge에서만 지원해요</span></>;
  }
  return <button {...common} data-bulk="" disabled={!!state.bulk?.running} onClick={() => runBulkDownload(items)}>{face}</button>;
}

/** 설치 안내. 확장프로그램이 응답하기 전까지는 펼쳐 두어 처음 온 사람이 바로 설치할 수 있게 하고, 설치된 사람에게는 접어 둔다. */
function InstallGuide() {
  const ready = state.extensionReady && !state.extensionMissing;
  const open = !!state.extensionMissing || (!ready && !state.guideDismissed);
  return (
    <details class={`guide${ready ? "" : " needs-install"}`} open={open}
      onToggle={(e) => { state.guideDismissed = !(e.currentTarget as HTMLDetailsElement).open; }}>
      <summary><span class="guide-title"><Icon name="puzzle" />{ready ? "크롬 확장프로그램 안내" : "크롬 확장프로그램을 설치해 주세요"}</span></summary>
      <div class="guide-body">
        {state.extensionMissing && <p class="warning" role="alert">{state.extensionMissing}</p>}
        <ol class="install-steps">
          <li><strong>웹 스토어에서 설치</strong><span>‘Chrome에 추가’를 누르세요.</span>
            <a class="btn btn-primary mt-auto gap-1.5 self-start" href={EXTENSION_STORE_URL} target="_blank" rel="noopener">Chrome 웹 스토어 열기<Icon name="external" /></a></li>
          <li><strong>이 페이지 새로고침</strong><span>설치한 뒤 한 번만 하면 돼요.</span>
            <button class="btn mt-auto gap-1.5 self-start" type="button" data-guide-reload onClick={() => location.reload()}><Icon name="refresh" />새로고침</button></li>
          <li><strong>내려받기</strong><span>처음 열린 창에서 저장 폴더를 고르세요. 진행 상황과 취소도 그 창에서 볼 수 있어요.</span></li>
        </ol>
        <p class="guide-note">데스크톱 크롬에서만 쓸 수 있어요. 저장이 끝날 때까지 다운로드 창과 이 탭을 열어 두세요.</p>
      </div>
    </details>
  );
}

const VIDEO_STATUS: Record<string, [string, string]> = {
  done: ["check-circle", "수강 완료"],
  todo: ["circle", "미수강"],
  expired: ["clock", "시청기간 종료"],
  locked: ["lock", "아직 열리지 않음"],
};
const videoStatus = (v: VideoItem) => (v.available ? (v.completed ? "done" : "todo") : v.savedRow ? "expired" : "locked");

/** 글자를 뺀 아이콘의 뜻을 목록 위에 한 번만 적는다. 터치 화면에는 title이 없어서 필요하다. 지금 목록에 있는 것만 보인다. */
function IconLegend({ items, phone }: { items: VideoItem[]; phone: boolean }) {
  const has = (test: (v: VideoItem) => unknown) => items.some(test);
  const rows = [
    !phone && has((v) => v.linkPath && !v.downloaded_at) && ["download", "내려받기"],
    has((v) => v.downloaded_at) && ["download-done", "받음"],
    has((v) => v.mediaPath && !v.transcript_id) && ["text-convert", "텍스트로 변환"],
    has((v) => v.transcript_id) && ["text-view", "텍스트 보기"],
    ...Object.entries(VIDEO_STATUS).map(([key, row]) => has((v) => videoStatus(v) === key) && row),
  ].filter(Boolean) as [string, string][];
  return <ul class="icon-legend" aria-label="아이콘 설명">{rows.map(([name, label]) => <li key={name}><Icon name={name} />{label}</li>)}</ul>;
}

function groupBy<T extends { module_id: number | string; module_title: string }>(items: T[]): { id: number | string; title: string; items: T[] }[] {
  const groups = new Map<number | string, { id: number | string; title: string; items: T[] }>();
  for (const v of items) {
    if (!groups.has(v.module_id)) groups.set(v.module_id, { id: v.module_id, title: v.module_title, items: [] });
    groups.get(v.module_id)!.items.push(v);
  }
  return [...groups.values()];
}

/** 다시 그려도 표시가 남도록 원본 목록에도 받은 시각을 적는다. */
function markDownloaded(v: VideoItem): void {
  v.downloaded_at = new Date().toISOString();
  const src = state.videoList?.items.find((x: VideoItem) => x.module_item_id === v.module_item_id);
  if (src) src.downloaded_at = v.downloaded_at;
  if (v.savedRow) v.savedRow.downloaded_at = v.downloaded_at;
}

/** LMS 목록을 기준으로 하고, LMS에서 사라진 저장 영상을 뒤에 붙인다. */
function videoItems(courseId: number, phone: boolean): VideoItem[] {
  const course = state.courses.find((c) => c.lms_course_id === courseId);
  const saved = new Map((state.savedVideos as SavedVideo[]).filter((v) => v.lms_course_id === courseId).map((v) => [v.module_item_id, v]));
  let items: VideoItem[] = state.videoList.items.map((v: VideoItem) => ({ ...v, savedRow: saved.get(v.module_item_id) }));
  const live = new Set(items.map((v) => v.module_item_id));
  for (const s of saved.values()) {
    if (!live.has(s.module_item_id)) items.push({ ...s, module_id: `saved-${s.module_title}`, available: false, completed: false, savedRow: s } as unknown as VideoItem);
  }
  for (const v of items) {
    v.courseName = course?.name || v.savedRow?.course_name || "";
    v.downloaded_at = v.downloaded_at || v.savedRow?.downloaded_at || null;
    v.transcript_id = v.transcript_id ?? v.savedRow?.transcript_id ?? null;
    v.linkPath = v.available ? `/courses/${courseId}/videos/${v.module_item_id}/direct-download`
      : v.savedRow?.saved ? `/saved-videos/${v.savedRow.id}/direct-download` : null;
    v.mediaPath = phone ? null : v.linkPath;  // 오디오는 PC의 확장 프로그램이 뽑는다
  }
  if (state.notDownloadedOnly) items = items.filter((v) => !v.downloaded_at);
  return items;
}

function VideoStatus({ v }: { v: VideoItem }) {
  const key = videoStatus(v);
  const [name, label] = VIDEO_STATUS[key];
  const full = v.savedRow?.saved ? `${label}, 주소 저장됨` : label;
  return <span class={`video-status ${key}`} role="img" aria-label={full} title={full}><Icon name={name} /></span>;
}

function VideoList() {
  const courseId: number = state.videoList.courseId;
  const phone = isPhoneDevice();
  const items = videoItems(courseId, phone);
  if (!items.length) {
    return <div class="empty"><p>{state.notDownloadedOnly ? "아직 받지 않은 영상이 없습니다." : "이 과목에는 내려받을 수 있는 강의 영상이 없습니다."}</p></div>;
  }
  const done = (v: VideoItem) => () => { markDownloaded(v); renderVideoList(); };
  return (
    <>
      {!phone && <InstallGuide />}
      <IconLegend items={items} phone={phone} />
      {groupBy(items).map((g) => (
        <Fragment key={g.id}>
          <div class="group-label">
            <span class="group-title">{g.title}</span>
            <span class="group-actions">
              <BulkButton title={g.title} items={g.items.filter((v) => v.linkPath).map((v) => ({ linkPath: v.linkPath!, label: v.title, onDone: () => markDownloaded(v) }))} />
              <TranscribeGroupButton items={g.items} title={g.title} courseId={courseId} />
            </span>
          </div>
          <ul class="list">
            {g.items.map((v) => (
              <li class="video-row" key={v.module_item_id}>
                <VideoStatus v={v} />
                {v.thumbnail_url && <img class="thumb" src={v.thumbnail_url} alt="" loading="lazy" referrerpolicy="no-referrer"
                  onError={(e) => (e.currentTarget as HTMLImageElement).remove()} />}
                <span class="video-info"><span class="title">{v.title}</span><br />
                  <span class="meta flex flex-wrap items-center gap-x-2.5 gap-y-0.5"><span>{v.duration ? fmtDuration(v.duration) : ""}</span><span>{fmtSize(v.size)}</span><DoneBadge at={v.downloaded_at} /></span>
                  {/* 내려받을 수 없는 이유는 마우스를 올리지 않아도 보이게 글자로 둔다. 휴대폰은 화면 위쪽 안내 하나로 갈음한다. */}
                  {!v.linkPath && !phone && <span class="reason">{v.savedRow?.resolve_error || "지금은 내려받을 수 없어요"}</span>}
                </span>
                <span class="row-actions">
                  <TranscribeButton v={v} courseId={courseId} />
                  {v.linkPath && !phone
                    ? <DownloadButton linkPath={v.linkPath} at={v.downloaded_at} onDone={done(v)} />
                    : <button class="btn btn-icon btn-ghost" disabled><IconOnly name={v.downloaded_at ? "download-done" : "download"} label="내려받기" /></button>}
                </span>
              </li>
            ))}
          </ul>
        </Fragment>
      ))}
    </>
  );
}

function MaterialList() {
  const courseId: number = state.materialList.courseId;
  let items: MaterialItem[] = state.materialList.items.map((m: MaterialItem) => ({ ...m, linkPath: `/courses/${courseId}/materials/${encodeURIComponent(m.key)}/download-link` }));
  if (state.notDownloadedOnly) items = items.filter((m) => !m.downloaded_at);
  if (!items.length) {
    return <div class="empty"><p>{state.notDownloadedOnly ? "아직 받지 않은 자료가 없습니다." : "이 과목에는 받을 수 있는 수업자료가 없습니다."}</p></div>;
  }
  const mark = (key: string) => {
    const src = state.materialList.items.find((x: MaterialItem) => x.key === key);
    if (src) src.downloaded_at = new Date().toISOString();
  };
  return (
    <>
      {groupBy(items).map((g) => (
        <Fragment key={g.id}>
          <div class="group-label">
            <span class="group-title">{g.title}</span>
            <span class="group-actions"><BulkButton title={g.title} items={g.items.map((m) => ({ linkPath: m.linkPath, label: m.file_name, onDone: () => mark(m.key) }))} /></span>
          </div>
          <ul class="list">
            {g.items.map((m) => (
              <li class="video-row" key={m.key}>
                <span class="video-info"><span class="title">{m.file_name}</span><br />
                  <span class="meta flex flex-wrap items-center gap-x-2.5 gap-y-0.5"><span>{m.title}</span><span>{fmtSize(m.size)}</span>
                    {m.kind === "canvas_file" && <span>위키 첨부</span>}<DoneBadge at={m.downloaded_at} /></span></span>
                <span class="row-actions"><DownloadButton linkPath={m.linkPath} at={m.downloaded_at} onDone={() => { mark(m.key); renderCurrentTab(); }} /></span>
              </li>
            ))}
          </ul>
        </Fragment>
      ))}
    </>
  );
}

/* ---------- 화면 ---------- */

const TITLE = "내려받기·텍스트 변환";

function NoCourses() {
  return <><ViewHead title={TITLE} /><Empty><p>과목이 없습니다. ‘LMS 동기화’를 눌러 불러오세요.</p></Empty></>;
}

function Videos({ loading }: { loading: boolean }) {
  const { courses, pastCourses, isPast } = courseOptions();
  const phone = isPhoneDevice();
  const list = state.videoTab === "materials" ? state.materialList : state.videoList;
  const ready = !loading && list?.courseId === state.videoCourse;
  const setTab = (tab: string) => { state.videoTab = tab; viewVideos(); };
  return (
    <>
      <ViewHead title={TITLE}>
        <label>과목<select id="video-course" value={String(state.videoCourse)}
          onChange={(e) => { state.videoCourse = Number(e.currentTarget.value); state.videoList = null; state.materialList = null; viewVideos(); }}>
          {courses.map((c) => <option key={c.lms_course_id} value={String(c.lms_course_id)}>{c.name}</option>)}
          {pastCourses.length > 0 && (
            <optgroup label="지난 과목 (저장된 영상)">{pastCourses.map(([id, name]) => <option key={id} value={String(id)}>{name}</option>)}</optgroup>
          )}
        </select></label>
      </ViewHead>
      <div class="flex flex-wrap items-center gap-1.5" role="tablist">
        <button class="min-h-touch cursor-pointer rounded-chip border-2 border-ink bg-surface px-3.5 font-sans text-md leading-normal font-semibold text-ink aria-selected:bg-ink aria-selected:text-white disabled:cursor-not-allowed disabled:opacity-45 max-phone:px-3" role="tab" data-tab="videos" aria-selected={state.videoTab === "videos"} onClick={() => setTab("videos")}>영상</button>
        <button class="min-h-touch cursor-pointer rounded-chip border-2 border-ink bg-surface px-3.5 font-sans text-md leading-normal font-semibold text-ink aria-selected:bg-ink aria-selected:text-white disabled:cursor-not-allowed disabled:opacity-45 max-phone:px-3" role="tab" data-tab="materials" aria-selected={state.videoTab === "materials"} disabled={isPast}
          aria-describedby={isPast ? "tabs-reason" : undefined} onClick={() => setTab("materials")}>수업자료</button>
        <label class="check ml-auto text-sm text-ink-2"><input type="checkbox" id="not-downloaded" checked={state.notDownloadedOnly}
          onChange={(e) => { state.notDownloadedOnly = e.currentTarget.checked; renderCurrentTab(); }} /><span>안 받은 것만</span></label>
      </div>
      {isPast && <p class="tabs-reason" id="tabs-reason">지난 과목은 수업자료를 볼 수 없어요</p>}
      {phone && <p class="notice-inline" role="note">{PHONE_NOTICE}</p>}
      <details class="notice-inline notice-fold overflow-hidden p-0"><summary>개인 복습용이에요. 공유하지 마세요</summary><p>본인이 수강하는 강의와 자료를 개인 복습용으로 받는 기능입니다. 강의 영상과 자료는 교수와 학교의 저작물이므로 다른 사람에게 공유하거나 올리지 마세요. 파일은 서버에 저장하지 않고 학교에서 받는 대로 바로 전달합니다. 동기화 때 영상 주소를 저장해 두므로 시청기간이 끝난 영상도 받을 수 있지만, 학교 서버가 주소를 막거나 영상을 지우면 받을 수 없습니다.</p></details>
      <div class="panel" id="video-list" aria-busy={ready ? undefined : "true"}>
        {!ready
          ? <><p class="sr-only" role="status">LMS에서 목록을 불러오는 중</p><div class="skeleton-list" aria-hidden="true">{[0, 1, 2, 3].map((n) => <div key={n} class="skeleton" />)}</div></>
          : state.videoTab === "materials" ? <MaterialList /> : <VideoList />}
      </div>
    </>
  );
}
