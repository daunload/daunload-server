/* 계정 화면(#account): LMS 연결 상태, 개인 액세스 토큰 등록·교체·제거, 계정 삭제. */
import { useRef, useState } from "preact/hooks";
import { logout } from "../app";
import { api, fmtDate, state } from "../core";
import type { Credentials } from "../types";
import { confirmDialog, toast } from "../ui";
import { Panel, PanelBody, ViewHead } from "../ui/layout";
import { PendingButton, usePending } from "../ui/pending";
import { mountScreen } from "./mount";

export function viewAccount(): void {
  mountScreen("account", <Account />);
}

const STATUS_NAME: Record<string, string> = { ok: "정상", invalid: "다시 로그인 필요", error: "최근 오류" };
const date = (iso: string | null, none: string) => (iso ? fmtDate(new Date(iso)) : none);

function tokenStatus(c: Credentials | null): string {
  if (!c?.personal_token_registered) return "로그인하면 서버가 토큰을 자동 발급합니다. 직접 발급한 토큰을 등록할 수도 있습니다.";
  if (c.personal_token_status === "invalid") {
    return c.personal_token_managed ? "자동 발급한 토큰이 만료되거나 폐기됐습니다. 다시 로그인하면 새로 발급합니다." : "만료되거나 폐기된 토큰입니다. 새 토큰을 등록하세요.";
  }
  return c.personal_token_managed
    ? "로그인할 때 서버가 자동 발급해 관리합니다 · 학습 연결 자동 복구에 사용합니다. 제거해도 Canvas 설정 > 승인된 통합의 ‘SSU LMS 자동 수강’ 토큰은 직접 삭제해야 합니다."
    : "등록됨 · 학습 연결 자동 복구에 사용합니다.";
}

/** 토큰 등록(PUT)·제거(DELETE). 입력값은 보내자마자 지우고, 실패하면 오류 문구로 포커스를 옮긴다. */
function TokenForm({ c }: { c: Credentials | null }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"PUT" | "DELETE" | null>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const registered = !!c?.personal_token_registered;

  const update = async (method: "PUT" | "DELETE") => {
    const body = method === "PUT" ? { access_token: value.trim() } : undefined;
    setValue("");
    setError("");
    setBusy(method);
    try {
      state.cred = await api<Credentials>("/lms/access-token", { method, ...(body ? { json: body } : {}) });
      if (state.view === "account") viewAccount();
      toast(method === "PUT" ? "토큰을 등록하고 학습 연결을 확인했습니다" : "저장된 개인 토큰을 제거했습니다");
    } catch (err) {
      setError((err as Error).message);
      requestAnimationFrame(() => errorRef.current?.isConnected && errorRef.current.focus());
    } finally {
      setBusy(null);
    }
  };

  return (
    <form id="personal-token-form" onSubmit={(e) => { e.preventDefault(); update("PUT"); }}>
      <label for="personal-token">개인 액세스 토큰</label>
      <input id="personal-token" type="password" required maxLength={4096} autocomplete="off" spellcheck={false}
        aria-describedby="personal-token-help personal-token-error" value={value} disabled={busy !== null}
        onInput={(e) => setValue(e.currentTarget.value)} />
      <p id="personal-token-error" class="error small-text" role="alert" tabIndex={-1} ref={errorRef}>{error}</p>
      <div class="actions">
        <PendingButton class="btn primary" type="submit" busy={busy !== null} busyLabel={busy === "DELETE" ? "제거 중…" : "학교 연결 확인 중…"}>
          {registered ? "토큰 교체" : "토큰 등록"}
        </PendingButton>
        {registered && (
          <button class="btn ghost" type="button" id="remove-personal-token" disabled={busy !== null} onClick={() => update("DELETE")}>저장된 토큰 제거</button>
        )}
      </div>
    </form>
  );
}

function DeleteAccount() {
  const [busy, run] = usePending();
  const remove = async () => {
    const ok = await confirmDialog({
      title: "계정을 삭제할까요?",
      body: ["저장된 학교 로그인 세션과 동기화한 과목·할 일, 재생 기록, 알림 설정을 모두 지웁니다. 되돌릴 수 없습니다.",
        "서버가 발급한 Canvas 토큰은 Canvas 설정 > 승인된 통합에서 ‘SSU LMS 자동 수강’ 항목을 직접 삭제해 주세요."],
      requireText: state.me!.student_id, requireLabel: "확인을 위해 학번을 입력하세요",
      confirmLabel: "계정 삭제", danger: true,
    });
    if (!ok) return;
    try {
      await run(() => api("/me", { method: "DELETE" }));
      logout();
      toast("계정과 저장된 정보를 모두 지웠습니다");
    } catch (err) { toast((err as Error).message, true); }
  };
  return <div><PendingButton class="btn danger" id="delete-me" busy={busy} onClick={remove}>계정 삭제</PendingButton></div>;
}

function Account() {
  const c = state.cred;
  return (
    <>
      <ViewHead title="계정" />
      <Panel><PanelBody>
        <div class="summary-line"><span><b>{state.me?.student_id || ""}</b>학번</span>
          {c && <span><b>{STATUS_NAME[c.status] || c.status}</b>LMS 연결</span>}</div>
        {c && <p class="muted small-text">마지막 LMS 로그인 {date(c.last_login_at, "없음")}, 다음 자동 동기화 {date(c.next_sync_at, "예정 없음")}</p>}
        {c?.last_error && <p class="error">{c.last_error}</p>}
        <p class="small-text">학습 연결이 만료되면 등록한 개인 토큰, 학교 로그인 정보 순으로 복구합니다. 모두 만료되면 다시 로그인하거나 새 토큰을 등록하세요. 학교 비밀번호는 저장하지 않습니다.</p>
      </PanelBody></Panel>
      <Panel><PanelBody>
        <h3>개인 액세스 토큰</h3>
        <p class="small-text">{tokenStatus(c)}{c?.personal_token_registered_at ? ` 등록일 ${fmtDate(new Date(c.personal_token_registered_at))}` : ""}</p>
        <p class="muted small-text" id="personal-token-help">
          <a href="https://canvas.ssu.ac.kr/profile/settings" target="_blank" rel="noopener noreferrer">Canvas 설정에서 토큰 발급</a> 후 붙여넣으세요. 토큰은 서버에 암호화해 보관합니다. 만료·폐기 시 새 토큰이 필요합니다.
        </p>
        <TokenForm c={c} />
        <p class="muted small-text">여기서 제거해도 학교의 토큰과 현재 학습 세션은 유지됩니다. 토큰 자체를 폐기하려면 Canvas 설정에서 삭제하세요.</p>
      </PanelBody></Panel>
      <Panel><PanelBody>
        <h3>계정 삭제</h3>
        <p class="muted small-text">저장된 학교 로그인 세션과 동기화한 과목·할 일, 재생 기록, 알림 설정을 모두 지웁니다. 되돌릴 수 없습니다.</p>
        <DeleteAccount />
      </PanelBody></Panel>
    </>
  );
}
