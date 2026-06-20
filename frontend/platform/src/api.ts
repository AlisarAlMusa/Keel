/**
 * Typed fetch wrappers for the Keel Platform Console.
 * Token is stored in memory only (never localStorage) per spec §S5.
 */

const BASE = '/platform';
let _token: string | null = null;

export function setToken(t: string) { _token = t; }
export function clearToken() { _token = null; }
export function hasToken() { return !!_token; }

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string> ?? {}),
  };
  if (_token) headers['Authorization'] = `Bearer ${_token}`;
  const res = await fetch(path, { ...opts, headers });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const b = await res.json(); msg = b?.detail ?? b?.error ?? msg; } catch {}
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export interface LoginResponse { token: string; role: string; expires_in: number; }

export async function login(email: string, password: string): Promise<LoginResponse> {
  return req<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

// ── Tenants ───────────────────────────────────────────────────────────────────

export interface TenantRow {
  id: string; slug: string; name: string; status: string;
  created_at: string; student_count: number; admin_count: number;
}
export interface TenantListResponse { tenants: TenantRow[]; }

export const listTenants = () => req<TenantListResponse>(`${BASE}/tenants`);

export interface ProvisionResponse { tenant_id: string; admin_email: string; status: string; }
export const provision = (name: string, admin_email: string) =>
  req<ProvisionResponse>(`${BASE}/tenants`, {
    method: 'POST', body: JSON.stringify({ name, admin_email }),
  });

export interface SuspendResponse { tenant_id: string; status: string; }
export const suspendTenant = (id: string) =>
  req<SuspendResponse>(`${BASE}/tenants/${id}/suspend`, { method: 'POST' });
export const unsuspendTenant = (id: string) =>
  req<SuspendResponse>(`${BASE}/tenants/${id}/unsuspend`, { method: 'POST' });

export interface EraseResponse { tenant_id: string; status: string; }
export const eraseTenant = (id: string, confirm_name: string) =>
  req<EraseResponse>(`${BASE}/tenants/${id}/erase`, {
    method: 'POST', body: JSON.stringify({ confirm_name }),
  });

// ── Cost ──────────────────────────────────────────────────────────────────────

export interface CostRow { tenant_id: string; kind: string; calls: number; tokens: number; cost_usd: number; }
export interface CostResponse { period: string; rows: CostRow[]; note: string; }

export const getCost = (period = 'week') =>
  req<CostResponse>(`${BASE}/cost?period=${period}`);

// ── Audit ─────────────────────────────────────────────────────────────────────

export interface AuditRow { id: number; action: string; target_tenant_id: string | null; detail: Record<string, unknown> | null; created_at: string; }
export interface AuditResponse { rows: AuditRow[]; }

export const getAudit = (limit = 50) =>
  req<AuditResponse>(`${BASE}/audit?limit=${limit}`);
