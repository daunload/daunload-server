/* 과목 화면(#courses): 과목별 출석 칸, 남은 영상 수, 자동 수강 켜고 끄기. */
import { useState } from "preact/hooks";
import { needCred } from "../app";
import { api, state } from "../core";
import type { Course } from "../types";
import { toast } from "../ui";
import { Panel, ViewHead } from "../ui/layout";
import { mountScreen } from "./mount";

export function viewCourses(): void {
  if (needCred()) return;
  mountScreen("courses", <Courses />);
}

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
      if (state.view === "courses") viewCourses();
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

function Courses() {
  return (
    <>
      <ViewHead title="과목" />
      <p class="alert alert-outline">자동 수강을 켜면 동기화할 때마다 새로 열린 영상을 순서대로 재생합니다. 직접 시청하지 않은 수강은 학칙상 문제가 될 수 있으니 확인 후 켜세요.</p>
      <Panel>
        {state.courses.length
          ? state.courses.map((c) => <CourseRow key={c.lms_course_id} c={c} />)
          : <div class="empty"><p>과목이 없습니다. ‘LMS 동기화’를 눌러 불러오세요.</p></div>}
      </Panel>
      <p class="text-sm text-ink-3">초록 출석, 노랑 지각, 빨강 결석, 회색 기록 없음</p>
    </>
  );
}
