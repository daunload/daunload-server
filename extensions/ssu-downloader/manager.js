"use strict";
let directory = null;
const active = new Map();
const reservedNames = new Set();
let refreshing = false;
let refreshAgain = false;

async function send(action, data = {}) {
  const reply = await chrome.runtime.sendMessage({ action, ...data });
  if (reply.error) throw new Error(reply.error);
  return reply.result;
}
const patch = (job, update) => send("manager-update", { job, patch: update });

async function uniqueFile(name) {
  const stem = name.slice(0, -4);
  for (let i = 0; i < 10000; i++) {
    const candidate = i ? `${stem} (${i}).mp4` : name;
    if (reservedNames.has(candidate)) continue;
    reservedNames.add(candidate);
    try {
      await directory.getFileHandle(candidate);
      reservedNames.delete(candidate);
    } catch (error) {
      if (error.name !== "NotFoundError") { reservedNames.delete(candidate); throw error; }
      return { name: candidate, handle: await directory.getFileHandle(candidate, { create: true }) };
    }
  }
  throw new Error("같은 이름의 파일이 너무 많습니다.");
}

async function transfer(key, job, controller) {
  let file = null;
  let writable = null;
  let committed = false;
  try {
    await patch(key, { state: "in_progress" });
    const response = await fetch(job.url, { signal: controller.signal, credentials: "omit" });
    if (!response.ok) throw new Error(`학교 서버 응답 오류 (${response.status}). 대시보드에서 다시 시도해 주세요.`);
    // DNR blocks off-domain redirects; validate the final URL as a second check.
    const final = new URL(response.url);
    if (final.protocol !== "https:" || !["ssu.ac.kr", "commonscdn.com"].some(h => final.hostname === h || final.hostname.endsWith(`.${h}`))) {
      throw new Error("학교 밖의 다운로드 주소입니다.");
    }
    if ((response.headers.get("content-type") || "").includes("text/html")) throw new Error("영상 대신 로그인 페이지를 받았습니다.");
    file = await uniqueFile(job.filename);
    writable = await file.handle.createWritable();
    const totalBytes = Number(response.headers.get("content-length")) || 0;
    let bytesReceived = 0;
    let lastUpdate = 0;
    const meter = new TransformStream({ transform(chunk, output) {
      bytesReceived += chunk.byteLength;
      output.enqueue(chunk);
      if (Date.now() - lastUpdate > 1000) {
        lastUpdate = Date.now();
        patch(key, { bytesReceived, totalBytes }).catch(() => {});
      }
    } });
    await response.body.pipeThrough(meter).pipeTo(writable, { signal: controller.signal });
    committed = true;
    await patch(key, { state: "complete", bytesReceived, totalBytes, savedName: file.name });
  } catch (error) {
    if (writable && !committed) await writable.abort().catch(() => {});
    // The selected name was newly created: never remove a pre-existing user file.
    if (file && !committed) await directory.removeEntry(file.name).catch(() => {});
    await patch(key, { state: "interrupted", error: controller.signal.aborted ? "사용자가 취소했습니다." : error.message });
  } finally {
    if (file) reservedNames.delete(file.name);
    active.delete(key);
    refresh();
  }
}

const jobRows = new Map();
const videoIcon = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="3"/><path d="m10 8 5 4-5 4V8Z"/></svg>';

