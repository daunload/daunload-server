/* ---------------- 첫 화면을 막지 않는 자원 ----------------
 * 가장 먼저 불러온다. 첫 화면에 필요 없는 자원은 여기서 늦게 붙인다.
 * - 글꼴 CSS: index.html이 media="print"로 미리 받아 두고, 여기서 화면에 적용한다.
 * - 광고: 첫 화면이 다 그려지고 브라우저가 한가할 때 로더를 붙인다.
 */

document.querySelectorAll("link[data-font]").forEach((link) => { link.media = "all"; });

const ADSENSE_SRC = "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1006974862356513";

function loadAds() {
  if (document.querySelector(`script[src="${ADSENSE_SRC}"]`)) return;
  const script = document.createElement("script");
  script.async = true;
  script.crossOrigin = "anonymous";
  script.src = ADSENSE_SRC;
  document.head.append(script);
}

export function whenIdle(fn) {
  if ("requestIdleCallback" in window) requestIdleCallback(fn, { timeout: 4000 });
  else setTimeout(fn, 1500);
}

if (document.readyState === "complete") whenIdle(loadAds);
else window.addEventListener("load", () => whenIdle(loadAds), { once: true });

/* ---------------- 하단 고정 광고 피하기 ----------------
 * 자동 광고가 화면 아래에 붙이는 ins[data-anchor-status="displayed"]의 높이를 --ad-inset에 넣어
 * 패널과 토스트가 광고 위로 올라오게 한다. 이 속성은 공개 API가 아니므로 광고가 바뀌면 확인해야 한다.
 */

function syncAdInset() {
  const ad = document.querySelector('ins.adsbygoogle[data-anchor-status="displayed"]');
  const box = ad?.getBoundingClientRect();
  const fixedAtBottom = ad && getComputedStyle(ad).position === "fixed" && box.bottom >= innerHeight - 1;
  const inset = fixedAtBottom ? `${Math.ceil(box.height)}px` : "0px";
  if (document.documentElement.style.getPropertyValue("--ad-inset") !== inset) {
    document.documentElement.style.setProperty("--ad-inset", inset);
  }
}

let adSyncFrame = 0;
function scheduleAdInsetSync() {
  if (!adSyncFrame) adSyncFrame = requestAnimationFrame(() => { adSyncFrame = 0; syncAdInset(); });
}
new MutationObserver(scheduleAdInsetSync).observe(document.body, {
  childList: true, subtree: true, attributes: true, attributeFilter: ["data-anchor-status", "style", "class"],
});
window.addEventListener("resize", scheduleAdInsetSync);
syncAdInset();
