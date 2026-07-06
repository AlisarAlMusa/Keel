# Keel Constitution

The non-negotiable principles that govern this codebase. Every spec, PR, and design decision is checked against this document. Items marked **NON-NEGOTIABLE** cannot be relaxed; everything else can be amended via the governance process in the final section.

---

## Core Principles

### I. Three-Layer Boundary (NON-NEGOTIABLE)

Intelligence proposes. Deterministic systems verify. Models predict. Execution requires approval.

The LLM never decides feasibility, never invents prerequisites, never bypasses the verifier. The deterministic engine owns all hard constraints (prerequisites, time conflicts, capacity, credit caps, offering term, corequisites, holds, eligibility) and its verdict is final. Prediction models score whether a *valid* plan is *advisable* — they never gate feasibility. No write executes without explicit student or registrar approval. This boundary is enforced by code structure (the engine sits between the LLM and the user; the ApprovalToken sits between the agent and the database) — not by convention or vigilance.

### II. Spec-Before-Code (NON-NEGOTIABLE for the engine and the action pattern)

Every component's contract is written in `SPEC.md` before implementation begins. For the deterministic engine (`domain/engine/`) and the action pattern (`services/actions/`), the human writes the edge-case tests *before* the code is accepted as done — Claude Code may draft candidates, but the human owns the nasty cases. Specs evolve; code follows. If a contract is missing or vague, the spec is extended first, then the code is written.

### III. Verifier-Gated Surfaces (NON-NEGOTIABLE)

No plan reaches a student that hasn't satisfied `validate_plan(...) == []`. No side-effecting write executes without a valid, scoped, single-use `ApprovalToken`. These are not enforced by good behavior — they are enforced by code structure: `propose_plan` returns only verifier-clean candidates; write tools reject before reaching a transaction if no token is present; the token is consumed atomically. A failure to enforce either is a security incident, not a bug.

### IV. Defense-in-Depth Tenant Isolation (NON-NEGOTIABLE)

Tenant boundaries are enforced at three independent layers:
1. PostgreSQL Row-Level Security on every tenant-owned table.
2. Repository-layer `tenant_id` filtering (defense-in-depth over RLS).
3. pgvector tenant-filtered retrieval.

No single layer is trusted alone. A misconfigured RLS policy is caught by the repository; a buggy repository is caught by RLS; a leaked embedding is impossible because retrieval filters before similarity. Cross-tenant access is impossible by construction, not by convention. CI red-team probes prove it on every PR.

### V. Continuous Eval Gates

Every component lands with its CI gate the same day it is built — not at the end. Thresholds live in `tests/eval/eval_thresholds.yaml` and block regression. The eval report (`eval_report.json`) is diffed against the last green build on every PR; any metric that drops is surfaced, even if still above threshold. CI is not a quality pass at the end of the project; it is the merge contract from Day 1.

### VI. Honest by Design

The defense must hold. Synthetic data is documented as synthetic, with the generative assumptions and class distribution disclosed in `DATA.md` and the model card. Soft spots — the career-path recommender (no ground truth), the GPA estimate (LLM baseline, uncalibrated) — are framed as *suggestions* and *baselines*, never as predictions. Model cards disclose what the data is and what the model actually learned ("the generator's notion of risk, not validated real-world risk"). Limitations are stated in `SECURITY.md` §9 and `DATA.md`. We claim only what we can prove, and we prove what we claim with the eval gates.

### VII. Bounded Intelligence

One bounded LangGraph agent handles only *hard* turns. A trained classifier router (the intent model) handles enumerable decisions — and is never replaced by an LLM, because that defeats its purpose (cost, latency, blast radius). Within the agent: tool allowlists, loop iteration caps, and token budgets are enforced and committed to source. No multi-agent setups, no MCP, no GraphRAG — each was considered and rejected with a documented reason in `DECISIONS.md`. Simplicity is a feature; agentic sprawl is a regression.

---

## Additional Constraints

