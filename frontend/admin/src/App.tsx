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
            style={{ width: 'calc(100% - 40px)', maxWidth: 380, objectFit: 'contain', display: 'block', margin: '0 auto' }}
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
          boxShadow: [
            '0 0 0 1px rgba(100,140,210,0.18)',       /* thin steel-blue ring — acts as border */
            '0 0 24px rgba(80,120,190,0.22)',          /* soft blue aura — starts the merge */
            '0 0 56px rgba(35,53,77,0.30)',            /* mid navy bloom */
            '0 32px 96px rgba(0,4,53,0.55)',          /* deep shadow for depth */
          ].join(', '),
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

// ── Premium line icons (stroke-based, inherit currentColor) ───────────────────

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const p = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.7,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  switch (name) {
    case 'knowledge': // open book
      return (<svg {...p}><path d="M12 7v14" /><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" /></svg>);
    case 'widget': // sliders
      return (<svg {...p}><line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" /><line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" /></svg>);
    case 'snippet': // code
      return (<svg {...p}><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>);
    case 'cost': // dollar
      return (<svg {...p}><line x1="12" y1="2" x2="12" y2="22" /><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" /></svg>);
    case 'audit': // clipboard list
      return (<svg {...p}><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" /><rect x="9" y="3" width="6" height="4" rx="1" /><line x1="8.5" y1="12" x2="15.5" y2="12" /><line x1="8.5" y1="16" x2="13" y2="16" /></svg>);
    case 'tenants': // buildings
      return (<svg {...p}><path d="M3 21h18" /><path d="M9 21V8l-5 3v10" /><path d="M9 21V4a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v17" /><line x1="13" y1="7" x2="15" y2="7" /><line x1="13" y1="11" x2="15" y2="11" /><line x1="13" y1="15" x2="15" y2="15" /></svg>);
    default:
      return (<svg {...p}><circle cx="12" cy="12" r="9" /></svg>);
  }
}

// ── Shared sidebar shell ──────────────────────────────────────────────────────

interface NavItem { label: string; icon: string; }

// One nav row — a creamy box that turns brown on hover/active (brand colours).
function NavButton({ item, isActive, onClick }: { item: NavItem; isActive: boolean; onClick: () => void }) {
  const [hover, setHover] = useState(false);
  const brown = hover || isActive;
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 13,
        width: '100%',
        padding: '13px 15px',
        marginBottom: 9,
        borderRadius: 12,
        background: brown ? '#4B2E0A' : '#F0ECDD',
        color: brown ? '#F0ECDD' : '#1A1206',
        border: 'none',
        cursor: 'pointer',
        fontFamily: "'Inter', system-ui, sans-serif",
        fontSize: '0.96rem',
        fontWeight: isActive ? 700 : 500,
        letterSpacing: '0.01em',
        textAlign: 'left',
        boxShadow: brown
          ? '0 6px 18px rgba(75,46,10,0.40)'
          : '0 1px 3px rgba(0,4,53,0.10)',
        transform: brown ? 'translateX(3px)' : 'none',
        transition: 'background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease, transform 0.12s ease',
      }}
    >
      <Icon name={item.icon} size={21} />
      {item.label}
    </button>
  );
}

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
      <nav style={{ width: 272, flexShrink: 0, background: '#010619', display: 'flex', flexDirection: 'column', height: '100vh', position: 'sticky', top: 0 }}>
        {/* Logo banner — larger, more breathing room */}
        <div style={{ padding: '22px 22px 16px', borderBottom: '1px solid rgba(240,236,221,0.08)' }}>
          <img
            src="/static/final-keel-logo.jpeg"
            alt="Keel"
            style={{ width: '100%', maxWidth: 248, objectFit: 'contain', display: 'block', margin: '0 auto' }}
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
          />
          <div style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: '0.66rem', color: '#A9805A', marginTop: '8px', letterSpacing: '0.18em', textTransform: 'uppercase', fontWeight: 600, textAlign: 'center' }}>
            {role === 'platform_operator' ? 'Platform Console' : 'Admin Console'}
          </div>
        </div>

        <div style={{ flex: 1, padding: '20px 16px', overflowY: 'auto' }}>
          {nav.map((item, i) => (
            <NavButton key={item.label} item={item} isActive={i === active} onClick={() => onChange(i)} />
          ))}
        </div>

        <div style={{ padding: '18px 20px', borderTop: '1px solid rgba(240,236,221,0.1)' }}>
          <div style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: '0.78rem', color: '#E7DFC9', fontWeight: 600, marginBottom: '4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={tenantName ?? identity}>
            {role === 'platform_operator' ? 'Platform Operator' : (tenantName ?? `Tenant: ${identity.slice(0, 8)}…`)}
          </div>
          <button onClick={onSignOut} style={{ background: 'transparent', border: 'none', color: '#9FB3CE', fontFamily: "'Inter', system-ui, sans-serif", fontSize: '0.74rem', cursor: 'pointer', padding: 0, textDecoration: 'underline' }}>
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
  { label: 'Knowledge Base', icon: 'knowledge' },
  { label: 'Widget Config',  icon: 'widget' },
  { label: 'Embed Snippet',  icon: 'snippet' },
  { label: 'Usage & Cost',   icon: 'cost' },
  { label: 'Audit Log',      icon: 'audit' },
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
  { label: 'Tenants',     icon: 'tenants' },
  { label: 'Usage Cost',  icon: 'cost' },
  { label: 'Audit Log',   icon: 'audit' },
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
