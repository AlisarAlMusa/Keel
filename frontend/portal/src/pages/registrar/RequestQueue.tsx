/**
 * RequestQueue — the one functional registrar action.
 * Approve or reject pending institutional requests.
 */

import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Modal, Spinner, Table, Toast } from '@keel/ui';
import { getRegistrarRequests, postDecision } from '../../api';
import type { RequestItem } from '../../api';

export function RequestQueue() {
  const [requests, setRequests] = useState<RequestItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' | 'info' } | null>(null);

  // Decision modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [pendingDecision, setPendingDecision] = useState<{
    id: string;
    decision: 'approve' | 'reject';
    studentId: string;
    type: string;
  } | null>(null);
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getRegistrarRequests('pending');
      setRequests(data.requests);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load request queue');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function openDecisionModal(req: RequestItem, decision: 'approve' | 'reject') {
    setPendingDecision({ id: req.id, decision, studentId: req.student_id, type: req.type });
    setNote('');
    setModalOpen(true);
  }

  async function submitDecision() {
    if (!pendingDecision) return;
    setSubmitting(true);
    try {
      await postDecision(pendingDecision.id, pendingDecision.decision, note);
      setModalOpen(false);
      setToast({
        message: `Request ${pendingDecision.decision === 'approve' ? 'approved' : 'rejected'} successfully.`,
        kind: 'success',
      });
      // Refresh queue
      await load();
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Failed to process decision',
        kind: 'error',
      });
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--text-muted)' }}>
        <Spinner size={18} />
        <span>Loading request queue…</span>
      </div>
    );
  }

  if (error) {
    return <div style={{ color: '#c0392b', fontSize: '0.875rem' }}>Error: {error}</div>;
  }

  const headers = ['Student ID', 'Type', 'Details', 'Submitted', 'Actions'];

  const rows = requests.map((r) => {
    const details =
      r.payload && typeof r.payload === 'object'
        ? Object.entries(r.payload)
            .slice(0, 3)
            .map(([k, v]) => `${k}: ${v}`)
            .join(', ')
        : '—';

    return [
      <span style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>{r.student_id}</span>,
      <span style={{ fontWeight: 500, textTransform: 'capitalize' }}>{r.type.replace(/_/g, ' ')}</span>,
      <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', maxWidth: '200px', display: 'block' }}>
        {details}
      </span>,
      <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
        {new Date(r.created_at).toLocaleDateString()}
      </span>,
      <div style={{ display: 'flex', gap: '6px' }}>
        <Button
          variant="primary"
          size="sm"
          onClick={() => openDecisionModal(r, 'approve')}
          style={{ background: '#2d7a5a', color: '#fff', fontSize: '0.78rem', padding: '3px 10px' }}
        >
          Approve
        </Button>
        <Button
          variant="danger"
          size="sm"
          onClick={() => openDecisionModal(r, 'reject')}
          style={{ fontSize: '0.78rem', padding: '3px 10px' }}
        >
          Reject
        </Button>
      </div>,
    ];
  });

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <h2 className="page-heading" style={{ margin: 0 }}>Request Queue</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Badge variant="status-pending" label={`${requests.length} pending`} />
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
      </div>

      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '6px',
          overflow: 'hidden',
        }}
      >
        <Table headers={headers} rows={rows} emptyMessage="No pending requests." />
      </div>

      {/* Decision modal */}
      <Modal
        open={modalOpen}
        title={pendingDecision?.decision === 'approve' ? 'Approve Request' : 'Reject Request'}
        onClose={() => setModalOpen(false)}
      >
        <div className="sis-light">
          {pendingDecision && (
            <p style={{
              fontSize: '0.875rem',
              color: 'var(--text-muted)',
              fontFamily: 'Inter, system-ui, sans-serif',
              marginBottom: '16px',
            }}>
              {pendingDecision.decision === 'approve' ? 'Approving' : 'Rejecting'}{' '}
              <strong>{pendingDecision.type.replace(/_/g, ' ')}</strong>{' '}
              request from student{' '}
              <code style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>{pendingDecision.studentId}</code>.
            </p>
          )}
          <div style={{ marginBottom: '16px' }}>
            <label
              style={{
                display: 'block',
                fontSize: '0.875rem',
                fontWeight: 600,
                color: 'var(--text-muted)',
                fontFamily: 'Inter, system-ui, sans-serif',
                marginBottom: '6px',
              }}
            >
              Note (optional)
            </label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Add a note for the student…"
              rows={3}
              style={{
                width: '100%',
                border: '1px solid #bbc5d4',
                borderRadius: '4px',
                padding: '8px 12px',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: '0.875rem',
                resize: 'vertical',
                outline: 'none',
              }}
            />
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
            <Button variant="secondary" onClick={() => setModalOpen(false)} disabled={submitting}>
              Cancel
            </Button>
            <Button
              variant={pendingDecision?.decision === 'approve' ? 'primary' : 'danger'}
              onClick={submitDecision}
              loading={submitting}
              style={
                pendingDecision?.decision === 'approve'
                  ? { background: '#2d7a5a', color: '#fff' }
                  : {}
              }
            >
              {pendingDecision?.decision === 'approve' ? 'Confirm Approval' : 'Confirm Rejection'}
            </Button>
          </div>
        </div>
      </Modal>

      {toast && (
        <Toast message={toast.message} kind={toast.kind} onClose={() => setToast(null)} />
      )}
    </div>
  );
}
