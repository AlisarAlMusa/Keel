# Keel

**A sanctioned academic co-pilot that plans, predicts, advises — and safely acts. The AI proposes the course; the keel keeps it upright.**

> AI Bootcamp · Final Capstone · Solo · ~2-Week AI-Assisted Build

**Prepared by:** ______  
**An AI layer over the university's SIS · Two Keel surfaces**  
**Four capabilities:** Plan · Register · Advise · Predict

---

| 01 · SOFT | 02 · HARD | 03 · LEARNED | 04 · GATED |
|---|---|---|---|
| **Intelligence proposes** | **The engine verifies** | **Models predict** | **The student approves** |
| The LLM reads intent and drafts the plan. | Deterministic rules prove every plan legal. | Graduation-risk and workload score the plan. | Nothing is written without explicit approval. |

---

## 1. The Idea

Keel is a multi-tenant AI advising-and-registration layer a university would deploy on top of its existing Student Information System — Banner, Workday, PeopleSoft, or similar.

The SIS is the system of record for the academic backbone (catalog, sections, students, transcripts, enrollments, institutional requests); Keel adds the intelligence and the safe-action layer above it. The engine speaks one internal language — **Student, Course, Section, Enrollment, Transcript, Request** — a canonical domain model used throughout the core.

> **IMPLEMENTATION NOTE · READ WITH §7**
>
> In the **current implementation** those canonical fields are **not reached through an adapter**. The SIS-domain tables are seeded directly into the **same Postgres database** as Keel's own tables, and Keel's repositories read and write them directly. There is no SISGateway abstraction and no per-tenant adapter in the demo path.
>
> The pluggable per-tenant adapter and the SISGateway interface — the layer that would let the core sit over a real, external SIS without touching a vendor's table structure — are **designed and documented as the production seam (§7), but deliberately not built in the demo.**

It runs two React surfaces over one FastAPI backend: a Keel admin console where the registrar grounds and configures the agent, and a chat widget embedded in the university's registration portal that students use. The portal is the SIS's, not Keel's — the widget rides on it the way Intercom or Stripe ride on a host page. A student talks in plain language — *"15 credits, Fridays free, must take Data Structures, no 8 AMs"* — and Keel produces a valid, conflict-free plan, predicts whether that plan is wise (graduation risk and workload, not just legality), explains the trade-offs, and — only after the student approves — writes the enrollment back to the system of record.

It does four things and does them safely. It **plans** (next-semester, full path to graduation, what-if scenarios, and saved/named plans the student can compare and activate), it **registers** (finds open, conflict-free sections matching preferences, waitlists and notifies, and files real institutional requests such as petitions and graduation applications), it **advises** (grounded course information, prerequisite explanations, failure-recovery and major-switch reasoning), and it **predicts** (a trained graduation-risk model and a deterministic workload signal that score every candidate plan). Keel began as a registration copilot; it has matured into a student-success co-pilot — with planning and registration kept as first-class capabilities, not buried under new features.

The deployed demo runs the full planning, registration, and advising flow end-to-end against SIS-domain tables seeded directly into the same tenant-isolated Postgres as the Keel tables — no adapter or gateway indirection in the demo path. Introducing the SISGateway interface and the per-tenant Banner/Workday/REST adapters is the production swap described in §7; the canonical domain types are chosen so that swap can land without rewriting the core.

---

## 2. The Problem It Solves

Registration and degree planning are universally painful — hidden time conflicts, prerequisite surprises, full sections, no advisor available at 11 PM, and no clear answer to *"am I on track to graduate?"* Students fall back on brittle, unsanctioned scraping bots; official advising chatbots only answer questions and route to a human; the SIS portal lets you act but cannot reason.

Keel is the sanctioned middle ground: an assistant that integrates with the SIS and can safely act on it — with tenant isolation, human approval before any write, and a full audit trail — and that doesn't merely tell a student they are at risk, but builds them a legal, lower-risk plan, registers it on approval against the system of record, and files the institutional paperwork that normally requires an office visit.

---

## 3. Core Principle — Propose · Verify · Predict · Explain

The defining rule: **the LLM never decides feasibility, and never predicts outcomes.**

Three kinds of work live in three layers. Hard constraints (prerequisites, time conflicts, capacity, credit caps, offering term, holds) are owned by a deterministic engine and are non-negotiable. Outcome estimates (graduation risk, workload) are owned by prediction models. Everything fuzzy, conflicting, and underspecified — what the student actually wants, which plan to propose, how to rank and explain it — is where the LLM genuinely decides.

### SOFT · FUZZY · GENERATIVE — The LLM decides

Intent disambiguation when ambiguous · constraint & preference extraction from natural language · proposing candidate plans, elective sets, orderings · ranking and explaining trade-offs · repairing a plan from structured violations · drafting petitions and request justifications · advisory recommendations.

