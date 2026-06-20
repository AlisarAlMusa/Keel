import { useEffect, useRef, useState } from 'react';
import { Spinner } from '@keel/ui';
import { ChatWidget } from './ChatWidget';
import './index.css';

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [personaName, setPersonaName] = useState<string>('Keel Advisor');
  const listenerAttached = useRef(false);

  useEffect(() => {
    if (listenerAttached.current) return;
    listenerAttached.current = true;

    function handleMessage(e: MessageEvent) {
      if (e.data?.type === 'KEEL_TOKEN' && typeof e.data.token === 'string') {
        setToken(e.data.token);
        if (typeof e.data.personaName === 'string' && e.data.personaName) {
          setPersonaName(e.data.personaName);
        }
      }
    }

    window.addEventListener('message', handleMessage);

    // Ask the parent for the token on mount
    window.parent.postMessage({ type: 'KEEL_TOKEN_REFRESH' }, '*');

    return () => {
      window.removeEventListener('message', handleMessage);
    };
  }, []);

  return (
    // keel-dark skin applied here — all child components inherit the CSS variables
    <div
      className="keel-dark"
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--oxford)',
        color: 'var(--moonlight)',
        fontFamily: 'Inter, "IBM Plex Sans", system-ui, sans-serif',
      }}
    >
      {token === null ? (
        // Loading state — token not yet received from parent
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 'var(--sp-3)',
            color: 'var(--text-muted)',
          }}
        >
          <Spinner size={28} />
          <span
            style={{
              fontSize: 'var(--text-sm)',
              fontFamily: 'Inter, system-ui, sans-serif',
            }}
          >
            Connecting…
          </span>
        </div>
      ) : (
        <ChatWidget token={token} personaName={personaName} />
      )}
    </div>
  );
}
