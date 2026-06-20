/**
 * Keel Platform Console — operator-only surface (spec §S5, plan §P4).
 *
 * Pages:
 *   Login   — email + password → operator JWT (in memory, role-gated)
 *   Tenants — list, suspend/unsuspend, provision form, erase modal
 *   Cost    — per-tenant aggregate table + period selector (no content)
 *   Audit   — platform_audit action log
 *
 * A tenant_admin token is rejected at the API level (require_role gate).
 * The console has no view of conversations, plans, or tenant data — by design.
 */

import { useState, useEffect } from 'react';
import * as api from './api';

// ── Styles ────────────────────────────────────────────────────────────────────

const S = {
  shell: { display: 'flex', minHeight: '100vh', background: '#f4f6f9' } as React.CSSProperties,
  sidebar: {
    width: 220, background: '#000435', color: '#f0ecdd',
    display: 'flex', flexDirection: 'column' as const, padding: '24px 0',
  } as React.CSSProperties,
  logoArea: { padding: '0 20px 24px', borderBottom: '1px solid rgba(240,236,221,0.1)' },
  logoTitle: { fontFamily: 'Georgia, serif', fontSize: '1.1rem', fontWeight: 700, color: '#f0ecdd' },
  logoSub: { fontSize: '0.7rem', color: 'rgba(240,236,221,0.5)', marginTop: 2 },
  navBtn: (active: boolean): React.CSSProperties => ({
    display: 'block', width: '100%', textAlign: 'left', padding: '9px 20px',
    background: active ? 'rgba(240,236,221,0.12)' : 'transparent',
    border: 'none', color: active ? '#f0ecdd' : 'rgba(240,236,221,0.6)',
    fontSize: '0.85rem', fontWeight: active ? 600 : 400, cursor: 'pointer',
    borderLeft: active ? '3px solid #6b93e0' : '3px solid transparent',
    transition: 'all 0.1s',
  }),
  main: { flex: 1, padding: '32px 36px', overflow: 'auto' } as React.CSSProperties,
  pageTitle: { fontSize: '1.4rem', fontWeight: 700, marginBottom: 4 } as React.CSSProperties,
  pageSub: { fontSize: '0.85rem', color: '#5a6880', marginBottom: 24 } as React.CSSProperties,
  card: { background: '#fff', border: '1px solid #dde2eb', borderRadius: 8, padding: 20, marginBottom: 20 } as React.CSSProperties,
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '0.85rem' },
  th: { textAlign: 'left' as const, padding: '8px 10px', color: '#5a6880', fontWeight: 600, borderBottom: '1px solid #dde2eb', fontSize: '0.75rem', textTransform: 'uppercase' as const },
  td: { padding: '10px 10px', borderBottom: '1px solid #f0f2f6', verticalAlign: 'middle' as const },
  badge: (s: string): React.CSSProperties => ({
    display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: '0.75rem', fontWeight: 600,
    background: s === 'active' ? '#e8f5e9' : s === 'suspended' ? '#fff3e0' : '#fce4ec',
    color: s === 'active' ? '#2e7d32' : s === 'suspended' ? '#e65100' : '#c62828',
  }),
  btn: (variant: 'primary' | 'danger' | 'ghost'): React.CSSProperties => ({
    padding: '6px 12px', borderRadius: 4, border: 'none', cursor: 'pointer', fontWeight: 600,
    fontSize: '0.8rem', transition: 'opacity 0.1s',
    background: variant === 'primary' ? '#4B2E0A' : variant === 'danger' ? '#c62828' : '#f0f2f6',
    color: variant === 'primary' ? '#f0ecdd' : variant === 'danger' ? '#fff' : '#1a2436',
  }),
  input: { padding: '8px 10px', border: '1px solid #dde2eb', borderRadius: 4, width: '100%', fontSize: '0.875rem', boxSizing: 'border-box' as const },
  row: { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' as const } as React.CSSProperties,
};

// ── Shared helpers ────────────────────────────────────────────────────────────

function ErrorBox({ msg }: { msg: string }) {
  return <div style={{ color: '#c62828', background: '#fce4ec', padding: '8px 12px', borderRadius: 4, fontSize: '0.85rem', marginBottom: 12 }}>{msg}</div>;
}

