"use strict";

const MEDIA_DOMAINS = ["ssu.ac.kr", "commonscdn.com"];
const AUDIO_DOMAINS = ["commonscdn.com"];
const AUDIO_UNSUPPORTED = "이 영상은 텍스트 변환을 지원하지 않습니다.";
const JOB_PATTERN = /^[a-zA-Z0-9-]{16,80}$/;
let serial = Promise.resolve();
function exclusive(fn) {
  const result = serial.then(fn);
  serial = result.catch(() => {});
  return result;
}

function trustedSender(sender) {
  if (sender.id !== chrome.runtime.id || sender.frameId !== 0 || !sender.tab) return false;
  const origin = new URL(sender.url).origin;
  return chrome.runtime.getManifest().content_scripts[0].matches
    .some((pattern) => new URL(pattern.replace(/\*$/, "")).origin === origin);
}

function validateDownload(data) {
  const url = new URL(data?.url);
  if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443") ||
      !MEDIA_DOMAINS.some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`))) {
    throw new Error("학교 CDN의 HTTPS 주소만 다운로드할 수 있습니다.");
  }
  const name = data.file_name;
  if (typeof name !== "string" || name.length > 200 || !name.toLowerCase().endsWith(".mp4") ||
      /[\\/:*?"<>|\x00-\x1f]/.test(name) || name.startsWith(".") || /[. ]$/.test(name)) {
    throw new Error("올바르지 않은 영상 파일명입니다.");
  }
  return { url: url.href, filename: name };
}

function validateAudioUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error(AUDIO_UNSUPPORTED); }
  if (url.protocol !== "https:" || url.username || url.password || url.port ||
      !AUDIO_DOMAINS.some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`))) {
    throw new Error(AUDIO_UNSUPPORTED);
  }
  return url.href;
}

function validateJob(key) {
  if (typeof key !== "string" || !JOB_PATTERN.test(key)) throw new Error("올바르지 않은 작업 번호입니다.");
  return key;
}

async function configureRules() {
  // Only extension-initiated requests are changed; normal school browsing is untouched.
  await chrome.declarativeNetRequest.updateDynamicRules({
    removeRuleIds: [1, 2],
    addRules: [{
      id: 1, priority: 1,
      condition: { initiatorDomains: [chrome.runtime.id], requestDomains: MEDIA_DOMAINS,
        resourceTypes: ["main_frame", "sub_frame", "other", "media", "xmlhttprequest"] },
      action: { type: "modifyHeaders", requestHeaders: [
        { header: "Referer", operation: "set", value: "https://commons.ssu.ac.kr/" },
        { header: "Origin", operation: "set", value: "https://commons.ssu.ac.kr" }
      ] }
    }, {
      id: 2, priority: 2,
      condition: { initiatorDomains: [chrome.runtime.id], excludedRequestDomains: MEDIA_DOMAINS,
        regexFilter: "^https?://", resourceTypes: ["main_frame", "sub_frame", "other", "media", "xmlhttprequest"] },
      action: { type: "block" }
    }]
  });
}

const CLOSED_MESSAGE = "다운로드 창을 닫았습니다. 다시 내려받기를 눌러 주세요.";
const isPending = job => ["queued", "in_progress"].includes(job.state);

async function closeManager(tabId) {
  const state = await chrome.storage.session.get(["managerTab", "managerSession", "jobs"]);
  if (state.managerTab !== tabId) return;
  const jobs = state.jobs || {};
  for (const job of Object.values(jobs)) {
    if ((job.session === state.managerSession || !job.session) && isPending(job)) {
      Object.assign(job, { state: "interrupted", error: CLOSED_MESSAGE, code: "MANAGER_CLOSED" });
    }
  }
  await chrome.storage.session.set({ managerTab: null, managerWindow: null, managerSession: null, jobs });
}

