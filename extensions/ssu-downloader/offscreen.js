// 서비스 워커가 넘긴 audio-* 요청을 처리한다. 추출기와 재발급 대기 같은 작업 상태는 이 문서만 가진다
// (서비스 워커는 언제든 멈출 수 있다). 한 번에 한 작업만 연다.
import { AudioExtractError, extractPcm, openCdnSource } from "./audio-extract.mjs";

const RATE = 16000;
const READ_CHUNK_SECONDS = 5;
const LIMIT = RATE * READ_CHUNK_SECONDS;
const FAILED = "학교 서버가 영상을 주지 않았습니다. 목록을 새로고침한 뒤 다시 시도하세요.";
const MISSING = "텍스트 변환 작업이 끝났습니다. 다시 시도해 주세요.";

let current = null;
let reads = Promise.resolve();

function closeJob(job) {
  if (!job) return;
  job.ctrl.abort();
  if (current === job) current = null;
}

function open(id, url, chunkBytes) {
  closeJob(current);
  const ctrl = new AbortController();
  const job = {
    id, ctrl, iterator: null,
    pendingRefresh: null, refreshAsked: false, wake: null,
    next: null, leftover: null, finished: null,
  };
  // 추출기가 주소 재발급을 요청하면 다음 audio-read의 url로 풀린다.
  const refresh = () => new Promise((resolve, reject) => {
    if (ctrl.signal.aborted) return reject(ctrl.signal.reason);
    ctrl.signal.addEventListener("abort", () => reject(ctrl.signal.reason), { once: true });
    job.pendingRefresh = { resolve };
    job.refreshAsked = true;
    job.wake?.();
  });
  const source = openCdnSource(url, { signal: ctrl.signal, refresh });
  job.iterator = extractPcm(source, { signal: ctrl.signal, rate: RATE, ...(chunkBytes ? { chunkBytes } : {}) });
  current = job;
  return { opened: true };
}

// Float32 → Int16 LE(클램프) → base64
function encode(parts, count) {
  const bytes = new Uint8Array(count * 2);
  const view = new DataView(bytes.buffer);
  let at = 0;
  for (const part of parts) {
    for (let i = 0; i < part.length; i++, at += 2) {
      const s = Math.max(-1, Math.min(1, part[i]));
      view.setInt16(at, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
  }
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

async function read(job, url) {
  if (url && job.pendingRefresh) {
    job.pendingRefresh.resolve(url);
    job.pendingRefresh = null;
    job.refreshAsked = false;
  }
  if (job.refreshAsked) return { refresh: true };

  const parts = [];
  let count = 0;
  const take = (pcm) => {
    const room = LIMIT - count;
    if (pcm.length > room) {
      job.leftover = pcm.subarray(room);
      pcm = pcm.subarray(0, room);
    }
    parts.push(pcm);
    count += pcm.length;
  };
  if (job.leftover) {
    const rest = job.leftover;
    job.leftover = null;
    take(rest);
  }
  while (count < LIMIT && !job.finished) {
    if (!job.next) {
      job.next = job.iterator.next();
      job.next.catch(() => {}); // 재발급으로 먼저 돌아가도 거부는 다음 read에서 받는다.
    }
    const asked = new Promise((resolve) => {
      job.wake = resolve;
      if (job.refreshAsked) resolve();
    });
    const step = await Promise.race([job.next, asked.then(() => null)]);
    job.wake = null;
    if (step === null) {
      // 대기 중인 next()는 그대로 두고, 다음 read에서 새 주소를 받은 뒤 이어서 기다린다.
      if (count) break;
      return { refresh: true };
    }
    job.next = null;
    if (step.done) job.finished = step.value;
    else take(step.value);
  }
  if (count) return { pcm: encode(parts, count), seconds: count / RATE };
  closeJob(job);
  return { done: true, duration: job.finished.duration };
}

async function handle(message) {
  const job = current && current.id === message.job ? current : null;
  if (message.action === "audio-open") return open(message.job, message.url, message.chunkBytes);
  if (message.action === "audio-close") {
    closeJob(job);
    return { closed: true };
  }
  if (message.action === "audio-read") {
    const run = reads.then(async () => {
      if (!job || current !== job) throw new Error(MISSING);
      try {
        return await read(job, message.url);
      } catch (error) {
        const active = current === job;
        closeJob(job);
        if (!active) throw new Error(MISSING);
        console.warn("audio extraction failed", error);
        throw new Error(error instanceof AudioExtractError ? error.message : FAILED);
      }
    });
    reads = run.catch(() => {});
    return run;
  }
  throw new Error("지원하지 않는 요청입니다.");
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.target !== "offscreen-audio" || sender.id !== chrome.runtime.id || sender.tab) return false;
  handle(message).then(
    (result) => respond({ result }),
    (error) => respond({ error: error.message }),
  );
  return true;
});
