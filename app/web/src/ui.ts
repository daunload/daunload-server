/* 공통 UI 창구. 화면 모듈은 여기서 import한다.
 * 아이콘·토스트·확인 창은 Preact 컴포넌트(ui/)이고, 아래는 기존 HTML 문자열 화면이 쓰는 작은 도구다. */
import { fmtDuration, fmtTime } from "./core";
import type { Plan } from "./types";
import { confirmDialog } from "./ui/confirm";

export { icon, iconOnly, Icon, IconOnly } from "./ui/icons";
export { clearToasts, mountToasts, toast } from "./ui/toast";
export { closeConfirmDialog, confirmDialog } from "./ui/confirm";
export type { ConfirmOptions } from "./ui/confirm";

export const PLAYBACK_WARNING = "직접 시청하지 않은 수강은 학칙상 문제가 될 수 있어요.";

/** 버튼을 잠그고 label을 보여 주는 동안 fn을 실행한다. 끝나면(실패해도) 원래 모습으로 되돌린다. */
export async function withPending<T>(btn: HTMLButtonElement, label: string, fn: () => Promise<T>): Promise<T> {
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
export function skeletonRows(n: number): string {
  return `<div class="skeleton-list" aria-hidden="true">${'<div class="skeleton"></div>'.repeat(n)}</div>`;
}

/** 터치 기기인지. 다운로드 안내에만 쓰고 레이아웃 분기에는 쓰지 않는다. */
export function isPhoneDevice(): boolean {
  return window.matchMedia("(hover: none) and (pointer: coarse)").matches;
}

/** 일괄 재생 확인 창. scopeLabel은 "이번 주" 또는 "전체". */
export function confirmPlayback(plan: Plan, scopeLabel: string): Promise<boolean> {
  const risky = plan.items.filter((i) => i.at_risk).length;
  const end = plan.expected_end ? ` · ${fmtTime(new Date(plan.expected_end))}쯤 끝나요` : "";
  return confirmDialog({
    title: `${scopeLabel} 강의 ${plan.items.length}개를 순서대로 재생할까요?`,
    body: [`1배속 · 약 ${fmtDuration(plan.total_seconds)}${end}`],
    warnings: [PLAYBACK_WARNING, ...(risky ? [`마감 전에 끝나지 않는 강의가 ${risky}개 있어요`] : [])],
    confirmLabel: `${plan.items.length}개 재생 시작`,
  });
}
