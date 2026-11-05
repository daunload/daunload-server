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
