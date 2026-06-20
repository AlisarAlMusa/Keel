import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import {
  Badge,
  Button,
  Card,
  Modal,
  Spinner,
  StreamingText,
  Tabs,
  Toast,
} from '@keel/ui';
import { sendChat, approveAction } from './api';
import type { ChatMessage, PlanData, RiskLevel, WorkloadLevel } from './types';

// ── helpers ────────────────────────────────────────────────────────────────────

function generateId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function riskBadgeVariant(risk: RiskLevel) {
  return risk === 'on_track' ? 'risk-ontrack' : 'risk-atrisk';
}
function riskBadgeLabel(risk: RiskLevel) {
  return risk === 'on_track' ? 'On Track' : 'At Risk';
}
function workloadBadgeVariant(load: WorkloadLevel) {
  const map: Record<WorkloadLevel, 'load-light' | 'load-medium' | 'load-heavy'> = {
    light: 'load-light',
    medium: 'load-medium',
    heavy: 'load-heavy',
  };
  return map[load];
}
function workloadBadgeLabel(load: WorkloadLevel) {
  const map: Record<WorkloadLevel, string> = {
    light: 'Light load',
    medium: 'Medium load',
    heavy: 'Heavy load',
  };
  return map[load];
}

// ── Thinking indicator ─────────────────────────────────────────────────────────