### HARD · EXACT · VERIFIED — The engine guarantees

Prerequisite ordering (DAG topological check) · time-conflict detection · section capacity · credit caps · offering-term cadence · corequisites · registration holds · eligibility · workload aggregation (difficulty index) · idempotent, transactional enrollment write-through.

### PROBABILISTIC · LEARNED — The models predict

A trained graduation-risk classifier (compared across three model families, with class-imbalance handling) scores a valid plan as on-track / at-risk and feeds a mitigation explanation. The GPA estimate is an explicit LLM baseline — the weak, uncalibrated option, framed as such. Workload is deterministic, not a model.

### HOW THE LAYERS MEET — The loop

A generate–verify–repair loop: the LLM proposes, the engine validates and returns structured violations, the LLM repairs and re-proposes; valid candidates are then scored by the models, and the LLM ranks and explains using both feasibility and risk. A greedy deterministic planner sits behind it as a fallback.

> **Intelligence proposes. Deterministic systems verify. Models predict. Execution requires approval.**

---

## 4. What Keel Does — Features

Every feature reuses a small set of engines (the deterministic core, the advising RAG, the LLM, the model-server) and a single action pattern — propose → engine-validate → human-approve → idempotent transactional write-through → audit. The modules below are intents and actions over those engines, not independent pipelines.

---

### Module A — Planning

#### A1 Next-Semester Planning `MVP`

Answer "what should I take next term?" and feed registration. **Constraints:** plan must satisfy prerequisites, credit caps, corequisites, no repeats of passed courses, degree progress. **AI:** LLM extracts hard constraints + soft preferences, proposes 2–3 candidate plans (balanced / graduation-focused / lighter), ranks and explains. **Deterministic:** the audit engine builds the eligible course pool; the verifier validates every candidate and returns structured violations.

#### A2 Graduation Planning `MVP`

Map the whole path from current term to graduation. **Constraints:** full multi-term path must respect the prerequisite DAG, offering cadence, and per-term credit limits end-to-end. **AI:** LLM optimizes the skeleton toward a goal (fastest / balanced / easier terms) and explains the sequence. **Deterministic:** engine computes remaining requirements and credits, generates the path skeleton, validates the whole path; risk is scored per term and the highest-risk term surfaced — never the path flattened into one prediction.

#### A3 What-If Simulation `MVP`

"What if I switch to Math?" / "Can I graduate a semester early?" **Constraints:** returns graduation date + required credits-per-term; never presents an infeasible alternative as feasible. **AI:** LLM explains consequences and trade-offs. **Deterministic:** engine recomputes audit against the target program and derives the new timeline.

#### A4 Saved Plans — Save / Load / Activate `MVP`

Persist named plans ("Fast Graduation", "Easy Semester", "Career-aligned"); load, compare, and activate one. **Constraints:** a first-class, versioned Plan entity with exactly one active plan; every saved plan was verifier-valid at save time and is re-validated on load if the catalog changed. **AI:** none for storage; the LLM names and explains plans on request. **Deterministic:** Plan entity, versioning, activation, comparison view. The Plan entity is Keel-owned state — it lives in Keel's database, not the SIS.

#### A5 Course Swap `ADVANCED`

"Replace Database with Networks" → validate → update the active plan. **Constraints:** the swap must keep the plan verifier-valid; reject and explain if it cannot. **AI:** LLM interprets the request and explains the consequence. **Deterministic:** engine re-validates the edited plan; idempotent transactional update on the Plan entity.

#### A6 Automatic Replanning `ADVANCED`

A saved plan becomes invalid (course removed, prerequisite changed, section cancelled) → re-audit, replan, notify the student. **Constraints:** scope which saved plans are actually affected; recompute must converge or flag for a human; avoid notification storms. **AI:** LLM explains what changed and what the recompute did. **Deterministic:** background worker re-runs audit + planner on affected plans; transactional write + outbox notification. Invalidation currently triggers off seeded catalog edits; in production the per-tenant adapter's catalog sync is the change source.

---

### Module B — Registration

#### B1 Registration Assistant `MVP`

Turn an approved plan (or manually named courses) into a real, conflict-free enrollment. **Constraints:** a phased, bounded workflow — not an open agent loop. The engine owns eligibility; the write requires explicit approval and is transactional + idempotent. **AI:** LLM interprets schedule preferences and explains section options. Limited, deliberately. **Deterministic:** section search returns open, conflict-free combinations; on approval the enrollment is written directly to the SIS-domain enrollment row in the same Postgres, and Keel records the audit and outbox event. The write stamps a `source` field so it reads as "via Keel." In production this same write would pass through the SISGateway to the external SIS, where the SIS's own transaction-source marks the origin.

#### B2 Waitlist (Join / Leave) & Seat Tracking `MVP`