function formatBytes(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)} GB`;
  return `${(value / 1e6).toFixed(1)} MB`;
}

function setText(element, value) {
  // Avoid repeatedly announcing an unchanged live-region message.
  if (element.textContent !== value) element.textContent = value;
}

function createJobRow(key) {
  const row = document.createElement("li");
  row.className = "job";
  // This template is constant. All filenames and server errors use textContent below.
  row.innerHTML = `<div class="file-icon">${videoIcon}</div><div class="job-content">
    <h3 class="job-title"></h3><div class="job-meta"><span class="badge"></span><span class="job-size"></span></div>
    <div class="progress-line"><progress></progress><span class="percentage" aria-hidden="true"></span></div>
    <p class="job-error"></p></div><button class="button cancel" type="button">취소</button>`;
  const parts = Object.fromEntries(["job-title", "badge", "job-size", "progress-line", "percentage", "job-error", "cancel"]
    .map(name => [name, row.querySelector(`.${name}`)]));
  parts.progress = row.querySelector("progress");
  parts.cancel.onclick = async () => {
    const job = jobRows.get(key)?.job;
    if (!job) return;
    parts.cancel.disabled = true;
    try {
      active.get(key)?.abort();
      await patch(key, { cancelled: true, ...(job.state === "queued" ? { state: "interrupted", error: "사용자가 취소했습니다." } : {}) });
    } catch (error) {
      document.querySelector("#error").textContent = error.message;
      parts.cancel.disabled = false;
    }
  };
  return { row, parts, job: null };
}

function render(jobs) {
  const list = document.querySelector("#jobs");
  const entries = Object.entries(jobs);
  const count = state => entries.filter(([, job]) => job.state === state).length;
  const running = count("in_progress"), queued = count("queued"), complete = count("complete"), interrupted = count("interrupted");
  setText(document.querySelector("#total-count"), String(entries.length));
  setText(document.querySelector("#list-count"), String(entries.length));
  setText(document.querySelector("#active-count"), String(running));
  setText(document.querySelector("#complete-count"), String(complete));
  const summary = !entries.length ? "대시보드에서 내려받을 강의를 선택해 주세요." :
    !directory && queued ? `${queued}개 대기 중 · 먼저 저장 폴더를 선택해 주세요.` :
    running || queued ? `${running}개 저장 중 · ${queued}개 대기 · ${complete}개 완료` :
    interrupted ? `${complete}개 완료 · ${interrupted}개 중단됨. 아래 내용을 확인해 주세요.` : "선택한 강의를 모두 저장했어요.";
  setText(document.querySelector("#queue-status"), summary);
  document.querySelector("#empty").hidden = entries.length > 0;
  for (const [key, item] of jobRows) {
    if (!Object.hasOwn(jobs, key)) { item.row.remove(); jobRows.delete(key); }
  }
  for (const [key, job] of entries) {
    if (!jobRows.has(key)) {
      const item = createJobRow(key);
      jobRows.set(key, item);
      list.append(item.row);
    }
    const item = jobRows.get(key);
    item.job = job;
    const { row, parts } = item;
    const pending = ["queued", "in_progress"].includes(job.state);
    const cancelled = job.cancelled && job.state === "interrupted";
    const name = job.savedName || job.filename;
    row.dataset.state = cancelled ? "cancelled" : job.state;
    setText(parts["job-title"], name);
    setText(parts.badge, cancelled ? "취소됨" : ({queued: "대기 중", in_progress: "저장 중", complete: "저장 완료", interrupted: "저장 중단"}[job.state] || "상태 확인 중"));
    setText(parts["job-size"], job.state === "queued" ? (directory ? "차례가 되면 자동으로 시작합니다" : "저장 폴더 선택 후 시작") :
      job.state === "in_progress" ? `${formatBytes(job.bytesReceived)}${job.totalBytes > 0 ? ` / ${formatBytes(job.totalBytes)}` : ""}` :
      job.state === "complete" ? `${formatBytes(job.bytesReceived)} · 폴더에 저장됨` : "");
    setText(parts["job-error"], job.state === "interrupted" && !cancelled ? job.error || "대시보드에서 다시 내려받기를 눌러 주세요." : "");
    parts["progress-line"].hidden = !pending;
    parts.progress.setAttribute("aria-label", `${name} 다운로드 진행률`);
    if (job.state === "queued") {
      parts.progress.max = 100;
      parts.progress.value = 0;
      setText(parts.percentage, "0%");
    } else if (job.totalBytes > 0) {
      parts.progress.max = job.totalBytes;
      parts.progress.value = Math.min(job.bytesReceived || 0, job.totalBytes);
      setText(parts.percentage, `${Math.min(100, Math.floor((job.bytesReceived || 0) / job.totalBytes * 100))}%`);
    } else {
      parts.progress.removeAttribute("value");
      setText(parts.percentage, "—");
    }
    parts.cancel.hidden = !pending;
    parts.cancel.disabled = Boolean(job.cancelled);
    parts.cancel.setAttribute("aria-label", `${name} 다운로드 취소`);
  }
  const choose = document.querySelector("#choose");
  choose.disabled = active.size > 0;
  choose.title = active.size > 0 ? "다운로드가 끝나면 저장 폴더를 변경할 수 있습니다." : "";
  setText(document.querySelector("#choose-label"), directory ? "폴더 변경" : "저장 폴더 선택");
  setText(document.querySelector("#destination-title"), directory ? "강의 저장 폴더" : "어디에 저장할까요?");
}

async function refresh() {
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try {
    const jobs = await send("manager-list");
    for (const [key, job] of Object.entries(jobs)) {
      if (job.cancelled) active.get(key)?.abort();
      if (directory && job.state === "queued" && !job.cancelled && !active.has(key) && active.size < 2) {
        const controller = new AbortController();
        active.set(key, controller);
        transfer(key, job, controller).catch(error => { document.querySelector("#error").textContent = error.message; });
      }
    }
    render(jobs);
  } catch (error) { document.querySelector("#error").textContent = error.message; }
  finally { refreshing = false; if (refreshAgain) { refreshAgain = false; refresh(); } }
}

document.querySelector("#choose").onclick = async () => {
  try {
    directory = await window.showDirectoryPicker({ mode: "readwrite" });
    document.querySelector("#folder").textContent = `저장 폴더: ${directory.name}`;
    document.querySelector("#error").textContent = "";
    await refresh();
  } catch (error) { if (error.name !== "AbortError") document.querySelector("#error").textContent = error.message; }
};
chrome.storage.onChanged.addListener((changes, area) => { if (area === "session" && changes.jobs) refresh(); });
window.addEventListener("beforeunload", (event) => { if (active.size) { event.preventDefault(); event.returnValue = ""; } });
// A reloaded manager cannot resume old fetch streams; make interruption visible instead of hanging.
(async () => {
  const jobs = await send("manager-list");
  for (const [key, job] of Object.entries(jobs)) {
    if (job.state === "in_progress") await patch(key, { state: "interrupted", code: "MANAGER_RELOADED", error: "다운로드 창을 새로고침했습니다. 다시 시도해 주세요." });
  }
  await refresh();
})().catch(error => { document.querySelector("#error").textContent = error.message; });
