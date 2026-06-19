export type RiskLevel = 'on_track' | 'at_risk';
export type WorkloadLevel = 'light' | 'medium' | 'heavy';

export interface CourseRow {
  code: string;
  title: string;
  credits: number;
  section?: string;
  term?: string;
}

export interface PlanData {
  id: string;
  name: string;
  term: string;
  totalCredits: number;
  courses: CourseRow[];
  risk: RiskLevel;
  workload: WorkloadLevel;
  explanation?: string;
}

export interface ChatMessage {
  id: string;
  role: 'student' | 'keel';
  text: string;
  plans?: PlanData[];
  actionId?: string;
  pendingApproval?: boolean;
}

export interface ChatResponse {
  text: string;
  session_id: string;
  plan?: PlanData;
  plans?: PlanData[];
  action_id?: string;
  pending_approval?: boolean;
}
