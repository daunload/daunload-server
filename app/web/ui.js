"use strict";

/* ---------------- 공통 UI 조각 ----------------
 * 다른 스크립트보다 먼저 불러온다. 버튼 문구는 건드리지 않는 장식용 아이콘만 만든다.
 */

const PLAYBACK_WARNING = "직접 시청하지 않은 수강은 학칙상 문제가 될 수 있어요.";

const ICONS = {
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  download: '<path d="M12 4v11m-4-4 4 4 4-4M5 20h14"/>',
};

/** 장식용 인라인 SVG. aria-hidden이라 버튼의 접근 가능한 이름을 바꾸지 않는다. */
function icon(name) {
  const paths = ICONS[name];
  if (!paths) return "";
  return `<svg class="icon" data-icon="${name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${paths}</svg>`;
}

/* ---------------- 알림 토스트 ----------------
 * #toast는 메시지 묶음이다. 성공은 3200ms 뒤 사라지고, 오류는 닫기 전까지 남는다.
 * 같은 오류가 이미 떠 있으면 늘리지 않고, 최대 3개까지만 둔다. 넘치면 오류가 아닌 알림부터 지우고, 모두 오류일 때만 가장 오래된 것을 지운다.
 */

const TOAST_MS = 3200;
const TOAST_MAX = 3;

function toast(msg, bad = false) {
  const box = document.getElementById("toast");
  if (!box) return;
  if (bad && [...box.children].some((n) => n.classList.contains("bad") && n.firstChild?.textContent === msg)) return;
  const item = document.createElement("div");
  item.className = `toast${bad ? " bad" : ""}`;
  const text = document.createElement("span");
  text.textContent = msg;
  item.append(text);
  if (bad) {
    item.setAttribute("role", "alert");
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.textContent = "닫기";
    close.addEventListener("click", () => item.remove());
    item.append(close);
  } else {
    setTimeout(() => item.remove(), TOAST_MS);
  }
  box.append(item);
  while (box.children.length > TOAST_MAX) {
    const victim = [...box.children].find((n) => !n.classList.contains("bad")) || box.firstElementChild;
    victim.remove();
  }
}

function clearToasts() {
  document.getElementById("toast")?.replaceChildren();
}

/* ---------------- 처리 중 상태 · 스켈레톤 · 기기 ---------------- */

/** 버튼을 잠그고 label을 보여 주는 동안 fn을 실행한다. 끝나면(실패해도) 원래 모습으로 되돌린다. */
async function withPending(btn, label, fn) {
  const html = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = label;
  btn.setAttribute("aria-busy", "true");
  try {
    return await fn();
  } finally {
    if (btn.isConnected) {
      btn.innerHTML = html;
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
    }
  }
}

/** 목록이 불러와지는 동안 보여 줄 자리표시 줄 n개. 화면 낭독기에는 숨긴다. */
function skeletonRows(n) {
  return `<div class="skeleton-list" aria-hidden="true">${'<div class="skeleton"></div>'.repeat(n)}</div>`;
}

/** 터치 기기인지. 다운로드 안내에만 쓰고 레이아웃 분기에는 쓰지 않는다. */
function isPhoneDevice() {
  return window.matchMedia("(hover: none) and (pointer: coarse)").matches;
}

/* ---------------- 공통 확인 창 ----------------
 * confirmDialog({ title, body?: string[], warnings?: string[], confirmLabel, danger?, requireText?, requireLabel? })
 *   -> Promise<boolean>. 모든 문자열은 textContent로 넣는다. 이미 다른 확인 창이 열려 있으면 열지 않고 false로 끝낸다.
 * closeConfirmDialog(): 열린 창을 false로 닫는다(logout에서 호출).
 */

let openConfirm = null;

function confirmDialog({ title, body = [], warnings = [], confirmLabel, danger = false, requireText, requireLabel }) {
  if (openConfirm) return Promise.resolve(false);
  const dlg = document.getElementById("confirm-dialog");
  const opener = document.activeElement;
  const openerId = opener && opener.id;
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  dlg.replaceChildren();
  const heading = el("h2", "confirm-title", title);
  heading.id = "confirm-title";
  dlg.setAttribute("aria-labelledby", "confirm-title");
  dlg.append(heading);
  for (const line of body) dlg.append(el("p", "confirm-body", line));
  for (const line of warnings) dlg.append(el("p", "warning", line));
  let input = null;
  if (requireText) {
    const label = el("label", "confirm-require");
    label.append(requireLabel || "확인을 위해 입력하세요");
    input = el("input");
    input.type = "text";
    input.autocomplete = "off";
    input.setAttribute("inputmode", "numeric");
    label.append(input);
    dlg.append(label);
  }
  const cancel = el("button", "btn ghost", "취소");
  cancel.type = "button";
  const ok = el("button", `btn ${danger ? "danger" : "primary"}`, confirmLabel);
  ok.type = "button";
  const actions = el("div", "confirm-actions");
  actions.append(cancel, ok);
  dlg.append(actions);
  if (input) {
    ok.disabled = true;
    input.addEventListener("input", () => { ok.disabled = input.value.trim() !== String(requireText); });
  }

  let result = false;
  const state = { promise: null };
  state.promise = new Promise((resolve) => {
    cancel.addEventListener("click", () => dlg.close());
    ok.addEventListener("click", () => { if (ok.disabled) return; result = true; dlg.close(); });
    dlg.addEventListener("close", () => {
      openConfirm = null;
      dlg.replaceChildren();
      const target = (opener && opener.isConnected && opener) || (openerId && document.getElementById(openerId)) || document.getElementById("view");
      target?.focus?.();
      resolve(result);
    }, { once: true });
  });
  openConfirm = state;
  dlg.showModal();
  cancel.focus();
  return state.promise;
}

function closeConfirmDialog() {
  document.getElementById("confirm-dialog")?.close();
}

/** 일괄 재생 확인 창. scopeLabel은 "이번 주" 또는 "전체". */
function confirmPlayback(plan, scopeLabel) {
  const risky = plan.items.filter((i) => i.at_risk).length;
  const end = plan.expected_end ? ` · ${fmtTime(new Date(plan.expected_end))}쯤 끝나요` : "";
  return confirmDialog({
    title: `${scopeLabel} 강의 ${plan.items.length}개를 순서대로 재생할까요?`,
    body: [`1배속 · 약 ${fmtDuration(plan.total_seconds)}${end}`],
    warnings: [PLAYBACK_WARNING, ...(risky ? [`마감 전에 끝나지 않는 강의가 ${risky}개 있어요`] : [])],
    confirmLabel: `${plan.items.length}개 재생 시작`,
  });
}