Full section? Join or leave a waitlist as first-class actions; "notify me when a seat opens." **Constraints:** email-on-seat-open retries with backoff and never breaks the user-facing response. **AI:** none — pure workflow. **Deterministic:** background RQ worker polls capacity (reads the seeded SIS-domain counts directly in the demo; live-read through the SISGateway in production); transactional waitlist writes; notification via the outbox.

---

### Module C — Advising

#### C1 Course Advisor (RAG) `MVP`

Answer what a course covers, what it unlocks, and what it requires — grounded, never invented. **Constraints:** prerequisite chains are grounded in the DAG, so the model cannot invent a prerequisite. **AI:** hybrid dense + sparse retrieval with rerank over the tenant's catalog and policy prose; LLM answers from context with sources. **Deterministic:** retrieval is tenant-filtered; prerequisite facts come from the DAG, not the prose. The advising corpus is Keel-owned — registrar-uploaded `catalog.md` / `policy.md` in the admin console, distinct from the structured SIS catalog.

#### C2 Degree Audit Chat `MVP`

"What do I still need to graduate?" in plain language. **Constraints:** numbers come from the engine; the LLM may not restate them incorrectly. **AI:** LLM summarizes the audit result conversationally. **Deterministic:** engine computes missing requirements, remaining credits, required courses. Read-only — advising never writes.

#### C3 Academic Advisor Chat — Failure Recovery `ADVANCED`

"I failed Data Structures. Am I doomed?" → a concrete recovery plan. **Constraints:** the recovery plan must itself pass the verifier; graduation-impact figures come from the engine. **AI:** LLM composes the recovery narrative and re-plan rationale. **Deterministic:** engine computes downstream delayed courses and graduation-date impact.

#### C4 Major-Switch Advisor `ADVANCED`

"Should I switch majors?" — a recommendation layer over A3's what-if. (Filing the switch lives in F2.) **Constraints:** consequences are engine-computed; the recommendation is advisory, not a guarantee. **AI:** LLM analyzes performance patterns and strengths and frames a recommendation. **Deterministic:** engine computes graduation consequences against each candidate program.

---

### Module D — Prediction

#### D1 Graduation-Risk Predictor `ADVANCED`

"Am I on track?" — the headline ML feature. Scores a valid plan on-track / at-risk and drives a mitigation plan. **Constraints:** a trained model compared across three model families — linear, bagging, and boosting — on a tabular feature set; macro-F1 gates CI, with class-imbalance handling and per-class recall reported (at-risk is the minority class). Served lean from the model-server (no torch, no ONNX runtime — a pinned, hash-verified estimator loaded directly). **AI:** the model produces the risk score + reasons; the LLM generates the mitigation plan from those reasons. **Deterministic:** the nine numeric inputs (GPA trajectory, failures, repeats, progress rate, completion, planned credits, workload index, hard-course count) are computed by the engine from the shared feature module, scored on one candidate term at a time.

#### D2 Workload Signal `ADVANCED`

"How heavy is this semester?" **Constraints:** deterministic by design — a difficulty-weighted aggregation (per-course difficulty × credits → light / medium / heavy). Not a third trained model, to keep training scope at two. **AI:** none for the score; the LLM only narrates it inside plan explanations. **Deterministic:** the same difficulty × credits index that feeds risk feature #8 — one computation, two consumers.

#### D3 GPA Estimate `ADVANCED`

"What GPA might I get with this plan?" — a light, advisory estimate. **Constraints:** implemented as an LLM baseline and labelled as the weak, uncalibrated option — a live example of the "when not to use an LLM" lesson. Never a guarantee. **AI:** LLM estimate from transcript + course difficulty + load. Swappable for a model later. **Deterministic:** feature inputs are engine-computed; the estimate is explicitly bounded and caveated.

---

### Module E — Guidance

#### E1 Personalized Elective Recommender `ADVANCED`

Rank electives by fit (strengths, GPA goals, difficulty preference, career direction). **Constraints:** only recommends electives that exist in the catalog and are eligible — grounded in the DAG + audit, so it cannot suggest a course the student can't take. **AI:** LLM ranks and justifies using transcript strengths/weaknesses. **Deterministic:** the eligible elective set comes from the engine.

#### E2 Career Path Recommender `ADVANCED`

"I want to become an AI Engineer" → map interests + strengths to a direction and the catalog electives/skills that align. The roadmap can be saved as a career-aligned plan via A4. **Constraints:** the one feature with no hard verifier and no ground truth — framed explicitly as a suggestion, not a prediction. Recommendations are catalog-grounded so it cannot invent courses; if saved, the suggested courses are routed through the propose→verify→repair loop before persisting, so A4's "valid at save time" invariant holds even here. **AI:** LLM maps career interest → relevant skills → recommended catalog electives → projects. **Deterministic:** course/skill grounding is catalog-bound via RAG + DAG; legality is enforced only when the suggestion becomes a persisted plan.