async function currentManager() {
  const state = await chrome.storage.session.get(["managerTab", "managerWindow", "managerSession"]);
  if (state.managerTab == null) return null;
  let tab;
  try { tab = await chrome.tabs.get(state.managerTab); } catch { /* closed, event may still be queued */ }
  // A newly created tab may not yet expose its URL. The registered tab/window IDs
  // identify the manager throughout loading; navigation away is handled below.
  if (tab && tab.windowId === state.managerWindow && state.managerSession) return state;
  await closeManager(state.managerTab);
  return null;
}

async function managerUpdate(message, sender) {
  const state = await currentManager();
  if (!state || sender.id !== chrome.runtime.id || sender.frameId !== 0 ||
      sender.tab?.id !== state.managerTab || sender.url !== chrome.runtime.getURL("manager.html")) {
    throw new Error("현재 다운로드 창의 요청이 아닙니다.");
  }
  const { jobs = {} } = await chrome.storage.session.get("jobs");
  if (message.action === "manager-list") {
    // Retain old terminal results for dashboard polling, but never show/run them in a new window.
    return Object.fromEntries(Object.entries(jobs).filter(([, job]) => job.session === state.managerSession));
  }
  const job = jobs[message.job];
  if (!job || job.session !== state.managerSession) throw new Error("이 다운로드 창의 작업이 아닙니다.");
  if (message.action === "manager-update") {
    for (const key of ["state", "error", "code", "bytesReceived", "totalBytes", "savedName", "cancelled"]) {
      if (Object.hasOwn(message.patch || {}, key)) job[key] = message.patch[key];
    }
    await chrome.storage.session.set({ jobs });
    return {};
  }
  throw new Error("지원하지 않는 요청입니다.");
}

async function ensureManager(focus = false) {
  const state = await currentManager();
  if (state) {
    if (focus) {
      const current = await chrome.windows.get(state.managerWindow);
      await chrome.windows.update(state.managerWindow, {
        focused: true, ...(current.state === "minimized" ? { state: "normal" } : {})
      });
    }
    return state.managerSession;
  }
  const popup = await chrome.windows.create({
    url: chrome.runtime.getURL("manager.html"), type: "popup", width: 920, height: 760, focused: true
  });
  const [tab] = popup.tabs || await chrome.tabs.query({ windowId: popup.id });
  if (!tab) throw new Error("다운로드 창을 열지 못했습니다. 확장 아이콘을 눌러 다시 시도해 주세요.");
  const session = crypto.randomUUID();
  await chrome.storage.session.set({ managerTab: tab.id, managerWindow: popup.id, managerSession: session });
  return session;
}

const OFFSCREEN_URL = "offscreen.html";
let creatingOffscreen = null;

async function hasOffscreen() {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"], documentUrls: [chrome.runtime.getURL(OFFSCREEN_URL)]
  });
  return contexts.length > 0;
}

async function ensureOffscreen() {
  if (await hasOffscreen()) return;
  creatingOffscreen ??= chrome.offscreen.createDocument({
    url: OFFSCREEN_URL, reasons: ["WORKERS"], justification: "Decode lecture audio for in-browser transcription"
  }).finally(() => { creatingOffscreen = null; });
  await creatingOffscreen;
}

// Audio jobs live in the offscreen document so they survive service-worker restarts.
// They bypass `exclusive` so a slow decode never stalls download bookkeeping.
async function handleAudio(message) {
  const job = validateJob(message.job);
  const forward = { target: "offscreen-audio", action: message.action, job };
  if (message.action === "audio-open") {
    forward.url = validateAudioUrl(message.url);
    // Optional Range size (tests use small ranges to exercise flow control); defaults to the extractor's 2 MiB.
    if (message.chunkBytes != null) {
      if (!Number.isInteger(message.chunkBytes) || message.chunkBytes < 4096 || message.chunkBytes > 8 * 1024 * 1024) {
        throw new Error("올바르지 않은 요청입니다.");
      }
      forward.chunkBytes = message.chunkBytes;
    }
    await exclusive(configureRules);
    await ensureOffscreen();
  } else if (message.action === "audio-read") {
    if (message.url != null) forward.url = validateAudioUrl(message.url);
    await ensureOffscreen();
  } else if (message.action === "audio-close") {
    if (!(await hasOffscreen())) return { closed: true };
  } else {
    throw new Error("지원하지 않는 요청입니다.");
  }
  const reply = await chrome.runtime.sendMessage(forward);
  if (!reply) throw new Error("텍스트 변환 작업이 끝났습니다. 다시 시도해 주세요.");
  if (reply.idle) await chrome.offscreen.closeDocument().catch(() => {});
  if (reply.error) throw new Error(reply.error);
  return reply.result;
}

