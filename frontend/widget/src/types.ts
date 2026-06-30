export type RiskLevel = 'on_track' | 'at_risk';
export type WorkloadLevel = 'light' | 'medium' | 'heavy';

export interface CourseRow {
  code: string;
  title: string;
  credits: number;
  requirement?: string;
  section?: string;
  term?: string;
}

export interface PlanData {
  kind?: 'plan';
  id: string;
  name: string;
  term: string;
  totalCredits: number;
  courses: CourseRow[];
  risk: RiskLevel;
  workload: WorkloadLevel;
  explanation?: string;
}

export interface SectionScheduleItem {
  code: string;
  title?: string;
  credits?: number;
  requirement?: string;
  section_id: string;
  when: string;
  instructor: string;
  seats: number;
}

export interface SectionSchedule {
  id: string;
  label: string; // "Option 1", "Option 2", …
  items: SectionScheduleItem[];
}

export interface SectionUnavailable {
  code: string;
  reason: string; // "all sections full" | "not offered this term" | …
  remedy: string; // "waitlist" | "another term" | …
}

export interface SectionOptionsCard {
  kind: 'sections';
  id: string;
  term: string;
  hasPrefs: boolean;
  prefSummary?: string | null;
  schedules: SectionSchedule[];
  unavailable: SectionUnavailable[];
}

export interface GradPlanTerm {
  term: string;
  termKey: string;
  year: number;
  status?: 'registered' | 'upcoming' | 'done' | string;
  courses: CourseRow[];
  credits: number;
  workload: WorkloadLevel;
}

export interface GradPlanCard {
  kind: 'gradplan';
  id: string;
  label: string;
  blurb: string;
  termsToGrad: number;
  graduates: string;
  heaviestTerm: string | null;
  totalCredits: number;
  terms: GradPlanTerm[];
  /** True when this card is the student's already-saved active plan (loaded/swapped/
   *  synced) — the Save button is hidden, since there is nothing new to save. */
  saved?: boolean;
}

export type WidgetCard = PlanData | SectionOptionsCard | GradPlanCard;

export interface ChatMessage {
  id: string;
  role: 'student' | 'keel';
  text: string;
  plans?: WidgetCard[];
  actionId?: string;
  pendingApproval?: boolean;
}

export interface ChatResponse {
  response: string;  // matches API ChatResponse.response field
  request_id: string;
  plan?: PlanData;
  plans?: WidgetCard[];
  action_id?: string;
  pending_approval?: boolean;
}

export interface ActionDecisionResult {
  message: string;
  plans?: WidgetCard[];
}

export interface GradPlanSaveResult {
  message: string;
  plan?: GradPlanCard;
  conflict?: boolean;
  existing_plan_id?: string;
  existing_name?: string;
}
