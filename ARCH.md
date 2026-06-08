# ARCH.md — Keel Architecture

This is the architectural reference. `CLAUDE.md` has the rules; this explains the *why* and the *shape*. Read before working on the engine, the agent, the action pattern, or tenancy.

---

## 1. System overview

Keel is a multi-tenant SaaS. A university (tenant) deploys it; its students talk to one conversational agent embedded in the registration portal. The agent plans courses, predicts whether a plan is wise, advises in plain language, and safely executes registration and institutional requests after approval.

Two frontends, one backend, a separate lean model-server, and a background worker — all behind one Postgres instance with Row-Level Security.

```
┌── React Admin (registrar) ──┐        ┌── React Student Widget ──┐
│ catalog ingest · rules ·    │        │ chat · plan view ·       │
│ request-queue inbox ·       │        │ risk/workload badges ·   │
│ widget config · cost view   │        │ approval                 │
└──────────────┬──────────────┘        └────────────┬─────────────┘
               │   (admin JWT)                       │ (signed widget token + origin check)
               └───────────────┬─────────────────────┘
                               ▼
                    FastAPI backend (async, layered, RLS)
                  api → services → repositories → domain
                               │            ▲ infra (DI)
        ┌──────────────────────┼────────────────────────────┐
        ▼                      ▼                             ▼
   model-server         Postgres + pgvector              Redis + RQ worker
   (ONNX/joblib)        (RLS, Plan entity, queue,        (capacity sync, waitlist,
   intent + risk         outbox, audit, vectors)          outbox publisher, alerts,
                                                           auto-replan)
        └──────────── Vault (secrets) · MinIO (artifacts) · MLflow (registry) ────────────┘
                          OpenTelemetry / LangSmith tracing across all calls
```

---

## 2. The three layers (the core idea)

Keel's defensibility is that three kinds of work live in three layers with hard boundaries.

| Layer | Owns | Nature | Can it be wrong? |
|-------|------|--------|------------------|
| **LLM** | intent, constraint extraction, plan proposal, ranking, explanation, drafting | soft, generative | Yes — the engine catches it |
| **Engine** | prerequisites, conflicts, capacity, caps, terms, coreqs, holds, eligibility | hard, exact, deterministic | No — its verdict is final |
| **Models** | graduation-risk (trained), workload (deterministic) | probabilistic / learned | Advisory only — never gates feasibility |

The LLM has genuine agency over the *shape* of a plan but can never emit an invalid one, because the engine validates every candidate before it reaches the student. **The verifier is the seatbelt, not the driver.**

---

## 3. Request lifecycle

```
inbound message
  → GUARDRAILS (input rails: injection / cross-tenant refusal)
  → CLASSIFIER ROUTER (trained intent model)
      ├─ easy / enumerable intent → deterministic workflow (no agent)
      └─ hard / multi-step intent → bounded LangGraph agent
  → agent selects tools (allowlisted, Pydantic-validated, loop-capped)
  → tools call: engine (validate) · model-server (predict) · RAG (retrieve) · repositories (read)
  → response assembled
  → GUARDRAILS (output rails: PII redaction)
  → traced end-to-end (OTel/LangSmith)
```

Writes never happen inside this read/propose path — they go through the approval-gated **action pattern** (§7).

---

## 4. The planning loop

The heart of the product. A generate–verify–predict–explain loop, not a one-shot pass:

```
student request
  → LLM extracts intent + hard constraints + soft preferences
  → engine builds the eligible course pool (audit)
  → LLM proposes 2–3 candidate plans
  → engine validates each → returns structured Violation[]
      └─ repair loop: LLM repairs from violations, re-proposes (capped iterations)
  → models score the VALID candidates (graduation-risk + workload)
  → LLM ranks + explains using feasibility + risk
  → student overrides? → engine re-validates + re-scores
  → student approves
  → action pattern executes the enrollment (transactional + outbox + audit)
  → approved plan saved as a Plan entity, monitored for invalidation
greedy deterministic planner = fallback if the loop fails to converge
```

---

## 5. The deterministic engine (`domain/engine/`)

Pure, deterministic, no I/O, no LLM. Unit-testable in isolation. The crown jewel.

- **`dag.py`** — prerequisites → directed acyclic graph; topological sort gives valid orderings; cycle detection rejects malformed catalogs.
- **`audit.py`** — transcript + program requirements → remaining requirements, remaining credits, eligible course set, progress rate.
- **`validator.py`** — the verifier. Candidate plan → `Violation[]` (empty list = valid). Checks prereq order, time conflicts, section capacity, credit cap, corequisites, holds, repeated passed courses, offering term. **Never throws on bad input — returns violations.**
- **`sections.py`** — eligible courses + schedule preferences → open, conflict-free section combinations; reports full/not-offered with alternatives.
- **`workload.py`** — Σ(course difficulty × credits) → light/medium/heavy bands. Deterministic, not a model.
- **`planner.py`** — greedy fallback: produces a valid plan without the LLM (used when the loop fails or as a baseline).

Why deterministic and separate: it gives a hard correctness guarantee the LLM cannot, it's cheap, it's testable, and it's the thing that makes "the LLM can never emit an invalid plan" true.

---

