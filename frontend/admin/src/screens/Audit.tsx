import React, { useEffect, useState } from 'react';
import { Button, Card, EmptyState, Spinner, Toast } from '@keel/ui';
import { getAuditLog } from '../api';
import type { AuditEntry } from '../api';

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' });
  } catch { return iso; }
}

function ExpandableAfter({ after }: { after: unknown }) {
  const [expanded, setExpanded] = useState(false);
  if (after == null) return <span style={{ color: 'var(--text-muted)' }}>—</span>;

  const formatted = typeof after === 'string' ? after : JSON.stringify(after, null, 2);
  const isLong = formatted.length > 80 || formatted.includes('\n');

  if (!isLong) {
    return (
      <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
        {formatted}
      </span>
    );
  }

  return (
    <div>
      {expanded ? (
        <pre style={{
          fontFamily: 'ui-monospace, monospace',
          fontSize: 'var(--text-xs)',
          color: 'var(--text-muted)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          margin: '0 0 4px',
          maxWidth: 360,
          background: 'rgba(0,0,0,0.04)',
          borderRadius: 4,
          padding: '6px 8px',
        }}>
          {formatted}
        </pre>
      ) : (
        <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          {formatted.slice(0, 60)}…
        </span>
      )}
      <button
        onClick={() => setExpanded(e => !e)}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--mahogany)', fontSize: 'var(--text-xs)',
          fontFamily: "'Inter', system-ui, sans-serif",
          padding: '0 2px', textDecoration: 'underline',
        }}
      >
        {expanded ? 'collapse' : 'expand'}
      </button>
    </div>
  );
}

export function Audit() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [limit, setLimit] = useState(50);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(newLimit: number, isMore = false) {
    if (isMore) setLoadingMore(true);
    else setLoading(true);
    setError(null);
    try {
      const data = await getAuditLog(newLimit);
      setEntries(data.rows ?? []);
      setLimit(newLimit);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit log');
    } finally {
      if (isMore) setLoadingMore(false);
      else setLoading(false);
    }
  }

  useEffect(() => { load(50); }, []);

  const monoStyle: React.CSSProperties = { fontFamily: 'ui-monospace, Menlo, Monaco, Consolas, monospace', fontSize: 'var(--text-xs)' };

  const rows = entries.map((e) => [
    <div key="actor">
      <div style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>
        {e.actor_name ?? e.actor.slice(0, 8) + '…'}
      </div>
      <div style={{ ...monoStyle, color: 'var(--text-muted)', fontSize: '0.7rem' }} title={e.actor}>
        {e.actor.slice(0, 12)}…
      </div>
    </div>,
    <span key="action" style={{ fontSize: 'var(--text-xs)', fontFamily: "'Inter', system-ui, sans-serif" }}>
      {e.action}
    </span>,
    <ExpandableAfter key="after" after={e.after} />,
    <span key="time" style={{ ...monoStyle, whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>{formatTime(e.created_at)}</span>,
  ]);

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <h1 style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 'var(--text-2xl)', color: 'var(--text)', marginBottom: 'var(--sp-6)' }}>
        Audit Log
      </h1>

      <Card style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, gap: 'var(--sp-3)', color: 'var(--text-muted)' }}>
            <Spinner size={20} />
            <span style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: 'var(--text-sm)' }}>Loading audit log…</span>
          </div>
        ) : entries.length === 0 ? (
          <EmptyState title="No audit entries yet." />
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
            <thead>
              <tr>
                {['Actor', 'Action', 'After', 'Time'].map(h => (
                  <th key={h} style={{
                    textAlign: 'left', padding: '8px 14px',
                    color: 'var(--text-muted)', fontWeight: 600,
                    borderBottom: '1px solid var(--border)',
                    fontSize: 'var(--text-xs)', textTransform: 'uppercase',
                    fontFamily: "'Inter', system-ui, sans-serif",
                  }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                  {row.map((cell, j) => (
                    <td key={j} style={{ padding: '10px 14px', verticalAlign: 'top' }}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {!loading && entries.length > 0 && (
          <div style={{ padding: 'var(--sp-4)', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 'var(--sp-3)' }}>
            <Button variant="secondary" size="sm" loading={loadingMore} onClick={() => load(limit + 50, true)}>Load more</Button>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: "'Inter', system-ui, sans-serif" }}>Showing {entries.length} entries</span>
          </div>
        )}
      </Card>

      {error && <Toast message={error} kind="error" onClose={() => setError(null)} />}
    </div>
  );
}
