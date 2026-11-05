"use strict";
// Runs only on the dashboard origins declared in the generated manifest.
window.addEventListener("message", async (event) => {
  const message = event.data;
  if (event.source !== window || event.origin !== location.origin ||
      message?.channel !== "ssu-download-request" ||
      typeof message.id !== "string" || message.id.length > 100) return;
  try {
    const result = await chrome.runtime.sendMessage({ ...message, channel: "ssu-download" });
    window.postMessage({ channel: "ssu-download-response", id: message.id, ...result }, location.origin);
  } catch {
    window.postMessage({ channel: "ssu-download-response", id: message.id,
      error: "확장프로그램이 갱신되었습니다. 페이지를 새로고침해 주세요." }, location.origin);
  }
});
