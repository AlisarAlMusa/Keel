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

Every repository method accepts `tenant_id` as a parameter and includes it in `WHERE` clauses. This is defense-in-depth — if an RLS policy were misconfigured, the query still filters. Repositories also **assert** that returned rows' `tenant_id` matches the caller, catching any leak at read time.

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

### 5.1 The action pattern (see SPEC.md §8)

Every write follows: validate → require `ApprovalToken` → single DB transaction (domain row + outbox event) → audit log. The `ApprovalToken` is:

- Issued only after the student explicitly approves (button click in the widget, not implicit).
- Scoped to a specific `action_id` and `idempotency_key`.
- Short-lived (≤ 5 minutes).
- Single-use (consumed on first execution; replay returns the original receipt).

### 5.2 What cannot happen

- An LLM tool call alone cannot trigger a write — the tool checks for a valid `ApprovalToken` before step 4. Missing/invalid token → immediate rejection, no DB write.
- A prompt-injection that convinces the LLM to call `execute_enrollment` still fails — the tool requires the token, which only the widget's approval button can issue.
- An expired or replayed token is rejected.

### 5.3 Acceptance

CI gate: inject a tool call without an approval token, with an expired token, and with a replayed token — all must be rejected before any DB write occurs. Test once, covers all action types.

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