/* 강의 재생 화면의 `자동 수강 과목` 묶음: 과목별 출석 칸, 남은 영상 수, 자동 수강 켜고 끄기.
 * 예전 과목 화면(#courses)을 강의 재생 화면으로 옮겼다. 출석 칸(Attendance)은 관리자 상세도 쓴다. */
import { useState } from "preact/hooks";
import { api, state } from "../core";
import type { Course } from "../types";
import { toast } from "../ui";
import { Section } from "../ui/layout";
import { viewPlayback } from "./playback";

const ATTEND_LABEL: Record<string, string> = { attendance: "출석", late: "지각", absent: "결석" };

interface AttendWeek { week: string | number; statuses?: string[] }

/** 주차별 출석 칸. 기록이 없으면 empty 문구를 쓴다. */
export function Attendance({ attendance, empty = "출석 기록이 아직 없습니다." }: { attendance: unknown; empty?: string }) {
  const weeks = ((attendance as AttendWeek[] | null) || []).filter((w) => w.statuses?.length);
  if (!weeks.length) return <p class="text-sm text-ink-3">{empty}</p>;
  return (
    <div class="attend" aria-label="주차별 출석">
      {weeks.map((w) => (
        <div class="week" key={w.week}>
          <div class="cells">{w.statuses!.map((s, i) => <i key={i} class={`cell ${s}`} title={`${w.week}주차 ${ATTEND_LABEL[s] || "기록 없음"}`} />)}</div>
          <small>{w.week}</small>
        </div>
      ))}
    </div>
  );
}

function CourseRow({ c }: { c: Course }) {
  const [pending, setPending] = useState<boolean | null>(null);  // 요청 중이면 사용자가 고른 값
  const pendingVideos = state.todos.filter((t) => t.lms_course_id === c.lms_course_id && t.kind === "video").length;
  const toggle = async (enabled: boolean) => {
    setPending(enabled);
    try {
      const row = await api<Course>(`/courses/${c.lms_course_id}/auto-play`, { method: "PUT", json: { enabled } });
      state.courses = state.courses.map((x) => (x.lms_course_id === c.lms_course_id ? row : x));
      toast(enabled ? `${row.name} 자동 수강을 켰습니다` : `${row.name} 자동 수강을 껐습니다`);
    } catch (err) {
      toast((err as Error).message, true);
    } finally {
      setPending(null);
      if (state.view === "playback") viewPlayback();
    }
  };
  return (
    <div class="course">
      <div class="course-head"><h3>{c.name}</h3><span class="text-sm text-ink-3">{c.professors || ""}</span>
        <label class="inline-flex cursor-pointer items-center gap-2 text-sm font-medium text-ink-2">
          <input type="checkbox" class="toggle" data-course={c.lms_course_id} checked={pending ?? c.auto_play} disabled={pending !== null}
            onChange={(e) => toggle(e.currentTarget.checked)} />자동 수강
        </label>
      </div>
      <Attendance attendance={c.attendance} />
      <p class="text-sm text-ink-3">남은 영상 {pendingVideos}개</p>
    </div>
  );
}

export function AutoPlayCourses() {
  return (
    <Section id="auto-play-title" title="자동 수강 과목" flush
      note="켜 두면 동기화할 때마다 새로 열린 영상을 순서대로 재생합니다. 직접 시청하지 않은 수강은 학칙상 문제가 될 수 있으니 확인 후 켜세요.">
      {state.courses.length
        ? state.courses.map((c) => <CourseRow key={c.lms_course_id} c={c} />)
        : <p class="px-5 pb-5 text-sm text-ink-3 max-phone:px-4">과목이 없습니다. ‘LMS 동기화’를 눌러 불러오세요.</p>}
      {state.courses.length > 0 && <p class="px-5 pt-2 pb-5 text-sm text-ink-3 max-phone:px-4">출석 칸: 초록 출석, 노랑 지각, 빨강 결석, 회색 기록 없음</p>}
    </Section>
  );
}