async function handle(message, sender) {
  if (message?.action?.startsWith("manager-")) return managerUpdate(message, sender);
  if (!trustedSender(sender) || message?.channel !== "ssu-download") throw new Error("허용되지 않은 요청입니다.");
  if (message.action === "ping") return { version: 1, sessions: true, queue: true, audio: true };
  if (message.action === "prepare") return { session: await ensureManager(true) };
  const manager = await currentManager();
  const owner = `${new URL(sender.url).origin}:${sender.tab.id}`;
  const { jobs = {} } = await chrome.storage.session.get("jobs");
  if (message.action === "start") {
    if (!manager || message.session !== manager.managerSession) {
      return { closed: true, code: "MANAGER_CLOSED", error: CLOSED_MESSAGE };
    }
    const options = validateDownload(message.data);
    const key = message.job;
    validateJob(key);
    if (jobs[key]) {
      if (jobs[key].owner !== owner) throw new Error("다른 창의 다운로드입니다.");
      return { started: true };
    }
    await configureRules();
    jobs[key] = { ...options, owner, session: manager.managerSession, created: Date.now(), state: "queued", bytesReceived: 0, totalBytes: 0 };
    for (const [oldKey, job] of Object.entries(jobs)) {
      if (Date.now() - job.created > 7 * 86400000 && !["queued", "in_progress"].includes(job.state)) delete jobs[oldKey];
    }
    await chrome.storage.session.set({ jobs });
    return { started: true };
  }
  const job = jobs[message.job];
  if (!job || job.owner !== owner) throw new Error("이 창에서 시작한 다운로드가 아닙니다.");
  if (message.action === "cancel") {
    if (!isPending(job)) return { cancelled: true };
    job.cancelled = true;
    if (job.state === "queued") { job.state = "interrupted"; job.error = "사용자가 취소했습니다."; }
    await chrome.storage.session.set({ jobs });
    return { cancelled: true };
  }
  if (message.action === "status") {
    return { state: job.state, error: job.error, code: job.code, bytesReceived: job.bytesReceived, totalBytes: job.totalBytes };
  }
  throw new Error("지원하지 않는 요청입니다.");
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.target === "offscreen-audio") return false;
  const audio = typeof message?.action === "string" && message.action.startsWith("audio-");
  const work = audio
    ? Promise.resolve().then(() => {
        if (!trustedSender(sender) || message.channel !== "ssu-download") throw new Error("허용되지 않은 요청입니다.");
        return handleAudio(message);
      })
    : exclusive(() => handle(message, sender));
  work.then(
    (result) => respond({ result }),
    (error) => respond({ error: error.message })
  );
  return true;
});

chrome.tabs.onRemoved.addListener(tabId => {
  exclusive(() => closeManager(tabId)).catch(error => console.error(error.message));
});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.url && change.url !== chrome.runtime.getURL("manager.html")) {
    exclusive(() => closeManager(tabId)).catch(error => console.error(error.message));
  }
});

// Keep transfers alive when the user clicks outside the window. A toolbar popup
// would be destroyed on blur, so the action opens/focuses the persistent window.
chrome.action.onClicked.addListener(() => {
  exclusive(() => ensureManager(true)).catch(error => console.error("다운로드 창 열기 실패:", error.message));
});
