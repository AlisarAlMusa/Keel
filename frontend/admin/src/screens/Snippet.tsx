import { useEffect, useState } from 'react';
import { Button, Card, Spinner, Toast } from '@keel/ui';
import { getWidgetSnippet } from '../api';
import type { AuthHeaders } from '../api';

interface Props {
  auth: AuthHeaders;
}

export function Snippet({ auth }: Props) {
  const [snippet, setSnippet] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getWidgetSnippet(auth);
        if (!cancelled) setSnippet(data.snippet);
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Failed to load snippet');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [auth]);

  async function handleCopy() {
    if (!snippet) return;
    await navigator.clipboard.writeText(snippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      <h1
        style={{
          fontFamily: "'Fraunces', Georgia, serif",
          fontSize: 'var(--text-2xl)',
          color: 'var(--text)',
          marginBottom: 'var(--sp-2)',
        }}
      >
        Embed Snippet
      </h1>
      <p
        style={{
          color: 'var(--text-muted)',
          fontSize: 'var(--text-sm)',
          marginBottom: 'var(--sp-6)',
        }}
      >
        Paste this into your SIS portal's HTML to embed the Keel widget.
      </p>

      <Card>
        {loading ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: 120,
              gap: 'var(--sp-3)',
              color: 'var(--text-muted)',
            }}
          >
            <Spinner size={20} />
            <span style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: 'var(--text-sm)' }}>
              Loading snippet…
            </span>
          </div>
        ) : error ? (
          <div
            style={{
              color: '#c0392b',
              fontSize: 'var(--text-sm)',
              fontFamily: "'Inter', system-ui, sans-serif",
              padding: 'var(--sp-4)',
            }}
          >
            {error}
          </div>
        ) : (
          <>
            {/* Code block */}
            <div
              style={{
                background: 'var(--oxford)',
                color: 'var(--moonlight)',
                borderRadius: 'var(--radius-md)',
                padding: 'var(--sp-4)',
                fontFamily: 'ui-monospace, Menlo, Monaco, Consolas, monospace',
                fontSize: 'var(--text-sm)',
                lineHeight: 1.6,
                overflowX: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                marginBottom: 'var(--sp-4)',
              }}
            >
              {snippet}
            </div>

            {/* Copy button */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)' }}>
              <Button variant={copied ? 'secondary' : 'primary'} onClick={handleCopy}>
                {copied ? '✓ Copied' : 'Copy'}
              </Button>
              <p
                style={{
                  fontSize: 'var(--text-xs)',
                  color: 'var(--text-muted)',
                  fontFamily: "'Inter', system-ui, sans-serif",
                }}
              >
                The widget is lazy — no token is fetched until a student opens the chat.
              </p>
            </div>
          </>
        )}
      </Card>

      {error && (
        <Toast message={error} kind="error" onClose={() => setError(null)} />
      )}
    </div>
  );
}
