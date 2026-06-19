/**
 * Typed fetch wrappers for all Portal API endpoints.
 * All requests use same-origin cookies (credentials: 'include').
 */

const BASE = '/api/portal';

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      msg = body?.error ?? body?.detail ?? body?.message ?? msg;
    } catch {
      // ignore parse errors
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export interface LoginResponse {
  ok: boolean;
  role: string;
}

export async function login(studentId: string, role: string): Promise<LoginResponse> {
  const res = await fetch(`${BASE}/login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ student_id: studentId, role }),
  });
  return handleResponse<LoginResponse>(res);
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/logout`, { method: 'POST', credentials: 'include' });
}

export interface KeelTokenResponse {
  token: string;
  expires_in: number;
}

export async function getKeelToken(): Promise<KeelTokenResponse> {
  const res = await fetch(`${BASE}/keel-token`, { credentials: 'include' });
  return handleResponse<KeelTokenResponse>(res);
}

// ── Student ───────────────────────────────────────────────────────────────────

export interface Enrollment {
  id: string;
  student_id: string;
  status: string;
  source: string;
  section_id: string;
  term: string;
  days: string;
  start_time: string;
  end_time: string;
  instructor: string;
  course_code: string;
  course_title: string;
  credits: number;
}

export interface ScheduleResponse {
  enrollments: Enrollment[];
}

export async function getSchedule(): Promise<ScheduleResponse> {
  const res = await fetch(`${BASE}/schedule`, { credentials: 'include' });
  return handleResponse<ScheduleResponse>(res);
}

export interface RequestItem {
  id: string;
  student_id: string;
  type: string;
  status: string;
  payload: Record<string, unknown>;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface RequestsResponse {
  requests: RequestItem[];
}

export async function getRequests(): Promise<RequestsResponse> {
  const res = await fetch(`${BASE}/requests`, { credentials: 'include' });
  return handleResponse<RequestsResponse>(res);
}

export interface ActivityItem {
  id: string;
  actor: string;
  action: string;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  created_at: string;
}

export interface ActivityResponse {
  activity: ActivityItem[];
}

export async function getActivity(): Promise<ActivityResponse> {
  const res = await fetch(`${BASE}/activity`, { credentials: 'include' });
  return handleResponse<ActivityResponse>(res);
}

// ── Registrar ─────────────────────────────────────────────────────────────────

export async function getRegistrarRequests(status = 'pending'): Promise<RequestsResponse> {
  const res = await fetch(`${BASE}/registrar/requests?status=${encodeURIComponent(status)}`, {
    credentials: 'include',
  });
  return handleResponse<RequestsResponse>(res);
}

export interface DecisionResponse {
  id: string;
  status: string;
  note: string | null;
}

export async function postDecision(
  id: string,
  decision: 'approve' | 'reject',
  note: string
): Promise<DecisionResponse> {
  const res = await fetch(`${BASE}/registrar/requests/${id}/decision`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, note }),
  });
  return handleResponse<DecisionResponse>(res);
}

export interface Course {
  id: string;
  code: string;
  title: string;
  credits: number;
  department: string;
  description: string;
}

export interface CatalogResponse {
  courses: Course[];
}

export async function getCatalog(): Promise<CatalogResponse> {
  const res = await fetch(`${BASE}/registrar/catalog`, { credentials: 'include' });
  return handleResponse<CatalogResponse>(res);
}

export interface Section {
  id: string;
  course_code: string;
  course_title: string;
  term: string;
  days: string;
  start_time: string;
  end_time: string;
  instructor: string;
  capacity: number;
  enrolled: number;
}

export interface SectionsResponse {
  sections: Section[];
}

export async function getSections(): Promise<SectionsResponse> {
  const res = await fetch(`${BASE}/registrar/sections`, { credentials: 'include' });
  return handleResponse<SectionsResponse>(res);
}

export interface Student {
  id: string;
  name: string;
  email: string;
  program: string;
  year: number;
  status: string;
}

export interface StudentsResponse {
  students: Student[];
}

export async function getStudents(): Promise<StudentsResponse> {
  const res = await fetch(`${BASE}/registrar/students`, { credentials: 'include' });
  return handleResponse<StudentsResponse>(res);
}
