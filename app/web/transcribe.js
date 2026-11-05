"use strict";

/* ---------------- 브라우저 Whisper 텍스트 변환 ----------------
 * 서버의 16kHz PCM 스트림을 Worker로 넘겨 텍스트로 변환하고, 끝나면 결과를 서버에 저장한다.
 * 한 번에 하나씩 처리한다. 탭을 닫으면 진행 중인 작업은 사라진다. */

const WHISPER_RATE = 16000;
const READ_AHEAD_SECONDS = 60;  // Worker보다 이만큼만 앞서 받는다(메모리 일정)
const MIN_AUDIO_RATIO = 0.97;   // LMS의 영상 길이와 실제 오디오 길이의 오차 허용
const ASSET_QUERY = document.currentScript?.src ? new URL(document.currentScript.src).search : "";  // 서버가 붙인 버전을 워커 주소에도 쓴다
const tx = { worker: null, device: null, modelPercent: null, queue: [], current: null, finished: [], nextId: 1, epoch: 0 };

function txKey(courseId, moduleItemId) { return `${courseId}:${moduleItemId}`; }

function ensureWorker() {
  if (tx.worker) return tx.worker;
  const w = new Worker(`/whisper.worker.js${ASSET_QUERY}`, { type: "module" });
  w.addEventListener("message", onWorkerMessage);
  w.addEventListener("error", (e) => failAll(`Whisper를 불러오지 못했습니다: ${e.message || "네트워크를 확인하세요"}`));
  tx.worker = w;
  return w;
}

function onWorkerMessage({ data: m }) {
  if (m.type === "model-progress") { tx.modelPercent = m.percent; renderTaskPanel(); return; }
  if (m.type === "device") {
    tx.device = m.device;
    tx.modelPercent = null;
    if (m.device === "wasm" && !store.get("whisper-cpu-notice")) {
      toast("이 브라우저는 WebGPU를 지원하지 않아 CPU로 텍스트 변환합니다. 강의 길이보다 오래 걸릴 수 있습니다.");
      store.set("whisper-cpu-notice", "1");
    }
    renderTaskPanel();
    return;
  }
  const job = tx.current;
  if (!job || m.jobId !== job.id) return;
  if (m.type === "progress") { job.seconds = m.seconds; job.wake?.(); renderTaskPanel(); }
  else if (m.type === "segment") job.segments.push({ start: m.start, end: m.end, text: m.text });
  else if (m.type === "done") job.resolve();
  else if (m.type === "error") {
    job.reject(new Error(m.message));
    if (m.fatal) failAll(m.message);
  }
}

function failAll(message) {
  tx.worker?.terminate();
  tx.worker = null;
  tx.current?.reject?.(new Error(message));
  for (const job of tx.queue.splice(0)) tx.finished.push({ ...job, status: "failed", error: message });
  renderTaskPanel();
}

function enqueueTranscription(items) {
  for (const it of items) {
    const key = txKey(it.courseId, it.moduleItemId);
    if (tx.current?.key === key || tx.queue.some((q) => q.key === key)) continue;
    tx.finished = tx.finished.filter((f) => f.key !== key);
    tx.queue.push({ ...it, key, id: tx.nextId++, status: "queued", seconds: 0, duration: it.duration || null });
  }
  renderTaskPanel();
  if (!tx.current) runNext();
}

function cancelTranscription() {
  const job = tx.current;
  if (!job) return;
  job.status = "cancelled";
  job.ctrl.abort();
  tx.worker?.postMessage({ type: "cancel", jobId: job.id });
  job.reject(new Error("중지했습니다."));
}

