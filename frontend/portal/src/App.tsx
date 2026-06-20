/**
 * App — real email+password login, env-parameterized university branding.
 *
 * Branding env vars (VITE_* — set per portal instance in docker-compose):
 *   VITE_UNIVERSITY_NAME  e.g. "Northane University" | "Summit College"
 *   VITE_UNIVERSITY_INITIAL  e.g. "N" | "S"
 *   VITE_PORTAL_TENANT  slug for display only (real enforcement is server-side)
 *
 * Auth flow (spec §S8):
 *   POST /api/portal/login {email, password}
 *   → server validates, sets HttpOnly session cookie
 *   → role from response drives page routing (student | registrar)
 *
 * No student_id switcher — this is real portal login.
 */

import { useCallback, useState } from 'react';
import { Spinner, Toast } from '@keel/ui';
import { login, logout } from './api';
import { PortalLayout } from './components/PortalLayout';
import { KeelWidget } from './components/KeelWidget';
import { Schedule } from './pages/student/Schedule';
import { Requests } from './pages/student/Requests';
import { Activity } from './pages/student/Activity';
import { StagedSearch } from './pages/student/StagedSearch';
import { StagedPetition } from './pages/student/StagedPetition';
import { RequestQueue } from './pages/registrar/RequestQueue';
import { Catalog } from './pages/registrar/Catalog';
import { Sections } from './pages/registrar/Sections';

// ── Branding (per-instance env vars, with Northane defaults) ─────────────────

const UNIVERSITY_NAME = import.meta.env.VITE_UNIVERSITY_NAME || 'Northane University';
const UNIVERSITY_INITIAL = import.meta.env.VITE_UNIVERSITY_INITIAL || 'N';

// Set browser tab title to match the university
document.title = `${UNIVERSITY_NAME} Portal`;

// ── Nav definitions ───────────────────────────────────────────────────────────

const STUDENT_NAV = [
  { key: 'schedule', label: 'My Schedule' },
  { key: 'requests', label: 'Requests' },
  { key: 'activity', label: 'Activity' },
  { key: 'search', label: 'Section Search' },
  { key: 'petition', label: 'Submit Petition' },
];

const REGISTRAR_NAV = [
  { key: 'queue', label: 'Request Queue' },
  { key: 'catalog', label: 'Catalog' },
  { key: 'sections', label: 'Sections' },
];

// ── Session type ──────────────────────────────────────────────────────────────

interface SessionState {
  email: string;
  role: string;
}

// ── Login form ────────────────────────────────────────────────────────────────

interface LoginFormProps {
  onLogin: (email: string, password: string) => void;
  loading: boolean;
  error: string | null;
}

