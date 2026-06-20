/**
 * Typed fetch wrappers for the unified Keel console.
 * Auth: Bearer JWT issued by POST /auth/login.
 *   - role=tenant_admin    → /admin/* endpoints
 *   - role=platform_operator → /platform/* endpoints
 * Token is stored in memory only (never localStorage).
 */

const BASE = (import.meta.env.VITE_KEEL_API_URL as string) || '';

let _token: string | null = null;
let _tenantId: string | null = null;

export function setToken(t: string) { _token = t; }
export function clearToken() { _token = null; _tenantId = null; }
export function hasToken() { return !!_token; }
export function setTenantId(id: string) { _tenantId = id; }
export function getTenantId() { return _tenantId ?? ''; }

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = {};
  if (_token) h['Authorization'] = `Bearer ${_token}`;
  return h;
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      msg = body?.detail ?? body?.message ?? msg;
    } catch {
      // ignore parse errors
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string> ?? {}),
    ...authHeaders(),
  };
  const res = await fetch(`${BASE}${path}`, { ...opts, headers });
  return handleResponse<T>(res);
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export interface LoginResponse {
  tenant_name: string | null;
  token: string;
  role: string;
  tenant_id: string | null;
  expires_in: number;
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  return handleResponse<LoginResponse>(res);
}

// ── Admin: RAG Upload ─────────────────────────────────────────────────────────

export interface RagUploadResponse {
  source: string;
  job_id: string;
  chunks_estimated: number;
  status: string;
}

export async function uploadDocument(file: File, chunkType: string): Promise<RagUploadResponse> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE}/admin/rag/upload?chunk_type=${encodeURIComponent(chunkType)}`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });
  return handleResponse<RagUploadResponse>(res);
}

// ── Admin: Widget Config ──────────────────────────────────────────────────────

export interface WidgetConfig {
  persona: string;
  persona_name: string;
  allowed_origins: string[];
  enabled_tools: string[];
}

export async function getWidgetConfig(): Promise<WidgetConfig> {
  return req<WidgetConfig>('/admin/widget-config');
}

export async function putWidgetConfig(config: WidgetConfig): Promise<WidgetConfig> {
  return req<WidgetConfig>('/admin/widget-config', {
    method: 'PUT',
    body: JSON.stringify(config),
  });
}

// ── Admin: Widget Snippet ─────────────────────────────────────────────────────

export interface WidgetSnippetResponse {
  snippet: string;
  widget_id: string;
}

export async function getWidgetSnippet(): Promise<WidgetSnippetResponse> {
  return req<WidgetSnippetResponse>('/admin/widget-snippet');
}

// ── Admin: Cost ───────────────────────────────────────────────────────────────

export type CostPeriod = 'day' | 'week' | 'month';

export interface CostRow {
  kind: string;
  model: string | null;
  total_tokens: number;
  total_cost_usd: number;
  event_count: number;
}

export interface CostResponse {
  period: CostPeriod;
  tenant_id: string;
  rows: CostRow[];
  total_cost_usd: number;
}

export async function getCost(period: CostPeriod): Promise<CostResponse> {
  return req<CostResponse>(`/admin/cost?period=${period}`);
}

// ── Admin: Audit Log ──────────────────────────────────────────────────────────

export interface AuditEntry {
  actor_name: string | null;
  id: number;
  actor: string;
  action: string;
  before: unknown;
  after: unknown;
  created_at: string;
}

export interface AuditResponse {
  rows: AuditEntry[];
  total: number;
}

export async function getAuditLog(limit: number): Promise<AuditResponse> {
  return req<AuditResponse>(`/admin/audit?limit=${limit}`);
}

// ── Platform: Tenants ─────────────────────────────────────────────────────────

export interface TenantRow {
  id: string;
  slug: string;
  name: string;
  status: string;
  created_at: string;
  student_count: number;
  admin_count: number;
}

export interface TenantListResponse { tenants: TenantRow[]; }
export interface ProvisionResponse { tenant_id: string; admin_email: string; status: string; }
export interface SuspendResponse { tenant_id: string; status: string; }
export interface EraseResponse { tenant_id: string; status: string; }

export const listTenants = () => req<TenantListResponse>('/platform/tenants');

export const provision = (name: string, admin_email: string) =>
  req<ProvisionResponse>('/platform/tenants', {
    method: 'POST',
    body: JSON.stringify({ name, admin_email }),
  });

export const suspendTenant = (id: string) =>
  req<SuspendResponse>(`/platform/tenants/${id}/suspend`, { method: 'POST' });

export const unsuspendTenant = (id: string) =>
  req<SuspendResponse>(`/platform/tenants/${id}/unsuspend`, { method: 'POST' });

export const eraseTenant = (id: string, confirm_name: string) =>
  req<EraseResponse>(`/platform/tenants/${id}/erase`, {
    method: 'POST',
    body: JSON.stringify({ confirm_name }),
  });

// ── Platform: Cost (cross-tenant aggregate) ───────────────────────────────────

export interface PlatformCostRow {
  tenant_id: string;
  kind: string;
  calls: number;
  tokens: number;
  cost_usd: number;
}

export interface PlatformCostResponse {
  period: string;
  rows: PlatformCostRow[];
  note: string;
}

export const getPlatformCost = (period = 'week') =>
  req<PlatformCostResponse>(`/platform/cost?period=${period}`);

// ── Platform: Audit ───────────────────────────────────────────────────────────

export interface PlatformAuditRow {
  id: number;
  action: string;
  target_tenant_id: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface PlatformAuditResponse { rows: PlatformAuditRow[]; }

export const getPlatformAudit = (limit = 50) =>
  req<PlatformAuditResponse>(`/platform/audit?limit=${limit}`);
