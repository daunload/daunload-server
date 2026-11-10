/* 서버 API 응답 타입. 원본은 app/api/schemas.py(그리고 GET /me는 app/api/routes_account.py)다.
 * 날짜는 JSON에서 ISO 문자열로 온다. 서버 스키마를 바꾸면 여기도 고친다. */

export type ISODate = string;

export interface Me {
  student_id: string;
  consent_at: ISODate | null;
  is_admin: boolean;
  access_status: "approved" | "none" | "pending" | "rejected" | "revoked";
  access_requested_at: ISODate | null;
  access_retry_at: ISODate | null;
}

export interface Credentials {
  student_id: string;
  status: string;
  last_error: string | null;
  last_login_at: ISODate | null;
  last_synced_at: ISODate | null;
  next_sync_at: ISODate | null;
  personal_token_registered: boolean;
  personal_token_status: "none" | "ok" | "invalid";
  personal_token_registered_at: ISODate | null;
  personal_token_managed: boolean;
}

export interface Course {
  lms_course_id: number;
  name: string;
  professors: string | null;
  auto_play: boolean;
  attendance: Record<string, unknown>[] | null;
}

export interface Todo {
  kind: "video" | "assignment" | "quiz" | string;
  lms_course_id: number;
  course_name: string;
  lms_item_id: number;
  title: string;
  deadline: ISODate | null;
  due_at: ISODate | null;
  url: string | null;
  module_id: number | null;
  module_title: string | null;
  duration: number | null;
}

export interface Assignment {
  /** open / overdue / upcoming / locked / no_due(미제출) 또는 submitted / excused / unknown */
  status: string;
  kind: string;
  lms_course_id: number;
  course_name: string;
  assignment_id: number;
  title: string;
  group_name: string | null;
  due_at: ISODate | null;
  deadline: ISODate | null;
  lock_at: ISODate | null;
  unlock_at: ISODate | null;
  submitted_at: ISODate | null;
  missing: boolean;
  score: number | null;
  points_possible: number | null;
  url: string | null;
}

export interface Week {
  start: ISODate;
  end: ISODate;
  videos: Todo[];
  video_seconds: number;
  assignments: Assignment[];
  missed: Assignment[];
  later_assignments: Assignment[];
  late_assignments: Assignment[];
}

export interface PlanItem {
  position: number;
  lms_course_id: number;
  course_name: string;
  module_id: number;
  module_title: string;
  module_item_id: number;
  title: string;
  duration: number;
  deadline: ISODate | null;
  expected_start: ISODate;
  expected_end: ISODate;
  at_risk: boolean;
}

export interface Plan {
  items: PlanItem[];
  total_seconds: number;
  expected_end: ISODate | null;
  warnings: string[];
}

export interface Job {
  expected_start: ISODate | null;
  expected_end: ISODate | null;
  position: number;
  lms_course_id: number;
  module_item_id: number;
  title: string;
  duration: number;
  deadline: ISODate | null;
  status: "queued" | "playing" | "done" | "failed" | "skipped" | "locked" | "cancelled" | string;
  attempts: number;
  progress_seconds: number;
  progress_updated_at: ISODate | null;
  error: string | null;
  started_at: ISODate | null;
  finished_at: ISODate | null;
}

export interface Batch {
  queue_position: number | null;
  estimated_start: ISODate | null;
  estimated_wait_seconds: number | null;
  deadline_at_risk: boolean;
  id: number;
  scope: string;
  lms_course_id: number | null;
  module_id: number | null;
  status: "queued" | "running" | "done" | "partial" | "failed" | "cancelled" | string;
  total: number;
  done_count: number;
  failed_count: number;
  skipped_count: number;
  current_position: number | null;
  expected_end: ISODate | null;
  cancel_requested: boolean;
  error: string | null;
  created_at: ISODate;
  started_at: ISODate | null;
  finished_at: ISODate | null;
}

export interface BatchDetail extends Batch {
  jobs: Job[];
}
