/**
 * Keel shared UI primitives (frontend.md §3).
 * Used by widget, admin, and portal — same tokens, three skins.
 */
import React, { useEffect } from 'react';

// ── Button ────────────────────────────────────────────────────────────────────

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
  size?: 'sm' | 'md';
}

export function Button({
  variant = 'primary',
  loading = false,
  size = 'md',
  children,
  disabled,
  style,
  ...rest
}: ButtonProps) {
  const base: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 'var(--sp-2)',
    fontFamily: 'Inter, IBM Plex Sans, system-ui, sans-serif',
    fontWeight: 600,
    fontSize: size === 'sm' ? 'var(--text-sm)' : 'var(--text-base)',
    padding: size === 'sm' ? 'var(--sp-1) var(--sp-3)' : 'var(--sp-2) var(--sp-4)',
    borderRadius: 'var(--radius-md)',
    border: 'none',
    cursor: loading || disabled ? 'not-allowed' : 'pointer',
    opacity: loading || disabled ? 0.6 : 1,
    transition: 'background var(--transition-fast), opacity var(--transition-fast)',
    whiteSpace: 'nowrap',
  };

  const variants: Record<ButtonVariant, React.CSSProperties> = {
    primary: {
      background: 'var(--btn-primary-bg)',
      color: 'var(--btn-primary-text)',
    },
    secondary: {
      background: 'var(--btn-secondary-bg)',
      border: '1px solid var(--btn-secondary-border)',
      color: 'var(--btn-secondary-text)',
    },
    ghost: {
      background: 'transparent',
      color: 'var(--text-muted)',
    },
    danger: {
      background: '#c0392b',
      color: '#fff',
    },
  };

  return (
    <button
      disabled={disabled || loading}
      style={{ ...base, ...variants[variant], ...style }}
      {...rest}
    >
      {loading ? <Spinner size={14} /> : null}
      {children}
    </button>
  );
}

// ── Badge ─────────────────────────────────────────────────────────────────────

type BadgeVariant =
  | 'via-keel'
  | 'risk-ontrack'
  | 'risk-atrisk'
  | 'load-light'
  | 'load-medium'
  | 'load-heavy'
  | 'status-pending'
  | 'status-approved'
  | 'status-rejected'
  | 'active';

const BADGE_COLORS: Record<BadgeVariant, { bg: string; text: string }> = {
  'via-keel':        { bg: 'var(--oxford)',       text: 'var(--moonlight)' },
  'risk-ontrack':    { bg: 'var(--risk-ontrack)', text: '#fff' },
  'risk-atrisk':     { bg: 'var(--risk-atrisk)',  text: '#fff' },
  'load-light':      { bg: 'var(--load-light)',   text: 'var(--oxford)' },
  'load-medium':     { bg: 'var(--load-medium)',  text: 'var(--moonlight)' },
  'load-heavy':      { bg: 'var(--load-heavy)',   text: '#fff' },
  'status-pending':  { bg: 'var(--status-pending)', text: 'var(--oxford)' },
  'status-approved': { bg: 'var(--status-approved)', text: '#fff' },
  'status-rejected': { bg: 'var(--status-rejected)', text: '#fff' },
  'active':          { bg: 'var(--accent)',        text: 'var(--oxford)' },
};

interface BadgeProps {
  variant: BadgeVariant;
  label: string;
  title?: string;
}

export function Badge({ variant, label, title }: BadgeProps) {
  const { bg, text } = BADGE_COLORS[variant];
  return (
    <span
      title={title}
      style={{
        display: 'inline-block',
        background: bg,
        color: text,
        fontSize: 'var(--text-xs)',
        fontWeight: 600,
        fontFamily: 'Inter, system-ui, sans-serif',
        padding: '2px 8px',
        borderRadius: '9999px',
        whiteSpace: 'nowrap',
        letterSpacing: '0.03em',
      }}
    >
      {label}
    </span>
  );
}

// ── Card / Panel ──────────────────────────────────────────────────────────────

export function Card({
  children,
  style,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-lg)',
        padding: 'var(--sp-4)',
        boxShadow: 'var(--shadow)',
        ...style,
      }}
      {...rest}
    >
      {children}
    </div>
  );
}

// ── Field (label + input) ─────────────────────────────────────────────────────

interface FieldProps {
  label: string;
  error?: string;
  children: React.ReactNode;
}

export function Field({ label, error, children }: FieldProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-1)' }}>
      <label
        style={{
          fontSize: 'var(--text-sm)',
          fontWeight: 600,
          color: 'var(--text-muted)',
          fontFamily: 'Inter, system-ui, sans-serif',
        }}
      >
        {label}
      </label>
      {children}
      {error && (
        <span style={{ fontSize: 'var(--text-xs)', color: '#c0392b' }}>{error}</span>
      )}
    </div>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      style={{
        background: 'var(--input-bg)',
        color: 'var(--input-text)',
        border: '1px solid var(--input-border)',
        borderRadius: 'var(--radius-md)',
        padding: 'var(--sp-2) var(--sp-3)',
        fontSize: 'var(--text-base)',
        fontFamily: 'Inter, system-ui, sans-serif',
        outline: 'none',
        width: '100%',
      }}
      {...props}
    />
  );
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      style={{
        background: 'var(--input-bg)',
        color: 'var(--input-text)',
        border: '1px solid var(--input-border)',
        borderRadius: 'var(--radius-md)',
        padding: 'var(--sp-2) var(--sp-3)',
        fontSize: 'var(--text-base)',
        fontFamily: 'Inter, system-ui, sans-serif',
        outline: 'none',
        width: '100%',
        resize: 'vertical',
      }}
      {...props}
    />
  );
}

