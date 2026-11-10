import { ApiError, api, state } from "./core";
import { toast } from "./ui";
import { showAccessScreen } from "./access";
import { ext } from "./extension";

/* ---------------- 여러 개 받기 ----------------
 * 영상: 확장 다운로드 창에서 CDN → 선택 폴더로 직접 저장한다.
 * 자료: <a download>는 끝났는지 알 수 없어 동시 수를 지킬 수 없다. 사용자가 고른 폴더에
 * fetch 응답을 스트리밍으로 써서 하나씩 끝을 확인한다(메모리에 올리지 않는다).
 * 서버 제한과 같게 동시 2개. 429(텍스트 변환 등이 자리를 쓰는 중)면 자리가 날 때까지 또는 중지할 때까지 기다린다. */

const BULK_CONCURRENCY = 2;
const BULK_RETRY_MS = 3000;
const LINK_REUSE_MS = 8 * 60 * 1000;  // 링크는 10분짜리다. 만료 전까지는 같은 링크로 다시 시도한다
/** 여러 개 받기의 한 항목. onDone은 받은 뒤 목록에 받음 표시를 남긴다. */
export interface BulkItem { linkPath: string; label: string; onDone?: () => void }
/** 진행 상황(하단 패널과 videos 화면이 읽는다). */
export interface BulkStat {
  done: number; failed: number; total: number; running: boolean; errors: string[]; failedItems: BulkItem[]; direct: boolean; stopped?: boolean;
}
interface Link { url: string; file_name: string; receipt?: string }
interface DirectHandle { job: string; link: Link }
type CodedError = Error & { code?: string };
// 폴더에 바로 쓰는 File System Access API(데스크톱 Chrome·Edge). 표준 타입에 아직 없다.
type DirectoryPicker = (opts: { mode: "readwrite" }) => Promise<{ getFileHandle(name: string, o: { create: boolean }): Promise<{ createWritable(): Promise<WritableStream> }> }>;
const picker = () => (window as unknown as { showDirectoryPicker?: DirectoryPicker }).showDirectoryPicker;

export const bulk: { ctrl: AbortController | null } = { ctrl: null };

export function bulkDownloadSupported() {
  return typeof picker() === "function";
}

export function stopBulkDownload() {
  bulk.ctrl?.abort();
}

