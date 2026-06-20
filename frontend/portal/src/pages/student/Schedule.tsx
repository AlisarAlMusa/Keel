/**
 * My Schedule — the write-proof demo payoff page.
 *
 * Rows enrolled via Keel show a "via Keel" badge (source='keel').
 * Refreshes on mount and on ENROLLMENT_COMPLETE postMessage from the widget.
 */

import { useCallback, useEffect, useState } from 'react';
import { Badge, Spinner, Table } from '@keel/ui';
import { getSchedule } from '../../api';
import type { Enrollment } from '../../api';

interface ScheduleProps {
  refreshSignal: number; // increments when parent receives ENROLLMENT_COMPLETE
}

export function Schedule({ refreshSignal }: ScheduleProps) {
  const [enrollments, setEnrollments] = useState<Enrollment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getSchedule();
      setEnrollments(data.enrollments);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load schedule');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load, refreshSignal]);

  const headers = ['Course', 'Title', 'Credits', 'Section', 'Term', 'Time', 'Status', 'Source'];

  const rows = enrollments.map((e) => [
    <span style={{ fontWeight: 600, fontFamily: 'Inter, system-ui, sans-serif' }}>{e.course_code}</span>,
    e.course_title,
    <span style={{ textAlign: 'center', display: 'block' }}>{e.credits}</span>,
    <span style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>
      {String(e.section_num ?? 1).padStart(3, '0')}
    </span>,
    `${e.term.charAt(0).toUpperCase() + e.term.slice(1)} ${e.year ?? ''}`.trim(),
    e.days && e.start_time && e.end_time
      ? `${e.days} ${e.start_time}–${e.end_time}`
      : e.days ?? '—',
    <Badge
      variant={
        e.status === 'enrolled'
          ? 'status-approved'
          : e.status === 'waitlisted'
          ? 'status-pending'
          : e.status === 'dropped'
          ? 'status-rejected'
          : 'status-pending'
      }
      label={e.status.charAt(0).toUpperCase() + e.status.slice(1)}
    />,
    e.source === 'keel' ? (
      <Badge
        variant="via-keel"
        label="via Keel"
        title="This enrollment was initiated through the Keel advisor"
      />
    ) : (
      <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>SIS</span>
    ),
  ]);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--text-muted)' }}>
        <Spinner size={18} />
        <span>Loading schedule…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ color: '#c0392b', fontSize: '0.875rem' }}>
        Error: {error}
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <h2 className="page-heading" style={{ margin: 0 }}>My Schedule</h2>
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
        <Table
          headers={headers}
          rows={rows}
          emptyMessage="No current enrollments."
        />
      </div>

      {enrollments.some((e) => e.source === 'keel') && (
        <p
          style={{
            marginTop: '12px',
            fontSize: '0.78rem',
            color: 'var(--text-muted)',
            fontFamily: 'Inter, system-ui, sans-serif',
          }}
        >
          Rows marked{' '}
          <Badge variant="via-keel" label="via Keel" />{' '}
          were enrolled through the Keel AI advisor.
        </p>
      )}
    </div>
  );
}
