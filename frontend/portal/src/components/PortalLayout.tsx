/**
 * PortalLayout — top nav bar + left sidebar nav + main content area.
 * Applies the .sis-light skin class to the root.
 */

import React from 'react';

export interface NavItem {
  label: string;
  key: string;
}

interface PortalLayoutProps {
  universityName: string;
  role: string;
  navItems: NavItem[];
  activeNav: string;
  onNavChange: (key: string) => void;
  topRight: React.ReactNode;
  children: React.ReactNode;
}

export function PortalLayout({
  universityName,
  role,
  navItems,
  activeNav,
  onNavChange,
  topRight,
  children,
}: PortalLayoutProps) {
  return (
    <div
      className="sis-light"
      style={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: '100%',
        background: 'var(--bg)',
        color: 'var(--text)',
      }}
    >
      {/* Top nav bar */}
      <header
        style={{
          background: '#1a2e52',
          color: '#f0ecdd',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 24px',
          height: '52px',
          flexShrink: 0,
          borderBottom: '2px solid #142345',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {/* University crest placeholder */}
          <div
            style={{
              width: '30px',
              height: '30px',
              background: '#f0ecdd',
              borderRadius: '4px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '14px',
              fontWeight: 700,
              color: '#1a2e52',
              fontFamily: 'Source Serif 4, Georgia, serif',
              flexShrink: 0,
            }}
          >
            N
          </div>
          <span
            style={{
              fontFamily: 'Source Serif 4, Georgia, serif',
              fontSize: '1rem',
              fontWeight: 600,
              letterSpacing: '0.01em',
            }}
          >
            {universityName}
          </span>
          <span
            style={{
              fontSize: '0.75rem',
              color: '#8ba3c5',
              background: 'rgba(139,163,197,0.15)',
              padding: '2px 8px',
              borderRadius: '9999px',
              fontFamily: 'Inter, system-ui, sans-serif',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              fontWeight: 600,
            }}
          >
            {role === 'registrar' ? 'Registrar' : 'Student'}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {topRight}
        </div>
      </header>

      {/* Body: sidebar + content */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left sidebar */}
        <nav
          style={{
            width: '210px',
            flexShrink: 0,
            background: '#ffffff',
            borderRight: '1px solid var(--border)',
            padding: '16px 0',
            overflowY: 'auto',
          }}
        >
          {navItems.map((item) => {
            const isActive = item.key === activeNav;
            return (
              <button
                key={item.key}
                onClick={() => onNavChange(item.key)}
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  padding: '9px 20px',
                  background: isActive ? '#eef2f8' : 'transparent',
                  border: 'none',
                  borderLeft: isActive ? '3px solid #2c4a7c' : '3px solid transparent',
                  color: isActive ? '#2c4a7c' : 'var(--text-muted)',
                  fontFamily: 'Inter, system-ui, sans-serif',
                  fontSize: '0.875rem',
                  fontWeight: isActive ? 600 : 400,
                  cursor: 'pointer',
                  transition: 'all 0.12s ease',
                }}
              >
                {item.label}
              </button>
            );
          })}
        </nav>

        {/* Main content */}
        <main
          style={{
            flex: 1,
            padding: '28px 32px',
            overflowY: 'auto',
            background: 'var(--bg)',
          }}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
