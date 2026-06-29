/**
 * Requests — shows the student's institutional request history
 * (graduation applications, petitions, major-change requests).
 */

import { useCallback, useEffect, useState } from 'react';
import { Badge, Spinner, Table } from '@keel/ui';
import { getRequests } from '../../api';
import type { RequestItem } from '../../api';

export function Requests() {
  const [requests, setRequests] = useState<RequestItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getRequests();
      setRequests(data.requests);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load requests');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--text-muted)' }}>
        <Spinner size={18} />
        <span>Loading requests…</span>
      </div>
    );
  }

  if (error) {
    return <div style={{ color: '#c0392b', fontSize: '0.875rem' }}>Error: {error}</div>;
  }

  const headers = ['Type', 'Status', 'Details', 'Resolved', 'Submitted'];

  const rows = requests.map((r) => {
    const statusVariant =
      r.status === 'approved'
        ? 'status-approved'
        : r.status === 'rejected'
        ? 'status-rejected'
        : 'status-pending';

    const details =
      r.payload && typeof r.payload === 'object'
        ? Object.entries(r.payload)
            .map(([k, v]) => `${k}: ${v}`)
            .join(', ')
        : '—';

    return [
      <span style={{ fontWeight: 500, textTransform: 'capitalize' }}>{r.type.replace(/_/g, ' ')}</span>,
      <Badge variant={statusVariant} label={r.status.charAt(0).toUpperCase() + r.status.slice(1)} />,
      <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{details}</span>,
      r.resolved_at
        ? <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{new Date(r.resolved_at).toLocaleDateString()}</span>
        : <span style={{ color: 'var(--text-muted)' }}>—</span>,
      <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
        {new Date(r.created_at).toLocaleDateString()}
      </span>,
    ];
  });

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <h2 className="page-heading" style={{ margin: 0 }}>My Requests</h2>
        <button
          onClick={load}
          style={{
            background: 'transparent',
            border: '1px solid var(--border)',
            borderRadius: '4px',
            padding: '5px 12px',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            fontFamily: 'Inter, system-ui, sans-serif',
            fontSize: '0.8rem',
          }}
        >
          Refresh
        </button>
      </div>
      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '6px',
          overflow: 'hidden',
        }}
      >
        <Table headers={headers} rows={rows} emptyMessage="No institutional requests on file." />
      </div>
    </div>
  );
}
