import { useEffect, useState } from 'react';
import { Button, Card, EmptyState, Spinner, Table, Toast } from '@keel/ui';
import { getPlatformAudit } from '../api';
import type { PlatformAuditRow } from '../api';

const ACTION_COLOR: Record<string, string> = {
  provision: '#2e7d32',
  suspend: '#e65100',
  unsuspend: '#1565c0',
  erase: '#c62828',
};

function formatTime(iso: string) {
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' }); }
  catch { return iso; }
}

export function PlatformAudit() {
  const [rows, setRows] = useState<PlatformAuditRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true); setError(null);
    try {
      const d = await getPlatformAudit(100);
      setRows(d.rows ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const monoStyle = { fontFamily: 'ui-monospace, Menlo, Monaco, Consolas, monospace', fontSize: '0.78rem' };

  const tableRows = rows.map(r => [
    <code key="id" style={monoStyle}>{r.id}</code>,
    <span key="action" style={{ color: ACTION_COLOR[r.action] ?? 'var(--text)', fontWeight: 700, fontSize: '0.8rem', textTransform: 'uppercase' as const }}>{r.action}</span>,
    <div key="tid">
      <div style={{ fontWeight: 600, fontSize: '0.8rem' }}>{r.target_tenant_name ?? '—'}</div>
      {r.target_tenant_id && (
        <code style={{ ...monoStyle, color: 'var(--text-muted)', fontSize: '0.7rem' }}>
          {r.target_tenant_id.slice(0, 8)}…
        </code>
      )}
    </div>,
    <span key="detail" style={{ ...monoStyle, color: 'var(--text-muted)' }}>{r.detail ? JSON.stringify(r.detail).slice(0, 80) : '—'}</span>,
    <span key="time" style={{ ...monoStyle, whiteSpace: 'nowrap' as const }}>{formatTime(r.created_at)}</span>,
  ]);

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <h1 style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 'var(--text-2xl)', color: 'var(--text)', marginBottom: 'var(--sp-2)' }}>
        Platform Audit Log
      </h1>
      <p style={{ color: 'var(--text-muted)', fontSize: 'var(--text-sm)', marginBottom: 'var(--sp-4)', fontFamily: "'Inter', system-ui, sans-serif" }}>
        Every operator action — provision, suspend, unsuspend, erase — recorded here. This log survives tenant erase.
      </p>

      <div style={{ marginBottom: 'var(--sp-4)' }}>
        <Button variant="secondary" onClick={load}>Refresh</Button>
      </div>

      <Card style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, gap: 'var(--sp-3)', color: 'var(--text-muted)' }}>
            <Spinner size={20} />
            <span style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: 'var(--text-sm)' }}>Loading…</span>
          </div>
        ) : rows.length === 0 ? (
          <EmptyState title="No audit events yet." />
        ) : (
          <Table headers={['ID', 'Action', 'Target Tenant', 'Detail', 'Time']} rows={tableRows} />
        )}
      </Card>

      {error && <Toast message={error} kind="error" onClose={() => setError(null)} />}
    </div>
  );
}