async function runNext() {
  const job = tx.queue.shift() || null;
  tx.current = job;
  renderTaskPanel();
  if (!job) return;
  job.status = "running";
  job.epoch = tx.epoch;
  job.segments = [];
  job.ctrl = new AbortController();
  job.done = new Promise((resolve, reject) => { job.resolve = resolve; job.reject = reject; });
  job.done.catch(() => {});  // 처리하지 않은 거부 경고 방지(아래에서 await)
  try {
    const link = await api(job.audioPath, { method: "POST" });
    const res = await fetch(link.url, { signal: job.ctrl.signal });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error(typeof data?.detail === "string" ? data.detail : `오디오를 받지 못했습니다 (${res.status})`);
    }
    job.duration = Number(res.headers.get("X-Audio-Duration")) || job.duration;
    const worker = ensureWorker();
    worker.postMessage({ type: "start", jobId: job.id, duration: job.duration });
    await pumpAudio(res.body, job, worker);
    worker.postMessage({ type: "end", jobId: job.id });
    await job.done;
    const saved = await api(`/courses/${job.courseId}/videos/${job.moduleItemId}/transcript`, { method: "PUT", json: {
      course_name: job.courseName, title: job.title,
      text: job.segments.map((s) => s.text).join("\n"),
      segments: job.segments, model: `whisper-small/${tx.device || "unknown"}`, duration: job.totalSeconds,
    } });
    job.status = "done";
    if (job.epoch === tx.epoch) onTranscriptSaved(job, saved.id);  // 로그아웃 전의 작업은 다음 사용자에게 알리지 않는다
  } catch (err) {
    if (job.status !== "cancelled") { job.status = "failed"; job.error = err.message; }
  } finally {
    // 실패해도 오디오 요청을 끊어야 서버의 동시 실행 자리가 풀린다
    job.ctrl.abort();
    tx.worker?.postMessage({ type: "cancel", jobId: job.id });
  }
  // 로그아웃으로 취소된 작업은 다음 사용자에게 보이면 안 되므로 기록하지 않는다
  if (job.epoch === tx.epoch) tx.finished.push(job);
  tx.current = null;
  runNext();
  // 끝난 줄이 '변환 중'에 머물지 않도록 현재 작업이 바뀐 뒤에 다시 그린다 (성공·실패·중지 모두)
  if (state.view === "videos" && state.videoTab === "videos" && state.videoList) renderVideoList();
}

async function pumpAudio(body, job, worker) {
  const reader = body.getReader();
  let carry = null;
  let sent = 0;
  for (;;) {
    while (sent / WHISPER_RATE - job.seconds > READ_AHEAD_SECONDS) {
      await Promise.race([new Promise((r) => { job.wake = r; }), job.done]);
    }
    const { value, done } = await reader.read();
    if (done) break;
    let bytes = value;
    if (carry) {
      const merged = new Uint8Array(carry.length + bytes.length);
      merged.set(carry);
      merged.set(bytes, carry.length);
      bytes = merged;
      carry = null;
    }
    const even = bytes.length - (bytes.length % 2);  // 16비트 샘플 경계
    if (even < bytes.length) carry = bytes.slice(even);
    const pcm = new Int16Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + even));
    const samples = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768;
    worker.postMessage({ type: "chunk", jobId: job.id, samples }, [samples.buffer]);
    sent += samples.length;
  }
  job.totalSeconds = sent / WHISPER_RATE;
  if (sent === 0) throw new Error("오디오가 비어 있습니다. 소리가 없는 영상이거나 서버에서 변환하지 못했습니다.");
  // 서버는 변환 중 오류가 나도 스트림을 정상 종료하므로 길이로 끊김을 알아낸다
  if (job.duration && job.totalSeconds < job.duration * MIN_AUDIO_RATIO) {
    throw new Error(`오디오가 중간에 끊겼습니다 (${fmtDuration(job.totalSeconds)} / ${fmtDuration(job.duration)}). 다시 시도하세요.`);
  }
}

function onTranscriptSaved(job, id) {
  for (const list of [state.videoList?.items, state.savedVideos]) {
    const row = list?.find((v) => (v.lms_course_id ?? state.videoList?.courseId) === job.courseId && v.module_item_id === job.moduleItemId);
    if (row) row.transcript_id = id;
  }
  if (state.view === "videos" && state.videoTab === "videos" && state.videoList?.courseId === job.courseId) renderVideoList();
  if (id) toast(`「${job.title}」 텍스트 변환을 마쳤습니다.`);  // id가 null이면 삭제
}

/* ---------- 목록에 붙는 버튼 (app.js의 renderVideoList가 호출) ---------- */

function txStatusOf(courseId, moduleItemId) {
  const key = txKey(courseId, moduleItemId);
  if (tx.current?.key === key) return "running";
  if (tx.queue.some((q) => q.key === key)) return "queued";
  return null;
}

function transcribeButton(v, courseId) {
  // 줄마다 한 칸만 쓴다. 다시 변환은 드문 일이라 텍스트 창 안에 둔다.
  const st = v.audioPath ? txStatusOf(courseId, v.module_item_id) : null;
  if (st) {
    const label = st === "running" ? "변환 중" : "대기 중";
    return `<button class="btn small ghost" data-tx="${v.module_item_id}" title="${label}" disabled>${iconOnly(st === "running" ? "loader" : "clock", label)}</button>`;
  }
  if (v.transcript_id) return `<button class="btn small ghost" data-tx-open="${v.transcript_id}" title="텍스트 보기">${iconOnly("text-view", "텍스트 보기")}</button>`;
  if (!v.audioPath) return "";
  return `<button class="btn small ghost" data-tx="${v.module_item_id}" title="텍스트로 변환">${iconOnly("text-convert", "텍스트로 변환")}</button>`;
}

