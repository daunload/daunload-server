/* 알림 토스트. index.html의 #toast(.toast-stack) 안에 그린다.
 * 성공은 3200ms 뒤 사라지고, 오류는 닫기 전까지 남는다(role="alert" + 닫기 버튼).
 * 같은 오류가 이미 떠 있으면 늘리지 않고, 최대 3개까지만 둔다. 넘치면 오류가 아닌 알림부터 지우고, 모두 오류일 때만 가장 오래된 것을 지운다.
 * 위치(.toast-stack)는 진행 패널·하단 광고와 함께 움직이므로 legacy.css에 남아 있다.
 */
import { render } from "preact";

const TOAST_MS = 3200;
const TOAST_MAX = 3;

interface ToastItem { id: number; msg: string; bad: boolean }

let toasts: ToastItem[] = [];
let nextId = 1;

/** 목록을 바꾸고 다시 그린다. 상태가 이 파일 하나에만 있어 signals 없이 render()로 충분하다. */
function update(list: ToastItem[]): void {
  toasts = list;
  const box = document.getElementById("toast");
  if (box) render(<ToastStack />, box);
}

const remove = (id: number) => update(toasts.filter((t) => t.id !== id));

export function toast(msg: string, bad = false): void {
  if (bad && toasts.some((t) => t.bad && t.msg === msg)) return;
  const item = { id: nextId++, msg, bad };
  let list = [...toasts, item];
  while (list.length > TOAST_MAX) {
    const victim = list.find((t) => !t.bad) ?? list[0];
    list = list.filter((t) => t !== victim);
  }
  update(list);
  if (!bad) setTimeout(() => remove(item.id), TOAST_MS);
}

export function clearToasts(): void {
  update([]);
}

function Toast({ item }: { item: ToastItem }) {
  return (
    <div
      class={`toast ${item.bad ? "bad bg-red" : "bg-ink"} pointer-events-auto flex max-w-full items-center gap-3 rounded-card border-2 border-surface px-4.5 py-3 text-sm text-white animate-toast-in motion-reduce:animate-none`}
      role={item.bad ? "alert" : undefined}
    >
      <span>{item.msg}</span>
      {item.bad && (
        <button type="button"
          class="toast-close flex-none min-h-touch min-w-touch cursor-pointer rounded-btn border border-current bg-transparent px-2.5 font-sans text-sm font-bold text-white"
          onClick={() => remove(item.id)}>닫기</button>
      )}
    </div>
  );
}

function ToastStack() {
  return <>{toasts.map((item) => <Toast key={item.id} item={item} />)}</>;
}

export function mountToasts(): void {
  update(toasts);
}