## 6. Catalog: two representations, one source

The registrar uploads the catalog once; it populates **two stores** serving two layers:

- **Structured DB tables** (`courses`, `prerequisites`, `sections`, `program_requirements`) — what the **engine queries**. Exact, relational, used for the DAG, audit, validation, section search. You cannot run prerequisite ordering on prose.
- **RAG corpus in pgvector** (course descriptions, policy docs, chunked + embedded, tenant-tagged) — what the **LLM explains from**. Used for advising ("what does this course cover?"). Prerequisite *facts* in answers are grounded against the DAG, never invented by the LLM.

Rule of thumb: **anything computed on → DB table; anything explained from → vector store.**

---

## 7. The action pattern (`services/actions/`)

Every side-effecting write is the same shape, implemented once:

```
build request (Pydantic) → engine validates preconditions
  → require explicit approval
  → BEGIN TX: write domain row + insert outbox event  COMMIT
  → write audit_log row
  → worker publishes outbox (email/notification, retry+backoff)
```

Instances: `execute_enrollment`, `waitlist_join/leave`, `apply_graduation`, `request_major_change`, `submit_petition`. The **outbox** guarantees the write and its notification are atomic (no dual-write inconsistency: you never enroll-without-notifying or notify-without-enrolling). Idempotency keys prevent duplicates. Security: nothing reaches the TX step without approval.

---

## 8. The agent (`agent/`)

- One **bounded** LangGraph agent, reached only for hard turns. The classifier router handles easy/enumerable turns directly — deliberate, to cut cost and blast radius. **Do not replace the router with an LLM.**
- Bounded = tool allowlist + max iterations + token budget. Failure → escalate or greedy fallback.
- Tools are thin wrappers over services/engine/model-server, each with a Pydantic input schema.
- The agent **proposes and explains; it never writes.** Writes go through §7.
- Prompts are versioned in `agent/prompts/`.

Tool set: `audit_degree · propose_plan · simulate_whatif · predict_risk · search_sections · save_plan · swap_course · execute_enrollment · apply_graduation · request_major_change · submit_petition · escalate`.

---

## 9. Prediction & model serving

- **model-server** is a separate service using `onnxruntime`/`joblib` only — no torch in any runtime container. Serves the intent classifier and the graduation-risk model over HTTP.
- Models trained offline (Colab), three-way comparison (classical ML / small DL→ONNX / LLM baseline) logged to **MLflow**.
- Artifacts pinned by SHA-256 in a model card; the server refuses to boot on mismatch. MLflow registry is the artifact + promotion source of truth (staging→production; rollback = re-point).
- **Workload** is deterministic (engine). **GPA estimate** is a light LLM baseline, always caveated.

---

## 10. Multi-tenancy & security

- **RLS** on every tenant-owned table; the request sets a tenant context and Postgres enforces it.
- Repositories **also** filter by tenant (defense in depth).
- pgvector queries are tenant-filtered.
- **Roles:** Admin (registrar) — grounds/configures + works the request queue; Student — own identity/transcript only; Platform operator — provision/suspend/erase tenants, never reads tenant data. No separate advisor role (escalation is an email with an LLM handoff summary).
- **Widget auth:** public widget_id → short-lived signed token; server-side origin allowlist; CORS/CSP as defense-in-depth, never the boundary.
- **Guardrails:** in-process input/output rails (injection, cross-tenant, PII redaction), hardcoded, not tenant-configurable. Red-team gate in CI.
- **Secrets:** Vault at startup; refuse to boot if unreachable.

---

## 11. Background work (`workers/`)

RQ worker on Redis: capacity sync, waitlist processing, **outbox publisher** (email/notifications, retry+backoff), **personalized alerts** (4 deterministic triggers: seat open, eligibility unlocked, risk threshold crossed, registration window), **automatic replanning** (detect catalog/section/prereq change → find affected saved plans → re-audit + re-plan → notify). All tenant-scoped, deduped, structured-logged.

---

## 12. Key subsystems summary

- **Plan entity** — first-class, versioned, one active per student. Underpins save/load/activate, swap, automatic replanning.
- **Institutional-request queue** — one registrar-worked inbox (graduation applications, major-change, petitions). One subsystem, not four features, no new role.
- **Outbox** — atomic write+notify across all actions.
- **model-server + MLflow** — lean serving + registry/rollback.

---

## 13. Data flow at a glance

```
catalog upload ──► DB tables (engine) + pgvector corpus (RAG)
student msg  ──► guardrails ─► router ─► [workflow | agent] ─► engine/models/RAG ─► guardrails ─► reply (traced)
approval     ──► action pattern ─► TX(write + outbox) ─► audit ─► worker ─► notify
catalog change ─► worker ─► affected saved plans ─► re-audit/re-plan ─► notify
```

---

## 14. Where it breaks at scale (be honest in DESIGN.md)

- RLS + one Postgres is fine to ~hundreds of tenants; beyond that, schema-per-tenant or sharding.
- pgvector is fine for one university's catalog; a very large multi-campus corpus would want a dedicated vector store.
- The synchronous propose→verify→repair loop is bounded; heavy concurrent planning would move to a queue.
- These are deliberate MVP choices, documented — not oversights.