/**
 * Sections — read-only section list for the registrar.
 * The "Add Section" button is disabled.
 */

import { useEffect, useState } from 'react';
import { Button, Spinner, Table } from '@keel/ui';
import { getSections } from '../../api';
import type { Section } from '../../api';

export function Sections() {
  const [sections, setSections] = useState<Section[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await getSections();
        setSections(data.sections);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load sections');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--text-muted)' }}>
        <Spinner size={18} />
        <span>Loading sections…</span>
      </div>
    );
  }

  if (error) {
    return <div style={{ color: '#c0392b', fontSize: '0.875rem' }}>Error: {error}</div>;
  }

  const headers = ['Course', 'Title', 'Term', 'Days / Time', 'Instructor', 'Enrolled / Cap'];

  const rows = sections.map((s) => [
    <span style={{ fontWeight: 600, fontFamily: 'monospace', fontSize: '0.9rem' }}>{s.course_code}</span>,
    s.course_title,
    s.term,
    s.start_time && s.end_time
      ? `${s.days ?? ''} ${s.start_time}–${s.end_time}`.trim()
      : (s.days ?? '—'),
    <span style={{ color: 'var(--text-muted)' }}>{s.instructor ?? '—'}</span>,
    <span>
      <span style={{ fontWeight: 600 }}>{s.enrolled ?? 0}</span>
      <span style={{ color: 'var(--text-muted)' }}> / {s.capacity ?? '—'}</span>
    </span>,
  ]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <h2 className="page-heading" style={{ margin: 0 }}>Sections</h2>
        <div title="Section management is handled in the SIS backend">
          <Button variant="secondary" disabled>
            Add Section
          </Button>
        </div>
      </div>

      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '6px',
          overflow: 'hidden',
        }}
      >
        <Table headers={headers} rows={rows} emptyMessage="No sections on file." />
      </div>
    </div>
  );
}
