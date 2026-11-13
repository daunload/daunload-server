/* 설정 화면의 `알림 받을 곳` 묶음: 알림 받을 곳(Discord 웹훅, Telegram 봇) 목록·추가·테스트·삭제. */
import { useEffect, useState } from "preact/hooks";
import { api, fmtDate } from "../core";
import { confirmDialog, toast } from "../ui";
import { Section } from "../ui/layout";
import { PendingButton, usePending } from "../ui/pending";

interface Channel { id: number; kind: string; enabled: boolean; created_at: string }

const CHANNEL_NAME: Record<string, string> = { discord: "Discord", telegram: "Telegram" };

function ChannelRow({ c, reload }: { c: Channel; reload: () => void }) {
  const [testing, runTest] = usePending();
  const [deleting, runDelete] = usePending();
  const sendTest = async () => {
    try { await runTest(() => api(`/notifications/channels/${c.id}/test`, { method: "POST" })); toast("테스트 메시지를 보냈습니다"); }
    catch (err) { toast((err as Error).message, true); }
  };
  const remove = async () => {
    const ok = await confirmDialog({ title: "이 알림 채널을 삭제할까요?", body: ["삭제하면 이 채널로 더 이상 알림을 보내지 않아요."],
      confirmLabel: "알림 채널 삭제", danger: true });
    if (!ok) return;
    try { await runDelete(() => api(`/notifications/channels/${c.id}`, { method: "DELETE" })); toast("삭제했습니다"); reload(); }
    catch (err) { toast((err as Error).message, true); }
  };
  return (
    <li class="items-center">
      <span class="title">{CHANNEL_NAME[c.kind] || c.kind}</span>
      <span class="meta">{fmtDate(new Date(c.created_at))} 등록</span>
      <span class="ml-auto flex flex-wrap items-center gap-2">
        <PendingButton class="btn btn-sm" data-test={c.id} busy={testing} onClick={sendTest}>테스트 메시지 보내기</PendingButton>
        <PendingButton class="btn btn-sm btn-error btn-outline" data-del={c.id} busy={deleting} onClick={remove}>삭제</PendingButton>
      </span>
    </li>
  );
}

function ChannelForm({ reload }: { reload: () => void }) {
  const [kind, setKind] = useState("discord");
  const [busy, run] = usePending();
  const submit = async (e: Event) => {
    e.preventDefault();
    const form = e.currentTarget as HTMLFormElement;
    const value = (name: string) => (form.elements.namedItem(name) as HTMLInputElement).value || null;
    const body = { kind, webhook_url: value("webhook_url"), bot_token: value("bot_token"), chat_id: value("chat_id") };
    try {
      await run(() => api("/notifications/channels", { method: "POST", json: body }));
      toast("알림 받을 곳을 추가했습니다"); reload();
    } catch (err) { toast((err as Error).message, true); }
  };
  return (
    <form class="grid gap-3 border-t-[1.5px] border-line pt-4" id="channel-form" onSubmit={submit}>
      <h3 class="font-bold">알림 받을 곳 추가</h3>
      <label>종류<select class="select" name="kind" value={kind} onChange={(e) => setKind(e.currentTarget.value)}>
        <option value="discord">Discord 웹훅</option><option value="telegram">Telegram 봇</option></select></label>
      <label data-for="discord" hidden={kind !== "discord"}>웹훅 URL<input class="input" name="webhook_url" placeholder="https://discord.com/api/webhooks/..." /></label>
      <label data-for="telegram" hidden={kind !== "telegram"}>봇 토큰<input class="input" name="bot_token" /></label>
      <label data-for="telegram" hidden={kind !== "telegram"}>채팅 ID<input class="input" name="chat_id" /></label>
      <div><PendingButton class="btn" busy={busy}>추가하기</PendingButton></div>
    </form>
  );
}

/** 목록은 이 묶음이 직접 불러온다. 응답이 늦게 와도 화면을 떠났으면(컴포넌트가 사라졌으면) 그리지 않는다. */
export function NotificationsSection() {
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((n) => n + 1);
  useEffect(() => {
    let alive = true;
    api<Channel[]>("/notifications/channels").then(
      (rows) => { if (alive) { setChannels(rows); setError(""); } },
      (err: Error) => { if (alive) setError(err.message); });
    return () => { alive = false; };
  }, [nonce]);
  return (
    <Section id="settings-notify" title="알림 받을 곳" note="마감 24시간·3시간 전과 재생 결과를 보내 드립니다.">
      {error ? (
        <div class="alert alert-error alert-soft" role="alert"><p>{error}</p><div><button class="btn btn-sm" type="button" onClick={reload}>다시 시도</button></div></div>
      ) : channels === null ? (
        <div class="skeleton-list p-0" aria-busy="true"><p class="sr-only" role="status">알림 설정을 불러오는 중입니다.</p><div class="skeleton" aria-hidden="true" /></div>
      ) : channels.length ? (
        <ul class="list p-0 [&>li]:px-0">{channels.map((c) => <ChannelRow key={c.id} c={c} reload={reload} />)}</ul>
      ) : (
        <p class="text-sm text-ink-3">아직 알림 받을 곳이 없어요.</p>
      )}
      <ChannelForm reload={reload} />
    </Section>
  );
}
