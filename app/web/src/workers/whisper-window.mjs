// Whisper 창 나누기 규칙. whisper.worker.js가 쓰고, 외부 라이브러리 없이 따로 테스트한다.
//
// 30초 창의 끝에 걸친 문장은 잘려서 나온다. 끝나는 시각이 "창 길이 - 겹침" 안에 있는 문장만 내고,
// 다음 창은 마지막으로 낸 문장의 끝에서 시작해 잘린 문장을 처음부터 다시 듣는다.

export function planWindow(chunks, takeSeconds, isFinal, overlapSeconds) {
  const clean = (chunks || [])
    .map((c) => ({ start: c.timestamp?.[0] ?? 0, end: c.timestamp?.[1] ?? null, text: (c.text || "").trim() }))
    .filter((c) => c.text);
  if (isFinal) {
    return { segments: clean.map((c) => ({ ...c, end: c.end ?? takeSeconds })), advanceSeconds: takeSeconds };
  }
  const cut = takeSeconds - overlapSeconds;
  const done = [];
  for (const c of clean) {
    if (c.end === null || c.end > cut) break;  // 여기부터는 다음 창에서 다시 듣는다
    done.push(c);
  }
  const lastEnd = done.length ? done[done.length - 1].end : 0;
  if (lastEnd >= 1) return { segments: done, advanceSeconds: lastEnd };
  // 끊을 곳이 없을 만큼 긴 문장: 겹침 앞까지 내고 넘어간다(진행 보장)
  const forced = clean.filter((c) => c.start < cut).map((c) => ({ ...c, end: Math.min(c.end ?? cut, cut) }));
  return { segments: forced, advanceSeconds: cut };
}
