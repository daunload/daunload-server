/* 공통 확인 창. index.html의 <dialog id="confirm-dialog"> 안에 그린다.
 *
 * confirmDialog({ title, body?, warnings?, confirmLabel, danger?, requireText?, requireLabel? }) -> Promise<boolean>
 *   모든 문자열은 글자로 들어간다(HTML 아님). 이미 다른 확인 창이 열려 있으면 열지 않고 false로 끝낸다.
 *   첫 포커스는 취소 버튼, Esc로 닫히고, 닫히면 포커스를 연 버튼(없으면 #view)으로 돌려준다.
 *   requireText가 있으면 그 글자를 똑같이 입력해야 확인 버튼이 켜진다(계정 삭제).
 * closeConfirmDialog(): 열린 창을 false로 닫는다(로그아웃에서 호출).
 */
import { render } from "preact";
import { useState } from "preact/hooks";

export interface ConfirmOptions {
  title: string;
  body?: string[];
  warnings?: string[];
  confirmLabel: string;
  danger?: boolean;
  requireText?: string | number;
  requireLabel?: string;
}

let open = false;

function ConfirmContent({ opts, onConfirm }: { opts: ConfirmOptions; onConfirm: () => void }) {
  const { title, body = [], warnings = [], confirmLabel, danger = false, requireText, requireLabel } = opts;
  const [typed, setTyped] = useState("");
  const blocked = requireText !== undefined && typed.trim() !== String(requireText);
  const close = () => (document.getElementById("confirm-dialog") as HTMLDialogElement).close();
  return (
    <>
      <h2 id="confirm-title" class="text-lg font-bold">{title}</h2>
      {body.map((line) => <p class="text-sm text-ink-2">{line}</p>)}
      {warnings.map((line) => <p class="warning">{line}</p>)}
      {requireText !== undefined && (
        <label class="grid gap-1.5 text-sm">
          {requireLabel || "확인을 위해 입력하세요"}
          <input type="text" autocomplete="off" inputMode="numeric" value={typed}
            onInput={(e) => setTyped((e.target as HTMLInputElement).value)} />
        </label>
      )}
      <div class="flex flex-wrap justify-end gap-2">
        <button type="button" class="btn btn-ghost" data-confirm-cancel onClick={close}>취소</button>
        <button type="button" class={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={blocked}
          onClick={() => { if (!blocked) { onConfirm(); close(); } }}>{confirmLabel}</button>
      </div>
    </>
  );
}

export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  if (open) return Promise.resolve(false);
  const dlg = document.getElementById("confirm-dialog") as HTMLDialogElement;
  const opener = document.activeElement as HTMLElement | null;
  const openerId = opener?.id;
  let result = false;
  open = true;
  dlg.setAttribute("aria-labelledby", "confirm-title");
  render(<ConfirmContent opts={opts} onConfirm={() => { result = true; }} />, dlg);
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => {
      open = false;
      render(null, dlg);
      const target = (opener?.isConnected && opener) || (openerId && document.getElementById(openerId)) || document.getElementById("view");
      target?.focus?.();
      resolve(result);
    }, { once: true });
    dlg.showModal();
    dlg.querySelector<HTMLButtonElement>("[data-confirm-cancel]")?.focus();
  });
}

export function closeConfirmDialog(): void {
  (document.getElementById("confirm-dialog") as HTMLDialogElement | null)?.close();
}
