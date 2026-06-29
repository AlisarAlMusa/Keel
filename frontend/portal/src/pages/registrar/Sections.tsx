/**
 * Sections — read-only section list for the registrar.
 * The "Add Section" button is disabled.
 */

import { useCallback, useEffect, useState } from 'react';
import { Button, Spinner, Table, Toast } from '@keel/ui';
import { getSections, openSeat } from '../../api';
import type { Section } from '../../api';

export function Sections() {
  const [sections, setSections] = useState<Section[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' } | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getSections();
      setSections(data.sections);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sections');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function handleOpenSeat(sectionId: string) {
    setBusy(sectionId);
    try {
      const r = await openSeat(sectionId);
      setToast({
        message: `Freed a seat in ${r.course_code} (${r.enrolled}/${r.capacity}). The waitlist worker will fill it shortly.`,
        kind: 'success',
      });
      await load();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Could not open a seat', kind: 'error' });
      // Re-sync the list so a stale enrolled count (e.g. the worker just filled the seat)
      // can't keep the button enabled on a section that has nothing to free.
      await load();
    } finally {
      setBusy(null);
    }
  }

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

  const headers = ['Course', 'Section', 'Title', 'Term', 'Days / Time', 'Instructor', 'Enrolled / Cap', 'Seats'];

  const rows = sections.map((s) => {
    const full = (s.enrolled ?? 0) >= (s.capacity ?? 0);
    return [
      <span style={{ fontWeight: 600, fontFamily: 'monospace', fontSize: '0.9rem' }}>{s.course_code}</span>,
      <span style={{ fontFamily: 'monospace', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
        {String(s.section_num ?? 1).padStart(3, '0')}
      </span>,
      s.course_title,
      `${s.term.charAt(0).toUpperCase() + s.term.slice(1)} ${s.year ?? ''}`.trim(),
      s.days && s.start_time && s.end_time
        ? `${s.days} ${s.start_time}–${s.end_time}`
        : s.days ?? '—',
      <span style={{ color: 'var(--text-muted)' }}>{s.instructor ?? '—'}</span>,
      <span>
        <span style={{ fontWeight: 600, color: full ? '#c0392b' : 'var(--text)' }}>{s.enrolled ?? 0}</span>
        <span style={{ color: 'var(--text-muted)' }}> / {s.capacity ?? '—'}</span>
      </span>,
      <Button
        variant="secondary"
        onClick={() => void handleOpenSeat(s.id)}
        disabled={busy === s.id || (s.enrolled ?? 0) === 0}
        title="Free one seat (simulate a drop) so the waitlist worker can fill it"
      >
        {busy === s.id ? '…' : 'Open a seat'}
      </Button>,
    ];
  });

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
      {toast && <Toast message={toast.message} kind={toast.kind} onClose={() => setToast(null)} />}
    </div>
  );
}
