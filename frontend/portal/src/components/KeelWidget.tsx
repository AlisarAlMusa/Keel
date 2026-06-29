/**
 * KeelWidget — floating chat button that opens the Keel advisor iframe.
 *
 * Token flow (D-P5-006):
 *   1. Portal JS fetches a JWT from /api/portal/keel-token (same-origin, session cookie).
 *   2. Widget iframe is pointed at {KEEL_API_URL}/widget/.
 *   3. Once the iframe loads, portal postMessages {type:'KEEL_TOKEN', token} to it.
 *
 * Listens for postMessages from the iframe:
 *   KEEL_CLOSE          → close the iframe
 *   ENROLLMENT_COMPLETE → call onEnrollmentComplete so parent can refresh schedule
 *   KEEL_TOKEN_REFRESH  → re-fetch token and re-post to iframe
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { getKeelToken, getWidgetStatus } from '../api';

const KEEL_API_URL = (import.meta.env.VITE_KEEL_API_URL as string) || '';

interface KeelWidgetProps {
  onEnrollmentComplete: () => void;
}

export function KeelWidget({ onEnrollmentComplete }: KeelWidgetProps) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false); // true once opened first time
  const [available, setAvailable] = useState(true); // false → tenant suspended, grey out
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const personaNameRef = useRef<string>('Keel Advisor');

  // Probe widget availability on mount (and on focus) so the launcher greys out when the
  // institution is suspended — without minting a token or waiting for a failed click.
  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const s = await getWidgetStatus();
        if (alive) setAvailable(s.available);
      } catch {
        if (alive) setAvailable(true); // fail-open
      }
    };
    void check();
    window.addEventListener('focus', check);
    return () => {
      alive = false;
      window.removeEventListener('focus', check);
    };
  }, []);

  // Fetch token and post to iframe — also sends personaName so widget can set header
  const postToken = useCallback(async () => {
    try {
      const data = await getKeelToken();
      personaNameRef.current = (data as { token: string; persona_name?: string }).persona_name || 'Keel Advisor';
      iframeRef.current?.contentWindow?.postMessage(
        { type: 'KEEL_TOKEN', token: data.token, personaName: personaNameRef.current },
        KEEL_API_URL || '*'
      );
    } catch (err) {
      console.error('[KeelWidget] Failed to fetch keel-token:', err);
      setError('Could not load advisor. Please try again.');
    }
  }, []);

  // Handle iframe load
  const handleIframeLoad = useCallback(() => {
    setLoading(false);
    postToken();
  }, [postToken]);

  // Handle messages from the iframe
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      // Only accept messages from the keel-api origin (or any if not configured)
      if (KEEL_API_URL && event.origin && !event.origin.startsWith(KEEL_API_URL)) {
        return;
      }
      const data = event.data as { type?: string };
      if (!data || typeof data.type !== 'string') return;

      switch (data.type) {
        case 'KEEL_CLOSE':
          setOpen(false);
          break;
        case 'ENROLLMENT_COMPLETE':
          onEnrollmentComplete();
          break;
        case 'KEEL_TOKEN_REFRESH':
          postToken();
          break;
        default:
          break;
      }
    }

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [onEnrollmentComplete, postToken]);

  function handleOpen() {
    if (!available) return; // suspended → launcher is greyed; do nothing
    setError(null);
    setOpen(true);
    if (!mounted) {
      setMounted(true);
      setLoading(true);
    }
  }

  function handleClose() {
    setOpen(false);
    setError(null);
  }

  const widgetSrc = KEEL_API_URL ? `${KEEL_API_URL}/widget/` : null;

  return (
    <>
      {/* Floating chat button — chat-bubble shape (reverse Q: circle + tail bottom-left) */}
      <button
        onClick={handleOpen}
        disabled={!available}
        aria-label={available ? 'Open Keel advisor' : 'Keel advisor unavailable — institution suspended'}
        title={available ? 'Keel Advisor' : 'Advisor unavailable — your institution is suspended'}
        style={{
          position: 'fixed',
          bottom: '28px',
          right: '28px',
          width: '68px',
          height: '68px',
          /* border-radius: large on top-left, top-right, bottom-right; 0 on bottom-left for the tail */
          borderRadius: '50% 50% 50% 8px',
          background: available ? '#000435' : '#3a3f4d',
          border: `2.5px solid ${available ? '#4B2E0A' : '#5a5f6d'}`,
          cursor: available ? 'pointer' : 'not-allowed',
          opacity: available ? 1 : 0.5,
          filter: available ? 'none' : 'grayscale(1)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: '0 4px 24px rgba(0,4,53,0.55), 0 0 0 0 rgba(75,46,10,0.4)',
          zIndex: 900,
          padding: '6px',
          overflow: 'hidden',
          transition: 'transform 0.15s ease, box-shadow 0.15s ease',
        }}
        onMouseEnter={(e) => {
          if (!available) return;
          e.currentTarget.style.transform = 'scale(1.08)';
          e.currentTarget.style.boxShadow = '0 8px 32px rgba(0,4,53,0.65), 0 0 0 4px rgba(75,46,10,0.3)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = 'scale(1)';
          e.currentTarget.style.boxShadow = '0 4px 24px rgba(0,4,53,0.55), 0 0 0 0 rgba(75,46,10,0.4)';
        }}
      >
        <img
          src="/creamy-keel-icon.png"
          alt="Keel"
          width={52}
          height={52}
          style={{ objectFit: 'contain', display: 'block', borderRadius: '50%' }}
          onError={(e) => {
            const btn = (e.target as HTMLImageElement).parentElement as HTMLButtonElement;
            (e.target as HTMLImageElement).style.display = 'none';
            btn.innerHTML = '<span style="color:#F0ECDD;font-family:Fraunces,Georgia,serif;font-size:24px;font-weight:700">K</span>';
          }}
        />
      </button>

      {/* Widget drawer — always mounted once opened so iframe state persists */}
      {mounted && (
        <div
          style={{
            position: 'fixed',
            bottom: expanded ? '20px' : '96px',
            right: expanded ? '20px' : '28px',
            width: expanded ? 'min(85vw, 1000px)' : '480px',
            height: expanded ? 'calc(90vh - 20px)' : '680px',
            maxWidth: expanded ? 'calc(100vw - 40px)' : 'calc(100vw - 40px)',
            maxHeight: expanded ? 'calc(100vh - 40px)' : 'calc(100vh - 120px)',
            background: '#000435',
            borderRadius: '16px',
            boxShadow: '0 16px 56px rgba(0,4,53,0.55)',
            zIndex: 950,
            display: open ? 'flex' : 'none',
            flexDirection: 'column',
            overflow: 'hidden',
            transition: 'width 0.25s ease, height 0.25s ease, bottom 0.25s ease, right 0.25s ease',
          }}
        >
          {/* Header bar */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '12px 16px',
              borderBottom: '1px solid rgba(73,91,125,0.4)',
            }}
          >
            <span
              style={{
                color: '#F0ECDD',
                fontFamily: 'Source Serif 4, Georgia, serif',
                fontSize: '0.95rem',
                fontWeight: 600,
              }}
            >
              Keel Advisor
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              {/* Expand / restore toggle */}
              <button
                onClick={() => setExpanded(e => !e)}
                aria-label={expanded ? 'Restore size' : 'Expand advisor'}
                title={expanded ? 'Restore' : 'Expand'}
                style={{
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  color: '#8BA3C5',
                  fontSize: '14px',
                  lineHeight: 1,
                  padding: '4px 6px',
                  borderRadius: '4px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                {expanded
                  ? /* collapse icon ⊡ */ <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M6 2H2v4M10 2h4v4M6 14H2v-4M10 14h4v-4"/></svg>
                  : /* expand icon ⊞ */ <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M2 6V2h4M14 6V2h-4M2 10v4h4M14 10v4h-4"/></svg>
                }
              </button>
            <button
              onClick={handleClose}
              aria-label="Close advisor"
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: '#8BA3C5',
                fontSize: '20px',
                lineHeight: 1,
                padding: '2px 6px',
              }}
            >
              ×
            </button>
            </div>
          </div>

          {/* Loading / error states */}
          {loading && (
            <div
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#8BA3C5',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: '0.875rem',
              }}
            >
              Loading advisor…
            </div>
          )}

          {error && !loading && (
            <div
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexDirection: 'column',
                gap: '12px',
                color: '#8BA3C5',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: '0.875rem',
                padding: '16px',
                textAlign: 'center',
              }}
            >
              <span>{error}</span>
              <button
                onClick={() => { setError(null); setLoading(true); }}
                style={{
                  background: '#F0ECDD',
                  color: '#000435',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '6px 16px',
                  cursor: 'pointer',
                  fontFamily: 'Inter, system-ui, sans-serif',
                  fontWeight: 600,
                  fontSize: '0.875rem',
                }}
              >
                Retry
              </button>
            </div>
          )}

          {/* The actual iframe */}
          {widgetSrc && !error && (
            <iframe
              ref={iframeRef}
              src={widgetSrc}
              title="Keel Advisor"
              onLoad={handleIframeLoad}
              style={{
                flex: 1,
                border: 'none',
                display: loading ? 'none' : 'block',
                width: '100%',
                height: '100%',
              }}
              allow="clipboard-write"
            />
          )}

          {/* Fallback when no KEEL_API_URL is configured */}
          {!widgetSrc && !error && (
            <div
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#8BA3C5',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: '0.875rem',
                padding: '16px',
                textAlign: 'center',
              }}
            >
              Advisor not configured — set VITE_KEEL_API_URL.
            </div>
          )}
        </div>
      )}
    </>
  );
}
