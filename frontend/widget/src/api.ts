import type { ActionDecisionResult, ChatResponse, GradPlanCard, GradPlanSaveResult } from './types';

const KEEL_API_URL = (import.meta.env.VITE_KEEL_API_URL as string) ?? '';

function requestTokenRefresh(): void {
  window.parent.postMessage({ type: 'KEEL_TOKEN_REFRESH' }, '*');
}

export async function sendChat(
  token: string,
  message: string,
  sessionId: string,
): Promise<ChatResponse> {
  const res = await fetch(`${KEEL_API_URL}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  if (res.status === 401) {
    requestTokenRefresh();
    throw new Error('UNAUTHORIZED');
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Chat request failed (${res.status}): ${detail}`);
  }

  return res.json() as Promise<ChatResponse>;
}

async function decideAction(
  token: string,
  actionId: string,
  decision: 'approve' | 'reject',
): Promise<ActionDecisionResult> {
  const res = await fetch(`${KEEL_API_URL}/actions/${actionId}/${decision}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });

  if (res.status === 401) {
    requestTokenRefresh();
    throw new Error('UNAUTHORIZED');
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`${decision} action failed (${res.status}): ${detail}`);
  }

  // The backend returns the REAL result message (e.g. "Enrolled in 3 course(s) ✓"
  // for an enrollment, or the registrar-queue note for an institutional request).
  const body = (await res.json().catch(() => ({}))) as ActionDecisionResult;
  return {
    message: body.message ?? '',
    plans: body.plans ?? [],
  };
}

export interface KeelNotification {
  id: number;
  kind: string;
  body: string;
  created_at: string;
}

export async function getNotifications(token: string): Promise<KeelNotification[]> {
  const res = await fetch(`${KEEL_API_URL}/chat/notifications`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: 'no-store',
  });
  if (res.status === 401) {
    requestTokenRefresh();
    return [];
  }
  if (!res.ok) return [];
  const body = (await res.json().catch(() => ({ notifications: [] }))) as {
    notifications?: KeelNotification[];
  };
  return body.notifications ?? [];
}

export function approveAction(token: string, actionId: string): Promise<ActionDecisionResult> {
  return decideAction(token, actionId, 'approve');
}

export function rejectAction(token: string, actionId: string): Promise<ActionDecisionResult> {
  return decideAction(token, actionId, 'reject');
}

export async function saveGradPlan(
  token: string,
  plan: GradPlanCard,
  replace = false,
): Promise<GradPlanSaveResult> {
  const res = await fetch(`${KEEL_API_URL}/plans/grad/active`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      name: plan.label,
      replace,
      terms: plan.terms.map((term) => ({
        term: term.termKey,
        year: term.year,
        courses: term.courses.map((course) => ({ code: course.code })),
      })),
    }),
  });

  if (res.status === 401) {
    requestTokenRefresh();
    throw new Error('UNAUTHORIZED');
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Save plan failed (${res.status}): ${detail}`);
  }

  return res.json() as Promise<GradPlanSaveResult>;
}

export async function deleteGradPlan(token: string): Promise<GradPlanSaveResult> {
  const res = await fetch(`${KEEL_API_URL}/plans/grad/active`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  });

  if (res.status === 401) {
    requestTokenRefresh();
    throw new Error('UNAUTHORIZED');
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Delete plan failed (${res.status}): ${detail}`);
  }

  return res.json() as Promise<GradPlanSaveResult>;
}
