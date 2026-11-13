/* 화면 뼈대 컴포넌트. 모든 화면은 같은 틀을 쓴다(design-system/MASTER.md 0장):
 *   ViewHead(제목 + 한 줄 부제 + 오른쪽 동작) → Section(제목 있는 흰 카드)들
 * 카드·안내의 모양은 daisyUI의 card, card-body, alert 클래스(뚱이 모양은 styles/daisy.css)와 components.css의 empty가 정한다
 * (정적 HTML과 HTML 문자열도 같은 클래스를 쓰기 때문). 한 화면만 다른 부분은 class에 유틸리티를 덧붙인다. */
import type { ComponentChildren, JSX } from "preact";

/** 화면 제목(자막 상자), 그 아래 한 줄 부제, 오른쪽 동작 버튼. 화면마다 하나만 둔다(h1). */
export function ViewHead({ title, sub, id, children }: { title: string; sub?: ComponentChildren; id?: string; children?: ComponentChildren }) {
  // view-head는 안에 넣은 label·select의 모양(legacy.css)을 고르는 이름이다.
  return (
    <div class="view-head grid gap-1.5 px-1 pt-1 max-phone:px-0">
      <div class="flex flex-wrap items-center gap-2"><h1 class="caption mr-auto" id={id}>{title}</h1>{children}</div>
      {sub && <p class="text-sm text-ink-3">{sub}</p>}
    </div>
  );
}

type PanelProps = Omit<JSX.HTMLAttributes<HTMLElement>, "ref"> & { as?: "div" | "section"; children?: ComponentChildren };

/** 흰 카드. 제목이 있으면 Section을 쓴다. */
export function Panel({ as: Tag = "div", class: cls, children, ...rest }: PanelProps) {
  return <Tag class={`card${cls ? ` ${cls}` : ""}`} {...rest}>{children}</Tag>;
}

export function PanelBody({ class: cls, children }: { class?: string; children?: ComponentChildren }) {
  return <div class={`card-body${cls ? ` ${cls}` : ""}`}>{children}</div>;
}

/**
 * 제목이 있는 카드 한 묶음. 화면 안의 모든 묶음 제목은 이것으로 그린다(h2, 같은 크기·여백).
 * 설명(note)은 제목 바로 아래 한 줄, 안내·범례도 카드 밖이 아니라 이 안에 둔다.
 * flush면 본문에 안쪽 여백을 두지 않는다(목록이 카드 끝까지 닿을 때).
 */
export function Section({ id, title, note, actions, flush, class: cls, children }: {
  id: string; title: ComponentChildren; note?: ComponentChildren; actions?: ComponentChildren; flush?: boolean;
  class?: string; children?: ComponentChildren;
}) {
  return (
    <section class={`card${cls ? ` ${cls}` : ""}`} aria-labelledby={id}>
      <div class={`section-head${flush ? "" : " pb-0"}`}>
        <div class="flex flex-wrap items-center gap-2"><h2 class="card-title mr-auto" id={id}>{title}</h2>{actions}</div>
        {note && <p class="text-sm text-ink-3">{note}</p>}
      </div>
      {flush ? children : <div class="card-body pt-3">{children}</div>}
    </section>
  );
}

/** 빈 화면·안내 카드(뚱이 사진이 붙는다). */
export function Empty({ children, role }: { children: ComponentChildren; role?: "alert" | "status" }) {
  return <div class="card empty" role={role}>{children}</div>;
}

/** 빨간 경고 한 줄(학칙 경고, 마감 위험, 오류). */
export function Warning({ children }: { children: ComponentChildren }) {
  return <p class="alert alert-error alert-soft">{children}</p>;
}
