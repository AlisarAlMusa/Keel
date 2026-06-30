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
  width?: number | string;
}

export function Modal({ open, title, children, onClose, width = '440px' }: ModalProps) {
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
          width,
          maxWidth: '90vw',
          maxHeight: '90vh',
          overflowY: 'auto',
          boxSizing: 'border-box',
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

// ── Markdown rendering (lightweight, safe — no dangerouslySetInnerHTML) ─────────
//
// The agent replies in Markdown (## headings, **bold**, bullet/numbered lists,
// `inline code`). We render a deliberately small subset to React nodes so the
// chat bubble shows formatted text instead of literal `##`/`**`. No HTML is
// injected — every node is a real React element, so this is XSS-safe by default.

// Inline pass: **bold**, *italic* / _italic_, `code`. Returns React nodes.
function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // Split on bold / italic / inline-code while keeping the delimiters.
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*|_[^_]+_)/g;
  const parts = text.split(pattern);
  parts.forEach((part, i) => {
    if (!part) return;
    const key = `${keyPrefix}-i${i}`;
    if (part.startsWith('**') && part.endsWith('**')) {
      nodes.push(<strong key={key}>{part.slice(2, -2)}</strong>);
    } else if (part.startsWith('`') && part.endsWith('`')) {
      nodes.push(
        <code
          key={key}
          style={{
            fontFamily: '"JetBrains Mono", "Fira Code", monospace',
            fontSize: '0.92em',
            background: 'rgba(0,4,53,0.08)',
            padding: '1px 4px',
            borderRadius: '4px',
          }}
        >
          {part.slice(1, -1)}
        </code>,
      );
    } else if (
      (part.startsWith('*') && part.endsWith('*')) ||
      (part.startsWith('_') && part.endsWith('_'))
    ) {
      nodes.push(<em key={key}>{part.slice(1, -1)}</em>);
    } else {
      nodes.push(part);
    }
  });
  return nodes;
}

// Block pass: headings, ordered/unordered lists, paragraphs. Lines within a
// paragraph keep their soft breaks.
function renderMarkdown(src: string): React.ReactNode[] {
  const lines = src.replace(/\r\n/g, '\n').split('\n');
  const blocks: React.ReactNode[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let key = 0;

  const flushPara = () => {
    if (para.length === 0) return;
    blocks.push(
      <p key={`p${key++}`} style={{ margin: '0 0 8px' }}>
        {para.map((ln, i) => (
          <React.Fragment key={i}>
            {i > 0 && <br />}
            {renderInline(ln, `p${key}-${i}`)}
          </React.Fragment>
        ))}
      </p>,
    );
    para = [];
  };

  const flushList = () => {
    const current = list;
    if (!current) return;
    const style: React.CSSProperties = { margin: '0 0 8px', paddingLeft: '20px' };
    blocks.push(
      current.ordered ? (
        <ol key={`l${key++}`} style={style}>
          {current.items.map((it, i) => (
            <li key={i} style={{ marginBottom: '2px' }}>
              {renderInline(it, `l${key}-${i}`)}
            </li>
          ))}
        </ol>
      ) : (
        <ul key={`l${key++}`} style={style}>
          {current.items.map((it, i) => (
            <li key={i} style={{ marginBottom: '2px' }}>
              {renderInline(it, `l${key}-${i}`)}
            </li>
          ))}
        </ul>
      ),
    );
    list = null;
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);

    if (heading) {
      flushPara();
      flushList();
      const level = heading[1].length;
      blocks.push(
        <div
          key={`h${key++}`}
          style={{
            fontFamily: 'Fraunces, "Source Serif 4", Georgia, serif',
            fontWeight: 700,
            fontSize: level <= 1 ? '1.15em' : level === 2 ? '1.07em' : '1em',
            margin: '4px 0 6px',
          }}
        >
          {renderInline(heading[2], `h${key}`)}
        </div>,
      );
    } else if (bullet) {
      flushPara();
      if (!list || list.ordered) {
        flushList();
        list = { ordered: false, items: [] };
      }
      list.items.push(bullet[1]);
    } else if (numbered) {
      flushPara();
      if (!list || !list.ordered) {
        flushList();
        list = { ordered: true, items: [] };
      }
      list.items.push(numbered[1]);
    } else if (line.trim() === '') {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}

// ── StreamingText ─────────────────────────────────────────────────────────────

interface StreamingTextProps {
  text: string;
  isStreaming: boolean;
}

export function StreamingText({ text, isStreaming }: StreamingTextProps) {
  return (
    <div className="keel-md" style={{ position: 'relative', wordBreak: 'break-word' }}>
      {renderMarkdown(text)}
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
      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
        .keel-md > :last-child { margin-bottom: 0 !important; }`}</style>
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
