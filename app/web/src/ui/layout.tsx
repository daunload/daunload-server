/* 화면 뼈대 컴포넌트. 카드·안내의 모양은 daisyUI의 card, card-body, alert 클래스(뚱이 모양은 styles/daisy.css)와 components.css의 empty가 정한다
 * (정적 HTML과 HTML 문자열도 같은 클래스를 쓰기 때문). 한 화면만 다른 부분은 class에 유틸리티를 덧붙인다. */
import type { ComponentChildren, JSX } from "preact";

/** 화면 제목(자막 상자)과 오른쪽 동작 버튼. */
export function ViewHead({ title, children }: { title: string; children?: ComponentChildren }) {
  // view-head는 안에 넣은 label·select의 모양(legacy.css)을 고르는 이름이다.
  return <div class="view-head flex flex-wrap items-center gap-2 px-1 pt-1 max-phone:px-0"><h1 class="caption mr-auto">{title}</h1>{children}</div>;
}

type PanelProps = Omit<JSX.HTMLAttributes<HTMLElement>, "ref"> & { as?: "div" | "section"; children?: ComponentChildren };

/** 흰 카드. 제목이 있는 묶음은 as="section" + aria-labelledby로 쓴다. */
export function Panel({ as: Tag = "div", class: cls, children, ...rest }: PanelProps) {
  return <Tag class={`card${cls ? ` ${cls}` : ""}`} {...rest}>{children}</Tag>;
}

export function PanelBody({ class: cls, children }: { class?: string; children?: ComponentChildren }) {
  return <div class={`card-body${cls ? ` ${cls}` : ""}`}>{children}</div>;
}

/** 빈 화면·안내 카드(뚱이 사진이 붙는다). */
export function Empty({ children, role }: { children: ComponentChildren; role?: "alert" | "status" }) {
  return <div class="card empty" role={role}>{children}</div>;
}

/** 빨간 경고 한 줄(학칙 경고, 마감 위험, 오류). */
export function Warning({ children }: { children: ComponentChildren }) {
  return <p class="alert alert-error alert-soft">{children}</p>;
}
