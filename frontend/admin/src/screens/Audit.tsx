import React, { useEffect, useState } from 'react';
import { Button, Card, EmptyState, Spinner, Table, Toast } from '@keel/ui';
import { getAuditLog } from '../api';
import type { AuditEntry, AuthHeaders } from '../api';

interface Props {
  auth: AuthHeaders;
}

function formatAfter(after: unknown): string {
  if (after == null) return '—';
  if (typeof after === 'string') return after;
  try {
    return JSON.stringify(after);
  } catch {
    return String(after);
  }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: 'short',
      timeStyle: 'medium',
    });
  } catch {
    return iso;
  }
}

export function Audit({ auth }: Props) {
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
      const data = await getAuditLog(auth, newLimit);
      setEntries(data.entries ?? []);
      setLimit(newLimit);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit log');
    } finally {
      if (isMore) setLoadingMore(false);
      else setLoading(false);
    }
  }

  useEffect(() => {
    load(50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth]);

  const monoStyle: React.CSSProperties = {
    fontFamily: 'ui-monospace, Menlo, Monaco, Consolas, monospace',
    fontSize: 'var(--text-xs)',
  };

  const rows = entries.map((e) => [
    <span key="actor" style={monoStyle}>{e.actor}</span>,
    e.action,
    <span
      key="after"
      style={{
        ...monoStyle,
        maxWidth: 320,
        display: 'inline-block',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
      title={formatAfter(e.after)}
    >
      {formatAfter(e.after)}
    </span>,
    <span key="time" style={{ ...monoStyle, whiteSpace: 'nowrap' }}>
      {formatTime(e.time)}
    </span>,
  ]);

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <h1
        style={{
          fontFamily: "'Fraunces', Georgia, serif",
          fontSize: 'var(--text-2xl)',
          color: 'var(--text)',
          marginBottom: 'var(--sp-6)',
        }}
      >
        Audit Log
      </h1>

      <Card style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: 200,
              gap: 'var(--sp-3)',
              color: 'var(--text-muted)',
            }}
          >
            <Spinner size={20} />
            <span
              style={{
                fontFamily: "'Inter', system-ui, sans-serif",
                fontSize: 'var(--text-sm)',
              }}
            >
              Loading audit log…
            </span>
          </div>
        ) : entries.length === 0 ? (
          <EmptyState title="No audit entries yet." />
        ) : (
          <Table
            headers={['Actor', 'Action', 'After', 'Time']}
            rows={rows}
            emptyMessage="No audit entries yet."
          />
        )}

        {!loading && entries.length > 0 && (
          <div
            style={{
              padding: 'var(--sp-4)',
              borderTop: '1px solid var(--border)',
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--sp-3)',
            }}
          >
            <Button
              variant="secondary"
              size="sm"
              loading={loadingMore}
              onClick={() => load(limit + 50, true)}
            >
              Load more
            </Button>
            <span
              style={{
                fontSize: 'var(--text-xs)',
                color: 'var(--text-muted)',
                fontFamily: "'Inter', system-ui, sans-serif",
              }}
            >
              Showing {entries.length} entries
            </span>
          </div>
        )}
      </Card>

      {error && (
        <Toast message={error} kind="error" onClose={() => setError(null)} />
      )}
    </div>
  );
}
