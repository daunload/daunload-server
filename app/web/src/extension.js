/* 확장 프로그램(extensions/ssu-downloader)과 주고받는 창구. 다른 모듈을 import하지 않는다.
 * 테스트(tests/test_download_extension.py)가 이 파일만 실제 확장 프로그램이 붙은 페이지에 넣어 확인한다. */

// The bridge is injected only on the configured dashboard origin by the extension.
const AUDIO_REQUEST_TIMEOUT_MS = 60000;  // 학교 CDN이 느릴 수 있어 오디오 요청은 더 기다린다
/** 확장 프로그램 호출은 모두 ext.request를 거친다. 테스트가 가짜 확장 프로그램으로 바꿔 끼우는 지점이다. */
export const ext = { request: (action, data) => extensionRequest(action, data) };

export function extensionRequest(action, data = {}) {
  const isAudio = action.startsWith("audio-");
  return new Promise((resolve, reject) => {
    const id = crypto.randomUUID();
    const cleanup = () => { clearTimeout(timer); window.removeEventListener("message", receive); };
    const receive = (event) => {
      if (event.source !== window || event.origin !== location.origin ||
          event.data?.channel !== "ssu-download-response" || event.data.id !== id) return;
      cleanup();
      if (event.data.error) reject(new Error(event.data.error));
      else resolve(event.data.result);
    };
    const timer = setTimeout(() => {
      cleanup();
      if (isAudio) return reject(new Error("학교 서버 응답이 늦어 텍스트 변환을 멈췄습니다. 다시 시도해 주세요."));
      const error = new Error("아유 하기싫어 확장프로그램에 연결하지 못했습니다. 설치·사용 설정 후 페이지를 새로고침해 주세요.");
      if (action === "ping") error.code = "EXTENSION_MISSING";
      reject(error);
    }, action === "ping" ? 1500 : isAudio ? AUDIO_REQUEST_TIMEOUT_MS : 15000);
    window.addEventListener("message", receive);
    window.postMessage({ channel: "ssu-download-request", id, action, ...data }, location.origin);
  });
}
