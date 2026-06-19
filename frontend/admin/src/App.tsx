import React, { useState } from 'react';
import { Button, Card, Field, Input } from '@keel/ui';
import { getWidgetConfig } from './api';
import type { AuthHeaders } from './api';
import { RagUpload } from './screens/RagUpload';
import { WidgetConfig } from './screens/WidgetConfig';
import { Snippet } from './screens/Snippet';
import { Cost } from './screens/Cost';
import { Audit } from './screens/Audit';

// ── Nav items ─────────────────────────────────────────────────────────────────

interface NavItem {
  label: string;
  icon: string;
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Knowledge Base', icon: '⬆' },
  { label: 'Widget Config', icon: '⚙' },
  { label: 'Embed Snippet', icon: '</>' },
  { label: 'Usage & Cost', icon: '$' },
  { label: 'Audit Log', icon: '≡' },
];

// ── Login screen ──────────────────────────────────────────────────────────────

interface LoginProps {
  onLogin: (auth: AuthHeaders) => void;
}

function LoginScreen({ onLogin }: LoginProps) {
  const [token, setToken] = useState('');
  const [tenantId, setTenantId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !tenantId) {
      setError('Both fields are required.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await getWidgetConfig({ token, tenantId });
      onLogin({ token, tenantId });
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'Sign-in failed. Check your token and tenant ID.',
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="keel-light"
      style={{
        minHeight: '100vh',
        background: 'var(--bg)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 'var(--sp-4)',
      }}
    >
      <div style={{ width: '100%', maxWidth: 400 }}>
        {/* Wordmark */}
        <div
          style={{
            textAlign: 'center',
            marginBottom: 'var(--sp-8)',
          }}
        >
          <div
            style={{
              fontFamily: "'Fraunces', Georgia, serif",
              fontSize: 'var(--text-2xl)',
              fontWeight: 700,
              color: 'var(--storm)',
              letterSpacing: '-0.5px',
            }}
          >
            Keel
          </div>
          <div
            style={{
              fontFamily: "'Inter', system-ui, sans-serif",
              fontSize: 'var(--text-sm)',
              color: 'var(--text-muted)',
              marginTop: 'var(--sp-1)',
            }}
          >
            Admin Console
          </div>
        </div>

        <Card style={{ padding: 'var(--sp-6)' }}>
          <form
            onSubmit={handleSubmit}
            style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}
          >
            <Field label="Admin token">
              <Input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Enter your admin token"
                autoComplete="current-password"
              />
            </Field>
            <Field label="Tenant ID">
              <Input
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                placeholder="e.g. university-abc"
              />
            </Field>

            {error && (
              <div
                style={{
                  background: '#fef2f2',
                  border: '1px solid #fca5a5',
                  borderRadius: 'var(--radius-md)',
                  padding: 'var(--sp-3)',
                  fontSize: 'var(--text-sm)',
                  color: '#c0392b',
                  fontFamily: "'Inter', system-ui, sans-serif",
                }}
              >
                {error}
              </div>
            )}

            <Button type="submit" loading={loading} style={{ width: '100%' }}>
              {loading ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}

// ── Sidebar nav ───────────────────────────────────────────────────────────────

interface SidebarProps {
  active: number;
  onChange: (i: number) => void;
  tenantId: string;
  onSignOut: () => void;
}

function Sidebar({ active, onChange, tenantId, onSignOut }: SidebarProps) {
  return (
    <nav
      style={{
        width: 220,
        flexShrink: 0,
        background: 'var(--storm)',
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        position: 'sticky',
        top: 0,
      }}
    >
      {/* Wordmark */}
      <div
        style={{
          padding: 'var(--sp-6) var(--sp-4) var(--sp-4)',
          borderBottom: '1px solid rgba(240,236,221,0.1)',
        }}
      >
        <div
          style={{
            fontFamily: "'Fraunces', Georgia, serif",
            fontSize: 'var(--text-xl)',
            fontWeight: 700,
            color: 'var(--moonlight)',
            letterSpacing: '-0.5px',
          }}
        >
          Keel
        </div>
        <div
          style={{
            fontFamily: "'Inter', system-ui, sans-serif",
            fontSize: 'var(--text-xs)',
            color: 'var(--frost)',
            marginTop: 'var(--sp-1)',
            letterSpacing: '0.03em',
            textTransform: 'uppercase',
          }}
        >
          Admin Console
        </div>
      </div>

      {/* Nav items */}
      <div style={{ flex: 1, paddingTop: 'var(--sp-3)' }}>
        {NAV_ITEMS.map((item, i) => {
          const isActive = i === active;
          return (
            <button
              key={item.label}
              onClick={() => onChange(i)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--sp-3)',
                width: '100%',
                padding: 'var(--sp-3) var(--sp-4)',
                background: isActive ? 'rgba(240,236,221,0.1)' : 'transparent',
                border: 'none',
                borderLeft: isActive
                  ? '3px solid var(--accent)'
                  : '3px solid transparent',
                color: isActive ? 'var(--moonlight)' : 'var(--frost)',
                cursor: 'pointer',
                fontFamily: "'Inter', system-ui, sans-serif",
                fontSize: 'var(--text-sm)',
                fontWeight: isActive ? 600 : 400,
                textAlign: 'left',
                transition:
                  'background var(--transition-fast), color var(--transition-fast)',
              }}
            >
              <span
                style={{
                  width: 18,
                  textAlign: 'center',
                  fontSize: 'var(--text-sm)',
                  flexShrink: 0,
                }}
              >
                {item.icon}
              </span>
              {item.label}
            </button>
          );
        })}
      </div>

      {/* Bottom tenant info */}
      <div
        style={{
          padding: 'var(--sp-4)',
          borderTop: '1px solid rgba(240,236,221,0.1)',
        }}
      >
        <div
          style={{
            fontFamily: "'Inter', system-ui, sans-serif",
            fontSize: 'var(--text-xs)',
            color: 'var(--frost)',
            marginBottom: 'var(--sp-2)',
          }}
        >
          Tenant: <strong style={{ color: 'var(--moonlight)' }}>{tenantId}</strong>
        </div>
        <button
          onClick={onSignOut}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--frost)',
            fontFamily: "'Inter', system-ui, sans-serif",
            fontSize: 'var(--text-xs)',
            cursor: 'pointer',
            padding: 0,
            textDecoration: 'underline',
          }}
        >
          Sign out
        </button>
      </div>
    </nav>
  );
}

// ── Main app ──────────────────────────────────────────────────────────────────

function AdminApp({ auth, onSignOut }: { auth: AuthHeaders; onSignOut: () => void }) {
  const [screen, setScreen] = useState(0);

  const screens = [
    <RagUpload key="rag" auth={auth} />,
    <WidgetConfig key="widget" auth={auth} />,
    <Snippet key="snippet" auth={auth} />,
    <Cost key="cost" auth={auth} />,
    <Audit key="audit" auth={auth} />,
  ];

  return (
    <div
      className="keel-light"
      style={{
        display: 'flex',
        minHeight: '100vh',
        background: 'var(--bg)',
        color: 'var(--text)',
      }}
    >
      <Sidebar
        active={screen}
        onChange={setScreen}
        tenantId={auth.tenantId}
        onSignOut={onSignOut}
      />

      {/* Content area */}
      <main
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: 'var(--sp-8)',
        }}
      >
        {screens[screen]}
      </main>
    </div>
  );
}

// ── Root ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [auth, setAuth] = useState<AuthHeaders | null>(null);

  if (!auth) {
    return <LoginScreen onLogin={setAuth} />;
  }

  return <AdminApp auth={auth} onSignOut={() => setAuth(null)} />;
}
