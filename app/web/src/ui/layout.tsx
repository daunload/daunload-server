/* 화면 뼈대 컴포넌트. 여러 화면이 같은 모양을 쓰므로 여기서만 스타일을 정한다.
 * 지금은 legacy.css의 클래스(.view-head, .panel, .panel-body, .empty, .warning)를 그대로 내보낸다.
 * 모든 화면을 옮긴 뒤 이 파일의 클래스를 Tailwind 유틸리티로 바꾸고 legacy 규칙을 지운다. */
import type { ComponentChildren, JSX } from "preact";

/** 화면 제목(자막 상자)과 오른쪽 동작 버튼. */
export function ViewHead({ title, children }: { title: string; children?: ComponentChildren }) {
  return <div class="view-head"><h1>{title}</h1>{children}</div>;
}

type PanelProps = Omit<JSX.HTMLAttributes<HTMLElement>, "ref"> & { as?: "div" | "section"; children?: ComponentChildren };

/** 흰 카드. 제목이 있는 묶음은 as="section" + aria-labelledby로 쓴다. */
export function Panel({ as: Tag = "div", class: cls, children, ...rest }: PanelProps) {
  return <Tag class={`panel${cls ? ` ${cls}` : ""}`} {...rest}>{children}</Tag>;
}

export function PanelBody({ class: cls, children }: { class?: string; children?: ComponentChildren }) {
  return <div class={`panel-body${cls ? ` ${cls}` : ""}`}>{children}</div>;
}

/** 빈 화면·안내 카드(뚱이 사진이 붙는다). */
export function Empty({ children, role }: { children: ComponentChildren; role?: "alert" | "status" }) {
  return <div class="panel empty" role={role}>{children}</div>;
}

/** 빨간 경고 한 줄(학칙 경고, 마감 위험, 오류). */
export function Warning({ children }: { children: ComponentChildren }) {
  return <p class="warning">{children}</p>;
}
