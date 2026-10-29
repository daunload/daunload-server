"use strict";

/* ---------------- 여러 개 받기 ----------------
 * <a download>는 끝났는지 알 수 없어 동시 수를 지킬 수 없다. 사용자가 고른 폴더에
 * fetch 응답을 스트리밍으로 써서 하나씩 끝을 확인한다(메모리에 올리지 않는다).
 * 서버 제한과 같게 동시 2개. 429(전사 등이 자리를 쓰는 중)면 자리가 날 때까지 또는 중지할 때까지 기다린다. */

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
  const dir = await window.showDirectoryPicker({ mode: "readwrite" });
  bulk.ctrl = new AbortController();
  const signal = bulk.ctrl.signal;
  const queue = [...items];
  const stat = { done: 0, failed: 0, total: items.length, running: true, errors: [] };
  onUpdate({ ...stat });
  const worker = async () => {
    while (queue.length && !signal.aborted) {
      const item = queue.shift();
      try {
        await saveToFolder(dir, item, signal);
        stat.done++;
        item.onDone?.();
      } catch (err) {
        if (signal.aborted) break;
        stat.failed++;
        stat.errors.push(`${item.label}: ${err.message}`);
      }
      onUpdate({ ...stat });
    }
  };
  try {
    await Promise.all(Array.from({ length: BULK_CONCURRENCY }, worker));
  } finally {
    bulk.ctrl = null;
    stat.running = false;
    onUpdate({ ...stat, stopped: signal.aborted });
  }
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(timer); reject(signal.reason); }, { once: true });
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
      throw new Error(typeof data?.detail === "string" ? data.detail : `요청 실패 (${res.status})`);
    }
    const handle = await dir.getFileHandle(link.file_name, { create: true });
    await res.body.pipeTo(await handle.createWritable(), { signal });
    return;
  }
}
