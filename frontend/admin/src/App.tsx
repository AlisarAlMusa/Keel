/**
 * Keel Console — unified surface for tenant_admin and platform_operator.
 *
 * One login page → role check → two app shells:
 *   tenant_admin      → Knowledge Base · Widget Config · Embed Snippet · Usage & Cost · Audit Log
 *   platform_operator → Tenants · Usage Cost · Audit Log
 */

import { useState } from 'react';
import * as api from './api';
import { RagUpload } from './screens/RagUpload';
import { WidgetConfig } from './screens/WidgetConfig';
import { Snippet } from './screens/Snippet';
import { Cost } from './screens/Cost';
import { Audit } from './screens/Audit';
import { Tenants } from './screens/Tenants';
import { PlatformCost } from './screens/PlatformCost';
import { PlatformAudit } from './screens/PlatformAudit';

// ── Login ─────────────────────────────────────────────────────────────────────

function LoginScreen({ onLogin }: { onLogin: (role: string, tenantId: string | null, tenantName: string | null) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true); setError(null);
    try {
      const data = await api.login(email, password);
      if (data.role !== 'tenant_admin' && data.role !== 'platform_operator') {
        setError(`Access denied — role '${data.role}' is not permitted here.`);
        return;
      }
      api.setToken(data.token);
      if (data.tenant_id) api.setTenantId(data.tenant_id);
      onLogin(data.role, data.tenant_id, data.tenant_name ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: '#010619',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
      }}
    >
      <div style={{ width: '100%', maxWidth: 480 }}>
        {/* Logo — keel-login.png has #0A1628 background, same as page bg → seamless */}
        <div style={{ textAlign: 'center', marginBottom: '24px' }}>
          <img
            src="/static/final-keel-logo.jpeg"
            alt="Keel"
            style={{ width: 'calc(100% - 80px)', maxWidth: 340, objectFit: 'contain', display: 'block', margin: '0 auto' }}
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = 'none';
            }}
          />
          <div style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: '0.75rem', color: '#8BA3C5', marginTop: '8px', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
            Administration Console
          </div>
        </div>

        {/* Card */}
        <div style={{
          background: '#F0ECDD',
          borderRadius: '16px',
          padding: '40px',
          boxShadow: '0 32px 96px rgba(0,4,53,0.6), 0 0 0 1px rgba(75,46,10,0.25)',
        }}>
          {error && (
            <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '8px', padding: '12px 16px', fontSize: '0.875rem', color: '#c0392b', fontFamily: "'Inter', system-ui, sans-serif", marginBottom: '24px' }}>
              {error}
            </div>
          )}
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <div>
              <label style={{ display: 'block', fontFamily: "'Fraunces', 'Source Serif 4', Georgia, serif", fontSize: '0.9rem', fontWeight: 600, color: '#2A1606', marginBottom: '8px' }}>
                Email address
              </label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                autoComplete="email"
                required
                disabled={loading}
                placeholder="you@institution.edu"
                style={{
                  width: '100%',
                  padding: '11px 14px',
                  border: '1.5px solid #c4d0df',
                  borderRadius: '8px',
                  fontSize: '0.95rem',
                  fontFamily: "'Inter', system-ui, sans-serif",
                  color: '#02122F',
                  background: '#fff',
                  outline: 'none',
                  boxSizing: 'border-box',
                  transition: 'border-color 0.15s ease',
                }}
                onFocus={e => { e.currentTarget.style.borderColor = '#4B2E0A'; }}
                onBlur={e => { e.currentTarget.style.borderColor = '#c4d0df'; }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontFamily: "'Fraunces', 'Source Serif 4', Georgia, serif", fontSize: '0.9rem', fontWeight: 600, color: '#2A1606', marginBottom: '8px' }}>
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                disabled={loading}
                placeholder="••••••••"
                style={{
                  width: '100%',
                  padding: '11px 14px',
                  border: '1.5px solid #c4d0df',
                  borderRadius: '8px',
                  fontSize: '0.95rem',
                  fontFamily: "'Inter', system-ui, sans-serif",
                  color: '#02122F',
                  background: '#fff',
                  outline: 'none',
                  boxSizing: 'border-box',
                  transition: 'border-color 0.15s ease',
                }}
                onFocus={e => { e.currentTarget.style.borderColor = '#4B2E0A'; }}
                onBlur={e => { e.currentTarget.style.borderColor = '#c4d0df'; }}
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              style={{
                width: '100%',
                padding: '13px',
                marginTop: '4px',
                background: loading ? 'rgba(75,46,10,0.6)' : '#4B2E0A',
                color: '#F0ECDD',
                border: 'none',
                borderRadius: '8px',
                fontSize: '0.95rem',
                fontFamily: "'Fraunces', 'Source Serif 4', Georgia, serif",
                fontWeight: 700,
                letterSpacing: '0.02em',
                cursor: loading ? 'not-allowed' : 'pointer',
                transition: 'background 0.15s ease, transform 0.1s ease',
              }}
              onMouseEnter={e => { if (!loading) e.currentTarget.style.background = '#3A2208'; }}
              onMouseLeave={e => { if (!loading) e.currentTarget.style.background = '#4B2E0A'; }}
            >
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>

        <p style={{ textAlign: 'center', marginTop: '20px', fontSize: '0.75rem', color: '#495B7D', fontFamily: "'Inter', system-ui, sans-serif" }}>
          For registrars and platform operators only.
        </p>
      </div>
    </div>
  );
}

