import { useEffect, useState } from 'react';
import { Card, EmptyState, Spinner, Table, Tabs, Toast } from '@keel/ui';
import { getPlatformCost } from '../api';
import type { PlatformCostRow } from '../api';

const PERIOD_TABS = [
  { label: 'Today', value: 'day' },
  { label: 'Week', value: 'week' },
  { label: 'Month', value: 'month' },
];

function fmt(n: number, decimals = 2) {
  return n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function PlatformCost() {
  const [tabIndex, setTabIndex] = useState(1);
  const [rows, setRows] = useState<PlatformCostRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const period = PERIOD_TABS[tabIndex].value;

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null);
    (async () => {
      try {
        const data = await getPlatformCost(period);
        if (!cancelled) setRows(data.rows ?? []);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load cost data');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [period]);

  const tableRows = rows.map(r => [
    <code key="tid" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{r.tenant_id.slice(0, 8)}…</code>,
    <span key="kind" style={{ background: '#e8eaf6', color: '#283593', padding: '2px 6px', borderRadius: 3, fontSize: '0.75rem', fontWeight: 600 }}>{r.kind}</span>,
    r.calls.toLocaleString(),
    r.tokens.toLocaleString(),
    `$${fmt(r.cost_usd, 6)}`,
  ]);

  return (
    <div style={{ maxWidth: 860, margin: '0 auto' }}>
      <h1 style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 'var(--text-2xl)', color: 'var(--text)', marginBottom: 'var(--sp-2)' }}>
        Usage Cost
      </h1>
      <div style={{ background: '#fff3e0', border: '1px solid #ffcc80', borderRadius: 6, padding: '10px 14px', fontSize: '0.82rem', color: '#e65100', marginBottom: 'var(--sp-4)', fontFamily: "'Inter', system-ui, sans-serif" }}>
        Usage metadata only — token counts and cost estimates. No conversation content is accessible here.
      </div>

      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '0 var(--sp-4)', borderBottom: '1px solid var(--border)' }}>
          <Tabs tabs={PERIOD_TABS.map(t => t.label)} active={tabIndex} onChange={setTabIndex} />
        </div>
        <div style={{ padding: 'var(--sp-4)' }}>
          {loading ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 160, gap: 'var(--sp-3)', color: 'var(--text-muted)' }}>
              <Spinner size={20} />
              <span style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: 'var(--text-sm)' }}>Loading usage data…</span>
            </div>
          ) : rows.length === 0 ? (
            <EmptyState title="No usage data for this period." />
          ) : (
            <Table headers={['Tenant ID', 'Kind', 'Calls', 'Tokens', 'Est. Cost (USD)']} rows={tableRows} />
          )}
        </div>
      </Card>

      {error && <Toast message={error} kind="error" onClose={() => setError(null)} />}
    </div>
  );
}
