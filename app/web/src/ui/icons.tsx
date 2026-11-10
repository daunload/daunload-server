/* 아이콘. 모두 장식용(aria-hidden)이라 버튼의 접근 가능한 이름을 바꾸지 않는다.
 * - Preact 컴포넌트: <Icon name="download" />
 * - HTML 문자열을 만드는 기존 화면: icon("download"), iconOnly("download", "내려받기")
 */

const ICONS: Record<string, string> = {
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  download: '<path d="M12 4v11m-4-4 4 4 4-4M5 20h14"/>',
  "download-done": '<path d="m6 10 4.5 4.5L19 6M5 20h14"/>',
  "text-convert": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 12h6M12 12v6"/>',
  "text-view": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6"/>',
  "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16 9.5"/>',
  circle: '<circle cx="12" cy="12" r="9"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  loader: '<path d="M12 3a9 9 0 1 0 9 9"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.3-4L3 10M3 4v6h6M4 13a8 8 0 0 0 14.3 4l2.7-3M21 20v-6h-6"/>',
  puzzle: '<path d="M19.439 7.85c-.049.322.059.648.289.878l1.568 1.568c.47.47.706 1.087.706 1.704s-.235 1.233-.706 1.704l-1.611 1.611a.98.98 0 0 1-.837.276c-.47-.07-.802-.48-.968-.925a2.501 2.501 0 1 0-3.214 3.214c.446.166.855.497.925.968a.979.979 0 0 1-.276.837l-1.61 1.61a2.404 2.404 0 0 1-1.705.707 2.402 2.402 0 0 1-1.704-.706l-1.568-1.568a1.026 1.026 0 0 0-.877-.29c-.493.074-.84.504-1.02.968a2.5 2.5 0 1 1-3.237-3.237c.464-.18.894-.527.967-1.02a1.026 1.026 0 0 0-.289-.877l-1.568-1.568A2.402 2.402 0 0 1 1.998 12c0-.617.236-1.234.706-1.704L4.23 8.77c.24-.24.581-.353.917-.303.515.077.877.528 1.073 1.01a2.5 2.5 0 1 0 3.259-3.259c-.482-.196-.933-.558-1.01-1.073-.05-.336.062-.676.303-.917l1.525-1.525A2.402 2.402 0 0 1 12 1.998c.617 0 1.234.236 1.704.706l1.568 1.568c.23.23.556.338.877.29.493-.074.84-.504 1.02-.968a2.5 2.5 0 1 1 3.237 3.237c-.464.18-.894.527-.967 1.02Z"/>',
};

/** 장식용 인라인 SVG 문자열. */
export function icon(name: string): string {
  const paths = ICONS[name];
  if (!paths) return "";
  return `<svg class="icon" data-icon="${name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${paths}</svg>`;
}

/** 글자 없는 아이콘 버튼의 내용. 이름은 보조기기와 테스트가 그대로 읽는다. 눈으로 볼 뜻은 title과 목록 위 범례가 맡는다. */
export function iconOnly(name: string, label: string): string {
  return `${icon(name)}<span class="sr-only">${label}</span>`;
}

export function Icon({ name }: { name: string }) {
  const paths = ICONS[name];
  if (!paths) return null;
  return (
    <svg class="icon" data-icon={name} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"
      dangerouslySetInnerHTML={{ __html: paths }} />
  );
}

/** 글자 없는 아이콘 버튼의 내용(컴포넌트판 iconOnly). */
export function IconOnly({ name, label }: { name: string; label: string }) {
  return <><Icon name={name} /><span class="sr-only">{label}</span></>;
}
