import type { ChatResponse } from './types';

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

export async function approveAction(
  token: string,
  actionId: string,
): Promise<void> {
  const res = await fetch(`${KEEL_API_URL}/actions/${actionId}/approve`, {
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
    throw new Error(`Approve action failed (${res.status}): ${detail}`);
  }
}
