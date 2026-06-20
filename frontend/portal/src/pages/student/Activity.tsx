/**
 * Activity — reverse-chronological list of recent audit log events for this tenant.
 */

import { useEffect, useState } from 'react';
import { Spinner } from '@keel/ui';
import { getActivity } from '../../api';
import type { ActivityItem } from '../../api';

export function Activity() {
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await getActivity();
        setActivity(data.activity);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load activity');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--text-muted)' }}>
        <Spinner size={18} />
        <span>Loading activity…</span>
      </div>
    );
  }

  if (error) {
    return <div style={{ color: '#c0392b', fontSize: '0.875rem' }}>Error: {error}</div>;
  }

  return (
    <div>
      <h2 className="page-heading">Recent Activity</h2>
      {activity.length === 0 ? (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>No recent activity.</p>
      ) : (
        <div
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: '6px',
            overflow: 'hidden',
          }}
        >
          {activity.map((item, idx) => (
            <div
              key={item.id ?? idx}
              style={{
                padding: '12px 20px',
                borderBottom: idx < activity.length - 1 ? '1px solid var(--border)' : 'none',
                display: 'flex',
                alignItems: 'flex-start',
                justifyContent: 'space-between',
                gap: '12px',
              }}
            >
              <div>
                <div
                  style={{
                    fontFamily: 'Inter, system-ui, sans-serif',
                    fontSize: '0.875rem',
                    fontWeight: 500,
                    color: 'var(--text)',
                    marginBottom: '2px',
                  }}
                >
                  <span style={{ color: '#2c4a7c' }} title={item.actor}>
                    {item.actor_name ?? item.actor_email ?? item.actor.slice(0, 8) + '…'}
                  </span>
                  {' '}
                  <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                    {item.action.replace(/_/g, ' ').replace(/\./g, ' → ')}
                  </span>
                </div>
                {item.after && (
                  <div
                    style={{
                      fontSize: '0.775rem',
                      color: 'var(--text-muted)',
                      fontFamily: 'Inter, system-ui, sans-serif',
                    }}
                  >
                    {typeof item.after === 'object'
                      ? Object.entries(item.after)
                          .map(([k, v]) => `${k}: ${String(v)}`)
                          .join(' · ')
                      : String(item.after)}
                  </div>
                )}
              </div>
              <span
                style={{
                  fontSize: '0.775rem',
                  color: 'var(--text-muted)',
                  fontFamily: 'Inter, system-ui, sans-serif',
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                }}
              >
                {new Date(item.created_at).toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