---

### Module F — Institutional Requests

Institutional requests are SIS-domain: the agent drafts and submits them through the action pattern; the registrar approves them in the SIS portal, where they read as plain SIS data. The agent automates the request, not the decision. All four share one action shape — validate → require approval → single transaction (request row + outbox event) → audit — implemented once. **No agent tool carries an `approved` field, so no message (or injection) can self-approve a write.**

#### F1 Graduation Application `ADVANCED`

Audit confirms eligibility → apply for graduation. **Deterministic:** engine confirms requirements before the action is offered; idempotent, audit-logged write routed to the registrar's queue.

#### F2 Major-Change Request `ADVANCED`

Impact analysis (C4) → submit a major-change request. **Deterministic:** engine computes lost credits / new timeline; routed write, not an auto-approved change.

#### F3 Petition / Prerequisite Override `ADVANCED`

Engine explains the block → LLM drafts a justified petition → submit an exception request. **Constraints:** the engine still refuses to auto-enroll; the petition is a request to a human, never a bypass. A submitted petition writes a request row and never an enrollment row, even after approval — the seatbelt gains a sanctioned override path, it is not removed.

#### F4 Advisor Escalation — Email + Handoff Summary `ADVANCED`

An out-of-scope turn or "I want to talk to an advisor" → decide escalation → generate a handoff summary → email it. **Constraints:** no advisor role, login, or dashboard; escalation is an email carrying the summary, with the advisor's address resolved from a tenant-scoped lookup table. **AI:** LLM decides escalation and writes the handoff — conversation recap, transcript summary, failed constraints, recommended actions. **Deterministic:** escalation routing; email via the outbox; audit.

---

### Module G — Proactive & Multilingual

#### G1 Personalized Alerts `ADVANCED`

A seat opened, you can now take X (eligibility unlocked), graduation risk crossed a threshold, the registration window is opening. **Constraints:** a fixed small set of triggers (~4), not a general rules engine; deduplicated; delivered in-app and by email. **AI:** LLM phrases the alert; the triggers are deterministic. **Deterministic:** background worker evaluates trigger rules; notification table; outbox delivery.

#### G2 Multilingual Support `STRETCH`

Arabic / French / English — locally relevant for the deploying institution. **Constraints:** low architectural complexity — prompt + UI; the engine and predictions are language-agnostic, and the multilingual embedding model already covers retrieval. **AI:** LLM responds in the student's language; retrieval stays grounded. **Deterministic:** no change to the engine, verifier, or writes.

---

## 5. Roles & Access

### Admin (registrar)

Grounds the agent — uploads the advising RAG prose (`catalog.md`, `policy.md`); configures persona, enabled tools, and guardrails; manages the widget embed snippet and allowed origins; reviews the audit log and per-tenant cost. Structured catalog, prerequisites, sections, and registration rules (credit caps, holds, windows) are SIS-domain, registrar-managed in the SIS — seeded in the demo, not edited in the Keel console. The registrar also works the institutional-request queue (graduation applications, major-change requests, petitions); in the deployed demo that queue is worked in the mock SIS portal, where requests read as plain SIS data.

### Student

Uses the chat widget — asks in plain language, reviews the proposed conflict-free plan, its predicted risk/workload, and trade-offs, saves and compares plans, and approves before any enrollment or request executes. Scoped to their own identity and transcript. The university authenticates the student via its own SSO; Keel never logs the student in. The demo uses a student switcher as an SSO stand-in, with the token still minted server-side.

### Platform operator

Provisions, suspends, and erases university tenants. Never reads a tenant's conversations or data — a controlled doorway, not god mode. Every action is audit-logged.

Three named Keel roles, fixed powers — deliberately no configurable RBAC matrix and no separate advisor role; advisor escalation is an email with a handoff summary, not a login. The mock SIS portal carries its own two roles (student and registrar) for the demo, reflecting the fact that in production these surfaces belong to the university's SIS, not to Keel.

---

## 6. Architecture Overview — as built (the demo)

### Surfaces

| Surface | Description |
|---|---|
| **React Admin (registrar)** | RAG prose · config · audit · cost |
| **React Student Widget** | NL chat · plan view · risk & workload badges · approval. Server-minted, memory-only token. |
| **Mock SIS portal** | Node/Express + React. Hosts the widget; registrar works the request queue. |

### ONE FASTAPI BACKEND · RLS · LAYERED · ASYNC