function Spinner() {
  return <span style={{ display: 'inline-block', width: 16, height: 16, border: '2px solid #dde2eb', borderTopColor: '#4B2E0A', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />;
}

// ── Login Page ────────────────────────────────────────────────────────────────

function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true); setError(null);
    try {
      const data = await api.login(email, password);
      if (data.role !== 'platform_operator') {
        setError(`Access denied — role '${data.role}' is not permitted here. This console is for platform operators only.`);
        return;
      }
      api.setToken(data.token);
      onLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ minHeight: '100vh', background: '#000435', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      <div style={{ background: '#fff', borderRadius: 10, padding: '40px 48px', width: 400, maxWidth: '90vw' }}>
        <div style={{ marginBottom: 28, textAlign: 'center' }}>
          <div style={{ width: 48, height: 48, background: '#000435', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 12px', color: '#f0ecdd', fontFamily: 'Georgia, serif', fontSize: 20, fontWeight: 700 }}>K</div>
          <div style={{ fontFamily: 'Georgia, serif', fontSize: '1.2rem', fontWeight: 700 }}>Keel Platform Console</div>
          <div style={{ fontSize: '0.8rem', color: '#5a6880', marginTop: 3 }}>Operator access only</div>
        </div>
        {error && <ErrorBox msg={error} />}
        <form onSubmit={submit}>
          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: '#5a6880', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Email</label>
            <input style={S.input} type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" required disabled={loading} />
          </div>
          <div style={{ marginBottom: 20 }}>
            <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: '#5a6880', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Password</label>
            <input style={S.input} type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required disabled={loading} />
          </div>
          <button type="submit" style={{ ...S.btn('primary'), width: '100%', padding: '10px 14px' }} disabled={loading}>
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ── Tenants Page ──────────────────────────────────────────────────────────────

function TenantsPage() {
  const [tenants, setTenants] = useState<api.TenantRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // tenant id being acted on

  // Provision form
  const [showProvision, setShowProvision] = useState(false);
  const [pName, setPName] = useState('');
  const [pEmail, setPEmail] = useState('');
  const [provisioning, setProvisioning] = useState(false);
  const [provisionMsg, setProvisionMsg] = useState<string | null>(null);

  // Erase modal
  const [eraseTarget, setEraseTarget] = useState<api.TenantRow | null>(null);
  const [eraseConfirm, setEraseConfirm] = useState('');
  const [erasing, setErasing] = useState(false);

  async function load() {
    setLoading(true); setError(null);
    try { const d = await api.listTenants(); setTenants(d.tenants); }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed to load'); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  async function doSuspend(t: api.TenantRow) {
    setBusy(t.id);
    try { await api.suspendTenant(t.id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setBusy(null); }
  }

  async function doUnsuspend(t: api.TenantRow) {
    setBusy(t.id);
    try { await api.unsuspendTenant(t.id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setBusy(null); }
  }

  async function doProvision(e: React.FormEvent) {
    e.preventDefault();
    setProvisioning(true); setProvisionMsg(null);
    try {
      const d = await api.provision(pName, pEmail);
      setProvisionMsg(`Provisioned tenant ${d.tenant_id}. Bootstrap admin: ${d.admin_email}`);
      setPName(''); setPEmail(''); setShowProvision(false);
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed'); }
    finally { setProvisioning(false); }
  }

  async function doErase() {
    if (!eraseTarget) return;
    setErasing(true);
    try {
      await api.eraseTenant(eraseTarget.id, eraseConfirm);
      setEraseTarget(null); setEraseConfirm('');
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Erase failed'); }
    finally { setErasing(false); }
  }

  return (
    <div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      <div style={S.pageTitle}>Tenants</div>
      <div style={S.pageSub}>Provision, suspend, unsuspend, or erase tenant subscriptions. This console has no access to tenant conversations or data.</div>
      {error && <ErrorBox msg={error} />}
      {provisionMsg && <div style={{ color: '#2e7d32', background: '#e8f5e9', padding: '8px 12px', borderRadius: 4, fontSize: '0.85rem', marginBottom: 12 }}>{provisionMsg}</div>}

      <div style={{ ...S.row, marginBottom: 16 }}>
        <button style={S.btn('primary')} onClick={() => setShowProvision(v => !v)}>+ Provision Tenant</button>
        <button style={S.btn('ghost')} onClick={load}>Refresh</button>
      </div>

      {showProvision && (
        <div style={{ ...S.card, background: '#f9fafb' }}>
          <div style={{ fontWeight: 700, marginBottom: 12 }}>Provision New Tenant</div>
          <form onSubmit={doProvision}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
              <div>
                <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: '#5a6880', marginBottom: 4 }}>Tenant Name</label>
                <input style={S.input} value={pName} onChange={e => setPName(e.target.value)} placeholder="e.g. Valley University" required />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: '#5a6880', marginBottom: 4 }}>Bootstrap Admin Email</label>
                <input style={S.input} type="email" value={pEmail} onChange={e => setPEmail(e.target.value)} placeholder="admin@university.edu" required />
              </div>
            </div>
            <div style={S.row}>
              <button type="submit" style={S.btn('primary')} disabled={provisioning}>{provisioning ? 'Provisioning…' : 'Provision'}</button>
              <button type="button" style={S.btn('ghost')} onClick={() => setShowProvision(false)}>Cancel</button>
            </div>
            <p style={{ fontSize: '0.75rem', color: '#8090a8', marginTop: 8 }}>Creates a tenant shell + bootstrap admin only. No catalog or students (no SIS integration).</p>
          </form>
        </div>
      )}

      <div style={S.card}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 20 }}><Spinner /><span style={{ color: '#5a6880' }}>Loading…</span></div>
        ) : (
          <table style={S.table}>
            <thead>
              <tr>
                {['Name', 'Slug', 'Status', 'Students', 'Admins', 'Created', 'Actions'].map(h => (
                  <th key={h} style={S.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tenants.map(t => (
                <tr key={t.id}>
                  <td style={S.td}><strong>{t.name}</strong></td>
                  <td style={S.td}><code style={{ fontSize: '0.8rem', color: '#5a6880' }}>{t.slug}</code></td>
                  <td style={S.td}><span style={S.badge(t.status)}>{t.status}</span></td>
                  <td style={S.td}>{t.student_count}</td>
                  <td style={S.td}>{t.admin_count}</td>
                  <td style={S.td}>{new Date(t.created_at).toLocaleDateString()}</td>
                  <td style={S.td}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      {t.status === 'active'
                        ? <button style={S.btn('ghost')} onClick={() => doSuspend(t)} disabled={busy === t.id}>Suspend</button>
                        : <button style={S.btn('primary')} onClick={() => doUnsuspend(t)} disabled={busy === t.id}>Unsuspend</button>
                      }
                      <button style={S.btn('danger')} onClick={() => { setEraseTarget(t); setEraseConfirm(''); }}>Erase</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Erase confirmation modal */}
      {eraseTarget && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
          <div style={{ background: '#fff', borderRadius: 10, padding: 32, width: 460, maxWidth: '90vw' }}>
            <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#c62828', marginBottom: 8 }}>⚠ Erase Tenant</div>
            <p style={{ fontSize: '0.875rem', color: '#1a2436', marginBottom: 16 }}>
              This will permanently delete <strong>{eraseTarget.name}</strong> and all associated data (enrollments, plans, requests, RAG chunks). This action is irreversible.
            </p>
            <p style={{ fontSize: '0.875rem', marginBottom: 8 }}>
              Type <code style={{ background: '#f4f6f9', padding: '1px 5px', borderRadius: 3 }}>{eraseTarget.name}</code> to confirm:
            </p>
            <input
              style={{ ...S.input, marginBottom: 16 }}
              value={eraseConfirm}
              onChange={e => setEraseConfirm(e.target.value)}
              placeholder={eraseTarget.name}
            />
            <div style={S.row}>
              <button
                style={S.btn('danger')}
                onClick={doErase}
                disabled={eraseConfirm !== eraseTarget.name || erasing}
              >
                {erasing ? 'Erasing…' : 'Permanently Erase'}
              </button>
              <button style={S.btn('ghost')} onClick={() => setEraseTarget(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Cost Page ─────────────────────────────────────────────────────────────────

function CostPage() {
  const [period, setPeriod] = useState<'day' | 'week' | 'month'>('week');
  const [data, setData] = useState<api.CostResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(p: string) {
    setLoading(true); setError(null);
    try { const d = await api.getCost(p); setData(d); }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(period); }, [period]);

  return (
    <div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      <div style={S.pageTitle}>Usage Cost</div>
      <div style={S.pageSub}>Per-tenant aggregate usage metadata — no conversation content is available here.</div>
      <div style={{ background: '#fff3e0', border: '1px solid #ffcc80', borderRadius: 6, padding: '10px 14px', fontSize: '0.82rem', color: '#e65100', marginBottom: 16 }}>
        ℹ️ <strong>Usage metadata only</strong> — this view shows token counts and cost estimates. No conversation content, plans, or student data are accessible to the platform operator.
      </div>
      {error && <ErrorBox msg={error} />}
      <div style={{ ...S.row, marginBottom: 16 }}>
        <span style={{ fontSize: '0.85rem', color: '#5a6880' }}>Period:</span>
        {(['day', 'week', 'month'] as const).map(p => (
          <button key={p} style={{ ...S.btn(p === period ? 'primary' : 'ghost'), padding: '5px 12px' }} onClick={() => setPeriod(p)}>{p}</button>
        ))}
        {loading && <Spinner />}
      </div>
      <div style={S.card}>
        <table style={S.table}>
          <thead>
            <tr>
              {['Tenant ID', 'Kind', 'Calls', 'Tokens', 'Est. Cost (USD)'].map(h => <th key={h} style={S.th}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {data?.rows.length === 0 && (
              <tr><td colSpan={5} style={{ ...S.td, textAlign: 'center', color: '#8090a8', padding: 24 }}>No usage data for this period.</td></tr>
            )}
            {data?.rows.map((r, i) => (
              <tr key={i}>
                <td style={S.td}><code style={{ fontSize: '0.75rem', color: '#5a6880' }}>{r.tenant_id.slice(0, 8)}…</code></td>
                <td style={S.td}><span style={{ background: '#e8eaf6', color: '#283593', padding: '2px 6px', borderRadius: 3, fontSize: '0.75rem', fontWeight: 600 }}>{r.kind}</span></td>
                <td style={S.td}>{r.calls.toLocaleString()}</td>
                <td style={S.td}>{r.tokens.toLocaleString()}</td>
                <td style={S.td}>${r.cost_usd.toFixed(6)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Audit Page ────────────────────────────────────────────────────────────────

function AuditPage() {
  const [rows, setRows] = useState<api.AuditRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true); setError(null);
    try { const d = await api.getAudit(100); setRows(d.rows); }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  const actionColor: Record<string, string> = {
    provision: '#2e7d32', suspend: '#e65100', unsuspend: '#1565c0', erase: '#c62828',
  };

  return (
    <div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      <div style={S.pageTitle}>Platform Audit Log</div>
      <div style={S.pageSub}>Every operator action — provision, suspend, unsuspend, erase — is recorded here. This log survives tenant erase.</div>
      {error && <ErrorBox msg={error} />}
      <div style={{ marginBottom: 12 }}>
        <button style={S.btn('ghost')} onClick={load}>Refresh</button>
      </div>
      <div style={S.card}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 20 }}><Spinner /><span style={{ color: '#5a6880' }}>Loading…</span></div>
        ) : (
          <table style={S.table}>
            <thead>
              <tr>{['ID', 'Action', 'Target Tenant', 'Detail', 'Time'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={5} style={{ ...S.td, textAlign: 'center', color: '#8090a8', padding: 24 }}>No audit events yet.</td></tr>
              )}
              {rows.map(r => (
                <tr key={r.id}>
                  <td style={S.td}><code style={{ fontSize: '0.75rem' }}>{r.id}</code></td>
                  <td style={S.td}>
                    <span style={{ color: actionColor[r.action] ?? '#1a2436', fontWeight: 700, fontSize: '0.8rem', textTransform: 'uppercase' }}>{r.action}</span>
                  </td>
                  <td style={S.td}><code style={{ fontSize: '0.75rem', color: '#5a6880' }}>{r.target_tenant_id ? r.target_tenant_id.slice(0, 8) + '…' : '—'}</code></td>
                  <td style={S.td}><span style={{ fontSize: '0.75rem', color: '#5a6880' }}>{r.detail ? JSON.stringify(r.detail).slice(0, 80) : '—'}</span></td>
                  <td style={S.td}>{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Shell ─────────────────────────────────────────────────────────────────────

type Page = 'tenants' | 'cost' | 'audit';

export default function App() {
  const [loggedIn, setLoggedIn] = useState(api.hasToken());
  const [page, setPage] = useState<Page>('tenants');

  function handleLogin() { setLoggedIn(true); }
  function handleLogout() { api.clearToken(); setLoggedIn(false); }

  if (!loggedIn) return <LoginPage onLogin={handleLogin} />;

  const nav: { key: Page; label: string }[] = [
    { key: 'tenants', label: 'Tenants' },
    { key: 'cost', label: 'Usage Cost' },
    { key: 'audit', label: 'Audit Log' },
  ];

  return (
    <div style={S.shell}>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      {/* Sidebar */}
      <nav style={S.sidebar}>
        <div style={S.logoArea}>
          <div style={S.logoTitle}>Keel</div>
          <div style={S.logoSub}>Platform Console</div>
        </div>
        <div style={{ marginTop: 16 }}>
          {nav.map(n => (
            <button key={n.key} style={S.navBtn(page === n.key)} onClick={() => setPage(n.key)}>
              {n.label}
            </button>
          ))}
        </div>
        <div style={{ marginTop: 'auto', padding: '16px 20px', borderTop: '1px solid rgba(240,236,221,0.1)' }}>
          <div style={{ fontSize: '0.7rem', color: 'rgba(240,236,221,0.4)', marginBottom: 6 }}>Platform Operator</div>
          <button style={{ ...S.btn('ghost'), width: '100%', background: 'rgba(240,236,221,0.08)', color: 'rgba(240,236,221,0.7)', border: 'none' }} onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </nav>

      {/* Main */}
      <main style={S.main}>
        {page === 'tenants' && <TenantsPage />}
        {page === 'cost' && <CostPage />}
        {page === 'audit' && <AuditPage />}
      </main>
    </div>
  );
}
