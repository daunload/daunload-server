// 진입점. 모듈을 예전 <script> 순서대로 불러온 뒤 boot()로 시작한다.
// 관리자 화면(screens/admin)은 여기서 불러오지 않는다. app.ts가 처음 열 때 import()로 받는다.
import "./styles/app.css";
import "./assets";
import { clearToasts, mountToasts, toast } from "./ui";
import { failedPlaybackBatches, renderTaskPanel } from "./tasks";
import { bulk } from "./downloads";
import { ext, extensionRequest } from "./extension";
import { cancelTranscription, tx } from "./transcribe";
import { boot, logout } from "./app";
import { renderVideoList, reportDownloadError } from "./screens/videos";
import { planHtml } from "./screens/playback";
import "./access";
import "./screens/home";
import { esc, fmtDate, state } from "./core";

// 테스트와 디버깅용 손잡이. 모듈 안의 상태는 전역이 아니므로 여기로만 들여다본다.
// ext.request는 확장 프로그램 호출을 바꿔 끼우는 지점이다(tests/test_transcribe_web.py).
// loadAdmin()은 관리자 묶음을 받아 그 모듈을 돌려준다(처음 화면에서는 받지 않는다).
declare global {
  interface Window { __ssu: Record<string, unknown> }
}

window.__ssu = {
  state, tx, bulk, ext, extensionRequest, toast, clearToasts, logout, renderTaskPanel, cancelTranscription, renderVideoList,
  esc, fmtDate, planHtml, failedPlaybackBatches, reportDownloadError,
  loadAdmin: () => import("./screens/admin"),
};

mountToasts();
boot();
