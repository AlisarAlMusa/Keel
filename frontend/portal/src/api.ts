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

export async function login(email: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${BASE}/login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  return handleResponse<LoginResponse>(res);
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/logout`, { method: 'POST', credentials: 'include' });
}

export interface KeelTokenResponse {
  token: string;
  expires_in: number;
  persona_name?: string;
}

export async function getKeelToken(): Promise<KeelTokenResponse> {
  const res = await fetch(`${BASE}/keel-token`, { credentials: 'include', cache: 'no-store' });
  return handleResponse<KeelTokenResponse>(res);
}

export async function getWidgetStatus(): Promise<{ available: boolean }> {
  const res = await fetch(`${BASE}/widget-status`, { credentials: 'include', cache: 'no-store' });
  return handleResponse<{ available: boolean }>(res);
}

// ── Student ───────────────────────────────────────────────────────────────────

export interface Enrollment {
  id: string;
  student_id: string;
  status: string;
  source: string;
  section_id: string;
  section_num: number;
  term: string;
  year: number;
  days: string | null;
  start_time: string | null;
  end_time: string | null;
  course_code: string;
  course_title: string;
  credits: number;
}

export interface ScheduleResponse {
  enrollments: Enrollment[];
  student?: { program_code: string | null; major: string | null } | null;
}

export async function getSchedule(): Promise<ScheduleResponse> {
  const res = await fetch(`${BASE}/schedule`, { credentials: 'include', cache: 'no-store' });
  return handleResponse<ScheduleResponse>(res);
}

export interface RequestItem {
  id: string;
  student_id: string;
  student_name?: string;
  student_email?: string;
  type: string;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
  resolved_at: string | null;
  target: string | null;
}

export interface RequestsResponse {
  requests: RequestItem[];
}

export async function getRequests(): Promise<RequestsResponse> {
  const res = await fetch(`${BASE}/requests`, { credentials: 'include', cache: 'no-store' });
  return handleResponse<RequestsResponse>(res);
}

export interface ActivityItem {
  id: string;
  actor: string;
  actor_name?: string;
  actor_email?: string;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
}

export interface ActivityResponse {
  activity: ActivityItem[];
}

export async function getActivity(): Promise<ActivityResponse> {
  const res = await fetch(`${BASE}/activity`, { credentials: 'include', cache: 'no-store' });
  return handleResponse<ActivityResponse>(res);
}

// ── Registrar ─────────────────────────────────────────────────────────────────

export async function getRegistrarRequests(status = 'pending'): Promise<RequestsResponse> {
  const res = await fetch(`${BASE}/registrar/requests?status=${encodeURIComponent(status)}`, {
    credentials: 'include',
    cache: 'no-store',
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
  const res = await fetch(`${BASE}/registrar/catalog`, { credentials: 'include', cache: 'no-store' });
  return handleResponse<CatalogResponse>(res);
}

export interface Section {
  id: string;
  course_code: string;
  course_title: string;
  section_num: number;
  term: string;
  year: number;
  days: string | null;
  start_time: string | null;
  end_time: string | null;
  instructor: string | null;
  capacity: number;
  enrolled: number;
}

export interface SectionsResponse {
  sections: Section[];
}

export async function getSections(): Promise<SectionsResponse> {
  const res = await fetch(`${BASE}/registrar/sections`, { credentials: 'include', cache: 'no-store' });
  return handleResponse<SectionsResponse>(res);
}

export async function openSeat(
  sectionId: string,
): Promise<{ id: string; course_code: string; enrolled: number; capacity: number }> {
  const res = await fetch(`${BASE}/registrar/sections/${sectionId}/open-seat`, {
    method: 'POST',
    credentials: 'include',
  });
  return handleResponse(res);
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
  const res = await fetch(`${BASE}/registrar/students`, { credentials: 'include', cache: 'no-store' });
  return handleResponse<StudentsResponse>(res);
}