// ── Shared sidebar shell ──────────────────────────────────────────────────────

interface NavItem { label: string; icon: string; }

function AppShell({
  nav,
  active,
  onChange,
  identity,
  role,
  tenantName,
  onSignOut,
  children,
}: {
  nav: NavItem[];
  active: number;
  onChange: (i: number) => void;
  identity: string;
  role: string;
  tenantName?: string | null;
  onSignOut: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="keel-light" style={{ display: 'flex', minHeight: '100vh', background: 'var(--bg)', color: 'var(--text)' }}>
      {/* Sidebar */}
      <nav style={{ width: 220, flexShrink: 0, background: '#000719', display: 'flex', flexDirection: 'column', height: '100vh', position: 'sticky', top: 0 }}>
        {/* Logo banner — transparent: sidebar is now #000719, matching the logo PNG background */}
        <div style={{ padding: '12px 16px 8px', borderBottom: '1px solid rgba(240,236,221,0.08)' }}>
          <img
            src="/static/dark-navy-logo.png"
            alt="Keel"
            style={{ width: '100%', maxWidth: 210, objectFit: 'contain', display: 'block', margin: '0 auto' }}
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
          />
          <div style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: '0.6rem', color: '#4B2E0A', marginTop: '4px', letterSpacing: '0.1em', textTransform: 'uppercase', fontWeight: 600, textAlign: 'center' }}>
            {role === 'platform_operator' ? 'Platform Console' : 'Admin Console'}
          </div>
        </div>

        <div style={{ flex: 1, paddingTop: 'var(--sp-3)' }}>
          {nav.map((item, i) => {
            const isActive = i === active;
            return (
              <button
                key={item.label}
                onClick={() => onChange(i)}
                style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)', width: '100%', padding: 'var(--sp-3) var(--sp-4)', background: isActive ? 'rgba(75,46,10,0.12)' : 'transparent', border: 'none', borderLeft: isActive ? '3px solid #4B2E0A' : '3px solid transparent', color: isActive ? 'var(--moonlight)' : 'var(--frost)', cursor: 'pointer', fontFamily: "'Inter', system-ui, sans-serif", fontSize: 'var(--text-sm)', fontWeight: isActive ? 600 : 400, textAlign: 'left' }}
              >
                <span style={{ width: 18, textAlign: 'center', fontSize: 'var(--text-sm)', flexShrink: 0 }}>{item.icon}</span>
                {item.label}
              </button>
            );
          })}
        </div>

        <div style={{ padding: 'var(--sp-4)', borderTop: '1px solid rgba(240,236,221,0.1)' }}>
          <div style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: 'var(--text-xs)', color: 'var(--frost)', marginBottom: 'var(--sp-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={tenantName ?? identity}>
            {role === 'platform_operator' ? 'Platform Operator' : (tenantName ?? `Tenant: ${identity.slice(0, 8)}…`)}
          </div>
          <button onClick={onSignOut} style={{ background: 'transparent', border: 'none', color: 'var(--frost)', fontFamily: "'Inter', system-ui, sans-serif", fontSize: 'var(--text-xs)', cursor: 'pointer', padding: 0, textDecoration: 'underline' }}>
            Sign out
          </button>
        </div>
      </nav>

      {/* Content */}
      <main style={{ flex: 1, overflowY: 'auto', padding: 'var(--sp-8)' }}>
        {/* Tenant name badge — top right */}
        {(tenantName || role === 'platform_operator') && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 'var(--sp-4)' }}>
            <span style={{
              fontFamily: "'Inter', system-ui, sans-serif",
              fontSize: '0.75rem',
              color: 'var(--text-muted)',
              background: 'rgba(75,46,10,0.08)',
              border: '1px solid rgba(75,46,10,0.2)',
              borderRadius: '12px',
              padding: '3px 12px',
              fontWeight: 500,
            }}>
              {role === 'platform_operator' ? 'Platform Operator' : tenantName}
            </span>
          </div>
        )}
        {children}
      </main>
    </div>
  );
}

