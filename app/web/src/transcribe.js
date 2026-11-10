import { api, fmtDuration, state, store } from "./core";
import { toast } from "./ui";
import { renderTaskPanel } from "./tasks";
import { requireAudioExtension } from "./downloads";
import { ext } from "./extension";
import { renderVideoList, reportDownloadError } from "./screens/videos";

/* ---------------- 브라우저 Whisper 텍스트 변환 ----------------
 * 확장 프로그램이 학교 CDN에서 읽어 오디오를 뽑아 주고, 브라우저 Whisper가 텍스트로 바꾼다. 끝나면 결과를 서버에 저장한다.
 * 한 번에 하나씩 처리한다. 탭을 닫으면 진행 중인 작업은 사라진다.
 * 목록의 변환 버튼과 결과 대화상자는 screens/transcript.tsx에 있다. */

const WHISPER_RATE = 16000;
const READ_AHEAD_SECONDS = 60;  // Worker보다 이만큼만 앞서 받는다(메모리 일정)
const MIN_AUDIO_RATIO = 0.97;   // LMS의 영상 길이와 실제 오디오 길이의 오차 허용
export const tx = { worker: null, device: null, modelPercent: null, queue: [], current: null, finished: [], nextId: 1, epoch: 0 };

function txKey(courseId, moduleItemId) { return `${courseId}:${moduleItemId}`; }

function ensureWorker() {
  if (tx.worker) return tx.worker;
  const w = new Worker(new URL("./workers/whisper.worker.js", import.meta.url), { type: "module" });
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

export function enqueueTranscription(items) {
  for (const it of items) {
    const key = txKey(it.courseId, it.moduleItemId);
    if (tx.current?.key === key || tx.queue.some((q) => q.key === key)) continue;
    tx.finished = tx.finished.filter((f) => f.key !== key);
    tx.queue.push({ ...it, key, id: tx.nextId++, status: "queued", seconds: 0, duration: it.duration || null });
  }
  renderTaskPanel();
  if (!tx.current) runNext();
}

export function cancelTranscription() {
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
    try { await requireAudioExtension(); }
    catch (err) { if (err.code === "EXTENSION_MISSING") reportDownloadError(err); throw err; }
    const link = await api(job.mediaPath, { method: "POST" });
    job.extJob = crypto.randomUUID();
    await ext.request("audio-open", { job: job.extJob, url: link.url });
    const worker = ensureWorker();
    worker.postMessage({ type: "start", jobId: job.id, duration: job.duration });
    await pumpAudio(job, worker);
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
    // 실패해도 확장 프로그램의 오디오 작업을 닫아야 다음 작업이 읽을 수 있다
    job.ctrl.abort();
    if (job.extJob) await ext.request("audio-close", { job: job.extJob }).catch(() => {});
    tx.worker?.postMessage({ type: "cancel", jobId: job.id });
  }
  // 로그아웃으로 취소된 작업은 다음 사용자에게 보이면 안 되므로 기록하지 않는다
  if (job.epoch === tx.epoch) tx.finished.push(job);
  tx.current = null;
  runNext();
  // 끝난 줄이 '변환 중'에 머물지 않도록 현재 작업이 바뀐 뒤에 다시 그린다 (성공·실패·중지 모두)
  if (state.view === "videos" && state.videoTab === "videos" && state.videoList) renderVideoList();
}

function pcmFromBase64(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const pcm = new Int16Array(bytes.buffer, 0, bytes.length >> 1);  // 16비트 리틀 엔디언
  const samples = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768;
  return samples;
}

async function pumpAudio(job, worker) {
  let sent = 0;
  let url;
  let doneDuration = 0;
  for (;;) {
    while (sent / WHISPER_RATE - job.seconds > READ_AHEAD_SECONDS) {
      await Promise.race([new Promise((r) => { job.wake = r; }), job.done]);
    }
    job.ctrl.signal.throwIfAborted();
    // 읽기가 시간 초과되면 확장이 이미 샘플을 소비했을 수 있어 이어 받지 않고 작업을 실패시킨다
    const read = ext.request("audio-read", { job: job.extJob, ...(url ? { url } : {}) });
    read.catch(() => {});
    const result = await Promise.race([read, job.done]);  // 취소·워커 오류로 job.done이 거부되면 응답을 기다리지 않는다
    job.ctrl.signal.throwIfAborted();
    url = undefined;
    if (result.refresh) {
      url = (await api(job.mediaPath, { method: "POST" })).url;
      continue;
    }
    if (result.done) { doneDuration = Number(result.duration) || 0; break; }
    const samples = pcmFromBase64(result.pcm);
    worker.postMessage({ type: "chunk", jobId: job.id, samples }, [samples.buffer]);
    sent += samples.length;
  }
  job.totalSeconds = sent / WHISPER_RATE;
  if (sent === 0) throw new Error("오디오가 비어 있습니다. 소리가 없는 영상입니다.");
  // 영상 길이보다 짧으면 중간에 끊긴 것이다
  const expected = job.duration || doneDuration;
  if (expected && job.totalSeconds < expected * MIN_AUDIO_RATIO) {
    throw new Error(`오디오가 중간에 끊겼습니다 (${fmtDuration(job.totalSeconds)} / ${fmtDuration(expected)}). 다시 시도하세요.`);
  }
}

export function onTranscriptSaved(job, id) {
  for (const list of [state.videoList?.items, state.savedVideos]) {
    const row = list?.find((v) => (v.lms_course_id ?? state.videoList?.courseId) === job.courseId && v.module_item_id === job.moduleItemId);
    if (row) row.transcript_id = id;
  }
  if (state.view === "videos" && state.videoTab === "videos" && state.videoList?.courseId === job.courseId) renderVideoList();
  if (id) toast(`「${job.title}」 텍스트 변환을 마쳤습니다.`);  // id가 null이면 삭제
}

/* ---------- 화면이 묻는 상태 ---------- */

/** 이 강의가 지금 변환 중("running")인지 대기 중("queued")인지. 버튼 모양(screens/transcript.tsx)이 쓴다. */
export function txStatusOf(courseId, moduleItemId) {
  const key = txKey(courseId, moduleItemId);
  if (tx.current?.key === key) return "running";
  if (tx.queue.some((q) => q.key === key)) return "queued";
  return null;
}

window.addEventListener("beforeunload", (e) => {
  if (tx.current || tx.queue.length) { e.preventDefault(); e.returnValue = ""; }
});
