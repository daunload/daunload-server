"use strict";

/* ---------------- 첫 화면을 막지 않는 자원 ----------------
 * 가장 먼저 불러온다. 첫 화면에 필요 없는 자원은 여기서 늦게 붙인다.
 * - 글꼴 CSS: index.html이 media="print"로 미리 받아 두고, 여기서 화면에 적용한다.
 * - 광고: 첫 화면이 다 그려지고 브라우저가 한가할 때 로더를 붙인다.
 * - 화면별 스크립트: <template id="scripts-NAME"> 안의 스크립트를 처음 필요할 때 붙인다.
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

function whenIdle(fn) {
  if ("requestIdleCallback" in window) requestIdleCallback(fn, { timeout: 4000 });
  else setTimeout(fn, 1500);
}

if (document.readyState === "complete") whenIdle(loadAds);
else window.addEventListener("load", () => whenIdle(loadAds), { once: true });

const scriptLoads = {};
const loadedScripts = new Set();

function appendScript(src) {
  if (loadedScripts.has(src)) return Promise.resolve();  // 다시 시도할 때 이미 실행한 파일은 건너뛴다(같은 const를 두 번 선언하면 오류)
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = () => { loadedScripts.add(src); resolve(); };
    script.onerror = () => { script.remove(); reject(new Error("화면을 불러오지 못했어요. 네트워크를 확인하고 다시 시도하세요.")); };
    document.body.append(script);
  });
}

/** <template id="scripts-NAME">의 스크립트를 적힌 순서대로 한 번만 불러온다. 서버가 붙인 버전 주소를 그대로 쓴다. */
function loadScripts(name) {
  if (scriptLoads[name]) return scriptLoads[name];
  const template = document.getElementById(`scripts-${name}`);
  const sources = template ? [...template.content.querySelectorAll("script[src]")].map((s) => s.getAttribute("src")) : [];
  scriptLoads[name] = sources.reduce((prev, src) => prev.then(() => appendScript(src)), Promise.resolve())
    .catch((err) => { delete scriptLoads[name]; throw err; });
  return scriptLoads[name];
}
