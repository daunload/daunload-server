/* 승인 전 사용자 화면: 로그인은 했지만 관리자 승인을 받지 못한 사용자가 보는 유일한 화면. */

let accessMe = null;
let accessTimer = null;
let accessSwitching = false;

/** 열려 있던 앱 화면을 모두 멈추고 숨긴다. 승인 전에는 서버가 어차피 막으므로 화면에 남은 작업도 접는다. */
function leaveApp() {
  stopPolling();
  stopBulkDownload();
  tx.queue = [];
  tx.epoch++;
  cancelTranscription();
  closeConfirmDialog();
  $("#transcript-dialog")?.close();
  clearToasts();
  $("#app").hidden = true;
  $("#startup").hidden = true;
  $("#auth").hidden = true;
}

/** me가 없으면(열어 둔 탭에서 API가 막힌 경우) 서버에서 지금 상태를 가져온다. */
async function showAccessScreen(me) {
  if (!me) {
    if (accessSwitching) return;
    accessSwitching = true;
    leaveApp();
    const known = state.me;
    // /me가 실패해도 403 ACCESS_REQUIRED 자체가 "승인 전"이라는 뜻이므로, 마지막으로 아는 값으로 화면을 먼저 보여 준다.
    try { me = await api("/me"); } catch { me = { ...known, access_status: "revoked", access_retry_at: null }; }
    accessSwitching = false;
    if (me.access_status === "approved") return boot();
  }
  accessMe = me;
  leaveApp();
  renderAccess();
  $("#access").hidden = false;
  history.replaceState(null, "", location.pathname);
}

const ACCESS_COPY = {
  none: ["아직 권한이 없어요", "관리자가 승인하면 강의 재생, 내려받기, 할 일을 쓸 수 있어요."],
  pending: ["승인을 기다리는 중", ""],
  rejected: ["요청이 거절됐어요", ""],
  revoked: ["권한이 회수됐어요", "관리자가 사용 권한을 거뒀어요. 다시 쓰려면 요청을 보내 주세요."],
};

function renderAccess() {
  clearTimeout(accessTimer);
  const me = accessMe;
  const status = Object.hasOwn(ACCESS_COPY, me.access_status) ? me.access_status : "none";
  const [title, fixed] = ACCESS_COPY[status];
  const retryAt = me.access_retry_at ? new Date(me.access_retry_at) : null;
  const waiting = status === "rejected" && retryAt && retryAt.getTime() > Date.now();
  let message = fixed;
  if (status === "pending") {
    const at = me.access_requested_at ? `${fmtDate(new Date(me.access_requested_at))}에 ` : "";
    message = `${at}요청을 보냈어요. 승인되면 다시 접속했을 때 바로 쓸 수 있어요.`;
  } else if (status === "rejected") {
    message = waiting ? `${fmtDate(retryAt)}부터 다시 요청할 수 있어요.` : "지금 다시 요청할 수 있어요.";
    if (waiting) accessTimer = setTimeout(renderAccess, Math.min(retryAt.getTime() - Date.now() + 500, 2 ** 31 - 1));
  }
  $("#access-title").textContent = title;
  $("#access-message").textContent = message;
  $("#access-tag").hidden = status !== "pending";
  $("#access-error").textContent = "";
  const request = $("#access-request");
  request.hidden = status === "pending";
  request.disabled = !!waiting;
  request.textContent = status === "rejected" ? "다시 요청하기" : "권한 요청 보내기";
  request.classList.toggle("primary", status !== "rejected");
  $("#access-box").dataset.status = status;
  $("#access-box").focus();
}

$("#access-request").addEventListener("click", async (e) => {
  try {
    const sent = await withPending(e.currentTarget, "보내는 중", () => api("/access/request", { method: "POST" }));
    accessMe = { ...accessMe, ...sent };
    renderAccess();
  } catch (err) {
    if (err.status === 429 || err.status === 400) return recheckAccess();  // 거절 직후라 아직 못 보낸다: 서버가 가진 재요청 시각으로 다시 그린다
    if (err.code === "ACCESS_REQUIRED") return;
    $("#access-error").textContent = err.message;
  }
});

async function recheckAccess() {
  try {
    const me = await api("/me");
    if (me.access_status === "approved") return boot();
    accessMe = me;
    renderAccess();
  } catch (err) {
    if (err.status !== 401) $("#access-error").textContent = err.message;
  }
}
$("#access-recheck").addEventListener("click", (e) => withPending(e.currentTarget, "확인 중", recheckAccess));

$("#access-logout").addEventListener("click", logout);
$("#access-delete").addEventListener("click", async (e) => {
  // 학번을 모르면(열린 탭에서 /me가 실패해 전환된 경우) 입력 확인을 건너뛰지 않고 먼저 학번을 알아낸다
  if (!accessMe.student_id) {
    try {
      const me = await withPending(e.currentTarget, "확인 중", () => api("/me"));
      accessMe = { ...accessMe, ...me };
    } catch (err) { if (err.code !== "ACCESS_REQUIRED") toast(err.message, true); }
    if (!accessMe.student_id) return;
  }
  const ok = await confirmDialog({
    title: "계정을 삭제할까요?",
    body: ["저장된 학교 비밀번호와 동기화한 과목·할 일·공지, 재생 기록, 알림 설정을 모두 지웁니다. 되돌릴 수 없습니다."],
    requireText: accessMe.student_id, requireLabel: "확인을 위해 학번을 입력하세요",
    confirmLabel: "계정 삭제", danger: true,
  });
  if (!ok) return;
  try {
    await withPending($("#access-delete"), "처리 중", () => api("/me", { method: "DELETE" }));
    logout();
    toast("계정과 저장된 정보를 모두 지웠습니다");
  } catch (err) { toast(err.message, true); }
});
