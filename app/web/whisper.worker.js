// 브라우저에서 Whisper를 실행하는 모듈 Worker.
// 모델은 처음 실행할 때 Hugging Face Hub에서 내려받아 브라우저 캐시에 남는다.
import { pipeline } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.1";
import { planWindow } from "./whisper-window.mjs";

const MODEL = "onnx-community/whisper-small";
const RATE = 16000;
const WINDOW = 30 * RATE;   // Whisper 한 번 입력 길이
const OVERLAP = 5 * RATE;   // 창 경계에서 말이 잘리지 않도록 겹친다

let pipePromise = null;
const jobs = new Map();

const post = (m) => self.postMessage(m);

async function hasWebGPU() {
  try { return Boolean(navigator.gpu && await navigator.gpu.requestAdapter()); } catch { return false; }
}

function loadPipeline() {
  if (pipePromise) return pipePromise;
  const progress_callback = (p) => {
    if (p.status === "progress" && p.total) post({ type: "model-progress", percent: Math.round((p.loaded / p.total) * 100) });
  };
  pipePromise = (async () => {
    if (await hasWebGPU()) {
      try {
        const pipe = await pipeline("automatic-speech-recognition", MODEL, {
          device: "webgpu", dtype: { encoder_model: "fp16", decoder_model_merged: "q4" }, progress_callback });
        post({ type: "device", device: "webgpu" });
        return pipe;
      } catch (err) {
        console.warn("WebGPU로 Whisper를 띄우지 못해 CPU로 전환합니다.", err);
      }
    }
    const pipe = await pipeline("automatic-speech-recognition", MODEL, { device: "wasm", dtype: "q8", progress_callback });
    post({ type: "device", device: "wasm" });
    return pipe;
  })();
  pipePromise.catch(() => { pipePromise = null; });  // 다음 작업에서 다시 시도할 수 있게
  return pipePromise;
}

function takeSamples(job, n) {
  const out = new Float32Array(n);
  let o = 0;
  for (const c of job.chunks) {
    const k = Math.min(c.length, n - o);
    out.set(c.subarray(0, k), o);
    o += k;
    if (o >= n) break;
  }
  return out;
}

function dropSamples(job, n) {
  job.length -= n;
  while (n > 0) {
    const c = job.chunks[0];
    if (c.length <= n) { n -= c.length; job.chunks.shift(); } else { job.chunks[0] = c.subarray(n); n = 0; }
  }
}

async function drain(id, job) {
  while (!job.cancelled && (job.length >= WINDOW || (job.ended && job.length > 0))) {
    const take = Math.min(WINDOW, job.length);
    const audio = takeSamples(job, take);
    const transcriber = await loadPipeline();
    const out = await transcriber(audio, { language: "korean", task: "transcribe", return_timestamps: true });
    if (job.cancelled) return;
    const base = job.base / RATE;
    const last = job.ended && take === job.length;
    const plan = planWindow(out.chunks, take / RATE, last, OVERLAP / RATE);
    for (const seg of plan.segments) {
      post({ type: "segment", jobId: id, start: base + seg.start, end: base + seg.end, text: seg.text });
    }
    const advance = last ? take : Math.min(take, Math.max(RATE, Math.round(plan.advanceSeconds * RATE)));
    dropSamples(job, advance);
    job.base += advance;
    post({ type: "progress", jobId: id, seconds: job.base / RATE });
    if (last) break;
  }
  if (job.ended && job.length === 0 && !job.cancelled) {
    jobs.delete(id);
    post({ type: "done", jobId: id });
  }
}

function schedule(id, job) {
  job.chain = job.chain.then(() => drain(id, job)).catch((err) => {
    jobs.delete(id);
    post({ type: "error", jobId: id, message: `전사 중 오류가 났습니다: ${err.message}`, fatal: false });
  });
}

self.onmessage = ({ data: m }) => {
  if (m.type === "start") {
    jobs.set(m.jobId, { chunks: [], length: 0, base: 0, ended: false, cancelled: false, chain: Promise.resolve() });
    loadPipeline().catch((err) => {
      jobs.delete(m.jobId);
      post({ type: "error", jobId: m.jobId, message: `Whisper 모델을 불러오지 못했습니다: ${err.message}`, fatal: true });
    });
    return;
  }
  const job = jobs.get(m.jobId);
  if (!job) return;
  if (m.type === "chunk") {
    job.chunks.push(m.samples);
    job.length += m.samples.length;
    if (job.length >= WINDOW) schedule(m.jobId, job);
  } else if (m.type === "end") {
    job.ended = true;
    schedule(m.jobId, job);
  } else if (m.type === "cancel") {
    job.cancelled = true;
    jobs.delete(m.jobId);
  }
};
