/**
 * App — session gate, role switcher, and top-level routing.
 *
 * The "SSO stand-in" is a dropdown of pre-seeded student IDs.
 * Selecting one calls POST /api/portal/login; "Registrar View" switches role.
 *
 * Student role pages: My Schedule, Requests, Activity, Section Search, Submit Petition.
 * Registrar role pages: Request Queue, Catalog, Sections.
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

// ── Seeded student IDs (populated by seeds/002_seed_data.sql) ─────────────────
// These are placeholder IDs that match the seeded Northgate tenant data.
const SEEDED_STUDENTS = [
  { id: 'stu-001', label: 'Alice Nguyen (stu-001)' },
  { id: 'stu-002', label: 'Ben Okoro (stu-002)' },
  { id: 'stu-003', label: 'Celia Ramos (stu-003)' },
  { id: 'stu-004', label: 'Daniel Park (stu-004)' },
  { id: 'stu-005', label: 'Esme Johansson (stu-005)' },
];

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
  studentId: string;
  role: string;
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [session, setSession] = useState<SessionState | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  // Active nav key
  const [activeNav, setActiveNav] = useState('schedule');

  // Toast
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' | 'info' } | null>(null);

  // Schedule refresh signal — incremented on ENROLLMENT_COMPLETE
  const [scheduleRefresh, setScheduleRefresh] = useState(0);

  // Switcher dropdown state
  const [switcherOpen, setSwitcherOpen] = useState(false);

  // ── Auto-login: if a session cookie already exists, set a default session
  // (we can't read an HttpOnly cookie from JS, so we just detect by probing
  //  the schedule endpoint on mount — simpler: just show the switcher on load)
  // We don't try to restore session on refresh — user picks from dropdown.

  // ── Login handler
  async function handleLogin(studentId: string, role: string) {
    setLoggingIn(true);
    setLoginError(null);
    try {
      const data = await login(studentId, role);
      setSession({ studentId, role: data.role });
      setActiveNav(role === 'registrar' ? 'queue' : 'schedule');
      setSwitcherOpen(false);
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoggingIn(false);
    }
  }

  // ── Logout handler
  async function handleLogout() {
    await logout();
    setSession(null);
    setActiveNav('schedule');
    setSwitcherOpen(false);
  }

  // ── ENROLLMENT_COMPLETE from widget
  const handleEnrollmentComplete = useCallback(() => {
    setScheduleRefresh((n) => n + 1);
    setToast({ message: 'Enrollment confirmed — schedule updated.', kind: 'success' });
    // Switch to schedule tab to show the update
    if (session?.role === 'student') setActiveNav('schedule');
  }, [session?.role]);

  // ── Role switch between student and registrar
  async function switchToRegistrar() {
    if (!session) return;
    await handleLogin(session.studentId, 'registrar');
    setToast({ message: 'Switched to Registrar view.', kind: 'info' });
  }

  async function switchToStudent(studentId: string) {
    await handleLogin(studentId, 'student');
  }

  // ── Render: not logged in ──────────────────────────────────────────────────
  if (!session) {
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
              N
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
              Northgate State University
            </h1>
            <p style={{ fontSize: '0.875rem', color: '#5a6880', fontFamily: 'Inter, system-ui, sans-serif' }}>
              Student Information System
            </p>
          </div>

          <label
            style={{
              display: 'block',
              fontSize: '0.8rem',
              fontWeight: 600,
              color: '#5a6880',
              fontFamily: 'Inter, system-ui, sans-serif',
              marginBottom: '8px',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
            }}
          >
            Select Identity (Demo SSO)
          </label>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '16px' }}>
            {SEEDED_STUDENTS.map((s) => (
              <button
                key={s.id}
                onClick={() => handleLogin(s.id, 'student')}
                disabled={loggingIn}
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  padding: '10px 14px',
                  background: 'transparent',
                  border: '1px solid #d8dde6',
                  borderRadius: '4px',
                  cursor: loggingIn ? 'not-allowed' : 'pointer',
                  fontFamily: 'Inter, system-ui, sans-serif',
                  fontSize: '0.875rem',
                  color: '#1a2436',
                  opacity: loggingIn ? 0.6 : 1,
                  transition: 'background 0.12s ease, border-color 0.12s ease',
                }}
                onMouseEnter={(e) => {
                  if (!loggingIn) {
                    e.currentTarget.style.background = '#eef2f8';
                    e.currentTarget.style.borderColor = '#2c4a7c';
                  }
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.borderColor = '#d8dde6';
                }}
              >
                {s.label}
              </button>
            ))}
          </div>

          <div style={{ borderTop: '1px solid #d8dde6', paddingTop: '16px' }}>
            <button
              onClick={() => handleLogin('registrar-1', 'registrar')}
              disabled={loggingIn}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: '10px 14px',
                background: '#f5f7fa',
                border: '1px solid #d8dde6',
                borderRadius: '4px',
                cursor: loggingIn ? 'not-allowed' : 'pointer',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: '0.875rem',
                color: '#2c4a7c',
                fontWeight: 600,
                opacity: loggingIn ? 0.6 : 1,
              }}
            >
              Registrar View
            </button>
          </div>

          {loggingIn && (
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: '16px' }}>
              <Spinner size={20} />
            </div>
          )}

          {loginError && (
            <p style={{ color: '#c0392b', fontSize: '0.8rem', marginTop: '12px', fontFamily: 'Inter, system-ui, sans-serif' }}>
              {loginError}
            </p>
          )}
        </div>
      </div>
    );
  }

  // ── Render: logged in ──────────────────────────────────────────────────────

  const navItems = session.role === 'registrar' ? REGISTRAR_NAV : STUDENT_NAV;

  // Ensure activeNav is valid for the current role
  const validNavKeys = navItems.map((n) => n.key);
  const currentNav = validNavKeys.includes(activeNav) ? activeNav : navItems[0].key;

  // Top-right switcher dropdown
  const topRight = (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => setSwitcherOpen((o) => !o)}
        style={{
          background: 'rgba(240,236,221,0.12)',
          border: '1px solid rgba(240,236,221,0.25)',
          borderRadius: '4px',
          color: '#f0ecdd',
          padding: '5px 12px',
          cursor: 'pointer',
          fontFamily: 'Inter, system-ui, sans-serif',
          fontSize: '0.8rem',
          fontWeight: 500,
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}
      >
        {session.role === 'registrar' ? 'Registrar' : session.studentId}
        <span style={{ fontSize: '10px', opacity: 0.7 }}>▼</span>
      </button>

      {switcherOpen && (
        <div
          style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            right: 0,
            background: '#ffffff',
            border: '1px solid #d8dde6',
            borderRadius: '6px',
            boxShadow: '0 4px 16px rgba(0,0,0,0.14)',
            minWidth: '220px',
            zIndex: 200,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              padding: '8px 12px 6px',
              fontSize: '0.7rem',
              fontWeight: 700,
              color: '#8090a8',
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              fontFamily: 'Inter, system-ui, sans-serif',
              borderBottom: '1px solid #eef0f4',
            }}
          >
            Switch Student
          </div>
          {SEEDED_STUDENTS.map((s) => (
            <button
              key={s.id}
              onClick={() => switchToStudent(s.id)}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: '8px 12px',
                background: session.studentId === s.id && session.role === 'student' ? '#eef2f8' : 'transparent',
                border: 'none',
                cursor: 'pointer',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: '0.85rem',
                color: '#1a2436',
                fontWeight: session.studentId === s.id && session.role === 'student' ? 600 : 400,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = '#f0f3f8'; }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background =
                  session.studentId === s.id && session.role === 'student' ? '#eef2f8' : 'transparent';
              }}
            >
              {s.label}
            </button>
          ))}
          <div style={{ borderTop: '1px solid #eef0f4' }}>
            <button
              onClick={switchToRegistrar}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: '8px 12px',
                background: session.role === 'registrar' ? '#eef2f8' : 'transparent',
                border: 'none',
                cursor: 'pointer',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: '0.85rem',
                color: '#2c4a7c',
                fontWeight: session.role === 'registrar' ? 600 : 400,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = '#f0f3f8'; }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = session.role === 'registrar' ? '#eef2f8' : 'transparent';
              }}
            >
              Registrar View
            </button>
          </div>
          <div style={{ borderTop: '1px solid #eef0f4' }}>
            <button
              onClick={handleLogout}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: '8px 12px',
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: '0.85rem',
                color: '#c0392b',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = '#fef0f0'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
            >
              Sign Out
            </button>
          </div>
        </div>
      )}
    </div>
  );

  // ── Page routing
  function renderPage() {
    if (session!.role === 'registrar') {
      switch (currentNav) {
        case 'queue':    return <RequestQueue />;
        case 'catalog':  return <Catalog />;
        case 'sections': return <Sections />;
        default:         return <RequestQueue />;
      }
    }
    // Student role
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
        universityName="Northgate State University"
        role={session.role}
        navItems={navItems}
        activeNav={currentNav}
        onNavChange={(key) => {
          setActiveNav(key);
          setSwitcherOpen(false);
        }}
        topRight={topRight}
      >
        {renderPage()}
      </PortalLayout>

      {/* Floating Keel widget — only for students */}
      {session.role === 'student' && (
        <KeelWidget onEnrollmentComplete={handleEnrollmentComplete} />
      )}

      {toast && (
        <Toast message={toast.message} kind={toast.kind} onClose={() => setToast(null)} />
      )}
    </>
  );
}
