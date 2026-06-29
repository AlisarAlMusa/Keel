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
import { sendChat, approveAction, rejectAction, getNotifications, saveGradPlan } from './api';
import type {
  ChatMessage,
  GradPlanCard,
  PlanData,
  RiskLevel,
  SectionOptionsCard,
  SectionSchedule,
  WidgetCard,
  WorkloadLevel,
} from './types';

// ── helpers ────────────────────────────────────────────────────────────────────

function generateId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// Primary action button used INSIDE the light (moonlight) cards — plan/grad-plan/
// sections. The shared @keel/ui <Button variant="primary"> resolves to a moonlight
// background under the widget's dark skin, which is invisible on a moonlight card. This
// matches the section "Choose" button instead: solid oxford fill, moonlight text.
function CardButton({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        marginTop: '6px',
        width: '100%',
        padding: '9px',
        borderRadius: '6px',
        border: 'none',
        background: disabled ? 'rgba(75,46,10,0.35)' : 'var(--mahogany)',
        color: 'var(--moonlight)',
        fontSize: '0.8rem',
        fontWeight: 600,
        cursor: disabled ? 'default' : 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '6px',
      }}
    >
      {children}
    </button>
  );
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
  onEnroll: (plan: PlanData) => void;
  actionId?: string;
  decisionPending: boolean;
  enrollDisabled: boolean;
}

function PlanCard({
  plan,
  onApprove,
  onEnroll,
  actionId,
  decisionPending,
  enrollDisabled,
}: PlanCardProps) {
  return (
    <Card
      style={{
        marginTop: 'var(--sp-3)',
        padding: 'var(--sp-4)',
        background: 'var(--moonlight)',
        border: '1px solid rgba(0,4,53,0.12)',
        boxShadow: '0 2px 12px rgba(0,4,53,0.12)',
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
              color: 'var(--oxford)',
              marginBottom: '2px',
            }}
          >
            {plan.name}
          </div>
          <div
            style={{
              fontSize: 'var(--text-xs)',
              color: 'rgba(0,4,53,0.6)',
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
              background: 'rgba(0, 4, 53, 0.05)',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', minWidth: 0 }}>
              <span
                style={{
                  fontFamily: '"JetBrains Mono", "Fira Code", "Courier New", monospace',
                  fontSize: 'var(--text-xs)',
                  color: 'var(--oxford)',
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
                  color: 'rgba(0,4,53,0.65)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {course.title}
              </span>
            </div>
            <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
              {course.requirement && <Badge variant="active" label={course.requirement} />}
              <Badge variant="via-keel" label={`${course.credits} cr`} />
            </div>
          </div>
        ))}
      </div>

      {/* Explanation */}
      {plan.explanation && (
        <p
          style={{
            fontSize: 'var(--text-xs)',
            color: 'rgba(0,4,53,0.65)',
            fontStyle: 'italic',
            margin: '0 0 var(--sp-3)',
            lineHeight: 1.5,
          }}
        >
          {plan.explanation}
        </p>
      )}

      {/* Actions */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
        {actionId ? (
          // An enrollment is already staged for this plan → final approval gate.
          <CardButton disabled={decisionPending} onClick={() => onApprove(actionId, plan)}>
            {decisionPending ? <><Spinner size={14} /> Working…</> : 'Approve & enroll'}
          </CardButton>
        ) : (
          // A proposed candidate → ask Keel to stage enrollment for this plan.
          <CardButton disabled={enrollDisabled} onClick={() => onEnroll(plan)}>
            Enroll in this plan
          </CardButton>
        )}
      </div>
    </Card>
  );
}

// ── Multi-plan tabbed card ─────────────────────────────────────────────────────

interface PlanTabsCardProps {
  plans: PlanData[];
  onApprove: (actionId: string, plan: PlanData) => void;
  onEnroll: (plan: PlanData) => void;
  actionId?: string;
  decisionPending: boolean;
  enrollDisabled: boolean;
}

function PlanTabsCard({
  plans,
  onApprove,
  onEnroll,
  actionId,
  decisionPending,
  enrollDisabled,
}: PlanTabsCardProps) {
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
          onEnroll={onEnroll}
          actionId={actionId}
          decisionPending={decisionPending}
          enrollDisabled={enrollDisabled}
        />
      )}
    </div>
  );
}