function transcribeGroupButton(items, title) {
  const n = items.filter((v) => v.audioPath && !v.transcript_id).length;
  if (n < 2) return "";  // 1개뿐이면 그 줄의 버튼과 같은 동작이다
  const label = `전체 텍스트로 변환 (${n})`;
  return `<button class="btn small link" data-tx-group="${esc(items[0].module_id)}" aria-label="${esc(title)} ${label}" title="${label}">${icon("text-convert")}전체 ${n}</button>`;
}

function wireTranscribe(box, items, courseId) {
  const toJob = (v) => ({ courseId, moduleItemId: v.module_item_id, title: v.title, courseName: v.courseName,
                          audioPath: v.audioPath, duration: v.duration });
  box.querySelectorAll("[data-tx]").forEach((b) => b.addEventListener("click", () => {
    const v = items.find((x) => String(x.module_item_id) === b.dataset.tx);
    if (v) { enqueueTranscription([toJob(v)]); renderVideoList(); }
  }));
  box.querySelectorAll("[data-tx-group]").forEach((b) => b.addEventListener("click", () => {
    enqueueTranscription(items.filter((v) => String(v.module_id) === b.dataset.txGroup && v.audioPath && !v.transcript_id).map(toJob));
    renderVideoList();
  }));
  box.querySelectorAll("[data-tx-open]").forEach((b) => b.addEventListener("click", () => {
    const v = items.find((x) => String(x.transcript_id) === b.dataset.txOpen);
    openTranscript(Number(b.dataset.txOpen), v?.audioPath ? toJob(v) : null);
  }));
}

window.addEventListener("beforeunload", (e) => {
  if (tx.current || tx.queue.length) { e.preventDefault(); e.returnValue = ""; }
});

/* ---------- 텍스트 변환 결과 대화상자 ---------- */

function fmtStamp(sec) {
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

async function openTranscript(id, redoJob = null) {
  const dlg = $("#transcript-dialog");
  dlg.innerHTML = `<p class="meta">불러오는 중</p>`;
  dlg.showModal();
  let t;
  try { t = await api(`/transcripts/${id}`); }
  catch (err) { dlg.innerHTML = `<p>${esc(err.message)}</p><button class="btn" id="tx-close">닫기</button>`; $("#tx-close").onclick = () => dlg.close(); return; }
  const plain = () => t.text;
  const stamped = () => t.segments.map((s) => `[${fmtStamp(s.start)}] ${s.text}`).join("\n");
  let withTime = false;
  const render = () => { $("#tx-text").textContent = withTime ? stamped() : plain(); };
  dlg.innerHTML = `
    <div class="dialog-head"><strong>${esc(t.title)}</strong><span class="meta">${esc(t.course_name)} · ${esc(t.model)}</span></div>
    <label class="check"><input type="checkbox" id="tx-time"><span>시간 표시</span></label>
    <pre id="tx-text" class="transcript-text"></pre>
    <div class="row dialog-actions">
      <button class="btn small" id="tx-copy">복사</button>
      <button class="btn small" id="tx-save">.txt로 받기</button>
      ${redoJob ? '<button class="btn small" id="tx-redo">다시 변환</button>' : ""}
      <button class="btn small danger" id="tx-delete">삭제</button>
      <button class="btn small primary" id="tx-close" style="margin-left:auto">닫기</button>
    </div>`;
  render();
  $("#tx-time").onchange = (e) => { withTime = e.target.checked; render(); };
  $("#tx-copy").onclick = async () => {
    try { await navigator.clipboard.writeText($("#tx-text").textContent); toast("복사했습니다."); }
    catch { toast("복사하지 못했습니다. 텍스트를 직접 선택해 복사하세요.", true); }
  };
  $("#tx-save").onclick = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([$("#tx-text").textContent], { type: "text/plain;charset=utf-8" }));
    a.download = `${t.title.replace(/[\\/:*?"<>|]/g, " ").trim() || "transcript"}.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  if (redoJob) $("#tx-redo").onclick = () => {
    dlg.close();
    enqueueTranscription([redoJob]);
    renderVideoList();
  };
  $("#tx-delete").onclick = async () => {
    const ok = await confirmDialog({ title: "변환한 텍스트를 삭제할까요?", body: [`${t.title}의 텍스트가 지워져요. 영상은 그대로 있어요.`],
      confirmLabel: "텍스트 삭제", danger: true });
    if (!ok) return;
    try { await withPending($("#tx-delete"), "처리 중", () => api(`/transcripts/${id}`, { method: "DELETE" })); }
    catch (err) { toast(err.message, true); return; }
    onTranscriptSaved({ courseId: t.lms_course_id, moduleItemId: t.module_item_id, title: t.title }, null);
    dlg.close();
  };
  $("#tx-close").onclick = () => dlg.close();
}