**Technology stack mandates** (substitutions require an amendment + `DECISIONS.md` entry):
- Package management: `uv` only — never `pip`. `pyproject.toml` + `uv.lock` committed.
- Runtime containers: `onnxruntime` and `joblib` only — **no `torch` in any container.** Models trained offline (Colab), exported, served lean.
- Secrets: HashiCorp Vault, fetched at startup; app refuses to boot if Vault is unreachable.
- Database: one PostgreSQL instance with Row-Level Security; pgvector in the same instance; no second database.
- Async throughout the request path. No sync DB calls in routers or services.
- Models: exactly two trained (intent classifier, graduation-risk). Workload is deterministic. GPA is an LLM baseline. Adding a third trained model requires an amendment.

**Security constraints** (see `SECURITY.md` for detail):
- Platform guardrails (injection refusal, cross-tenant refusal, PII redaction) are hardcoded in `infra/guardrails.py` and cannot be weakened by tenant config.
- The platform operator can provision/suspend/erase tenants but cannot read tenant content.
- Every side-effecting action writes an `audit_log` row.

**Data constraints:**
- Real student transcript data is privacy-protected (FERPA). Synthetic data is used and documented as such; no real student data is committed to the repository.

---

## Development Workflow

**Spec-first.** Before building a component, read its section in `SPEC.md`. If a contract is missing, extend the spec first. For engine and action-pattern work, confirm the human's edge-case tests exist before implementation.

**Edge cases first.** For the deterministic engine, the human writes the ten enumerated edge cases (`SPEC.md` §3.3) as failing unit tests before Claude Code writes the validator. Engine code is not "done" until those tests pass.

**Layered dependencies.** Code is added in the correct layer (`api → services → repositories → domain`; `infra` injected). Routers are thin (parse, authorize, delegate, serialize). The engine imports nothing from `infra` or `services`. Business logic never lives in routers.

**One write pattern.** Every side-effecting write goes through `services/actions/execute_action` — the six-step pattern (validate → approval → transactional write+outbox → audit). Bypassing this pattern, even "just this once," is a constitutional violation.

**Quality gates ratchet up.** When a component lands, its CI gate lands with it (`PLAN.md` ⚙ markers). When a threshold is raised, the change is committed with the eval data that justified it. Thresholds never silently drop.

**Decisions are logged.** Every non-obvious choice — a tech substitution, a threshold change, a deliberate non-adoption of a pattern — is appended to `DECISIONS.md` with the rationale. The "deliberately not adopted" list in `DECISIONS.md` (MLflow drift pipeline, MCP/A2A, GraphRAG, benchmarks) is part of the defense.

**Traces and logs are tenant-scoped.** Every log line and OTel/LangSmith span carries `tenant_id`. PII redaction runs before any text leaves the service boundary, including into traces.

---

## Governance

This constitution supersedes informal practices, ad-hoc decisions, and convenience. Where this constitution and any other document conflict, the constitution wins; the other document is amended.

**Amendments** require:
1. A pull request modifying this file with the rationale in the commit message.
2. An entry in `DECISIONS.md` explaining what changed and why.
3. If the amendment relaxes a `NON-NEGOTIABLE` principle, an explicit justification of why the principle no longer applies — and acknowledgment that this likely invalidates parts of `SECURITY.md` and `SPEC.md` that must also be updated.

**Compliance checks:**
- Every PR is reviewed against the principles before merge.
- CI gates enforce Principles III, IV, and V automatically.
- Principle II is enforced by reviewer: "is the spec updated? do the edge-case tests exist?"
- Principle VI is enforced at documentation time: "is the model card honest? are the limitations stated?"

**Complexity must be justified.** A new dependency, a new service, a new pattern, or a new abstraction requires a `DECISIONS.md` entry stating what it adds and why the simpler alternative was rejected. Cleverness is not justification; user/defensibility value is.

**Runtime development guidance** lives in `CLAUDE.md` (for Claude Code sessions), `PLAN.md` (for what to build next), and `SPEC.md` (for contracts). This constitution is the *why* behind those documents — they encode it; they do not override it.

---

**Version**: 1.0.0 | **Ratified**: 2026-06-06 | **Last Amended**: 2026-06-06 