// ── Generic approval card (non-plan actions: petition, graduation, escalation) ──

interface GenericApprovalCardProps {
  onDecide: (decision: 'approve' | 'reject') => void;
  pending: boolean;
}

function GenericApprovalCard({ onDecide, pending }: GenericApprovalCardProps) {
  return (
    <Card
      style={{
        marginTop: 'var(--sp-3)',
        padding: 'var(--sp-3)',
        background: 'var(--storm)',
        border: '1px solid var(--border)',
      }}
    >
      <div
        style={{
          fontSize: 'var(--text-xs)',
          color: 'var(--text-muted)',
          marginBottom: 'var(--sp-2)',
        }}
      >
        This action needs your approval before Keel does anything.
      </div>
      <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
        <Button
          variant="ghost"
          size="sm"
          style={{ flex: 1, justifyContent: 'center' }}
          disabled={pending}
          onClick={() => onDecide('reject')}
        >
          Decline
        </Button>
        <Button
          variant="primary"
          size="sm"
          style={{ flex: 1, justifyContent: 'center' }}
          disabled={pending}
          onClick={() => onDecide('approve')}
        >
          {pending ? <><Spinner size={14} /> Working…</> : 'Approve'}
        </Button>
      </div>
    </Card>
  );
}

// ── Section options card (read-only; the agent picks, the student confirms) ──────

