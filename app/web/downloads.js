"use strict";

/* ---------------- 여러 개 받기 ----------------
 * 영상: 확장 다운로드 창에서 CDN → 선택 폴더로 직접 저장한다.
 * 자료: <a download>는 끝났는지 알 수 없어 동시 수를 지킬 수 없다. 사용자가 고른 폴더에
 * fetch 응답을 스트리밍으로 써서 하나씩 끝을 확인한다(메모리에 올리지 않는다).
 * 서버 제한과 같게 동시 2개. 429(텍스트 변환 등이 자리를 쓰는 중)면 자리가 날 때까지 또는 중지할 때까지 기다린다. */

const BULK_CONCURRENCY = 2;
const BULK_RETRY_MS = 3000;
const LINK_REUSE_MS = 8 * 60 * 1000;  // 링크는 10분짜리다. 만료 전까지는 같은 링크로 다시 시도한다
const bulk = { ctrl: null };

function bulkDownloadSupported() {
  return typeof window.showDirectoryPicker === "function";
}

function stopBulkDownload() {
  bulk.ctrl?.abort();
}

async function startBulkDownload(items, onUpdate) {
  if (bulk.ctrl) throw new Error("이미 여러 개 받기를 진행하고 있습니다.");
  const direct = items.every((item) => item.linkPath.endsWith("/direct-download"));
  const controller = new AbortController();
  bulk.ctrl = controller;
  const signal = controller.signal;
  let dir = null;
  let session = null;
  try {
    if (direct) {
      await requireDownloadExtension();
      signal.throwIfAborted();
      session = (await extensionRequest("prepare")).session;
    } else {
      dir = await window.showDirectoryPicker({ mode: "readwrite" });
    }
    signal.throwIfAborted();
  } catch (error) { bulk.ctrl = null; throw error; }
  const queue = [...items];
  const stat = { done: 0, failed: 0, total: items.length, running: true, errors: [], failedItems: [], direct };
  onUpdate({ ...stat });
  const pending = [];
  const managerGone = (err) => ["MANAGER_CLOSED", "MANAGER_RELOADED"].includes(err?.code);
  const failed = (item, err) => {
    if (signal.aborted) {
      // 다운로드 창이 닫혀 멈춘 경우 끝내지 못한 항목은 다시 시도할 수 있게 남긴다. 사용자가 멈춘 경우는 남기지 않는다.
      if (managerGone(signal.reason)) stat.failedItems.push(item);
      return;
    }
    if (managerGone(err)) {
      stat.errors.push(err.message);
      stat.failedItems.push(item);
      controller.abort(err);
      return;
    }
    stat.failed++;
    stat.failedItems.push(item);
    stat.errors.push(`${item.label}: ${err.message}`);
  };
  const track = async (item, task) => {
    try {
      await task;
      stat.done++;
      item.onDone?.();
    } catch (err) { failed(item, err); }
    onUpdate({ ...stat });
  };
  const worker = async () => {
    while (queue.length && !signal.aborted) {
      const item = queue.shift();
      try {
        if (direct) {
          // Register every selected video without waiting for previous transfers.
          // Metadata requests remain limited to two; the extension schedules file I/O.
          const handle = await enqueueDirectVideo(item, signal, session);
          pending.push(track(item, waitDirectVideo(handle, signal)));
        } else {
          await track(item, saveToFolder(dir, item, signal));
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

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

async function saveToFolder(dir, item, signal) {
  let link = null;
  let issuedAt = 0;
  for (;;) {
    // 링크 발급이 이력에 남으므로, 기다리는 동안에는 만료가 가까울 때만 새로 받는다
    if (!link || Date.now() - issuedAt > LINK_REUSE_MS) {
      link = await api(item.linkPath, { method: "POST" });
      issuedAt = Date.now();
    }
    const res = await fetch(link.url, { signal });
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
    const handle = await dir.getFileHandle(link.file_name, { create: true });
    await res.body.pipeTo(await handle.createWritable(), { signal });
    return;
  }
}


// The bridge is injected only on the configured dashboard origin by the extension.
function extensionRequest(action, data = {}) {
  return new Promise((resolve, reject) => {
    const id = crypto.randomUUID();
    const cleanup = () => { clearTimeout(timer); window.removeEventListener("message", receive); };
    const receive = (event) => {
      if (event.source !== window || event.origin !== location.origin ||
          event.data?.channel !== "ssu-download-response" || event.data.id !== id) return;
      cleanup();
      if (event.data.error) reject(new Error(event.data.error));
      else resolve(event.data.result);
    };
    const timer = setTimeout(() => {
      cleanup();
      const error = new Error("아유 하기싫어 확장프로그램에 연결하지 못했습니다. 설치·사용 설정 후 페이지를 새로고침해 주세요.");
      if (action === "ping") error.code = "EXTENSION_MISSING";
      reject(error);
    }, action === "ping" ? 1500 : 15000);
    window.addEventListener("message", receive);
    window.postMessage({ channel: "ssu-download-request", id, action, ...data }, location.origin);
  });
}

async function requireDownloadExtension() {
  const result = await extensionRequest("ping");
  state.extensionReady = true;
  if (result?.version !== 1 || !result.sessions || !result.queue) throw new Error("아유 하기싫어 확장프로그램을 업데이트해 주세요.");
  state.extensionMissing = null;
}

async function enqueueDirectVideo(item, signal, session = null) {
  signal.throwIfAborted();
  if (!session) session = (await extensionRequest("prepare")).session;
  signal.throwIfAborted();
  const job = crypto.randomUUID();
  const link = await api(item.linkPath, { method: "POST" });
  try {
    for (;;) {
      signal.throwIfAborted();
      const result = await extensionRequest("start", { job, session, data: { url: link.url, file_name: link.file_name } });
      if (result.closed) throw Object.assign(new Error(result.error || "다운로드 창을 닫았습니다."), { code: "MANAGER_CLOSED" });
      if (result.started) return { job, link };
      if (!result.busy) throw new Error("다운로드를 시작하지 못했습니다.");
      await sleep(1500, signal);
    }
  } catch (error) {
    // Cancel even if the start response was lost after registering the task.
    await extensionRequest("cancel", { job }).catch(() => {});
    throw error;
  }
}

async function waitDirectVideo({ job, link }, signal) {
  try {
    for (;;) {
      signal.throwIfAborted();
      const result = await extensionRequest("status", { job });
      if (result.state === "complete") break;
      if (result.state === "interrupted") throw Object.assign(new Error(`다운로드가 중단되었습니다 (${result.error || "알 수 없는 오류"}). 확장프로그램 다운로드 창을 확인해 주세요.`), { code: result.code });
      await sleep(1000, signal);
    }
  } catch (error) {
    if (signal.aborted) await extensionRequest("cancel", { job }).catch(() => {});
    throw error;
  }
  try {
    await api("/downloads/complete", { method: "POST", json: { receipt: link.receipt } });
  } catch {
    toast("파일은 저장했지만 완료 이력을 서버에 기록하지 못했습니다.", true);
  }
}


async function downloadDirectVideo(item, signal, session = null) {
  await waitDirectVideo(await enqueueDirectVideo(item, signal, session), signal);
}
