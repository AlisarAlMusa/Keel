import { useEffect, useState } from 'react';
import { Button, Card, Field, Input, Spinner, Toast } from '@keel/ui';
import { listTenants, provision, suspendTenant, unsuspendTenant, eraseTenant } from '../api';
import type { TenantRow } from '../api';

type ToastState = { message: string; kind: 'success' | 'error' } | null;

const S = {
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '0.85rem' },
  th: { textAlign: 'left' as const, padding: '8px 12px', color: 'var(--text-muted)', fontWeight: 600, borderBottom: '1px solid var(--border)', fontSize: '0.75rem', textTransform: 'uppercase' as const, fontFamily: "'Inter', system-ui, sans-serif" },
  td: { padding: '10px 12px', borderBottom: '1px solid var(--border)', verticalAlign: 'middle' as const, fontFamily: "'Inter', system-ui, sans-serif", fontSize: '0.85rem' },
  badge: (s: string): React.CSSProperties => ({
    display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: '0.75rem', fontWeight: 600,
    background: s === 'active' ? '#e8f5e9' : s === 'suspended' ? '#fff3e0' : '#fce4ec',
    color: s === 'active' ? '#2e7d32' : s === 'suspended' ? '#e65100' : '#c62828',
  }),
};

export function Tenants() {
  const [tenants, setTenants] = useState<TenantRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);

  // Provision form
  const [showProvision, setShowProvision] = useState(false);
  const [pName, setPName] = useState('');
  const [pEmail, setPEmail] = useState('');
  const [provisioning, setProvisioning] = useState(false);
  const [provisionMsg, setProvisionMsg] = useState<string | null>(null);

  // Erase modal
  const [eraseTarget, setEraseTarget] = useState<TenantRow | null>(null);
  const [eraseConfirm, setEraseConfirm] = useState('');
  const [erasing, setErasing] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const d = await listTenants();
      setTenants(d.tenants);
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : 'Failed to load', kind: 'error' });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function doSuspend(t: TenantRow) {
    setBusy(t.id);
    try { await suspendTenant(t.id); await load(); }
    catch (e) { setToast({ message: e instanceof Error ? e.message : 'Failed', kind: 'error' }); }
    finally { setBusy(null); }
  }

  async function doUnsuspend(t: TenantRow) {
    setBusy(t.id);
    try { await unsuspendTenant(t.id); await load(); }
    catch (e) { setToast({ message: e instanceof Error ? e.message : 'Failed', kind: 'error' }); }
    finally { setBusy(null); }
  }

  async function doProvision(e: React.FormEvent) {
    e.preventDefault();
    setProvisioning(true); setProvisionMsg(null);
    try {
      const d = await provision(pName, pEmail);
      setProvisionMsg(`Provisioned. Bootstrap admin: ${d.admin_email}`);
      setPName(''); setPEmail(''); setShowProvision(false);
      await load();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed', kind: 'error' });
    } finally {
      setProvisioning(false);
    }
  }

  async function doErase() {
    if (!eraseTarget) return;
    setErasing(true);
    try {
      await eraseTenant(eraseTarget.id, eraseConfirm);
      setEraseTarget(null); setEraseConfirm('');
      await load();
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : 'Erase failed', kind: 'error' });
    } finally {
      setErasing(false);
    }
  }

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }}>
      <h1 style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 'var(--text-2xl)', color: 'var(--text)', marginBottom: 'var(--sp-2)' }}>
        Tenants
      </h1>
      <p style={{ color: 'var(--text-muted)', fontSize: 'var(--text-sm)', marginBottom: 'var(--sp-6)', fontFamily: "'Inter', system-ui, sans-serif" }}>
        Provision, suspend, unsuspend, or erase tenant subscriptions. This console has no access to tenant conversations or data.
      </p>

      {provisionMsg && (
        <div style={{ color: '#2e7d32', background: '#e8f5e9', padding: '8px 12px', borderRadius: 4, fontSize: '0.85rem', marginBottom: 12, fontFamily: "'Inter', system-ui, sans-serif" }}>
          {provisionMsg}
        </div>
      )}

      <div style={{ display: 'flex', gap: 'var(--sp-3)', marginBottom: 'var(--sp-4)' }}>
        <Button onClick={() => setShowProvision(v => !v)}>+ Provision Tenant</Button>
        <Button variant="secondary" onClick={load}>Refresh</Button>
      </div>

      {showProvision && (
        <Card style={{ marginBottom: 'var(--sp-4)', background: 'var(--bg)' }}>
          <div style={{ fontWeight: 700, marginBottom: 12, fontFamily: "'Inter', system-ui, sans-serif" }}>Provision New Tenant</div>
          <form onSubmit={doProvision}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
              <Field label="Tenant Name">
                <Input value={pName} onChange={e => setPName(e.target.value)} placeholder="e.g. Valley University" required />
              </Field>
              <Field label="Bootstrap Admin Email">
                <Input type="email" value={pEmail} onChange={e => setPEmail(e.target.value)} placeholder="admin@university.edu" required />
              </Field>
            </div>
            <div style={{ display: 'flex', gap: 'var(--sp-3)' }}>
              <Button type="submit" loading={provisioning}>{provisioning ? 'Provisioning…' : 'Provision'}</Button>
              <Button type="button" variant="secondary" onClick={() => setShowProvision(false)}>Cancel</Button>
            </div>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 8, fontFamily: "'Inter', system-ui, sans-serif" }}>
              Creates a tenant shell + bootstrap admin only. No catalog or students.
            </p>
          </form>
        </Card>
      )}

      <Card style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 160, gap: 'var(--sp-3)', color: 'var(--text-muted)' }}>
            <Spinner size={20} />
            <span style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: 'var(--text-sm)' }}>Loading…</span>
          </div>
        ) : (
          <table style={S.table}>
            <thead>
              <tr>{['Name', 'Slug', 'Status', 'Students', 'Admins', 'Created', 'Actions'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {tenants.length === 0 && (
                <tr><td colSpan={7} style={{ ...S.td, textAlign: 'center', color: 'var(--text-muted)', padding: 24 }}>No tenants yet.</td></tr>
              )}
              {tenants.map(t => (
                <tr key={t.id}>
                  <td style={S.td}><strong>{t.name}</strong></td>
                  <td style={S.td}><code style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{t.slug}</code></td>
                  <td style={S.td}><span style={S.badge(t.status)}>{t.status}</span></td>
                  <td style={S.td}>{t.student_count}</td>
                  <td style={S.td}>{t.admin_count}</td>
                  <td style={S.td}>{new Date(t.created_at).toLocaleDateString()}</td>
                  <td style={S.td}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      {t.status === 'active'
                        ? <Button size="sm" variant="secondary" onClick={() => doSuspend(t)} disabled={busy === t.id}>Suspend</Button>
                        : <Button size="sm" onClick={() => doUnsuspend(t)} disabled={busy === t.id}>Unsuspend</Button>
                      }
                      <button
                        onClick={() => { setEraseTarget(t); setEraseConfirm(''); }}
                        style={{ padding: '4px 10px', borderRadius: 4, border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: '0.78rem', background: '#fce4ec', color: '#c62828' }}
                      >
                        Erase
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Erase confirmation modal */}
      {eraseTarget && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
          <Card style={{ width: 460, maxWidth: '90vw' }}>
            <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#c62828', marginBottom: 8, fontFamily: "'Inter', system-ui, sans-serif" }}>⚠ Erase Tenant</div>
            <p style={{ fontSize: '0.875rem', color: 'var(--text)', marginBottom: 16, fontFamily: "'Inter', system-ui, sans-serif" }}>
              This will permanently delete <strong>{eraseTarget.name}</strong> and all associated data. Irreversible.
            </p>
            <p style={{ fontSize: '0.875rem', marginBottom: 8, fontFamily: "'Inter', system-ui, sans-serif" }}>
              Type <code style={{ background: 'var(--bg)', padding: '1px 5px', borderRadius: 3 }}>{eraseTarget.name}</code> to confirm:
            </p>
            <Field label="">
              <Input value={eraseConfirm} onChange={e => setEraseConfirm(e.target.value)} placeholder={eraseTarget.name} />
            </Field>
            <div style={{ display: 'flex', gap: 'var(--sp-3)', marginTop: 'var(--sp-4)' }}>
              <button
                onClick={doErase}
                disabled={eraseConfirm !== eraseTarget.name || erasing}
                style={{ padding: '8px 16px', borderRadius: 4, border: 'none', cursor: eraseConfirm !== eraseTarget.name || erasing ? 'not-allowed' : 'pointer', fontWeight: 700, fontSize: '0.85rem', background: '#c62828', color: '#fff', opacity: eraseConfirm !== eraseTarget.name ? 0.5 : 1 }}
              >
                {erasing ? 'Erasing…' : 'Permanently Erase'}
              </button>
              <Button variant="secondary" onClick={() => setEraseTarget(null)}>Cancel</Button>
            </div>
          </Card>
        </div>
      )}

      {toast && <Toast message={toast.message} kind={toast.kind} onClose={() => setToast(null)} />}
    </div>
  );
}
