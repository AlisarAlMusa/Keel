# SECURITY.md — Keel Security Model

How Keel protects tenant data, prevents unauthorized actions, and handles adversarial input. Written for reviewers, auditors, and the team — not as marketing. Honest about what is and isn't covered.

---

## 1. Threat model — what we defend against

Keel is a **multi-tenant system with an LLM that can trigger side-effecting writes** (enrollment, waitlist, petitions, graduation applications). That combination creates a specific threat surface:

| Threat | Impact if unmitigated |
|--------|----------------------|
| **Cross-tenant data leakage** | Student A sees Student B's transcript, plans, or chat. Tenant X's catalog leaks to Tenant Y. |
| **Prompt injection** | An adversarial message tricks the LLM into calling a write tool, bypassing approval, or leaking system prompts. |
| **Unauthorized write** | An enrollment, petition, or graduation application executes without explicit student/registrar approval. |
| **PII exfiltration** | API keys, student emails, national IDs, or transcript data appear in logs, traces, or responses to other tenants. |
| **Widget impersonation** | A malicious site embeds the widget and captures student sessions or triggers actions on their behalf. |
| **Secret exposure** | Database credentials, API keys, or signing keys leak via code, config, logs, or error messages. |

---

## 2. Tenant isolation — the primary security boundary

Isolation is enforced at **three independent layers** — any one failing is caught by the others.

### 2.1 Database: Row-Level Security (RLS)

Every tenant-owned table (`students`, `plans`, `enrollments`, `transcript`, `sections`, `request_queue`, `outbox`, `audit_log`, `notifications`, pgvector `embeddings`) has an RLS policy:

```sql
CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

The request middleware sets `app.tenant_id` from the authenticated token **before any query executes**. A query that forgets to filter by tenant still returns only the correct tenant's rows — the database enforces it.

### 2.2 Repository layer: explicit tenant scoping

Every repository is bound to a `(session, tenant_id)` and issues its queries inside a tenant-scoped (RLS) session. Most queries also include an explicit `WHERE tenant_id = :tid` filter as defense-in-depth — if an RLS policy were misconfigured, the query still scopes to the caller's tenant. (By-id lookups on the action-lifecycle table rely on the session's RLS scoping.)

### 2.3 pgvector: tenant-filtered retrieval

RAG queries include `WHERE tenant_id = :tid` on the embeddings table. A retrieval for Tenant A physically cannot return Tenant B's chunks, regardless of semantic similarity.

### 2.4 Acceptance

Proven by CI red-team tests: authenticated as Tenant A, attempt to read/write/retrieve Tenant B data at the API, repository, and pgvector layers — all must return empty or 403.

---

## 3. Authentication

### 3.1 Student widget — signed per-widget token

Students never log in directly. The university's portal (the embedding page) calls:

```
POST /widget/token   { widget_id }
```

The server validates `widget_id`, checks the request's `Origin` header against the tenant's configured origin allowlist, and returns a **short-lived signed JWT** (HS256, ≤ 15 minutes) containing `tenant_id`, `student_id`, `widget_id`, `exp`, and `allowed_origin`.

Every subsequent `/chat` and `/actions/*` request carries this token. The server validates:
1. Signature (HS256, key from Vault).
2. Expiry.
3. `Origin` header matches `allowed_origin` in the token.

**CORS and CSP are defense-in-depth, not the boundary.** A valid `Origin` without a valid token is rejected. A valid token from a disallowed `Origin` is rejected.

### 3.2 Admin console — JWT

Registrar admins authenticate via a standard JWT flow (the exact IdP is deployment-dependent). The JWT carries `tenant_id` and `role=admin`. Admin routes check `role` before delegating.

### 3.3 Platform operator — separate auth

Platform operator endpoints (provision/suspend/erase tenant) require a separate credential scoped to the platform, never to a tenant. The operator can manage tenants but **cannot read tenant conversations, plans, transcripts, or requests** — a controlled doorway, not god mode. Every operator action is audit-logged.

---

## 4. Guardrails — adversarial input defense

Location: `infra/guardrails.py`. Runs on **every** inbound message and **every** outbound response, regardless of route.

### 4.1 Input rails

- **Prompt injection detection:** pattern-matching + classifier for known injection families ("ignore previous instructions", "you are now", role-play attacks, delimiter-based injections). A flagged message is refused with a safe response — it **never reaches the agent or any tool**.
- **Cross-tenant probe refusal:** any message referencing another tenant's identifiers, or attempting to set/override `tenant_id`, is refused.

### 4.2 Output rails

- **PII redaction:** before any text leaves the service boundary (response to client, log line, trace span), a redaction pass replaces API keys (`sk-...`, `key-...`), email addresses, national ID patterns, and phone numbers with `[REDACTED]`. Patterns are configurable per deployment; the redaction step itself is **not skippable by tenant config**.

### 4.3 Non-weakening guarantee

Platform guardrails (injection, cross-tenant, PII) are **hardcoded** in `infra/guardrails.py`. The tenant admin can configure the agent's persona, enabled tools, and topical scope — but cannot lower or disable the platform rails. This is enforced by code structure: the rails run in middleware/pre-hook, before any tenant-configured layer.

### 4.4 Acceptance

CI red-team gate: a curated set of injection probes and cross-tenant probes must all be refused (100%); a fake API key pasted into chat must never appear unredacted in any response, log, or trace. Gate blocks merge on regression.

---

## 5. Write-action safety — the approval gate

The most critical security property: **no side-effecting write (enrollment, waitlist, petition, major-change, graduation application) ever executes without explicit, verified approval.**

### 5.1 The action pattern (see SPEC.md §8, DESIGN.md §4)

Every write follows: **stage a pending action (frozen payload) → require explicit
student approval → engine re-validates → single DB transaction (domain row + outbox
event) → audit log**. As-built, "approval" is a persisted state machine on the
`actions` table rather than a separate `ApprovalToken` object (the safety property is
equivalent; SPEC §8 is annotated):

- A staged action is `status='pending'`; it carries the **frozen** payload and the
  LangGraph `thread_id` to resume on (recorded from the runtime, **never** from an
  LLM argument).
- The student approves via `POST /actions/{action_id}/approve`, authenticated by the
  **verified widget Bearer JWT** — the same token `/chat` uses. Identity comes from
  the signed token, never from a client-supplied header.
- The approve handler loads the action under the caller's tenant RLS (cross-tenant →
  404), asserts `action.student_id == token.student_id` (else 403), and requires
  `status='pending'` (else 409). Only then is `status` advanced and the graph resumed.
- Execution (`execute_node`) runs **only on an approved resume**, re-validates engine
  preconditions, and writes once. The transition `approved → executed` is single-use
  (a second resume sees `executed`, not `approved`).

> Historical note: the approve/reject endpoints previously trusted spoofable
> `X-Student-Id`/`X-Tenant-Id` headers (and the widget never sent them, so approval
> 401'd). That scheme is removed — see §3.1. No agent tool exposes an `approved`
> field, and **no agent tool calls a write service with `approved=True`** (the F3
> petition tool that used to do so now stages like every other write).

### 5.2 What cannot happen

- An LLM tool call alone cannot trigger a write — write tools only *stage* a pending
  action; the actual write runs in `execute_node` after an approved resume.
- A prompt-injection that convinces the LLM to call a write/stage tool still cannot
  commit: the staged action sits `pending` until a human approves via the
  JWT-authenticated endpoint, and identity/tenant/thread are bound from the verified
  context, not the model's arguments.
- An approval for another tenant's/student's action is rejected (404/403).

### 5.3 Acceptance

CI gate: a stage/tool call without approval, with a non-pending action, or for a
cross-tenant/cross-student action — all must be rejected before any DB write occurs.
Test once, covers all action types.

---

## 6. Secrets management

- All secrets (DB credentials, signing keys, API keys, MLflow credentials) are stored in **HashiCorp Vault** and fetched at application startup.
- The application **refuses to boot** if Vault is unreachable or if a required secret is missing.
- No secret is ever hardcoded in source, committed to Git, or stored in environment files (`.env.example` documents the keys without values).
- Secrets never appear in logs, traces, or error responses — the PII redaction layer (§4.2) provides an additional safety net, and structured logging is configured to exclude secret-containing fields.

---

## 7. Data privacy considerations

- **FERPA context:** real student transcript data is privacy-protected. The system is designed to work with real data in production; for the capstone, synthetic or mock data is used. The `DATA.md` documents what is synthetic and what is real.
- **Right to erasure:** the platform operator's "erase tenant" endpoint deletes all tenant data (conversations, plans, transcripts, embeddings, audit logs) irreversibly. This is a hard delete, not a soft delete.
- **Conversation data:** stored per-tenant, scoped by RLS, never readable by the platform operator or other tenants. Chat history TTL is configurable per tenant.
- **Model inputs:** graduation-risk model features are computed from transcript data; the features (GPA, failed count, progress rate, load) are numeric and do not contain personally identifiable information. The model never sees names, IDs, or emails.

---

## 8. Audit trail

Every side-effecting action writes an `audit_log` row:

```python
class AuditEntry(BaseModel):
    id: UUID
    tenant_id: UUID
    actor_id: UUID             # student, admin, or platform operator
    actor_role: str
    action: str                # e.g. "enrollment.execute", "petition.submit"
    resource_id: UUID | None
    before: dict | None        # previous state (for updates)
    after: dict | None         # new state
    timestamp: datetime
```

Audit logs are append-only, tenant-scoped (except platform-operator actions, which are logged separately), and included in tenant data exports. The registrar admin can view the audit log for their tenant in the admin console.

---

## 9. What we do NOT protect against (honesty)

| Not covered | Rationale |
|-------------|-----------|
| **DDoS / volumetric attacks** | Infrastructure-level concern (load balancer, CDN, rate limiting at the edge); Keel has per-tenant rate limiting but not network-level DDoS mitigation. |
| **Compromised tenant admin** | A registrar admin with valid credentials can misconfigure rules, view student data within their tenant, and approve/reject requests. This is by design — they are the tenant authority. |
| **Browser-side attacks beyond CORS/CSP** | XSS in the embedding page is outside Keel's boundary. The widget runs in an iframe with a short-lived token; CORS/CSP reduce blast radius but cannot prevent all browser-side attacks on the host page. |
| **LLM jailbreaking for non-write actions** | Guardrails reduce the risk, but a sufficiently creative prompt may elicit an off-topic or stylistically wrong response. This cannot cause a write (the approval gate is independent of the LLM) and cannot leak cross-tenant data (RLS is independent of the LLM). The risk is limited to inaccurate or unhelpful answers, which are a quality issue, not a security issue. |
| **Supply chain / dependency attacks** | Standard dependency pinning and CI scanning apply; not Keel-specific. |

---

## 10. Security testing in CI

| Test | What it proves |
|------|----------------|
| Cross-tenant API probes | Tenant A auth → Tenant B data → 403 or empty |
| Cross-tenant pgvector probes | Tenant A query → no Tenant B chunks returned |
| Injection probes (curated set) | Every probe refused before reaching the agent |
| Write without approval token | Tool call without/expired/replayed token → rejected, no DB write |
| PII in response | Fake API key in chat → never unredacted in response |
| PII in logs/traces | Fake key/email in chat → never unredacted in structured logs or OTel spans |
| Tenant erasure | After erase, all data (including embeddings and audit) returns empty |

All gates block merge on regression. Thresholds: 100% pass rate (security gates are not "best-effort").

---

## 11. Institutional write-action safety (Phase 4 additions)

Four new write actions were added in Phase 4: F1 graduation application, F2
major-change request, F3 prerequisite-override petition, and F4 advisor escalation.
All four use the shared action pattern and carry the following safety properties.

### Injection-safety-by-construction

The agent's F-tool input schemas have **no `approved` parameter**. The LLM cannot
approve its own writes: even if a student's message says "file it now without
asking", the tool input schema rejects the field and the service function defaults
to `approved=False`. The only path to `approved=True` is the explicit student
approval action (UI / endpoint — Day 6 scope).

Verified by `tests/unit/test_institutional_write_safety.py`:
- `test_agent_f_tools_have_no_approved_param` — schema inspection: no F-tool exposes
  the `approved` field.
- `test_service_fn_defaults_to_not_approved` — parametrized: each service function's
  `approved` default is `False`.
- `test_*_no_write_without_approval` — four mocked checks: `approved=False` → the
  underlying session factory is never called.

### F3 never enrolls

`submit_petition` writes a `PETITION` row in `request_queue` (type = `'petition'`)
and **never** touches the `enrollments` table. The engine's eligibility block is
not removed. Verified by `tests/integration/test_institutional_write_safety.py`:
`test_petition_never_enrolls` asserts `enrollments` count = 0 after a petition is
filed.

### Cross-tenant isolation for institutional writes

Institutional writes run inside a `tenant_session` (RLS-scoped). A filing for
tenant A produces zero rows visible to tenant B. Verified by
`test_cross_tenant_never_writes`.

### Idempotency (no notification storms)

The partial unique index `uq_request_queue_pending` on
`(tenant_id, student_id, type, target) WHERE status='pending'` + `ON CONFLICT DO
NOTHING` means a duplicate filing before resolution is a safe no-op — one PENDING
row and one outbox event regardless of how many times the agent is invoked. Verified
by `test_idempotent_pending`.

### F4 email handoff only

Escalation writes no `request_queue` row — only an `outbox` event (`escalation_email`)
and one `audit_log` row. The advisor lookup is done against the RLS-scoped `advisors`
table; cross-tenant advisor data is never accessible.

### Updated CI gate table

| Test | What it proves |
|------|----------------|
| `test_agent_f_tools_have_no_approved_param` | F-tool schemas: no `approved` field → injection structurally impossible |
| `test_service_fn_defaults_to_not_approved` | Service layer: default gate is `False` |
| `test_*_no_write_without_approval` | `approved=False` → zero DB calls |
| `test_no_write_without_approval` (integration) | `approved=False` → 0 rows in `request_queue` + `outbox` |
| `test_cross_tenant_never_writes` (integration) | Tenant A filing invisible to tenant B |
| `test_petition_never_enrolls` (integration) | F3 writes `petition` row, never `enrollment` row |
| `test_idempotent_pending` (integration) | Double-file → exactly 1 PENDING row + 1 outbox event |

All institutional safety tests are always-on (unit) or skipped when
`TEST_DATABASE_URL` is absent (integration). All block merge on failure.

---

## Summary of the layered defense

```
inbound message
  → widget token validated (signature + expiry + origin)
  → tenant_id set in DB session (RLS active)
  → GUARDRAILS input rails (injection / cross-tenant refusal)
  → classifier router → [workflow | agent]
  → if write action: ApprovalToken required (scoped, short-lived, single-use)
  → DB write: transaction (domain row + outbox) — RLS + repo filter enforce tenant
  → GUARDRAILS output rails (PII redaction)
  → audit_log row appended
  → outbox → worker → notification/email (tenant-scoped)
  → OTel/LangSmith trace (redacted)
```

No single layer is trusted alone. RLS, repository filtering, guardrails, approval tokens, and PII redaction each operate independently — a failure in one is caught by another.