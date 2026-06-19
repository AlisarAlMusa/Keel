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
import { getKeelToken } from '../api';

const KEEL_API_URL = (import.meta.env.VITE_KEEL_API_URL as string) || '';

interface KeelWidgetProps {
  onEnrollmentComplete: () => void;
}

export function KeelWidget({ onEnrollmentComplete }: KeelWidgetProps) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  // Fetch token and post to iframe
  const postToken = useCallback(async () => {
    try {
      const { token } = await getKeelToken();
      iframeRef.current?.contentWindow?.postMessage(
        { type: 'KEEL_TOKEN', token },
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
    setError(null);
    setOpen(true);
    setLoading(true);
  }

  function handleClose() {
    setOpen(false);
    setError(null);
  }

  const widgetSrc = KEEL_API_URL ? `${KEEL_API_URL}/widget/` : null;

  return (
    <>
      {/* Floating chat button */}
      <button
        onClick={handleOpen}
        aria-label="Open Keel advisor"
        style={{
          position: 'fixed',
          bottom: '28px',
          right: '28px',
          width: '56px',
          height: '56px',
          borderRadius: '50%',
          background: '#02122f',
          border: 'none',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: '0 4px 20px rgba(2,18,47,0.35)',
          zIndex: 900,
          transition: 'transform 0.15s ease, box-shadow 0.15s ease',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = 'scale(1.08)';
          e.currentTarget.style.boxShadow = '0 6px 28px rgba(2,18,47,0.45)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = 'scale(1)';
          e.currentTarget.style.boxShadow = '0 4px 20px rgba(2,18,47,0.35)';
        }}
      >
        {/* Chat icon */}
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M20 2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4l4 4 4-4h4a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2Z"
            fill="#F0ECDD"
          />
          <circle cx="8" cy="10" r="1.2" fill="#02122f" />
          <circle cx="12" cy="10" r="1.2" fill="#02122f" />
          <circle cx="16" cy="10" r="1.2" fill="#02122f" />
        </svg>
      </button>

      {/* Widget drawer / iframe overlay */}
      {open && (
        <div
          style={{
            position: 'fixed',
            bottom: '96px',
            right: '28px',
            width: '400px',
            height: '640px',
            maxWidth: 'calc(100vw - 40px)',
            maxHeight: 'calc(100vh - 120px)',
            background: '#02122f',
            borderRadius: '16px',
            boxShadow: '0 16px 56px rgba(2,18,47,0.55)',
            zIndex: 950,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
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
                  color: '#02122f',
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