| Component | Description |
|---|---|
| **Classifier router** | Trained intent model, 15 labels. Confident → deterministic handler. Ambiguous / multi → bounded agent. |
| **Bounded agent (LangGraph)** | Tools: `audit_degree` · `propose_plan` · `simulate_whatif` · `predict_risk` · `search_sections` · `save_plan` · `swap_course` · `execute_enrollment` · `apply_graduation` · `request_major_change` · `submit_petition` · `escalate(email)` |
| **Deterministic core (verifier + planner)** | Prereq DAG · conflict checker · degree audit · credit/coreq/hold rules · workload difficulty index · plan validator · greedy fallback. LLM-proposes / engine-validates / models-predict loop. |
| **Lean model-server** | joblib + sklearn, torch-free & onnx-free. Intent classifier · grad-risk model, hash-pinned. |
| **pgvector RAG** | Hybrid + rerank, DAG-grounded, tenant-filtered. |
| **RQ worker (Redis)** | Capacity sync · waitlist · outbox publisher / alerts · auto-replan. |
| **Infra** | Redis cache/session · Vault · MinIO · MLflow registry · OTel/LangSmith. |

### ONE POSTGRES · RLS · TWO LOGICAL DOMAINS · NO PHYSICAL SPLIT

| Domain | Tables |
|---|---|
| **SIS-domain tables (seeded)** | catalog · sections · students · transcripts · enrollments · requests |
| **Keel-domain tables** | plans · conversations · risk · RAG/pgvector · config · audit · outbox · cost |

> **Demo:** Keel's repositories read & write both domains directly in the same database. The SIS-domain tables stand in for the university's SIS.

> **PRODUCTION SEAM — DESIGNED, NOT BUILT IN THE DEMO (SEE §7)**
>
> In production the SIS-domain tables leave Keel's database. A **SISGateway** interface and per-tenant **Banner / Workday / REST** adapters sit between the deterministic core and the university's real, external SIS, so the core reads and writes the canonical model without touching a vendor's table structure. The demo deliberately omits this indirection.

### Key subsystems

- **Canonical domain model** — the engine speaks one internal vocabulary (Student, Course, Section, Enrollment, Transcript, Request) and is real and in use today. In the demo it maps straight to the seeded SIS-domain tables in the same Postgres; there is no gateway indirection. The SISGateway interface and per-tenant adapters are the production seam (§7), chosen so the core never changes when a real SIS lands.
- **Plan entity** — a first-class, versioned, named, Keel-owned plan with one active version. Underpins save/load/activate (A4), course swap (A5), and automatic replanning (A6).
- **Institutional-request queue** — one inbox the registrar works for graduation applications, major-change requests, and petitions (F1–F3). SIS-domain data; one subsystem, not four features, and no new role.
- **Action pattern + outbox** — every side-effecting write follows propose → validate → approve → transactional write-through + outbox event → audit. Approval state is persisted (an ApprovalToken scoped to an action and idempotency key, short-lived and single-use); the outbox avoids dual-write inconsistency between the write and its notification.

---

## 7. From Demo to Production — The SIS Boundary

> **The single most important thing to understand about this build: the SIS boundary is not implemented in the demo.**

### What the demo actually does — AS BUILT

- SIS-domain tables (catalog, sections, students, transcripts, enrollments, requests) are seeded directly into the same Postgres as the Keel-domain tables.
- Keel's repositories read and write those SIS fields directly — there is no SISGateway, no adapter, no remote call.
- Tenant isolation is enforced inside that one database by Row-Level Security.
- An enrollment write stamps a `source` field so it reads as "via Keel"; otherwise it is a plain row insert.
- Capacity, catalog changes, and invalidation are driven by edits to the seeded tables, not by a sync from an external system.

### What production would add — DESIGNED · DEFERRED

- A **SISGateway** interface: one contract the core reads and writes the canonical model through, with no knowledge of any vendor's schema.
- Per-tenant **Banner / Workday / REST adapters** implementing that contract against the real SIS, selected by a one-row-per-tenant integration config.
- The university's SIS becomes the true **system of record**; the SIS-domain tables leave Keel's database, and Keel keeps only its Keel-domain tables.
- Catalog sync, live capacity reads, and enrollment write-through all flow through the adapter; the SIS's own transaction-source marks each write as "via Keel."
- Replanning invalidation triggers off the adapter's catalog sync rather than seeded edits.

> **WHY DEFERRED — AND WHY IT'S CHEAP TO LAND LATER**
>
> Building the gateway indirection before the demo would be cost without payoff: the demo needs the deterministic engine and the safe-write path proven, not a vendor integration. The canonical domain model and the repository boundary are chosen **now** so that introducing the gateway later is a swap, not a rewrite — the same repository calls route through the SISGateway to an adapter, and the domain, verifier, planner, action pattern, and Keel-owned RLS are untouched. The boundary is documented in `PRODUCTION.md`; the per-tenant adapters are the post-demo work.

### Stays identical across the swap