export async function startBulkDownload(items: BulkItem[], onUpdate: (stat: BulkStat) => void): Promise<void> {
  if (bulk.ctrl) throw new Error("이미 여러 개 받기를 진행하고 있습니다.");
  const direct = items.every((item) => item.linkPath.endsWith("/direct-download"));
  const controller = new AbortController();
  bulk.ctrl = controller;
  const signal = controller.signal;
  let dir: Awaited<ReturnType<DirectoryPicker>> | null = null;
  let session: string | null = null;
  try {
    if (direct) {
      await requireDownloadExtension();
      signal.throwIfAborted();
      session = (await ext.request("prepare")).session;
    } else {
      dir = await picker()!({ mode: "readwrite" });
    }
    signal.throwIfAborted();
  } catch (error) { bulk.ctrl = null; throw error; }
  const queue = [...items];
  const stat: BulkStat = { done: 0, failed: 0, total: items.length, running: true, errors: [], failedItems: [], direct };
  onUpdate({ ...stat });
  const pending: Promise<void>[] = [];
  const managerGone = (err: unknown) => ["MANAGER_CLOSED", "MANAGER_RELOADED"].includes((err as CodedError)?.code ?? "");
  const failed = (item: BulkItem, err: unknown) => {
    if (signal.aborted) {
      // 다운로드 창이 닫혀 멈춘 경우 끝내지 못한 항목은 다시 시도할 수 있게 남긴다. 사용자가 멈춘 경우는 남기지 않는다.
      if (managerGone(signal.reason)) stat.failedItems.push(item);
      return;
    }
    if (managerGone(err)) {
      stat.errors.push((err as Error).message);
      stat.failedItems.push(item);
      controller.abort(err);
      return;
    }
    stat.failed++;
    stat.failedItems.push(item);
    stat.errors.push(`${item.label}: ${(err as Error).message}`);
  };
  const track = async (item: BulkItem, task: Promise<void>) => {
    try {
      await task;
      stat.done++;
      item.onDone?.();
    } catch (err) { failed(item, err); }
    onUpdate({ ...stat });
  };
  const worker = async () => {
    while (queue.length && !signal.aborted) {
      const item = queue.shift()!;
      try {
        if (direct) {
          // Register every selected video without waiting for previous transfers.
          // Metadata requests remain limited to two; the extension schedules file I/O.
          const handle = await enqueueDirectVideo(item, signal, session);
          pending.push(track(item, waitDirectVideo(handle, signal)));
        } else {
          await track(item, saveToFolder(dir!, item, signal));
        }
      } catch (err) { failed(item, err); onUpdate({ ...stat }); }
    }
  };
  try {
    await Promise.all(Array.from({ length: BULK_CONCURRENCY }, worker));
    await Promise.all(pending);
  } finally {
    bulk.ctrl = null;
    stat.running = false;
    // 창이 닫혀 멈췄다면 아직 시작하지 못한 항목도 다시 시도 대상이다
    if (managerGone(signal.reason)) stat.failedItems.push(...queue.splice(0));
    onUpdate({ ...stat, stopped: signal.aborted && !managerGone(signal.reason) });
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

async function saveToFolder(dir: NonNullable<Awaited<ReturnType<DirectoryPicker>>>, item: BulkItem, signal: AbortSignal): Promise<void> {
  let link: Link | null = null;
  let issuedAt = 0;
  for (;;) {
    // 링크 발급이 이력에 남으므로, 기다리는 동안에는 만료가 가까울 때만 새로 받는다
    if (!link || Date.now() - issuedAt > LINK_REUSE_MS) {
      link = await api<Link>(item.linkPath, { method: "POST" });
      issuedAt = Date.now();
    }
    const res = await fetch(link!.url, { signal });
    if (res.status === 429) {
      await sleep(BULK_RETRY_MS, signal);
      continue;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      if (res.status === 403 && data?.detail?.code === "ACCESS_REQUIRED") {
        showAccessScreen();  // 화면을 접으면서 여러 개 받기도 멈춘다
        throw new ApiError(403, data.detail.message, "ACCESS_REQUIRED");
      }
      throw new Error(typeof data?.detail === "string" ? data.detail : `요청 실패 (${res.status})`);
    }
    const handle = await dir.getFileHandle(link!.file_name, { create: true });
    await res.body!.pipeTo(await handle.createWritable(), { signal });
    return;
  }
}


export async function requireDownloadExtension(): Promise<void> {
  const result = await ext.request("ping");
  state.extensionReady = true;
  if (result?.version !== 1 || !result.sessions || !result.queue) throw new Error("아유 하기싫어 확장프로그램을 업데이트해 주세요.");
  state.extensionMissing = null;
}

export async function requireAudioExtension(): Promise<void> {
  await requireDownloadExtension();
  const result = await ext.request("ping");
  if (!result?.audio) throw new Error("아유 하기싫어 확장프로그램을 업데이트해 주세요.");
}

async function enqueueDirectVideo(item: { linkPath: string }, signal: AbortSignal, session: string | null = null): Promise<DirectHandle> {
  signal.throwIfAborted();
  if (!session) session = (await ext.request("prepare")).session;
  signal.throwIfAborted();
  const job = crypto.randomUUID();
  const link = await api<Link>(item.linkPath, { method: "POST" });
  try {
    for (;;) {
      signal.throwIfAborted();
      const result = await ext.request("start", { job, session, data: { url: link.url, file_name: link.file_name } });
      if (result.closed) throw Object.assign(new Error(result.error || "다운로드 창을 닫았습니다."), { code: "MANAGER_CLOSED" });
      if (result.started) return { job, link };
      if (!result.busy) throw new Error("다운로드를 시작하지 못했습니다.");
      await sleep(1500, signal);
    }
  } catch (error) {
    // Cancel even if the start response was lost after registering the task.
    await ext.request("cancel", { job }).catch(() => {});
    throw error;
  }
}

async function waitDirectVideo({ job, link }: DirectHandle, signal: AbortSignal): Promise<void> {
  try {
    for (;;) {
      signal.throwIfAborted();
      const result = await ext.request("status", { job });
      if (result.state === "complete") break;
      if (result.state === "interrupted") throw Object.assign(new Error(`다운로드가 중단되었습니다 (${result.error || "알 수 없는 오류"}). 확장프로그램 다운로드 창을 확인해 주세요.`), { code: result.code });
      await sleep(1000, signal);
    }
  } catch (error) {
    if (signal.aborted) await ext.request("cancel", { job }).catch(() => {});
    throw error;
  }
  try {
    await api("/downloads/complete", { method: "POST", json: { receipt: link.receipt } });
  } catch {
    toast("파일은 저장했지만 완료 이력을 서버에 기록하지 못했습니다.", true);
  }
}


export async function downloadDirectVideo(item: { linkPath: string }, signal: AbortSignal, session: string | null = null): Promise<void> {
  await waitDirectVideo(await enqueueDirectVideo(item, signal, session), signal);
}
