import React, { useEffect, useState } from 'react';
import { Card, EmptyState, Spinner, Table, Tabs, Toast } from '@keel/ui';
import { getCost } from '../api';
import type { AuthHeaders, CostPeriod, CostRow } from '../api';

interface Props {
  auth: AuthHeaders;
}

const PERIOD_TABS: { label: string; value: CostPeriod }[] = [
  { label: 'Today', value: 'day' },
  { label: 'Week', value: 'week' },
  { label: 'Month', value: 'month' },
];

function fmt(n: number, decimals = 2) {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function Cost({ auth }: Props) {
  const [tabIndex, setTabIndex] = useState(0);
  const [rows, setRows] = useState<CostRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const period = PERIOD_TABS[tabIndex].value;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const data = await getCost(auth, period);
        if (!cancelled) setRows(data.rows ?? []);
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Failed to load cost data');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [auth, period]);

  const totals: CostRow = rows.reduce(
    (acc, r) => ({
      kind: 'Total',
      model: '',
      total_tokens: acc.total_tokens + r.total_tokens,
      estimated_cost_usd: acc.estimated_cost_usd + r.estimated_cost_usd,
      events: acc.events + r.events,
    }),
    { kind: 'Total', model: '', total_tokens: 0, estimated_cost_usd: 0, events: 0 },
  );

  const tableRows = rows.map((r) => [
    r.kind,
    r.model,
    r.total_tokens.toLocaleString(),
    `$${fmt(r.estimated_cost_usd, 4)}`,
    r.events.toLocaleString(),
  ]);

  // Append bold total row
  const totalRow: React.ReactNode[] = [
    <strong key="kind">Total</strong>,
    '',
    <strong key="tokens">{totals.total_tokens.toLocaleString()}</strong>,
    <strong key="cost">${fmt(totals.estimated_cost_usd, 4)}</strong>,
    <strong key="events">{totals.events.toLocaleString()}</strong>,
  ];

  return (
    <div style={{ maxWidth: 820, margin: '0 auto' }}>
      <h1
        style={{
          fontFamily: "'Fraunces', Georgia, serif",
          fontSize: 'var(--text-2xl)',
          color: 'var(--text)',
          marginBottom: 'var(--sp-6)',
        }}
      >
        Usage &amp; Cost
      </h1>

      <Card style={{ padding: 0, overflow: 'hidden' }}>
        {/* Period tabs */}
        <div style={{ padding: '0 var(--sp-4)', borderBottom: '1px solid var(--border)' }}>
          <Tabs
            tabs={PERIOD_TABS.map((t) => t.label)}
            active={tabIndex}
            onChange={setTabIndex}
          />
        </div>

        <div style={{ padding: 'var(--sp-4)' }}>
          {loading ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: 160,
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
                Loading usage data…
              </span>
            </div>
          ) : rows.length === 0 ? (
            <EmptyState title="No usage data for this period." />
          ) : (
            <Table
              headers={['Kind', 'Model', 'Total Tokens', 'Estimated Cost (USD)', 'Events']}
              rows={[...tableRows, totalRow]}
            />
          )}
        </div>
      </Card>

      {error && (
        <Toast message={error} kind="error" onClose={() => setError(null)} />
      )}
    </div>
  );
}
