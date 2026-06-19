/**
 * Catalog — read-only course catalog for the registrar.
 * The "Add Course" button is disabled (managed in the SIS).
 */

import { useEffect, useState } from 'react';
import { Button, Spinner, Table } from '@keel/ui';
import { getCatalog } from '../../api';
import type { Course } from '../../api';

export function Catalog() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await getCatalog();
        setCourses(data.courses);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load catalog');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--text-muted)' }}>
        <Spinner size={18} />
        <span>Loading catalog…</span>
      </div>
    );
  }

  if (error) {
    return <div style={{ color: '#c0392b', fontSize: '0.875rem' }}>Error: {error}</div>;
  }

  const headers = ['Code', 'Title', 'Credits', 'Department', 'Description'];

  const rows = courses.map((c) => [
    <span style={{ fontWeight: 600, fontFamily: 'monospace', fontSize: '0.9rem' }}>{c.code}</span>,
    c.title,
    <span style={{ textAlign: 'center', display: 'block' }}>{c.credits}</span>,
    <span style={{ color: 'var(--text-muted)' }}>{c.department ?? '—'}</span>,
    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
      {c.description ? c.description.slice(0, 80) + (c.description.length > 80 ? '…' : '') : '—'}
    </span>,
  ]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <h2 className="page-heading" style={{ margin: 0 }}>Course Catalog</h2>
        <div title="Course management is handled in the SIS backend">
          <Button variant="secondary" disabled>
            Add Course
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
        <Table headers={headers} rows={rows} emptyMessage="No courses in catalog." />
      </div>
    </div>
  );
}
