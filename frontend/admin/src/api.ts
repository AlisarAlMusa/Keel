/**
 * Typed fetch wrappers for all Keel Admin API endpoints.
 * All requests carry X-Admin-Token and X-Tenant-Id headers.
 */

const BASE = (import.meta.env.VITE_KEEL_API_URL as string) || '';

export interface AuthHeaders {
  token: string;
  tenantId: string;
}

function headers(auth: AuthHeaders): Record<string, string> {
  return {
    'X-Admin-Token': auth.token,
    'X-Tenant-Id': auth.tenantId,
  };
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

// ── RAG Upload ────────────────────────────────────────────────────────────────

export interface RagUploadResponse {
  chunks_estimated: number;
  filename?: string;
  chunk_type?: string;
}

export async function uploadDocument(
  auth: AuthHeaders,
  file: File,
  chunkType: string,
): Promise<RagUploadResponse> {
  const form = new FormData();
  form.append('file', file);
  const url = `${BASE}/admin/rag/upload?chunk_type=${encodeURIComponent(chunkType)}`;
  const res = await fetch(url, {
    method: 'POST',
    headers: headers(auth),
    body: form,
  });
  return handleResponse<RagUploadResponse>(res);
}

// ── Widget Config ─────────────────────────────────────────────────────────────

export interface WidgetConfig {
  persona_prompt: string;
  persona_name: string;
  allowed_origins: string[];
  enabled_tools: string[];
}

export async function getWidgetConfig(auth: AuthHeaders): Promise<WidgetConfig> {
  const res = await fetch(`${BASE}/admin/widget-config`, {
    headers: headers(auth),
  });
  return handleResponse<WidgetConfig>(res);
}

export async function putWidgetConfig(
  auth: AuthHeaders,
  config: WidgetConfig,
): Promise<WidgetConfig> {
  const res = await fetch(`${BASE}/admin/widget-config`, {
    method: 'PUT',
    headers: { ...headers(auth), 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  return handleResponse<WidgetConfig>(res);
}

// ── Widget Snippet ────────────────────────────────────────────────────────────

export interface WidgetSnippetResponse {
  snippet: string;
}

export async function getWidgetSnippet(
  auth: AuthHeaders,
): Promise<WidgetSnippetResponse> {
  const res = await fetch(`${BASE}/admin/widget-snippet`, {
    headers: headers(auth),
  });
  return handleResponse<WidgetSnippetResponse>(res);
}

// ── Cost Dashboard ────────────────────────────────────────────────────────────

export type CostPeriod = 'day' | 'week' | 'month';

export interface CostRow {
  kind: string;
  model: string;
  total_tokens: number;
  estimated_cost_usd: number;
  events: number;
}

export interface CostResponse {
  period: CostPeriod;
  rows: CostRow[];
}

export async function getCost(
  auth: AuthHeaders,
  period: CostPeriod,
): Promise<CostResponse> {
  const res = await fetch(`${BASE}/admin/cost?period=${period}`, {
    headers: headers(auth),
  });
  return handleResponse<CostResponse>(res);
}

// ── Audit Log ─────────────────────────────────────────────────────────────────

export interface AuditEntry {
  actor: string;
  action: string;
  after: unknown;
  time: string;
}

export interface AuditResponse {
  entries: AuditEntry[];
}

export async function getAuditLog(
  auth: AuthHeaders,
  limit: number,
): Promise<AuditResponse> {
  const res = await fetch(`${BASE}/admin/audit?limit=${limit}`, {
    headers: headers(auth),
  });
  return handleResponse<AuditResponse>(res);
}