- **The deterministic core** — DAG, conflict checker, audit, validator, greedy fallback. It already speaks the canonical model.
- **The action pattern** — propose → validate → approve → transactional write + outbox + audit. Production changes only where the final write lands.
- **The Keel-domain** — plans, conversations, RAG, config, audit, outbox, cost stay in Keel's database under RLS, in the demo and in production alike.

---

## 8. The Planning Loop

The linear pipeline is a generate–verify–predict–explain loop, with prediction inserted only after feasibility is proven (no point scoring an illegal plan):

1. Student request → LLM extracts intent + constraints + preferences
2. Engine builds the eligible course pool → LLM proposes candidate plans
3. Engine validates & returns structured violations → repair loop: LLM repairs & re-proposes until valid
4. Models predict graduation-risk & workload on the valid candidates → LLM ranks & explains using feasibility + risk
5. Student overrides → engine re-validates & re-scores → student approves
6. Idempotent, transactional enrollment write-through (+ outbox notification) → the approved plan is saved and monitored for invalidation

A greedy deterministic planner is the fallback if the loop fails to converge.

---

## 9. Core AI Components

- **Intent classifier (router)** — a trained text-classification model over 15 labels (plan, what-if, advise, audit, predict, register, waitlist, plans-manage, grad-apply, major-change, petition, escalate, my-info, chitchat, out-of-scope), one label per handler. A confident prediction routes straight to a deterministic handler; a low-confidence, ambiguous, or multi-intent turn falls to the bounded agent, which has conversation history. Cheap conversational turns are served by a lighter model at near-zero cost; the main model handles reasoning-heavy flows. Compared three ways (classical ML, a small DL model exported to ONNX, an LLM zero-shot baseline); macro-F1 plus routing-coverage accuracy gate CI. A misroute is never dangerous — every action still passes the engine check and the approval gate.

- **Graduation-risk model** — the second trained model; compared across three tabular model families (linear / bagging / boosting); a realistic at-risk minority handled with class weighting; risk-F1 and at-risk recall gate CI; feeds the LLM mitigation plan. Served as a hash-pinned estimator, not a deep model.

- **Constraint & preference extraction** — structured outputs turn free text into typed hard constraints and soft preferences.

- **Plan proposal & repair** — the LLM proposes candidates inside the generate-and-verify loop and repairs them from the engine's structured violations.

- **Advising RAG** — hybrid dense + sparse retrieval, reciprocal-rank fusion, and a multilingual reranker over catalog & policy prose, with faithful, DAG-grounded prerequisite explanations.

- **Drafting & explanation** — petition drafts, handoff summaries, trade-off explanations, strengths/weaknesses, mitigation, and recommendations.

- **Guardrails** — prompt-injection / cross-tenant refusal on input and PII redaction on output, before anything leaves the service boundary; platform rails are hardcoded and cannot be lowered by tenant config.

---

## 10. Core Software-Engineering Components

- **Multi-tenant isolation** — Postgres Row-Level Security (enabled and forced, app connecting as a non-superuser role) + repository-layer scoping + tenant-filtered pgvector. Three independent layers; isolation is the grade.

- **Layered backend** — api / services / repositories / domain / infra, async throughout, dependency injection, lifespan singletons, typed boundaries; the domain stays pure (no framework or IO imports) so the engine is fully unit-testable.

- **Canonical domain model + repository boundary** — the internal vocabulary is real and used throughout the engine today; in the demo it maps directly to the seeded SIS-domain tables in the same database. The SISGateway interface and per-tenant adapters are designed and documented (§7, `PRODUCTION.md`) but intentionally not implemented for the demo — they are the production seam, not a demo deliverable.

- **First-class Plan entity** (versioned, named, active) and the **institutional-request queue** worked by the registrar — no extra role.

- **Transactional writes + outbox** for the write-and-notify path (enrollment, waitlist, requests, alerts) to avoid dual-write inconsistency; idempotency keys; an audit-log row on every write; safety-critical approval state persisted in the database, with Redis holding only ephemeral session memory.

- **Background jobs** — an RQ / Redis worker for capacity sync, waitlist processing, email-on-seat-open, personalized alerts, and automatic replanning (retry + backoff, structured logging).

- **Caching** with deliberate invalidation on catalog change; per-tenant rate limiting.

- **Widget auth** — the university authenticates the student; the portal backend vouches with a server-to-server token mint (a separate shared secret, distinct from the widget signing key). The widget token is lazy-minted when the student opens the chat, passed to the iframe by postMessage with an origin check, and held in memory only — never in storage or a URL. CORS / CSP are defense-in-depth, never the boundary.

- **Model lifecycle** — MLflow (Postgres-backed, MinIO artifact store) tracks runs and holds the model registry; the serving image syncs the registry-promoted artifacts at boot, pins each by SHA-256, and refuses to boot on mismatch — controlled promotion without a heavyweight retraining pipeline.