// ── Tenant-admin shell ────────────────────────────────────────────────────────

const ADMIN_NAV: NavItem[] = [
  { label: 'Knowledge Base', icon: '⬆' },
  { label: 'Widget Config',  icon: '⚙' },
  { label: 'Embed Snippet',  icon: '</>' },
  { label: 'Usage & Cost',   icon: '$' },
  { label: 'Audit Log',      icon: '≡' },
];

function AdminApp({ tenantId, tenantName, onSignOut }: { tenantId: string; tenantName: string | null; onSignOut: () => void }) {
  const [screen, setScreen] = useState(0);
  const screens = [
    <RagUpload key="rag" />,
    <WidgetConfig key="widget" />,
    <Snippet key="snippet" />,
    <Cost key="cost" />,
    <Audit key="audit" />,
  ];
  return (
    <AppShell nav={ADMIN_NAV} active={screen} onChange={setScreen} identity={tenantId} role="tenant_admin" tenantName={tenantName} onSignOut={onSignOut}>
      {screens[screen]}
    </AppShell>
  );
}

// ── Platform-operator shell ───────────────────────────────────────────────────

const PLATFORM_NAV: NavItem[] = [
  { label: 'Tenants',     icon: '🏫' },
  { label: 'Usage Cost',  icon: '$' },
  { label: 'Audit Log',   icon: '≡' },
];

function PlatformApp({ onSignOut }: { onSignOut: () => void }) {
  const [screen, setScreen] = useState(0);
  const screens = [
    <Tenants key="tenants" />,
    <PlatformCost key="cost" />,
    <PlatformAudit key="audit" />,
  ];
  return (
    <AppShell nav={PLATFORM_NAV} active={screen} onChange={setScreen} identity="" role="platform_operator" tenantName={null} onSignOut={onSignOut}>
      {screens[screen]}
    </AppShell>
  );
}

// ── Root ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [role, setRole] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [tenantName, setTenantName] = useState<string | null>(null);

  function handleLogin(r: string, tid: string | null, tname: string | null) {
    setRole(r);
    setTenantId(tid);
    setTenantName(tname);
  }

  function handleSignOut() {
    api.clearToken();
    setRole(null);
    setTenantId(null);
  }

  if (!role) return <LoginScreen onLogin={handleLogin} />;
  if (role === 'platform_operator') return <PlatformApp onSignOut={handleSignOut} />;
  return <AdminApp tenantId={tenantId ?? ''} tenantName={tenantName} onSignOut={handleSignOut} />;
}
