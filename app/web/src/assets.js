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