// ── Table ─────────────────────────────────────────────────────────────────────

interface TableProps {
  headers: string[];
  rows: React.ReactNode[][];
  emptyMessage?: string;
}

export function Table({ headers, rows, emptyMessage = 'No data.' }: TableProps) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
        <thead>
          <tr>
            {headers.map((h) => (
              <th
                key={h}
                style={{
                  textAlign: 'left',
                  padding: 'var(--sp-2) var(--sp-3)',
                  borderBottom: '2px solid var(--border)',
                  color: 'var(--text-muted)',
                  fontWeight: 600,
                  fontFamily: 'Inter, system-ui, sans-serif',
                  whiteSpace: 'nowrap',
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={headers.length}
                style={{
                  padding: 'var(--sp-8)',
                  textAlign: 'center',
                  color: 'var(--text-muted)',
                }}
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr
                key={i}
                style={{
                  borderBottom: '1px solid var(--border)',
                  transition: 'background var(--transition-fast)',
                }}
              >
                {row.map((cell, j) => (
                  <td
                    key={j}
                    style={{
                      padding: 'var(--sp-2) var(--sp-3)',
                      color: 'var(--text)',
                      verticalAlign: 'middle',
                    }}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── Spinner ───────────────────────────────────────────────────────────────────

export function Spinner({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      style={{ animation: 'spin 0.8s linear infinite', flexShrink: 0 }}
    >
      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.25" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

// ── EmptyState ────────────────────────────────────────────────────────────────

interface EmptyStateProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 'var(--sp-3)',
        padding: 'var(--sp-12)',
        textAlign: 'center',
        color: 'var(--text-muted)',
      }}
    >
      <div style={{ fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--text)' }}>
        {title}
      </div>
      {description && <div style={{ fontSize: 'var(--text-sm)' }}>{description}</div>}
      {action}
    </div>
  );
}

// ── Toast ─────────────────────────────────────────────────────────────────────

interface ToastProps {
  message: string;
  kind: 'success' | 'error' | 'info';
  onClose: () => void;
}

export function Toast({ message, kind, onClose }: ToastProps) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);

  const bg = kind === 'success' ? '#2d7a5a' : kind === 'error' ? '#c0392b' : 'var(--storm)';
  return (
    <div
      role="alert"
      style={{
        position: 'fixed',
        bottom: 'var(--sp-6)',
        left: '50%',
        transform: 'translateX(-50%)',
        background: bg,
        color: '#fff',
        padding: 'var(--sp-3) var(--sp-6)',
        borderRadius: 'var(--radius-md)',
        boxShadow: '0 4px 20px rgba(0,0,0,0.3)',
        zIndex: 9999,
        fontSize: 'var(--text-sm)',
        fontFamily: 'Inter, system-ui, sans-serif',
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--sp-3)',
      }}
    >
      {message}
      <button
        onClick={onClose}
        style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer', fontSize: '16px' }}
      >
        ×
      </button>
    </div>
  );
}

// ── Modal ─────────────────────────────────────────────────────────────────────

interface ModalProps {
  open: boolean;
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}

export function Modal({ open, title, children, onClose }: ModalProps) {
  if (!open) return null;
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(2,18,47,0.65)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 10000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: 'var(--surface)',
          borderRadius: 'var(--radius-lg)',
          padding: 'var(--sp-6)',
          width: '440px',
          maxWidth: '90vw',
          boxShadow: '0 16px 48px rgba(2,18,47,0.45)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3
          style={{
            margin: '0 0 var(--sp-4)',
            fontSize: 'var(--text-lg)',
            color: 'var(--text)',
            fontFamily: 'Fraunces, Source Serif 4, Georgia, serif',
          }}
        >
          {title}
        </h3>
        {children}
      </div>
    </div>
  );
}

// ── StreamingText ─────────────────────────────────────────────────────────────

interface StreamingTextProps {
  text: string;
  isStreaming: boolean;
}

export function StreamingText({ text, isStreaming }: StreamingTextProps) {
  return (
    <div style={{ position: 'relative' }}>
      <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{text}</span>
      {isStreaming && (
        <span
          style={{
            display: 'inline-block',
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            background: 'var(--accent)',
            marginLeft: '4px',
            verticalAlign: 'middle',
            animation: 'pulse 1s ease-in-out infinite',
          }}
        />
      )}
      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }`}</style>
    </div>
  );
}

// ── Tabs ──────────────────────────────────────────────────────────────────────

interface TabsProps {
  tabs: string[];
  active: number;
  onChange: (i: number) => void;
}

export function Tabs({ tabs, active, onChange }: TabsProps) {
  return (
    <div style={{ display: 'flex', borderBottom: '2px solid var(--border)', gap: 0 }}>
      {tabs.map((tab, i) => (
        <button
          key={tab}
          onClick={() => onChange(i)}
          style={{
            background: 'none',
            border: 'none',
            padding: 'var(--sp-2) var(--sp-4)',
            cursor: 'pointer',
            fontFamily: 'Inter, system-ui, sans-serif',
            fontSize: 'var(--text-sm)',
            fontWeight: 600,
            color: active === i ? 'var(--accent)' : 'var(--text-muted)',
            borderBottom: active === i ? '2px solid var(--accent)' : '2px solid transparent',
            marginBottom: '-2px',
            transition: 'color var(--transition-fast)',
          }}
        >
          {tab}
        </button>
      ))}
    </div>
  );
}