function ThinkingDots() {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '5px',
        padding: '4px 0',
      }}
      aria-label="Keel is thinking"
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            display: 'inline-block',
            width: '7px',
            height: '7px',
            borderRadius: '50%',
            background: 'var(--mahogany)',
            animation: `thinking-bounce 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
    </div>
  );
}

function KeelAvatar() {
  return (
    <div
      style={{
        width: '32px',
        height: '32px',
        borderRadius: '50% 50% 50% 6px',
        background: 'var(--oxford)',
        border: '1.5px solid var(--mahogany)',
        flexShrink: 0,
        overflow: 'hidden',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <img
        src="/static/creamy-keel-icon.png"
        alt=""
        aria-hidden
        width={32}
        height={32}
        style={{ objectFit: 'contain', borderRadius: '50%' }}
        onError={(e) => {
          const el = e.target as HTMLImageElement;
          el.style.display = 'none';
          (el.parentElement as HTMLDivElement).textContent = 'K';
          (el.parentElement as HTMLDivElement).style.color = 'var(--moonlight)';
          (el.parentElement as HTMLDivElement).style.fontSize = '13px';
          (el.parentElement as HTMLDivElement).style.fontFamily = 'Fraunces, Georgia, serif';
          (el.parentElement as HTMLDivElement).style.fontWeight = '700';
        }}
      />
    </div>
  );
}

// ── Single plan card ───────────────────────────────────────────────────────────

interface PlanCardProps {
  plan: PlanData;
  onApprove: (actionId: string, plan: PlanData) => void;
  actionId?: string;
  approvalPending: boolean;
}

function PlanCard({ plan, onApprove, actionId, approvalPending }: PlanCardProps) {
  return (
    <Card
      style={{
        marginTop: 'var(--sp-3)',
        padding: 'var(--sp-4)',
        background: 'var(--storm)',
        border: '1px solid var(--border)',
      }}
    >
      {/* Plan header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 'var(--sp-2)',
          marginBottom: 'var(--sp-3)',
        }}
      >
        <div>
          <div
            style={{
              fontFamily: 'Fraunces, "Source Serif 4", Georgia, serif',
              fontSize: 'var(--text-base)',
              fontWeight: 700,
              color: 'var(--moonlight)',
              marginBottom: '2px',
            }}
          >
            {plan.name}
          </div>
          <div
            style={{
              fontSize: 'var(--text-xs)',
              color: 'var(--text-muted)',
            }}
          >
            {plan.term} &middot; {plan.totalCredits} credits
          </div>
        </div>
        <div style={{ display: 'flex', gap: 'var(--sp-1)', flexShrink: 0 }}>
          <Badge
            variant={riskBadgeVariant(plan.risk)}
            label={riskBadgeLabel(plan.risk)}
          />
          <Badge
            variant={workloadBadgeVariant(plan.workload)}
            label={workloadBadgeLabel(plan.workload)}
          />
        </div>
      </div>

      {/* Course rows */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
          marginBottom: 'var(--sp-3)',
        }}
      >
        {plan.courses.map((course) => (
          <div
            key={course.code}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 'var(--sp-2)',
              padding: '6px var(--sp-3)',
              background: 'rgba(0, 4, 53, 0.35)',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', minWidth: 0 }}>
              <span
                style={{
                  fontFamily: '"JetBrains Mono", "Fira Code", "Courier New", monospace',
                  fontSize: 'var(--text-xs)',
                  color: 'var(--frost)',
                  fontWeight: 600,
                  flexShrink: 0,
                }}
              >
                {course.code}
                {course.section ? `-${course.section}` : ''}
              </span>
              <span
                style={{
                  fontSize: 'var(--text-xs)',
                  color: 'var(--text-muted)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {course.title}
              </span>
            </div>
            <Badge variant="via-keel" label={`${course.credits} cr`} />
          </div>
        ))}
      </div>

      {/* Explanation */}
      {plan.explanation && (
        <p
          style={{
            fontSize: 'var(--text-xs)',
            color: 'var(--text-muted)',
            fontStyle: 'italic',
            margin: '0 0 var(--sp-3)',
            lineHeight: 1.5,
          }}
        >
          {plan.explanation}
        </p>
      )}

      {/* Actions */}
      {actionId && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
          <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
            <Button variant="ghost" size="sm" style={{ flex: 1 }}>
              Save plan
            </Button>
            <Button variant="secondary" size="sm" style={{ flex: 1 }}>
              Set as active
            </Button>
          </div>
          <Button
            variant="primary"
            style={{ width: '100%', justifyContent: 'center' }}
            disabled={approvalPending}
            onClick={() => onApprove(actionId, plan)}
          >
            {approvalPending ? <><Spinner size={14} /> Awaiting approval…</> : 'Approve & enroll'}
          </Button>
        </div>
      )}
    </Card>
  );
}

// ── Multi-plan tabbed card ─────────────────────────────────────────────────────

interface PlanTabsCardProps {
  plans: PlanData[];
  onApprove: (actionId: string, plan: PlanData) => void;
  actionId?: string;
  approvalPending: boolean;
}

function PlanTabsCard({ plans, onApprove, actionId, approvalPending }: PlanTabsCardProps) {
  const [activeTab, setActiveTab] = useState(0);
  const activePlan = plans[activeTab];

  return (
    <div style={{ marginTop: 'var(--sp-3)' }}>
      <Tabs
        tabs={plans.map((_p, i) => `Option ${i + 1}`)}
        active={activeTab}
        onChange={setActiveTab}
      />
      {activePlan && (
        <PlanCard
          plan={activePlan}
          onApprove={onApprove}
          actionId={actionId}
          approvalPending={approvalPending}
        />
      )}
    </div>
  );
}

// ── Message bubble ─────────────────────────────────────────────────────────────

interface BubbleProps {
  msg: ChatMessage;
  isLatest: boolean;
  onApprove: (actionId: string, plan: PlanData) => void;
  approvalPending: boolean;
}

function MessageBubble({ msg, isLatest, onApprove, approvalPending }: BubbleProps) {
  const isStudent = msg.role === 'student';

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: isStudent ? 'flex-end' : 'flex-start',
        gap: 'var(--sp-1)',
        padding: '0 var(--sp-3)',
      }}
    >
      {/* Avatar + bubble row for keel messages */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 'var(--sp-2)',
          flexDirection: isStudent ? 'row-reverse' : 'row',
          width: '100%',
        }}
      >
        {!isStudent && <KeelAvatar />}
        <div
          style={{
            maxWidth: '80%',
            background: isStudent ? 'var(--steel)' : 'var(--moonlight)',
            border: 'none',
            borderRadius: isStudent
              ? '12px 12px 2px 12px'
              : '2px 12px 12px 12px',
            padding: 'var(--sp-2) var(--sp-3)',
            color: isStudent ? 'var(--moonlight)' : 'var(--oxford)',
            fontSize: 'var(--text-sm)',
            lineHeight: 1.55,
            boxShadow: isStudent ? 'none' : '0 2px 12px rgba(0,4,53,0.18)',
          }}
        >
          {msg.role === 'keel' ? (
            <StreamingText text={msg.text} isStreaming={false} />
          ) : (
            <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {msg.text}
            </span>
          )}
        </div>
      </div>

      {/* Plan card(s) attached to this message */}
      {msg.plans && msg.plans.length > 0 && (
        <div style={{ width: '100%', maxWidth: '340px' }}>
          {msg.plans.length === 1 ? (
            <PlanCard
              plan={msg.plans[0]}
              onApprove={onApprove}
              actionId={isLatest ? msg.actionId : undefined}
              approvalPending={approvalPending}
            />
          ) : (
            <PlanTabsCard
              plans={msg.plans}
              onApprove={onApprove}
              actionId={isLatest ? msg.actionId : undefined}
              approvalPending={approvalPending}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ── Confirm modal ──────────────────────────────────────────────────────────────

interface ConfirmModalProps {
  open: boolean;
  plan: PlanData | null;
  actionId: string;
  onConfirm: () => void;
  onCancel: () => void;
  loading: boolean;
}

function ConfirmModal({
  open,
  plan,
  actionId: _actionId,
  onConfirm,
  onCancel,
  loading,
}: ConfirmModalProps) {
  if (!plan) return null;
  return (
    <Modal open={open} title="Confirm enrollment" onClose={onCancel}>
      <p
        style={{
          margin: '0 0 var(--sp-4)',
          fontSize: 'var(--text-sm)',
          color: 'var(--text)',
          lineHeight: 1.55,
        }}
      >
        You are about to enroll in the following courses for{' '}
        <strong>{plan.term}</strong>:
      </p>
      <ul
        style={{
          margin: '0 0 var(--sp-4)',
          padding: '0 0 0 var(--sp-4)',
          fontSize: 'var(--text-sm)',
          color: 'var(--text)',
          lineHeight: 1.8,
        }}
      >
        {plan.courses.map((c) => (
          <li key={c.code}>
            <strong style={{ fontFamily: 'monospace' }}>{c.code}{c.section ? `-${c.section}` : ''}</strong>{' '}
            {c.title} &mdash; {c.credits} credits
            {c.term ? `, ${c.term}` : ''}
          </li>
        ))}
      </ul>
      <p
        style={{
          margin: '0 0 var(--sp-6)',
          fontSize: 'var(--text-sm)',
          color: 'var(--text-muted)',
        }}
      >
        Total: <strong style={{ color: 'var(--text)' }}>{plan.totalCredits} credits</strong>.
        This action will submit your enrollment request for registrar processing.
      </p>
      <div style={{ display: 'flex', gap: 'var(--sp-2)', justifyContent: 'flex-end' }}>
        <Button variant="ghost" onClick={onCancel} disabled={loading}>
          Cancel
        </Button>
        <Button variant="primary" onClick={onConfirm} loading={loading}>
          Confirm enrollment
        </Button>
      </div>
    </Modal>
  );
}

// ── Main ChatWidget ────────────────────────────────────────────────────────────

interface ChatWidgetProps {
  token: string;
  personaName?: string;
  storageKey?: string;
}

export function ChatWidget({ token, personaName = 'Keel Advisor', storageKey }: ChatWidgetProps) {
  const sessionId = useRef<string>(generateId());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Lazy initializer: restore from localStorage if available
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    if (!storageKey) return [];
    try {
      const saved = localStorage.getItem(storageKey);
      return saved ? (JSON.parse(saved) as ChatMessage[]) : [];
    } catch {
      return [];
    }
  });
  const [draft, setDraft] = useState('');
  const [thinking, setThinking] = useState(false);
  const [approvalPending, setApprovalPending] = useState(false);

  // Confirm modal state
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmPlan, setConfirmPlan] = useState<PlanData | null>(null);
  const [confirmActionId, setConfirmActionId] = useState('');
  const [confirmLoading, setConfirmLoading] = useState(false);

  // Toast
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' | 'info' } | null>(null);

  // Persist messages to localStorage whenever they change
  useEffect(() => {
    if (!storageKey || messages.length === 0) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(messages));
    } catch {
      // Storage quota — ignore silently
    }
  }, [messages, storageKey]);

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, thinking]);

  const handleSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || thinking || approvalPending) return;

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'student',
      text,
    };
    setMessages((prev) => [...prev, userMsg]);
    setDraft('');
    setThinking(true);

    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    try {
      const res = await sendChat(token, text, sessionId.current);

      const plans: PlanData[] = [];
      if (res.plans && res.plans.length > 0) {
        plans.push(...res.plans);
      } else if (res.plan) {
        plans.push(res.plan);
      }

      const keelMsg: ChatMessage = {
        id: generateId(),
        role: 'keel',
        text: res.response,
        plans: plans.length > 0 ? plans : undefined,
        actionId: res.action_id,
        pendingApproval: res.pending_approval,
      };
      setMessages((prev) => [...prev, keelMsg]);

      if (res.pending_approval) {
        setApprovalPending(true);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Something went wrong.';
      if (message !== 'UNAUTHORIZED') {
        setToast({ message: `Error: ${message}`, kind: 'error' });
      }
    } finally {
      setThinking(false);
    }
  }, [draft, thinking, approvalPending, token]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handleTextareaInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value);
    // Auto-grow up to 4 rows
    const el = e.target;
    el.style.height = 'auto';
    const lineHeight = 22;
    const maxHeight = lineHeight * 4 + 16; // 4 rows + padding
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  };

  const handleApproveClick = useCallback((actionId: string, plan: PlanData) => {
    setConfirmActionId(actionId);
    setConfirmPlan(plan);
    setConfirmOpen(true);
  }, []);

  const handleConfirmEnroll = useCallback(async () => {
    setConfirmLoading(true);
    try {
      await approveAction(token, confirmActionId);
      setConfirmOpen(false);
      setApprovalPending(false);
      setToast({ message: 'Enrollment submitted successfully.', kind: 'success' });

      const successMsg: ChatMessage = {
        id: generateId(),
        role: 'keel',
        text: 'Your enrollment request has been submitted. You will receive a confirmation email once the registrar processes it.',
      };
      setMessages((prev) => [...prev, successMsg]);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Approval failed.';
      if (message !== 'UNAUTHORIZED') {
        setToast({ message: `Approval failed: ${message}`, kind: 'error' });
      }
    } finally {
      setConfirmLoading(false);
    }
  }, [token, confirmActionId]);

  const composerDisabled = thinking || approvalPending;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        width: '100%',
        background: 'var(--oxford)',
        overflow: 'hidden',
      }}
    >
      {/* ── Header ── */}
      <header
        style={{
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sp-3)',
          padding: 'var(--sp-3) var(--sp-4)',
          background: 'var(--mahogany)',
          borderBottom: '1px solid rgba(0,0,0,0.2)',
        }}
      >
        {/* Icon */}
        <div
          style={{
            width: '32px',
            height: '32px',
            borderRadius: '50% 50% 50% 6px',
            background: 'var(--oxford)',
            border: '1.5px solid var(--mahogany)',
            flexShrink: 0,
            overflow: 'hidden',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <img
            src="/static/creamy-keel-icon.png"
            alt="Keel"
            width={28}
            height={28}
            style={{ objectFit: 'contain', borderRadius: '50%' }}
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = 'none';
            }}
          />
        </div>

        {/* Wordmark + persona */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontFamily: 'Fraunces, "Source Serif 4", Georgia, serif',
              fontSize: 'var(--text-base)',
              fontWeight: 700,
              color: 'var(--moonlight)',
              lineHeight: 1.1,
            }}
          >
            Keel
          </div>
          <div
            style={{
              fontSize: 'var(--text-xs)',
              color: 'var(--text-muted)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {personaName}
          </div>
        </div>

        {/* Online status dot */}
        <div
          title="Online"
          style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            background: 'var(--accent)',
            flexShrink: 0,
            animation: 'status-glow 2.4s ease-in-out infinite',
          }}
        />

        {/* Clear history button */}
        {messages.length > 0 && storageKey && (
          <button
            aria-label="Clear chat history"
            title="Clear history"
            onClick={() => {
              setMessages([]);
              try { localStorage.removeItem(storageKey); } catch { /* ignore */ }
            }}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'rgba(240,236,221,0.55)', fontSize: '11px',
              lineHeight: 1, padding: '2px 4px', flexShrink: 0,
              fontFamily: 'Inter, system-ui, sans-serif',
            }}
          >
            clear
          </button>
        )}

        {/* Close button */}
        <button
          aria-label="Close widget"
          onClick={() => window.parent.postMessage({ type: 'KEEL_CLOSE' }, '*')}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text-muted)',
            fontSize: '18px',
            lineHeight: 1,
            padding: 'var(--sp-1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-sm)',
            transition: 'color var(--transition-fast)',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.color = 'var(--moonlight)';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)';
          }}
        >
          &#x2715;
        </button>
      </header>

      {/* ── Message stream ── */}
      <div
        role="log"
        aria-live="polite"
        aria-label="Conversation"
        style={{
          flex: 1,
          overflowY: 'auto',
          overflowX: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--sp-3)',
          paddingTop: 'var(--sp-4)',
          paddingBottom: 'var(--sp-3)',
          scrollbarWidth: 'thin',
          scrollbarColor: 'var(--steel) transparent',
        }}
      >
        {messages.length === 0 && !thinking && (
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 'var(--sp-3)',
              padding: 'var(--sp-8) var(--sp-6)',
              textAlign: 'center',
              color: 'var(--text-muted)',
            }}
          >
            <div
              style={{
                width: '56px',
                height: '56px',
                borderRadius: '50% 50% 50% 10px',
                background: 'var(--oxford)',
                border: '2px solid var(--mahogany)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                overflow: 'hidden',
              }}
            >
              <img
                src="/static/creamy-keel-icon.png"
                alt=""
                aria-hidden
                width={52}
                height={52}
                style={{ objectFit: 'contain' }}
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none';
                  ((e.target as HTMLImageElement).parentElement as HTMLDivElement).textContent = 'K';
                }}
              />
            </div>
            <div>
              <div
                style={{
                  fontFamily: 'Fraunces, "Source Serif 4", Georgia, serif',
                  fontSize: 'var(--text-base)',
                  color: 'var(--text)',
                  fontWeight: 600,
                  marginBottom: 'var(--sp-1)',
                }}
              >
                Session started
              </div>
              <div style={{ fontSize: 'var(--text-sm)' }}>
                Type a message to get started.
              </div>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble
            key={msg.id}
            msg={msg}
            isLatest={i === messages.length - 1}
            onApprove={handleApproveClick}
            approvalPending={approvalPending}
          />
        ))}

        {thinking && (
          <div style={{ padding: '0 var(--sp-3)', display: 'flex', alignItems: 'flex-start', gap: 'var(--sp-2)' }}>
            <KeelAvatar />
            <div
              style={{
                display: 'inline-flex',
                background: 'var(--moonlight)',
                borderRadius: '2px 12px 12px 12px',
                padding: 'var(--sp-2) var(--sp-3)',
                boxShadow: '0 2px 12px rgba(0,4,53,0.18)',
              }}
            >
              <ThinkingDots />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* ── Composer ── */}
      <div
        style={{
          flexShrink: 0,
          borderTop: '1px solid var(--border)',
          background: 'var(--storm)',
          padding: 'var(--sp-3) var(--sp-4)',
          display: 'flex',
          gap: 'var(--sp-2)',
          alignItems: 'flex-end',
        }}
      >
        {approvalPending && (
          <div
            style={{
              position: 'absolute',
              bottom: '72px',
              left: 'var(--sp-4)',
              right: 'var(--sp-4)',
              background: 'var(--risk-atrisk)',
              color: '#fff',
              fontSize: 'var(--text-xs)',
              padding: '6px var(--sp-3)',
              borderRadius: 'var(--radius-sm)',
              textAlign: 'center',
            }}
          >
            Approve the enrollment plan above before sending more messages.
          </div>
        )}
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={handleTextareaInput}
          onKeyDown={handleKeyDown}
          placeholder={
            approvalPending
              ? 'Approve the plan above first…'
              : 'Ask about courses, plans, graduation…'
          }
          disabled={composerDisabled}
          rows={1}
          aria-label="Message"
          style={{
            flex: 1,
            resize: 'none',
            background: 'var(--input-bg)',
            color: composerDisabled ? 'var(--text-muted)' : 'var(--input-text)',
            border: '1px solid var(--input-border)',
            borderRadius: 'var(--radius-md)',
            padding: 'var(--sp-2) var(--sp-3)',
            fontSize: 'var(--text-sm)',
            fontFamily: 'Inter, system-ui, sans-serif',
            outline: 'none',
            lineHeight: '22px',
            overflowY: 'auto',
            cursor: composerDisabled ? 'not-allowed' : 'text',
            opacity: composerDisabled ? 0.6 : 1,
            transition: 'border-color var(--transition-fast)',
          }}
          onFocus={(e) => {
            if (!composerDisabled) {
              e.currentTarget.style.borderColor = 'var(--accent)';
            }
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = 'var(--input-border)';
          }}
        />
        <button
          onClick={handleSend}
          disabled={composerDisabled || !draft.trim()}
          aria-label="Send message"
          style={{
            flexShrink: 0,
            width: '36px',
            height: '36px',
            borderRadius: 'var(--radius-md)',
            border: 'none',
            background:
              composerDisabled || !draft.trim()
                ? 'var(--steel)'
                : 'var(--mahogany)',
            color:
              composerDisabled || !draft.trim()
                ? 'var(--text-muted)'
                : 'var(--moonlight)',
            cursor:
              composerDisabled || !draft.trim() ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'background var(--transition-fast), color var(--transition-fast)',
            fontSize: '16px',
          }}
        >
          {thinking ? (
            <Spinner size={16} />
          ) : (
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          )}
        </button>
      </div>

      {/* ── Confirm modal ── */}
      <ConfirmModal
        open={confirmOpen}
        plan={confirmPlan}
        actionId={confirmActionId}
        onConfirm={handleConfirmEnroll}
        onCancel={() => setConfirmOpen(false)}
        loading={confirmLoading}
      />

      {/* ── Toast ── */}
      {toast && (
        <Toast
          message={toast.message}
          kind={toast.kind}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}