function SectionOptionsView({
  card,
  onChoose,
  disabled,
}: {
  card: SectionOptionsCard;
  onChoose: (card: SectionOptionsCard, schedule: SectionSchedule) => void;
  disabled: boolean;
}) {
  const [sel, setSel] = useState(0);
  const schedules = card.schedules;
  const active = schedules[Math.min(sel, Math.max(0, schedules.length - 1))];
  return (
    <div
      style={{
        width: '100%',
        maxWidth: '420px',
        background: 'var(--moonlight)',
        border: '1px solid rgba(0,4,53,0.12)',
        borderRadius: '10px',
        padding: 'var(--sp-3)',
        boxShadow: '0 2px 12px rgba(0,4,53,0.12)',
      }}
    >
      <div style={{ fontWeight: 700, fontSize: 'var(--text-sm)', color: 'var(--oxford)' }}>
        Suggested schedules · {card.term}
      </div>
      {card.prefSummary && (
        <div style={{ fontSize: '0.72rem', color: 'rgba(0,4,53,0.6)', marginBottom: 'var(--sp-1)' }}>
          Matching your preferences: {card.prefSummary}
        </div>
      )}

      {schedules.length === 0 || !active ? (
        <div style={{ fontSize: '0.78rem', color: '#c0392b', marginTop: 'var(--sp-2)' }}>
          No conflict-free schedule fits your preferences for these courses.
        </div>
      ) : (
        <>
          {/* Paginate the options (one shown at a time) — matches the plan card. */}
          {schedules.length > 1 && (
            <div style={{ marginTop: 'var(--sp-2)' }}>
              <Tabs
                tabs={schedules.map((_s, i) => `Option ${i + 1}`)}
                active={Math.min(sel, schedules.length - 1)}
                onChange={setSel}
              />
            </div>
          )}
          <div
            key={active.id}
            style={{
              marginTop: 'var(--sp-2)',
              border: '1px solid rgba(0,4,53,0.12)',
              borderRadius: '8px',
              padding: 'var(--sp-2)',
            }}
          >
            {active.items.map((it) => (
              <div
                key={it.section_id}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '0.78rem',
                  color: 'var(--oxford)',
                  padding: '2px 0',
                }}
              >
                <span style={{ minWidth: 0 }}>
                  <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{it.code}</span>
                  {it.title ? <span> · {it.title}</span> : null}
                  <span style={{ display: 'block', color: 'rgba(0,4,53,0.58)' }}>
                    {it.when} · {it.instructor}
                  </span>
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: '4px', whiteSpace: 'nowrap', flexShrink: 0 }}>
                  {it.requirement && <Badge variant="active" label={it.requirement} />}
                  {typeof it.credits === 'number' && <Badge variant="via-keel" label={`${it.credits} cr`} />}
                  <span style={{ color: 'rgba(0,4,53,0.55)' }}>{it.seats} seats</span>
                </span>
              </div>
            ))}
            <button
              onClick={() => onChoose(card, active)}
              disabled={disabled}
              style={{
                marginTop: '8px',
                width: '100%',
                padding: '8px',
                borderRadius: '6px',
                border: 'none',
                background: disabled ? 'rgba(75,46,10,0.35)' : 'var(--mahogany)',
                color: 'var(--moonlight)',
                fontSize: '0.78rem',
                fontWeight: 600,
                cursor: disabled ? 'default' : 'pointer',
              }}
            >
              Choose {active.label}
            </button>
          </div>
        </>
      )}

      {card.unavailable.length > 0 && (
        <div style={{ marginTop: 'var(--sp-2)', borderTop: '1px solid rgba(0,4,53,0.08)', paddingTop: 'var(--sp-2)' }}>
          {card.unavailable.map((u) => (
            <div key={u.code} style={{ fontSize: '0.74rem', color: '#c0392b' }}>
              ❌ <span style={{ fontFamily: 'monospace' }}>{u.code}</span> — {u.reason}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Graduation-plan cards (2–3 variants; tabbed; informational roadmap) ──────────

const WORKLOAD_DOT: Record<string, string> = { light: '#2e7d32', medium: '#b8860b', heavy: '#c0392b' };

function GradPlanView({
  cards,
  onSave,
  savingPlanId,
}: {
  cards: GradPlanCard[];
  onSave: (card: GradPlanCard) => void;
  savingPlanId: string | null;
}) {
  const [sel, setSel] = useState(0);
  const card = cards[Math.min(sel, cards.length - 1)];
  return (
    <div
      style={{
        width: '100%',
        maxWidth: '420px',
        background: 'var(--moonlight)',
        border: '1px solid rgba(0,4,53,0.12)',
        borderRadius: '10px',
        padding: 'var(--sp-3)',
        boxShadow: '0 2px 12px rgba(0,4,53,0.12)',
      }}
    >
      {/* Variant tabs */}
      {cards.length > 1 && (
        <div style={{ display: 'flex', gap: '6px', marginBottom: 'var(--sp-2)' }}>
          {cards.map((c, i) => (
            <button
              key={c.id}
              onClick={() => setSel(i)}
              style={{
                flex: 1,
                padding: '5px 6px',
                borderRadius: '6px',
                border: i === sel ? '1.5px solid var(--oxford)' : '1px solid rgba(0,4,53,0.2)',
                background: i === sel ? 'var(--oxford)' : 'transparent',
                color: i === sel ? 'var(--moonlight)' : 'var(--oxford)',
                fontSize: '0.72rem',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {c.label}
            </button>
          ))}
        </div>
      )}
      <div style={{ fontSize: '0.74rem', color: 'rgba(0,4,53,0.6)', marginBottom: 'var(--sp-1)' }}>
        {card.blurb}
      </div>
      <div style={{ fontWeight: 700, fontSize: 'var(--text-sm)', color: 'var(--oxford)' }}>
        {card.termsToGrad} terms · graduate {card.graduates}
      </div>
      {card.heaviestTerm && (
        <div style={{ fontSize: '0.72rem', color: 'rgba(0,4,53,0.55)', marginBottom: 'var(--sp-2)' }}>
          Heaviest term: {card.heaviestTerm}
        </div>
      )}
      {card.terms.map((t) => (
        <div key={t.term} style={{ borderTop: '1px solid rgba(0,4,53,0.08)', padding: '5px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600, fontSize: '0.78rem', color: 'var(--oxford)' }}>
              {t.term}
              {t.status && <Badge variant={t.status === 'registered' ? 'active' : 'via-keel'} label={t.status} />}
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '0.72rem', color: 'rgba(0,4,53,0.6)' }}>
              {t.credits} cr
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: WORKLOAD_DOT[t.workload] ?? '#999', display: 'inline-block' }} title={`${t.workload} load`} />
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '5px' }}>
            {t.courses.map((course) => (
              <div
                key={`${t.term}-${course.code}`}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '4px 6px',
                  borderRadius: '6px',
                  background: 'rgba(0,4,53,0.04)',
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '0.74rem', color: 'var(--oxford)', fontWeight: 700 }}>
                    <span style={{ fontFamily: 'monospace' }}>{course.code}</span>
                    <span style={{ fontWeight: 500 }}> · {course.title}</span>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                  {course.requirement && <Badge variant="active" label={course.requirement} />}
                  <Badge variant="via-keel" label={`${course.credits} cr`} />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
      {card.saved ? (
        <div
          style={{
            marginTop: 'var(--sp-2)',
            textAlign: 'center',
            fontSize: '0.72rem',
            fontWeight: 600,
            color: 'rgba(0,4,53,0.55)',
          }}
        >
          ✓ Saved graduation plan
        </div>
      ) : (
        <CardButton disabled={savingPlanId === card.id} onClick={() => onSave(card)}>
          {savingPlanId === card.id ? <><Spinner size={14} /> Saving…</> : 'Save this graduation plan'}
        </CardButton>
      )}
    </div>
  );
}

// ── Message bubble ─────────────────────────────────────────────────────────────

interface BubbleProps {
  msg: ChatMessage;
  isLatest: boolean;
  onApprove: (actionId: string, plan: PlanData) => void;
  onEnroll: (plan: PlanData) => void;
  onChooseSchedule: (card: SectionOptionsCard, schedule: SectionSchedule) => void;
  onSaveGradPlan: (card: GradPlanCard) => void;
  onGenericDecide: (actionId: string, decision: 'approve' | 'reject') => void;
  decisionPending: boolean;
  enrollDisabled: boolean;
  savingGradPlanId: string | null;
}

function MessageBubble({
  msg,
  isLatest,
  onApprove,
  onEnroll,
  onChooseSchedule,
  onSaveGradPlan,
  onGenericDecide,
  decisionPending,
  enrollDisabled,
  savingGradPlanId,
}: BubbleProps) {
  const isStudent = msg.role === 'student';
  const allCards: WidgetCard[] = msg.plans ?? [];
  const sectionCards = allCards.filter((c): c is SectionOptionsCard => c.kind === 'sections');
  const gradCards = allCards.filter((c): c is GradPlanCard => c.kind === 'gradplan');
  const planCards = allCards.filter(
    (c): c is PlanData => c.kind !== 'sections' && c.kind !== 'gradplan',
  );
  const hasPlans = planCards.length > 0;
  const showGenericApproval =
    isLatest && !isStudent && !hasPlans && !!msg.actionId && !!msg.pendingApproval;

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

      {/* Section schedules — the agent recommends; the student picks one to enroll */}
      {sectionCards.map((sc) => (
        <SectionOptionsView
          key={sc.id}
          card={sc}
          onChoose={onChooseSchedule}
          disabled={!isLatest || enrollDisabled}
        />
      ))}

      {/* Graduation-plan variants — tabbed roadmap */}
      {gradCards.length > 0 && (
        <GradPlanView cards={gradCards} onSave={onSaveGradPlan} savingPlanId={savingGradPlanId} />
      )}

      {/* Plan card(s) attached to this message */}
      {planCards.length > 0 && (
        <div style={{ width: '100%', maxWidth: '420px' }}>
          {planCards.length === 1 ? (
            <PlanCard
              plan={planCards[0]}
              onApprove={onApprove}
              onEnroll={onEnroll}
              actionId={isLatest ? msg.actionId : undefined}
              decisionPending={decisionPending}
              enrollDisabled={enrollDisabled}
            />
          ) : (
            <PlanTabsCard
              plans={planCards}
              onApprove={onApprove}
              onEnroll={onEnroll}
              actionId={isLatest ? msg.actionId : undefined}
              decisionPending={decisionPending}
              enrollDisabled={enrollDisabled}
            />
          )}
        </div>
      )}

      {/* Generic approval card for non-plan actions (petition, graduation, etc.) */}
      {showGenericApproval && msg.actionId && (
        <div style={{ width: '100%', maxWidth: '420px' }}>
          <GenericApprovalCard
            pending={decisionPending}
            onDecide={(decision) => onGenericDecide(msg.actionId as string, decision)}
          />
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
  // approvalPending: an action is staged and awaits the student's decision (blocks
  // the composer). deciding: the approve/reject request is in flight (drives the
  // button spinner). These are deliberately separate — conflating them left the
  // Approve button stuck on "Working…" the instant an action was staged.
  const [approvalPending, setApprovalPending] = useState(false);
  const [deciding, setDeciding] = useState(false);

  // Confirm modal state
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmPlan, setConfirmPlan] = useState<PlanData | null>(null);
  const [confirmActionId, setConfirmActionId] = useState('');
  const [confirmLoading, setConfirmLoading] = useState(false);

  // Toast
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' | 'info' } | null>(null);
  const [savingGradPlanId, setSavingGradPlanId] = useState<string | null>(null);
  const [replaceGradPlan, setReplaceGradPlan] = useState<GradPlanCard | null>(null);
  const [replaceExistingName, setReplaceExistingName] = useState('');

  // Poll for async in-app notifications (e.g. a waitlist seat opened → auto-enrolled)
  // and surface them as Keel chat messages — so the chat notifies, not just email.
  // The backend marks each returned notification read, so it appears exactly once.
  useEffect(() => {
    if (!token) return;
    let alive = true;
    const poll = async () => {
      try {
        const notes = await getNotifications(token);
        if (!alive || notes.length === 0) return;
        setMessages((prev) => [
          ...prev,
          ...notes.map((n) => ({ id: generateId(), role: 'keel' as const, text: `🔔 ${n.body}` })),
        ]);
      } catch {
        /* best-effort — never break the chat */
      }
    };
    void poll(); // immediate on open
    const handle = setInterval(poll, 20000);
    return () => {
      alive = false;
      clearInterval(handle);
    };
  }, [token]);

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

  const submit = useCallback(
    async (text: string) => {
      if (!text || thinking || approvalPending) return;

      const userMsg: ChatMessage = {
        id: generateId(),
        role: 'student',
        text,
      };
      setMessages((prev) => [...prev, userMsg]);
      setThinking(true);

      try {
        const res = await sendChat(token, text, sessionId.current);

        const plans: WidgetCard[] = [];
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
    },
    [thinking, approvalPending, token],
  );

  const handleSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || thinking || approvalPending) return;

    setDraft('');
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    await submit(text);
  }, [draft, thinking, approvalPending, submit]);

  // "Enroll in this plan" on a proposal card → ask Keel to stage enrollment for
  // exactly that plan's courses. The agent resolves sections + stages the action,
  // then the approval gate appears.
  const handleEnrollPlan = useCallback(
    (plan: PlanData) => {
      const codes = plan.courses.map((c) => c.code).join(', ');
      const text = `Enroll me in the "${plan.name}" plan for ${plan.term}: ${codes}.`;
      void submit(text);
    },
    [submit],
  );

  // "Choose Option N" on a section-schedule card → ask Keel to enroll in exactly that
  // schedule's sections. The section_ids are embedded so the agent stages enrollment
  // deterministically (engine re-verifies), then the approval gate appears.
  const handleChooseSchedule = useCallback(
    (card: SectionOptionsCard, schedule: SectionSchedule) => {
      const parts = schedule.items
        .map((it) => `${it.code} (section ${it.section_id})`)
        .join(', ');
      const text = `Enroll me in ${schedule.label} for ${card.term}: ${parts}.`;
      void submit(text);
    },
    [submit],
  );

  const handleSaveGradPlan = useCallback(
    async (plan: GradPlanCard, replace = false) => {
      if (savingGradPlanId) return;
      setSavingGradPlanId(plan.id);
      try {
        const result = await saveGradPlan(token, plan, replace);
        if (result.conflict) {
          setReplaceGradPlan(plan);
          setReplaceExistingName(result.existing_name ?? 'your current saved plan');
          return;
        }
        setReplaceGradPlan(null);
        setReplaceExistingName('');
        setToast({ message: result.message || 'Graduation plan saved.', kind: 'success' });
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: 'keel',
            text: result.message || 'Graduation plan saved.',
            plans: result.plan ? [result.plan] : undefined,
          },
        ]);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Could not save plan.';
        if (message !== 'UNAUTHORIZED') {
          setToast({ message: `Save failed: ${message}`, kind: 'error' });
        }
      } finally {
        setSavingGradPlanId(null);
      }
    },
    [savingGradPlanId, token],
  );

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

  // Generic approve/decline for non-plan actions (petition, graduation, escalation).
  const handleGenericDecide = useCallback(
    async (actionId: string, decision: 'approve' | 'reject') => {
      if (deciding) return;
      setDeciding(true);
      try {
        if (decision === 'approve') {
          const result = await approveAction(token, actionId);
          setToast({ message: 'Approved.', kind: 'success' });
          setMessages((prev) => [
            ...prev,
            {
              id: generateId(),
              role: 'keel',
              // Show the backend's REAL result (enrollment confirmation, or the
              // registrar-queue note for an institutional request) — never a guess.
              text: result.message || 'Done — your action has been completed.',
              plans: result.plans && result.plans.length > 0 ? result.plans : undefined,
            },
          ]);
        } else {
          await rejectAction(token, actionId);
          setToast({ message: 'Declined — nothing was written.', kind: 'info' });
          setMessages((prev) => [
            ...prev,
            { id: generateId(), role: 'keel', text: "No problem — I won't proceed with that. Anything else?" },
          ]);
        }
        // Decision made → unblock the composer.
        setApprovalPending(false);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Action failed.';
        if (message !== 'UNAUTHORIZED') {
          setToast({ message: `Action failed: ${message}`, kind: 'error' });
        }
      } finally {
        setDeciding(false);
      }
    },
    [token, deciding],
  );

  const handleConfirmEnroll = useCallback(async () => {
    setConfirmLoading(true);
    try {
      const result = await approveAction(token, confirmActionId);
      setConfirmOpen(false);
      setApprovalPending(false);
      setToast({ message: 'Enrolled ✓', kind: 'success' });

      const successMsg: ChatMessage = {
        id: generateId(),
        role: 'keel',
        // The real backend result — enrollment writes immediately on approval
        // (no registrar step); show exactly what happened.
        text: result.message || "You're enrolled ✓ — check your schedule for the 'via Keel' badge.",
        plans: result.plans && result.plans.length > 0 ? result.plans : undefined,
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
              // True reset: start a fresh session id so the agent's server-side Redis
              // memory for the old session is no longer referenced (the old key just
              // expires by TTL). Without this, "clear" only wiped the browser view.
              sessionId.current = generateId();
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
            onEnroll={handleEnrollPlan}
            onChooseSchedule={handleChooseSchedule}
            onSaveGradPlan={handleSaveGradPlan}
            onGenericDecide={handleGenericDecide}
            decisionPending={deciding}
            enrollDisabled={thinking || approvalPending}
            savingGradPlanId={savingGradPlanId}
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

      <Modal
        open={!!replaceGradPlan}
        title="Replace saved plan?"
        onClose={() => setReplaceGradPlan(null)}
      >
        <p
          style={{
            margin: '0 0 var(--sp-4)',
            fontSize: 'var(--text-sm)',
            color: 'var(--text)',
            lineHeight: 1.55,
          }}
        >
          This will replace {replaceExistingName || 'your current saved graduation plan'}.
        </p>
        <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
          <Button
            variant="ghost"
            style={{ flex: 1, justifyContent: 'center' }}
            onClick={() => setReplaceGradPlan(null)}
          >
            Keep current
          </Button>
          <Button
            variant="primary"
            style={{ flex: 1, justifyContent: 'center' }}
            disabled={!replaceGradPlan || savingGradPlanId === replaceGradPlan.id}
            onClick={() => {
              if (replaceGradPlan) void handleSaveGradPlan(replaceGradPlan, true);
            }}
          >
            {replaceGradPlan && savingGradPlanId === replaceGradPlan.id ? (
              <><Spinner size={14} /> Saving…</>
            ) : (
              'Replace plan'
            )}
          </Button>
        </div>
      </Modal>

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
