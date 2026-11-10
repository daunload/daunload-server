/* 공통 기반: 다른 모듈을 import하지 않는다(순환 import로 초기화 전 값을 읽는 일을 막는다).
 * 상태(state), 저장소(store), API 호출(api), 날짜·시간 형식, 문자열 이스케이프. */

export const $ = (sel, root = document) => root.querySelector(sel);
const DAY = 86400000;
export const KIND = { video: "영상", assignment: "과제", quiz: "퀴즈" };
export const JOB_STATUS = {
  queued: "대기", playing: "재생 중", done: "완료", failed: "실패",
  skipped: "이미 완료", locked: "잠김", cancelled: "취소",
};
export const BATCH_STATUS = {
  queued: "대기 중", running: "재생 중", done: "완료", partial: "일부 완료",
  failed: "실패", cancelled: "취소됨",
};
export const GAP_SECONDS = 20; // 서버 기본 간격(10~30초)의 평균. 예상 시각 계산용

export const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 저장 불가 환경 */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* 저장 불가 환경 */ } },
};

export const PRIVACY_NOTICE_KEY = "privacy-notice-2026-11-access";
export const VIEW_NAMES = { dashboard: "학습 현황", courses: "과목", playback: "강의 재생", videos: "내려받기·텍스트 변환", notifications: "알림", account: "계정", admin: "사용자 관리" };
/** 주소의 해시를 화면 이름과 하위 경로로 나눈다. `#admin/users/2020%201234` → { view: "admin", path: ["users", "2020 1234"] } */
export function parseHash(hash) {
  const parts = String(hash || "").replace(/^#/, "").split("/").filter(Boolean).map((part) => {
    try { return decodeURIComponent(part); } catch { return part; }
  });
  return { view: parts[0] || "", path: parts.slice(1) };
}
const initialView = parseHash(location.hash);

export const state = {
  token: store.get("token"),
  dashboardPlan: null, dashboardError: null, dashboardLoading: false, playbackStarting: false,
  view: Object.hasOwn(VIEW_NAMES, initialView.view) ? initialView.view : "dashboard",
  adminPath: initialView.view === "admin" && initialView.path.length ? initialView.path : ["overview"],
  me: null, cred: null, todos: [], courses: [],
  batches: [], active: null, plan: null,
  videoCourse: null, videoList: null, savedVideos: null,
  videoTab: "videos", materialList: null, notDownloadedOnly: false, bulk: null,
};

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export const pad = (n) => String(n).padStart(2, "0");
export const fmtTime = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
export const fmtDate = (d) => `${d.getMonth() + 1}/${d.getDate()} ${fmtTime(d)}`;
export const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

export function dayName(d) {
  const diff = Math.round((startOfDay(d) - startOfDay(new Date())) / DAY);
  if (diff === 0) return "오늘";
  if (diff === 1) return "내일";
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEKDAY[d.getDay()]})`;
}

export function fmtDuration(sec) {
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}시간 ${m % 60}분` : `${h}시간`;
}

/** 마감 없는 할 일(영상)은 어떤 시각보다 뒤로 보낸다. new Date(null)은 1970년이 되므로 직접 쓰지 않는다. */
export const dueTime = (t) => t.deadline ? new Date(t.deadline).getTime() : Infinity;
export const dueText = (iso) => iso ? `마감 ${fmtDate(new Date(iso))}` : "마감 없음";

/** api()가 401·권한 없음을 만났을 때 부르는 화면 전환. 이 모듈이 화면 모듈을 import하지 않도록 app.js가 등록한다. */
const apiHandlers = { unauthorized() {}, accessRequired() {} };
export function setApiHandlers(handlers) { Object.assign(apiHandlers, handlers); }

export class ApiError extends Error {
  constructor(status, message, code) { super(message); this.status = status; this.code = code; }
}

export async function api(path, { method = "GET", json, form } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = new URLSearchParams(form);
  const sentToken = state.token;
  const res = await fetch(path, { method, headers, body, ...(path.startsWith("/playback/") ? { cache: "no-store" } : {}) });
  const renewed = res.headers.get("X-Access-Token");
  if (renewed && sentToken && state.token === sentToken) {  // 그 사이 로그아웃·계정 전환했으면 버린다
    state.token = renewed;
    store.set("token", renewed);
  }
  if (res.status === 401 && state.token) { apiHandlers.unauthorized(); throw new ApiError(401, "다시 로그인하세요."); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail
      : detail?.message ? detail.message
      : Array.isArray(detail) ? detail.map((d) => d.msg).join(", ")
      : `요청 실패 (${res.status})`;
    if (res.status === 403 && detail?.code === "ACCESS_REQUIRED") apiHandlers.accessRequired();  // 열어 둔 탭에서 권한이 회수된 경우
    throw new ApiError(res.status, msg, detail?.code);
  }
  return data;
}


export const fmtSize = (n) => (!n ? "" : n >= 1e9 ? `${(n / 1e9).toFixed(1)}GB` : `${Math.round(n / 1e6)}MB`);

export function safeUrl(value) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}