function LoginForm({ onLogin, loading, error }: LoginFormProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (email && password) onLogin(email, password);
  }

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '9px 12px',
    border: '1px solid #d8dde6',
    borderRadius: '4px',
    fontFamily: 'Inter, system-ui, sans-serif',
    fontSize: '0.875rem',
    color: '#1a2436',
    background: '#fff',
    boxSizing: 'border-box',
    outline: 'none',
  };

  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontSize: '0.75rem',
    fontWeight: 600,
    color: '#5a6880',
    fontFamily: 'Inter, system-ui, sans-serif',
    marginBottom: '5px',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  };

  return (
    <div
      className="sis-light"
      style={{
        minHeight: '100vh',
        background: 'var(--bg)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '6px',
          padding: '40px 48px',
          width: '400px',
          maxWidth: '90vw',
        }}
      >
        {/* University branding */}
        <div style={{ textAlign: 'center', marginBottom: '28px' }}>
          <div
            style={{
              width: '52px',
              height: '52px',
              background: '#1a2e52',
              borderRadius: '6px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '22px',
              fontWeight: 700,
              color: '#f0ecdd',
              fontFamily: 'Source Serif 4, Georgia, serif',
              margin: '0 auto 12px',
            }}
          >
            {UNIVERSITY_INITIAL}
          </div>
          <h1
            style={{
              fontFamily: 'Source Serif 4, Georgia, serif',
              fontSize: '1.25rem',
              fontWeight: 700,
              color: '#1a2436',
              margin: '0 0 4px',
            }}
          >
            {UNIVERSITY_NAME}
          </h1>
          <p style={{ fontSize: '0.875rem', color: '#5a6880', fontFamily: 'Inter, system-ui, sans-serif', margin: 0 }}>
            Student Information System
          </p>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: '14px' }}>
            <label style={labelStyle}>University Email</label>
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@university.edu"
              style={inputStyle}
              required
              disabled={loading}
            />
          </div>

          <div style={{ marginBottom: '20px' }}>
            <label style={labelStyle}>Password</label>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              style={inputStyle}
              required
              disabled={loading}
            />
          </div>

          <button
            type="submit"
            disabled={loading || !email || !password}
            style={{
              display: 'block',
              width: '100%',
              padding: '10px 14px',
              background: loading ? '#8090a8' : '#1a2e52',
              border: 'none',
              borderRadius: '4px',
              color: '#f0ecdd',
              fontFamily: 'Inter, system-ui, sans-serif',
              fontSize: '0.9rem',
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'background 0.15s ease',
            }}
          >
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>

        {loading && (
          <div style={{ display: 'flex', justifyContent: 'center', marginTop: '14px' }}>
            <Spinner size={20} />
          </div>
        )}

        {error && (
          <p style={{ color: '#c0392b', fontSize: '0.8rem', marginTop: '12px', fontFamily: 'Inter, system-ui, sans-serif' }}>
            {error}
          </p>
        )}

        <p style={{ fontSize: '0.75rem', color: '#8090a8', fontFamily: 'Inter, system-ui, sans-serif', marginTop: '20px', textAlign: 'center' }}>
          Use your university email and password to sign in.
        </p>
      </div>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [session, setSession] = useState<SessionState | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  const [activeNav, setActiveNav] = useState('schedule');
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' | 'info' } | null>(null);
  const [scheduleRefresh, setScheduleRefresh] = useState(0);

  // ── Login handler
  async function handleLogin(email: string, password: string) {
    setLoggingIn(true);
    setLoginError(null);
    try {
      const data = await login(email, password);
      setSession({ email, role: data.role });
      setActiveNav(data.role === 'registrar' ? 'queue' : 'schedule');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Login failed';
      // Handle suspension gracefully
      if (msg.toLowerCase().includes('suspend') || msg.includes('403')) {
        setLoginError('The Keel advising assistant is currently unavailable. Your SIS portal is still accessible.');
      } else {
        setLoginError(msg);
      }
    } finally {
      setLoggingIn(false);
    }
  }

  // ── Logout handler
  async function handleLogout() {
    await logout();
    setSession(null);
    setActiveNav('schedule');
  }

  // ── ENROLLMENT_COMPLETE from widget
  const handleEnrollmentComplete = useCallback(() => {
    setScheduleRefresh((n) => n + 1);
    setToast({ message: 'Enrollment confirmed — schedule updated.', kind: 'success' });
    if (session?.role === 'student') setActiveNav('schedule');
  }, [session?.role]);

  // ── Render: not logged in ──────────────────────────────────────────────────
  if (!session) {
    return (
      <LoginForm
        onLogin={handleLogin}
        loading={loggingIn}
        error={loginError}
      />
    );
  }

  // ── Render: logged in ──────────────────────────────────────────────────────

  const navItems = session.role === 'registrar' ? REGISTRAR_NAV : STUDENT_NAV;
  const validNavKeys = navItems.map((n) => n.key);
  const currentNav = validNavKeys.includes(activeNav) ? activeNav : navItems[0].key;

  const topRight = (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
      <span style={{ fontSize: '0.8rem', color: 'rgba(240,236,221,0.7)', fontFamily: 'Inter, system-ui, sans-serif' }}>
        {session.email}
        {session.role === 'registrar' && <> · <strong style={{ color: '#f0ecdd' }}>Registrar</strong></>}
      </span>
      <button
        onClick={handleLogout}
        style={{
          background: 'rgba(240,236,221,0.12)',
          border: '1px solid rgba(240,236,221,0.25)',
          borderRadius: '4px',
          color: '#f0ecdd',
          padding: '4px 10px',
          cursor: 'pointer',
          fontFamily: 'Inter, system-ui, sans-serif',
          fontSize: '0.75rem',
        }}
      >
        Sign out
      </button>
    </div>
  );

  function renderPage() {
    if (session!.role === 'registrar') {
      switch (currentNav) {
        case 'queue':    return <RequestQueue />;
        case 'catalog':  return <Catalog />;
        case 'sections': return <Sections />;
        default:         return <RequestQueue />;
      }
    }
    switch (currentNav) {
      case 'schedule': return <Schedule refreshSignal={scheduleRefresh} />;
      case 'requests': return <Requests />;
      case 'activity': return <Activity />;
      case 'search':   return <StagedSearch />;
      case 'petition': return <StagedPetition />;
      default:         return <Schedule refreshSignal={scheduleRefresh} />;
    }
  }

  return (
    <>
      <PortalLayout
        universityName={UNIVERSITY_NAME}
        role={session.role}
        navItems={navItems}
        activeNav={currentNav}
        onNavChange={setActiveNav}
        topRight={topRight}
      >
        {renderPage()}
      </PortalLayout>

      {session.role === 'student' && (
        <KeelWidget onEnrollmentComplete={handleEnrollmentComplete} />
      )}

      {toast && (
        <Toast message={toast.message} kind={toast.kind} onClose={() => setToast(null)} />
      )}
    </>
  );
}
