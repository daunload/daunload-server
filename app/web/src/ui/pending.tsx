/* 서버를 기다리는 버튼. 기다리는 동안 잠그고 "처리 중"을 보인 뒤(aria-busy), 끝나면(실패해도) 원래대로 돌린다.
 * HTML 문자열 화면의 withPending(btn, label, fn)과 같은 모양이다.
 *
 *   const [busy, run] = usePending();
 *   <PendingButton class="btn small" busy={busy} onClick={() => run(() => api(...))}>테스트 메시지 보내기</PendingButton>
 */
import type { ComponentChildren, JSX } from "preact";
import { useState } from "preact/hooks";

export function usePending(): [boolean, <T>(fn: () => Promise<T>) => Promise<T>] {
  const [busy, setBusy] = useState(false);
  const run = async <T,>(fn: () => Promise<T>): Promise<T> => {
    setBusy(true);
    try { return await fn(); } finally { setBusy(false); }
  };
  return [busy, run];
}

type Props = Omit<JSX.IntrinsicElements["button"], "ref"> & { busy: boolean; busyLabel?: string; children: ComponentChildren };

export function PendingButton({ busy, busyLabel = "처리 중", disabled, children, ...rest }: Props) {
  return <button {...rest} disabled={busy || !!disabled} aria-busy={busy ? "true" : undefined}>{busy ? busyLabel : children}</button>;
}