- **Platform plumbing** — Secrets in Vault (fail-closed startup), blobs in MinIO, OpenTelemetry / LangSmith tracing, Alembic migrations, `docker compose up` from a clean clone, GitHub Actions CI with eval gates.

---

## 11. MVP · Advanced · Stretch

### MVP — The Demo Spine

Intent classifier + 15-label router · constraint extraction · next-semester / graduation / what-if planning (propose→verify→repair loop) · first-class Plan entity with save/load/activate · registration (section search + idempotent transactional write-through + approval) · waitlist join/leave + seat-tracking + email worker · Course Advisor RAG (hybrid + rerank) · Degree Audit chat · multi-tenant RLS · server-minted widget token auth · seeded SIS-domain tables in the same Postgres, accessed directly by the repositories (the SISGateway/adapter seam is documented for production, not built) · Vault · layered backend · outbox writes · CI eval gates (planner-correctness + intent-F1 + RAG + red-team + smoke).

### Advanced — Depth

Graduation-risk model + LLM mitigation · failure-recovery chat · major-switch advisor · personalized elective recommender · GPA estimate (LLM) · workload signal (deterministic) · course swap · automatic replanning · institutional requests (graduation application, major-change request, petition/override) · advisor escalation email + handoff summary · personalized alerts (capped triggers) · career-path recommender (catalog-grounded).

### Stretch — If Time

Multilingual support (Arabic / French / English) · career roadmap saved as a plan · NeMo Guardrails sidecar (a lightweight in-process layer covers the MVP) · the SISGateway interface plus per-tenant production SIS adapters (the full external-SIS seam).

---

## 12. Evaluation & Quality Gates

*fail CI on regression*

- **Planner correctness** — the headline gate. No generated plan ever violates a prerequisite, time conflict, capacity, credit cap, or hold; the verifier proves it on a hand-written golden set, and the greedy planner's output is itself run back through the verifier.

- **Intent classifier** — macro-F1 and routing-coverage accuracy on a held-out, group-split test set, with the ML / DL / LLM three-way comparison and a 100% gate on an obvious-case golden set committed alongside.

- **Graduation-risk model** — macro-F1 and at-risk recall on a held-out split; three-family comparison and a model card committed.

- **Agent tool-selection** — given a message, did it pick the right tool (or correctly pick none)?

- **RAG** — retrieval and generation quality (faithfulness, relevancy, context recall) on a curated golden set, plus a DAG-grounding assertion that any stated prerequisite actually exists in the graph.

- **Red-team / write-action safety** — cross-tenant and prompt-injection probes must all be refused; a fake key never leaks unredacted; and no injected or unapproved message ever produces a write. Testing the action pattern once covers every action type.

- **Stack smoke** — `docker compose up` from a clean clone brings every service healthy and completes one traced chat round-trip.

---

## 13. Data Sources & Honesty

