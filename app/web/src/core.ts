/* 공통 기반: 다른 모듈을 import하지 않는다(순환 import로 초기화 전 값을 읽는 일을 막는다. 타입만 예외).
 * 상태(state), 저장소(store), API 호출(api), 날짜·시간 형식, 문자열 이스케이프. */

import type { Batch, BatchDetail, Course, Credentials, Me, Plan, Todo } from "./types";
import type { BulkStat } from "./downloads";

/** querySelector 줄임. 화면 모듈은 index.html에 있는 요소를 찾을 때 쓴다(없으면 null이지만 대부분 있다고 가정한다). */
export const $ = (sel: string, root: ParentNode = document): any => root.querySelector(sel);
const DAY = 86400000;
export const KIND: Record<string, string> = { video: "영상", assignment: "과제", quiz: "퀴즈" };
export const JOB_STATUS: Record<string, string> = {
  queued: "대기", playing: "재생 중", done: "완료", failed: "실패",
  skipped: "이미 완료", locked: "잠김", cancelled: "취소",
};
export const BATCH_STATUS: Record<string, string> = {
  queued: "대기 중", running: "재생 중", done: "완료", partial: "일부 완료",
  failed: "실패", cancelled: "취소됨",
};
export const GAP_SECONDS = 20; // 서버 기본 간격(10~30초)의 평균. 예상 시각 계산용

export const store = {
  get(k: string): string | null { try { return localStorage.getItem(k); } catch { return null; } },
  set(k: string, v: string): void { try { localStorage.setItem(k, v); } catch { /* 저장 불가 환경 */ } },
  del(k: string): void { try { localStorage.removeItem(k); } catch { /* 저장 불가 환경 */ } },
};

export const PRIVACY_NOTICE_KEY = "privacy-notice-2026-11-access";
export type View = "dashboard" | "playback" | "videos" | "settings" | "admin";
export const VIEW_NAMES: Record<View, string> = { dashboard: "학습 현황", playback: "강의 재생", videos: "내려받기·텍스트 변환", settings: "설정", admin: "사용자 관리" };
/** 합친 화면의 예전 주소. 과목은 강의 재생, 알림·계정은 설정의 한 묶음이 됐다. 나머지 없어진 주소(#todos 등)는 홈으로 간다. */
const VIEW_ALIASES: Record<string, View> = { courses: "playback", notifications: "settings", account: "settings" };
export function toView(name: string): View {
  if (Object.hasOwn(VIEW_NAMES, name)) return name as View;
  return VIEW_ALIASES[name] ?? "dashboard";
}
/** 주소의 해시를 화면 이름과 하위 경로로 나눈다. `#admin/users/2020%201234` → { view: "admin", path: ["users", "2020 1234"] } */
export function parseHash(hash: string | null | undefined): { view: string; path: string[] } {
  const parts = String(hash || "").replace(/^#/, "").split("/").filter(Boolean).map((part) => {
    try { return decodeURIComponent(part); } catch { return part; }
  });
  return { view: parts[0] || "", path: parts.slice(1) };
}
const initialView = parseHash(location.hash);

/** 화면 전체가 공유하는 상태. 화면 모듈이 바꾸고 다시 그린다(이 객체 자체는 바꿔 끼우지 않는다). */
export interface State {
  token: string | null;
  view: View;
  adminPath: string[];
  me: Me | null;
  cred: Credentials | null;
  todos: Todo[];
  courses: Course[];
  batches: Batch[];
  active: BatchDetail | null;
  plan: Plan | null;
  dashboardPlan: Plan | null;
  dashboardError: string | null;
  dashboardLoading: boolean;
  playbackStarting: boolean;
  /* 아래는 아직 타입을 정하지 않은 화면별 값(내려받기·텍스트 변환 화면 등). 화면을 옮길 때 구체적인 타입으로 바꾼다. */
  videoCourse: number | null;
  videoList: any;
  savedVideos: any;
  videoTab: "videos" | "materials" | string;
  materialList: any;
  notDownloadedOnly: boolean;
  bulk: BulkStat | null;
  extensionReady?: boolean;
  extensionMissing?: string | null;
  guideDismissed?: boolean;
  planForm?: { scope: string; course: string; module: string } | null;
  planQuery?: Record<string, string> | null;
  playbackHelpOpen?: boolean;
}

export const state: State = {
  token: store.get("token"),
  dashboardPlan: null, dashboardError: null, dashboardLoading: false, playbackStarting: false,
  view: toView(initialView.view),
  adminPath: initialView.view === "admin" && initialView.path.length ? initialView.path : ["overview"],
  me: null, cred: null, todos: [], courses: [],
  batches: [], active: null, plan: null,
  videoCourse: null, videoList: null, savedVideos: null,
  videoTab: "videos", materialList: null, notDownloadedOnly: false, bulk: null,
};

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
/** HTML 문자열에 넣을 사용자·서버 문자열. Preact 컴포넌트(JSX)는 알아서 이스케이프하므로 쓰지 않는다. */
export function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

export const pad = (n: number) => String(n).padStart(2, "0");
export const fmtTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
export const fmtDate = (d: Date) => `${d.getMonth() + 1}/${d.getDate()} ${fmtTime(d)}`;
export const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

export function dayName(d: Date): string {
  const diff = Math.round((startOfDay(d).getTime() - startOfDay(new Date()).getTime()) / DAY);
  if (diff === 0) return "오늘";
  if (diff === 1) return "내일";
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEKDAY[d.getDay()]})`;
}

export function fmtDuration(sec: number): string {
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}시간 ${m % 60}분` : `${h}시간`;
}

/** 마감 없는 할 일(영상)은 어떤 시각보다 뒤로 보낸다. new Date(null)은 1970년이 되므로 직접 쓰지 않는다. */
export const dueTime = (t: { deadline?: string | null }) => t.deadline ? new Date(t.deadline).getTime() : Infinity;
export const dueText = (iso: string | null | undefined) => iso ? `마감 ${fmtDate(new Date(iso))}` : "마감 없음";

/** api()가 401·권한 없음을 만났을 때 부르는 화면 전환. 이 모듈이 화면 모듈을 import하지 않도록 app.ts가 등록한다. */
const apiHandlers = { unauthorized() {}, accessRequired() {} };
export function setApiHandlers(handlers: Partial<typeof apiHandlers>): void { Object.assign(apiHandlers, handlers); }

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) { super(message); this.status = status; this.code = code; }
}

interface ApiOptions { method?: string; json?: unknown; form?: Record<string, string> }

/** 서버 API 호출. 토큰을 붙이고, 갱신 토큰을 저장하고, 실패는 ApiError(서버의 detail 문구)로 던진다. 204면 null. */
export async function api<T = any>(path: string, { method = "GET", json, form }: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let body: BodyInit | undefined;
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
  if (res.status === 204) return null as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail
      : detail?.message ? detail.message
      : Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg).join(", ")
      : `요청 실패 (${res.status})`;
    if (res.status === 403 && detail?.code === "ACCESS_REQUIRED") apiHandlers.accessRequired();  // 열어 둔 탭에서 권한이 회수된 경우
    throw new ApiError(res.status, msg, detail?.code);
  }
  return data;
}


export const fmtSize = (n: number | null | undefined) => (!n ? "" : n >= 1e9 ? `${(n / 1e9).toFixed(1)}GB` : `${Math.round(n / 1e6)}MB`);

export function safeUrl(value: string | null | undefined): string | null {
  try { const url = new URL(value ?? ""); return ["http:", "https:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}
