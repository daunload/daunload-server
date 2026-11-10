/* 텍스트 변환 화면 조각: 강의 줄의 변환 버튼, 주차 일괄 변환 버튼, 결과 대화상자(#transcript-dialog).
 * 변환 자체(확장 프로그램 → Whisper Worker → 서버 저장)는 transcribe.js가 한다. */
import { render } from "preact";
import { useState } from "preact/hooks";
import { api } from "../core";
import { enqueueTranscription, onTranscriptSaved, txStatusOf } from "../transcribe";
import { Icon, IconOnly, confirmDialog, toast } from "../ui";
import { PendingButton, usePending } from "../ui/pending";
import { renderVideoList } from "./videos";

/** 목록의 영상 한 줄에서 변환에 필요한 값. mediaPath가 없으면(휴대폰·받을 수 없는 영상) 변환할 수 없다. */
export interface TxSource {
  module_item_id: number; module_id: number | string; title: string; courseName: string;
  mediaPath: string | null; duration?: number | null; transcript_id?: number | null;
}

export interface TxJob { courseId: number; moduleItemId: number; title: string; courseName: string; mediaPath: string | null; duration?: number | null }

const toJob = (v: TxSource, courseId: number): TxJob => ({ courseId, moduleItemId: v.module_item_id, title: v.title, courseName: v.courseName,
  mediaPath: v.mediaPath, duration: v.duration });

/** 줄마다 한 칸만 쓴다: 변환 중·대기 중 / 텍스트 보기 / 텍스트로 변환. 다시 변환은 드문 일이라 텍스트 창 안에 둔다. */
export function TranscribeButton({ v, courseId }: { v: TxSource; courseId: number }) {
  const st = v.mediaPath ? txStatusOf(courseId, v.module_item_id) : null;
  if (st) {
    const label = st === "running" ? "변환 중" : "대기 중";
    return <button class="btn small ghost" data-tx={v.module_item_id} title={label} disabled><IconOnly name={st === "running" ? "loader" : "clock"} label={label} /></button>;
  }
  if (v.transcript_id) {
    return (
      <button class="btn small ghost" data-tx-open={v.transcript_id} title="텍스트 보기"
        onClick={() => openTranscript(v.transcript_id!, v.mediaPath ? toJob(v, courseId) : null)}>
        <IconOnly name="text-view" label="텍스트 보기" />
      </button>
    );
  }
  if (!v.mediaPath) return null;
  return (
    <button class="btn small ghost" data-tx={v.module_item_id} title="텍스트로 변환"
      onClick={() => { enqueueTranscription([toJob(v, courseId)]); renderVideoList(); }}>
      <IconOnly name="text-convert" label="텍스트로 변환" />
    </button>
  );
}

/** 주차 머리줄의 일괄 변환. 변환할 영상이 2개 이상일 때만(1개면 그 줄의 버튼과 같다). */
export function TranscribeGroupButton({ items, title, courseId }: { items: TxSource[]; title: string; courseId: number }) {
  const todo = items.filter((v) => v.mediaPath && !v.transcript_id);
  if (todo.length < 2) return null;
  const label = `전체 텍스트로 변환 (${todo.length})`;
  return (
    <button class="btn small link" data-tx-group={String(items[0].module_id)} aria-label={`${title} ${label}`} title={label}
      onClick={() => { enqueueTranscription(todo.map((v) => toJob(v, courseId))); renderVideoList(); }}>
      <Icon name="text-convert" />전체 {todo.length}
    </button>
  );
}

/* ---------- 결과 대화상자 ---------- */

interface Transcript {
  id: number; lms_course_id: number; module_item_id: number; course_name: string; title: string; model: string;
  text: string; segments: { start: number; end: number; text: string }[];
}

function fmtStamp(sec: number): string {
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

const dialog = () => document.getElementById("transcript-dialog") as HTMLDialogElement;

function TranscriptView({ t, redoJob }: { t: Transcript; redoJob: TxJob | null }) {
  const [withTime, setWithTime] = useState(false);
  const [deleting, runDelete] = usePending();
  const text = withTime ? t.segments.map((s) => `[${fmtStamp(s.start)}] ${s.text}`).join("\n") : t.text;
  const close = () => dialog().close();

  const copy = async () => {
    try { await navigator.clipboard.writeText(text); toast("복사했습니다."); }
    catch { toast("복사하지 못했습니다. 텍스트를 직접 선택해 복사하세요.", true); }
  };
  const save = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    a.download = `${t.title.replace(/[\\/:*?"<>|]/g, " ").trim() || "transcript"}.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const redo = () => { close(); enqueueTranscription([redoJob!]); renderVideoList(); };
  const remove = async () => {
    const ok = await confirmDialog({ title: "변환한 텍스트를 삭제할까요?", body: [`${t.title}의 텍스트가 지워져요. 영상은 그대로 있어요.`],
      confirmLabel: "텍스트 삭제", danger: true });
    if (!ok) return;
    try { await runDelete(() => api(`/transcripts/${t.id}`, { method: "DELETE" })); }
    catch (err) { toast((err as Error).message, true); return; }
    onTranscriptSaved({ courseId: t.lms_course_id, moduleItemId: t.module_item_id, title: t.title }, null);
    close();
  };

  return (
    <>
      <div class="dialog-head"><strong>{t.title}</strong><span class="meta">{t.course_name} · {t.model}</span></div>
      <label class="check"><input type="checkbox" id="tx-time" checked={withTime} onChange={(e) => setWithTime(e.currentTarget.checked)} /><span>시간 표시</span></label>
      <pre id="tx-text" class="transcript-text">{text}</pre>
      <div class="row dialog-actions">
        <button class="btn small" id="tx-copy" onClick={copy}>복사</button>
        <button class="btn small" id="tx-save" onClick={save}>.txt로 받기</button>
        {redoJob && <button class="btn small" id="tx-redo" onClick={redo}>다시 변환</button>}
        <PendingButton class="btn small danger" id="tx-delete" busy={deleting} onClick={remove}>삭제</PendingButton>
        <button class="btn small primary ml-auto" id="tx-close" onClick={close}>닫기</button>
      </div>
    </>
  );
}

export async function openTranscript(id: number, redoJob: TxJob | null = null): Promise<void> {
  const dlg = dialog();
  render(null, dlg);  // 지난번 창의 상태(시간 표시 등)를 버린다
  render(<p class="meta">불러오는 중</p>, dlg);
  dlg.showModal();
  let t: Transcript;
  try { t = await api<Transcript>(`/transcripts/${id}`); }
  catch (err) {
    render(<><p>{(err as Error).message}</p><button class="btn" id="tx-close" onClick={() => dlg.close()}>닫기</button></>, dlg);
    return;
  }
  render(<TranscriptView t={t} redoJob={redoJob} />, dlg);
}