- **Catalog & policy** — SIS-domain in production, registrar-managed there; a mock catalog with a real prerequisite DAG is seeded for the demo (directly into Keel's Postgres, as described in §6–§7). The advising RAG prose is separately registrar-uploaded into Keel.

- **Intent dataset** — hand-authored (no LLM or API generated the text), ~1,050 messy student utterances across the 15 labels, deduplicated and group-split so no paraphrase leaks across train/test.

- **Graduation-risk data** — synthetic, generated honestly from a documented logistic risk function over z-scored features with a required nonlinear interaction term and injected noise, tuned to a realistic at-risk minority. The model card states plainly that the model learns the generator's notion of risk, not validated real-world risk; a held-out split reports macro-F1 and at-risk recall.

- **On synthetic data** — the framing that survives a defense. Real transcript data is privacy-protected (FERPA), which is the legitimate reason to synthesize. The data carries a deliberate, documented signal plus noise — never "purely random." This is the "weak supervision, documented honestly" lesson applied directly.

---

## 14. Tech Stack — every choice justified, nothing decorative

| Component | Justification |
|---|---|
| **React (admin + widget) + mock SIS portal** | Two Keel surfaces; the widget is the production-shaped, embeddable student UI. The mock SIS portal (Node/Express + React) stands in for the university's own portal — a separate codebase, as it would be in production. |
| **FastAPI + Pydantic** | Async API, typed boundaries, structured LLM/tool I/O. |
| **Canonical domain model** | One internal vocabulary used throughout the engine; in the demo it maps directly to the seeded SIS-domain tables. The SISGateway/adapter integration seam that swaps in a real SIS is the production design (§7), not part of the demo. |
| **LangGraph bounded agent** | Tool-calling agent for the hard turns only; bounded loop = cost + safety control. |
| **Deterministic engine** | The core of the project — DAG, conflict checker, audit, validator, greedy fallback. The differentiator lives here. |
| **Postgres + RLS + Alembic** | One DB, two logical domains; RLS enforces tenant isolation at the database. Hosts the Plan entity, request queue, outbox, and audit log. |
| **pgvector (hybrid + rerank)** | Advising RAG in the same database, tenant-filtered by construction. |
| **Cohere (multilingual embeddings + rerank)** | 1024-dim multilingual embeddings (cover the multilingual stretch, no torch) and a multilingual reranker. |
| **Gemini (lite + main tiers)** | A lighter model for cheap conversational turns; the main model for reasoning-heavy flows and the agent. |
| **Model-server (joblib + sklearn)** | Intent + graduation-risk served lean — torch-free and onnx-free; train offline, serve a hash-pinned estimator. |
| **MLflow (Postgres + MinIO)** | Run tracking and the model registry; the serving image syncs registry-promoted, SHA-256-pinned artifacts at boot. |
| **Redis + RQ worker** | Session memory + cache; background capacity sync, waitlist, alerts, outbox publishing, auto-replan. |
| **Server-minted widget token** | Real widget auth; the portal vouches server-side, token is memory-only; CORS/CSP are defense-in-depth. |
| **Vault · MinIO** | Secrets at startup (fail-closed); blob for model artifacts, eval reports, snapshots. |
| **Email (Resend / SMTP)** | Seat-open notifications, alerts, and advisor-escalation emails — all via the outbox. |
| **Docker Compose · GitHub Actions** | One-command stack from a clean clone; CI with eval gates that fail on regression. |
| **OpenTelemetry / LangSmith** | Traces over every LLM/tool/retrieval call; joinable with redacted logs. |
| **NeMo Guardrails (sidecar)** — stretch | Topical + injection rails as a sidecar; a lightweight in-process layer covers the MVP. |

### Deliberately not adopted (and why)

- **Drift detection / automatic retraining pipeline** — meaningful only with real data over time; model cards + SHA-256 + refuse-to-boot + CI eval gates provide controlled promotion at the right scale.
- **MCP / A2A multi-agent** — would contradict the deliberate single-bounded-agent design; one agent behind a trained router is the correct, cheaper choice.
- **GraphRAG** — the prerequisite DAG is already used deterministically by the engine, which is stricter and more accurate than approximating it with graph retrieval.
- **Schema-per-tenant isolation, benchmarks/leaderboards, fine-tuned large transformer** — no real fit at this scale.

---

## 15. Deliverables

A public GitHub repository that comes up with a single `docker compose up` from a clean clone: the Keel admin + student widget, a mock SIS portal, the FastAPI backend, the deterministic engine, the lean model-server, the seeded SIS-domain tables in the same Postgres read and written directly (no adapter in the demo path), seeded tenants, CI with eval gates, and the documentation set:

- `SPEC.md`
- `DESIGN.md`
- `DECISIONS.md`
- `ENGINE.md` — the engine + verifier + outbox contract
- `PRODUCTION.md` — the SIS boundary and the SISGateway/adapter contract to be built for production
- `DATA.md` — the synthetic-data honesty note
- `EVALS.md`
- `SECURITY.md`
- `RUNBOOK.md`

Plus a short demo video of one end-to-end run from the widget through to a safely executed enrollment and a filed institutional request.

---

## 16. Scope Notes & Risks

- **The deterministic engine is the real time sink — and the core value.** The DAG, conflict checker, audit, and the generate-verify-repair loop are the hardest, least AI-assistant-friendly parts and they exist before any expansion. Build the engine correct first; a leaky verifier undermines everything above it.

- **The SIS boundary is the production seam — and it is not built yet.** Today Keel does not go through an adapter: the SIS-domain tables are seeded into the same Postgres as the Keel tables and the repositories read and write them directly. The canonical domain types are chosen so that a SISGateway interface and per-tenant Banner/Workday adapters can be introduced later without rewriting the core (§7). Building that indirection before the demo would be cost without payoff — the demo needs the engine and the safe-write path proven, not a vendor integration — so the gateway and adapters are deliberately deferred to production.

- **Two trained models, no more.** Intent + graduation-risk. GPA is a light LLM baseline; workload is deterministic. Resisting a third training job is a deliberate choice, not a gap.

- **Action sprawl lives in the test surface, not the code.** Every approval-gated write-through is something an injection could try to trigger. The single action pattern (validate → approve → transactional write + outbox + audit) is tested once and reused, so adding actions stays cheap and safe; no agent tool can self-approve.

- **Career path is the soft spot.** No verifier, no ground truth — framed as a grounded suggestion, with legality enforced only when it becomes a saved plan.

- **Richness without sprawl.** The features are intents and actions over a few engines plus two subsystems (the Keel-owned Plan entity and the SIS-domain request queue), not independent pipelines. Depth on the planning loop, the predict layer, and the safe-write path beats breadth across half-built features.

---

*Keel — Final Capstone Brief · Propose · Verify · Predict · Approve*