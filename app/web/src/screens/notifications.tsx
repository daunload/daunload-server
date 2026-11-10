/* 알림 화면(#notifications): 알림 받을 곳(Discord 웹훅, Telegram 봇) 목록·추가·테스트·삭제. */
import { useState } from "preact/hooks";
import { viewError } from "../app";
import { api, fmtDate, state } from "../core";
import { confirmDialog, toast } from "../ui";
import { Empty, Panel, ViewHead } from "../ui/layout";
import { PendingButton, usePending } from "../ui/pending";
import { mountScreen } from "./mount";

interface Channel { id: number; kind: string; enabled: boolean; created_at: string }

const CHANNEL_NAME: Record<string, string> = { discord: "Discord", telegram: "Telegram" };

export async function viewNotifications(): Promise<void> {
  mountScreen("notifications", <Empty role="status">알림 설정을 불러오는 중입니다.</Empty>);
  let channels: Channel[];
  try { channels = await api<Channel[]>("/notifications/channels"); }
  catch (err) { if (state.view === "notifications") viewError((err as Error).message, viewNotifications); return; }
  if (state.view !== "notifications") return;
  mountScreen("notifications", <Notifications channels={channels} />);
}

function ChannelRow({ c }: { c: Channel }) {
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
    try { await runDelete(() => api(`/notifications/channels/${c.id}`, { method: "DELETE" })); toast("삭제했습니다"); viewNotifications(); }
    catch (err) { toast((err as Error).message, true); }
  };
  return (
    <li class="row">
      <span class="title">{CHANNEL_NAME[c.kind] || c.kind}</span>
      <span class="meta">{fmtDate(new Date(c.created_at))} 등록</span>
      <span class="row ml-auto">
        <PendingButton class="btn small" data-test={c.id} busy={testing} onClick={sendTest}>테스트 메시지 보내기</PendingButton>
        <PendingButton class="btn small danger" data-del={c.id} busy={deleting} onClick={remove}>삭제</PendingButton>
      </span>
    </li>
  );
}

function ChannelForm() {
  const [kind, setKind] = useState("discord");
  const [busy, run] = usePending();
  const submit = async (e: Event) => {
    e.preventDefault();
    const form = e.currentTarget as HTMLFormElement;
    const value = (name: string) => (form.elements.namedItem(name) as HTMLInputElement).value || null;
    const body = { kind, webhook_url: value("webhook_url"), bot_token: value("bot_token"), chat_id: value("chat_id") };
    try {
      await run(() => api("/notifications/channels", { method: "POST", json: body }));
      toast("알림 받을 곳을 추가했습니다"); viewNotifications();
    } catch (err) { toast((err as Error).message, true); }
  };
  return (
    <form class="panel-body" id="channel-form" onSubmit={submit}>
      <h3>알림 받을 곳 추가</h3>
      <label>종류<select name="kind" value={kind} onChange={(e) => setKind(e.currentTarget.value)}>
        <option value="discord">Discord 웹훅</option><option value="telegram">Telegram 봇</option></select></label>
      <label data-for="discord" hidden={kind !== "discord"}>웹훅 URL<input name="webhook_url" placeholder="https://discord.com/api/webhooks/..." /></label>
      <label data-for="telegram" hidden={kind !== "telegram"}>봇 토큰<input name="bot_token" /></label>
      <label data-for="telegram" hidden={kind !== "telegram"}>채팅 ID<input name="chat_id" /></label>
      <div><PendingButton class="btn primary" busy={busy}>추가하기</PendingButton></div>
    </form>
  );
}

function Notifications({ channels }: { channels: Channel[] }) {
  return (
    <>
      <ViewHead title="알림" />
      <Panel>
        {channels.length
          ? <ul class="list">{channels.map((c) => <ChannelRow key={c.id} c={c} />)}</ul>
          : <div class="empty"><p>알림 받을 곳을 추가하면 마감 24시간·3시간 전, 재생 결과를 보내 드립니다.</p></div>}
      </Panel>
      <Panel><ChannelForm /></Panel>
    </>
  );
}
