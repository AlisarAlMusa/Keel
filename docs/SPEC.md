# SPEC.md — Keel Component Contracts

The contracts every component is built against. **Write or extend the relevant section before implementing a component; write tests against its Acceptance criteria.** This is the SpecKit input. Schemas are expressed in Pydantic-v2 style; translate directly. `CLAUDE.md` = rules, `ARCHITECTURE.md` = shape, this = contracts.

---

## 0. Global conventions

- **IDs**: `UUID` (v4). **Timestamps**: timezone-aware UTC (`datetime`). **Credits**: `int`. **GPA**: `Decimal` (2 dp).
- **Every tenant-owned model carries `tenant_id: UUID`.** Repositories filter by it; RLS enforces it.
- **Pydantic v2** everywhere at boundaries. Domain value objects are `frozen=True` where possible.
- **Enums** are string enums (`StrEnum`).
- **The engine (`domain/engine/`) imports nothing from `infra`/`services` and makes no I/O or LLM calls.**
- **Functions in the engine never raise on bad domain input** — they return structured results/violations. They may raise only on programmer error (e.g. malformed graph at load).
- **Money/credit math** never uses floats.

---

## 1. Core domain types (shared vocabulary)

```python
class Term(StrEnum):            # offering cadence + scheduling
    FALL = "fall"; SPRING = "spring"; SUMMER = "summer"

class DayOfWeek(StrEnum):
    MON="mon"; TUE="tue"; WED="wed"; THU="thu"; FRI="fri"; SAT="sat"; SUN="sun"

class TimeSlot(BaseModel, frozen=True):
    day: DayOfWeek
    start_min: int   # minutes from midnight, 0..1439
    end_min: int     # > start_min
    # invariant: 0 <= start_min < end_min <= 1439

class Course(BaseModel, frozen=True):
    tenant_id: UUID
    code: str                  # e.g. "CS201" — unique per tenant
    name: str
    credits: int               # > 0
    difficulty: int            # 1..5 (for workload index)
    offered_terms: set[Term]   # which terms it runs

class Section(BaseModel, frozen=True):
    tenant_id: UUID
    id: UUID
    course_code: str
    term: Term
    year: int
    slots: list[TimeSlot]      # meeting times
    capacity: int              # >= 0
    enrolled: int              # 0..capacity (waitlist separate)
    # derived: is_open = enrolled < capacity

class Prerequisite(BaseModel, frozen=True):
    tenant_id: UUID
    course_code: str
    requires_code: str         # must be completed before course_code
    min_grade: Decimal | None  # optional grade floor (e.g. C = 2.0)

class Corequisite(BaseModel, frozen=True):
    tenant_id: UUID
    course_code: str
    coreq_code: str            # must be taken same term or earlier

class TranscriptEntry(BaseModel, frozen=True):
    tenant_id: UUID
    student_id: UUID
    course_code: str
    term: Term
    year: int
    grade: Decimal | None      # None = in progress; failing < pass_threshold
    passed: bool

class Hold(BaseModel, frozen=True):
    tenant_id: UUID
    student_id: UUID
    kind: str                  # e.g. "financial", "advising"
    blocks_registration: bool

class ProgramRequirement(BaseModel, frozen=True):
    tenant_id: UUID
    program_code: str
    # a requirement is satisfied by N credits/courses from a course group
    group_name: str            # e.g. "Core", "Math Elective"
    required_credits: int
    eligible_course_codes: set[str]

class Student(BaseModel, frozen=True):
    tenant_id: UUID
    id: UUID
    program_code: str
    max_credits_per_term: int  # credit cap
    current_term: Term
    current_year: int
```

**Acceptance:** every type round-trips through Pydantic; `TimeSlot.overlaps(other) -> bool` is a pure helper with tests (touching/adjacent slots do **not** overlap).

---

## 2. Plan entity

Foundational. Underpins save/load/activate (A4), swap (A5), automatic replanning (A6).

```python
class PlanStatus(StrEnum):
    DRAFT="draft"; ACTIVE="active"; ARCHIVED="archived"; STALE="stale"  # stale = invalidated, needs replan

class PlannedCourse(BaseModel, frozen=True):
    course_code: str
    term: Term
    year: int
    section_id: UUID | None    # None until registration

class Plan(BaseModel):
    id: UUID
    tenant_id: UUID
    student_id: UUID
    name: str                  # e.g. "Fast Graduation"
    version: int               # monotonic per (student, name)
    status: PlanStatus
    courses: list[PlannedCourse]
    created_at: datetime
    # invariants:
    #  - at most ONE plan per student has status ACTIVE
    #  - every saved plan was verifier-valid at save time (validated_at recorded)
    validated_at: datetime | None
```

**Behaviour:** save creates a new `version`; activate sets this `ACTIVE` and demotes the previous active to `ARCHIVED` in one transaction; load re-validates if the catalog changed after `validated_at` and marks `STALE` if now-invalid.

**Acceptance:** activating plan B while plan A is active leaves exactly one ACTIVE (transactional); loading a plan whose course was removed from the catalog returns it marked `STALE` with the violations attached.

---

## 3. Deterministic engine (`domain/engine/`) — the crown jewel

### 3.1 DAG — `dag.py`

```python
class PrereqDAG:
    def __init__(self, prerequisites: list[Prerequisite]) -> None: ...
    def topological_order(self) -> list[str]: ...          # raises CycleError on cycle
    def prerequisites_of(self, code: str) -> set[str]: ...  # direct prereqs
    def all_ancestors(self, code: str) -> set[str]: ...     # transitive prereqs
    def unlocks(self, code: str) -> set[str]: ...           # courses this is a prereq for
```
**Rules:** acyclic only; `CycleError` (programmer/catalog error) is the *one* exception this module raises, surfaced at catalog-ingest time, never mid-conversation.
**Acceptance:** cycle A→B→A raises `CycleError`; `all_ancestors` is transitive; isolated node has empty prereqs.

### 3.2 Degree audit — `audit.py`

```python
class AuditResult(BaseModel, frozen=True):
    completed_credits: int
    remaining_credits: int
    satisfied_groups: dict[str, int]      # group_name -> credits satisfied
    remaining_requirements: list[ProgramRequirement]
    eligible_now: set[str]                # course codes the student may take next
    progress_rate: Decimal                # completed / total, 0..1

def audit_degree(student: Student, transcript: list[TranscriptEntry],
                 program: list[ProgramRequirement], dag: PrereqDAG,
                 holds: list[Hold]) -> AuditResult: ...
```
**Rules:** a course is `eligible_now` iff all prereqs satisfied (with `min_grade`), not already passed, offered in an upcoming term, and no `blocks_registration` hold applies. Pure.
**Acceptance:** a passed course never appears in `eligible_now`; a course with an unmet prereq never appears; with a registration hold, `eligible_now` is empty (or flagged) — your edge test decides which, document it.

### 3.3 Validator (the verifier) — `validator.py` ★ most important

```python
class ViolationType(StrEnum):
    PREREQ_NOT_MET="prereq_not_met"
    TIME_CONFLICT="time_conflict"
    SECTION_FULL="section_full"
    CREDIT_CAP_EXCEEDED="credit_cap_exceeded"
    COREQ_MISSING="coreq_missing"
    HOLD_BLOCKS="hold_blocks"
    NOT_OFFERED_THIS_TERM="not_offered_this_term"
    REPEAT_OF_PASSED="repeat_of_passed"
    UNKNOWN_COURSE="unknown_course"

class Violation(BaseModel, frozen=True):
    type: ViolationType
    course_code: str
    detail: str                 # human-readable, fed to the LLM for repair
    term: Term | None = None

def validate_plan(plan: Plan, *, student: Student, transcript: list[TranscriptEntry],
                  dag: PrereqDAG, courses: dict[str, Course],
                  sections: dict[UUID, Section], coreqs: list[Corequisite],
                  holds: list[Hold]) -> list[Violation]: ...
```
**Contract:** returns `[]` iff the plan is fully valid. **Never raises** on bad plan input (an unknown course yields `UNKNOWN_COURSE`, not an exception). Checks, per term, in order: unknown course → not offered this term → repeat of passed → prereq met (respecting `min_grade` and courses earlier *in the plan*) → coreq present (same term or earlier) → time conflicts among chosen sections → section capacity → per-term credit cap → registration holds.
**Edge cases (each gets a human-written unit test before code is "done"):**
1. Circular prereq in catalog → caught at DAG load, not here.
2. Coreq that is also a prereq → both rules apply; satisfying prereq-order satisfies coreq.
3. Course offered only in Spring, planned in Fall → `NOT_OFFERED_THIS_TERM`.
4. Student has a registration hold but plan is otherwise legal → `HOLD_BLOCKS`.
5. Plan relies on a course taken *later in the same plan* as a prereq → `PREREQ_NOT_MET` (prereq must be strictly earlier term).
6. Prereq passed with grade below `min_grade` → `PREREQ_NOT_MET`.
7. Two chosen sections overlap by one minute → `TIME_CONFLICT`; back-to-back (touching) → valid.
8. Credits == cap → valid; cap + 1 → `CREDIT_CAP_EXCEEDED`.
9. Re-taking a passed course → `REPEAT_OF_PASSED`.
10. Empty plan → `[]` (valid, trivially).

### 3.4 Section search — `sections.py`

```python
class SchedulePreferences(BaseModel):
    no_days: set[DayOfWeek] = set()
    earliest_min: int | None = None   # no class before
    latest_min: int | None = None     # no class after
    prefer_compact: bool = False

class SectionPlan(BaseModel, frozen=True):
    selections: dict[str, UUID]       # course_code -> section_id
    unmet: list[str]                  # courses with no conflict-free open section

def find_sections(course_codes: set[str], term: Term, year: int,
                  sections: dict[UUID, Section], prefs: SchedulePreferences
                  ) -> list[SectionPlan]: ...
```
**Rules:** returns conflict-free combinations honoring hard prefs (no_days/earliest/latest are hard filters; prefer_compact only orders results). Full/not-offered courses go to `unmet` with the combination still returned for the rest. Deterministic ordering.
**Acceptance:** if every section of a required course is full, that course is in `unmet`; no returned combination contains a time conflict.

### 3.5 Workload — `workload.py`

```python
class WorkloadBand(StrEnum):
    LIGHT="light"; MEDIUM="medium"; HEAVY="heavy"

class WorkloadScore(BaseModel, frozen=True):
    raw: int                # sum(difficulty * credits)
    band: WorkloadBand
    per_course: dict[str, int]

def score_workload(planned: list[PlannedCourse], courses: dict[str, Course],
                   term: Term, year: int) -> WorkloadScore: ...
```
**Rules:** deterministic; thresholds for bands defined here (document the cutoffs). Per a single term. **Not a model.**
**Acceptance:** identical input → identical band; raising one course's difficulty never lowers the band.

### 3.6 Greedy fallback planner — `planner.py`

```python
class PlanGoal(StrEnum):
    FASTEST="fastest"; BALANCED="balanced"; EASIEST="easiest"

def greedy_plan(student: Student, audit: AuditResult, dag: PrereqDAG,
                courses: dict[str, Course], goal: PlanGoal,
                horizon_terms: int) -> Plan: ...
```
**Rules:** produces a **verifier-valid** plan with no LLM. Respects DAG order, credit cap, offered terms. Used when the LLM loop fails to converge or as a baseline.
**Acceptance:** `validate_plan(greedy_plan(...))` returns `[]` for every seeded fixture and goal.

---

## 4. Prediction & model-server

Separate lean service, **`joblib` + sklearn only** (torch-free and onnx-free — the grad-risk
model ships as `joblib`; see DECISIONS D-P2-003). Refuses to boot if the loaded artifact's
SHA-256 ≠ the model card.

```python
# POST /predict/intent  →  the trained router (15 labels, not 5)
class IntentRequest(BaseModel):
    text: str
    tenant_id: UUID
class IntentResponse(BaseModel):
    intent: str                # one of 15 labels — see DATA.md §1 for the full set
    confidence: float          # 0..1; the router thresholds this (D-P2-001)

# POST /predict/grad-risk  →  9 engine-computed numeric features
class GradRiskRequest(BaseModel):
    cumulative_gpa: Decimal; gpa_trend: Decimal
    num_failures: int; num_repeats: int
    progress_rate: Decimal; pct_complete: Decimal
    planned_credits: int; planned_workload_index: int; num_hard_courses: int
class RiskLevel(StrEnum):
    ON_TRACK="on_track"; AT_RISK="at_risk"
class GradRiskResponse(BaseModel):
    level: RiskLevel
    probability: float         # P(at_risk)
    reasons: list[str]         # top feature contributions, fed to LLM mitigation
```
**Rules:** two trained models only (intent classifier + graduation-risk). Workload comes
from the engine (not here). GPA estimate is an LLM call in `services/`, always caveated, never
a model. The 15 intent labels and the 9 grad-risk features are documented in [`DATA.md`](DATA.md).
**Acceptance:** `/healthz` fails on SHA mismatch; both models are logged in MLflow with a
promoted "production" version the server loads at boot.

---

## 5. RAG (`infra/rag/`)

```python
class RetrievedChunk(BaseModel, frozen=True):
    text: str; source: str; score: float

def ingest_catalog(tenant_id: UUID, docs: list[Doc]) -> int: ...   # returns chunks indexed
def retrieve(tenant_id: UUID, query: str, k: int = 8) -> list[RetrievedChunk]: ...  # hybrid + rerank
```
**Rules:** every query tenant-filtered. Hybrid (dense + BM25) then cross-encoder rerank. **Prerequisite facts in any answer are grounded against the DAG, never taken from prose** — the answer layer cross-checks claimed prereqs with `dag.prerequisites_of`.
**Acceptance:** a query in Tenant A never returns Tenant B chunks; a prereq the LLM tries to state that isn't in the DAG is dropped/corrected.

---

## 6. Classifier router (`services/router.py`)

```python
def route(text: str, tenant_id: UUID, intent_threshold: float) -> Route:
    # Route = WORKFLOW(intent) for easy/enumerable, AGENT for hard/ambiguous/multi-step
```
**Rules:** uses the trained intent model (15 labels — [`DATA.md`](DATA.md) §1). A confident
prediction routes straight to the one handler mapped to that label; low-confidence or
multi-intent → AGENT (D-P2-001). **Never replace the trained router with an LLM** — its
purpose is to keep the LLM off cheap decisions.
**Acceptance:** clear single-intent messages route to WORKFLOW; ambiguous/compound messages route to AGENT.

---

## 7. Agent tools (`agent/tools/`)

Every tool has a Pydantic input and output. Read-tools have **no side effects**; write actions go through §8. The agent never writes directly.

```python
# READ / PROPOSE tools (no writes)
audit_degree(student_id) -> AuditResult
propose_plan(PlanRequest) -> ProposedPlans          # generate→validate→repair→rank; returns only VALID plans + risk + explanation
simulate_whatif(WhatIfRequest) -> WhatIfResult       # re-audit vs target program; graduation date + credits/term
predict_risk(plan_id) -> GradRiskResponse + mitigation_text
search_sections(course_codes, term, prefs) -> list[SectionPlan]
# As-built: realised as `propose_sections(course_codes, term, year, excluded_days,
# min_start_hour)` — a READ tool that lists the OPEN sections per course (day/time,
# instructor, seats) and which meet the prefs; the agent reasons over them and passes the
# same prefs to stage_enrollment, where the engine picks matching open, conflict-free
# sections. See DECISIONS D-P6-002 and specs/008-phase-5-frontend-widget-auth/spec.md §11.
rag_search(query) -> Answer(text, sources)
save_plan(plan_id|draft) -> Plan                      # Plan-entity write, NOT a registration write
swap_course(plan_id, drop_code, add_code) -> Plan|list[Violation]

# WRITE actions (delegate to §8 action pattern — require approval)
execute_enrollment(plan_id, section_plan) -> ActionReceipt
waitlist_join(section_id) / waitlist_leave(section_id) -> ActionReceipt
apply_graduation() -> ActionReceipt
request_major_change(target_program) -> ActionReceipt
submit_petition(course_code, justification) -> ActionReceipt   # LLM drafts; engine still blocks auto-enroll
escalate(reason) -> ActionReceipt                              # email handoff summary, no write to registration
```

```python
class PlanRequest(BaseModel):
    student_id: UUID
    scope: Literal["next_term","graduation"]
    hard_constraints: list[str]      # e.g. must-take codes, banned days
    soft_preferences: list[str]
    desired_credits: int | None
    goal: PlanGoal
class ProposedPlans(BaseModel):
    candidates: list[Plan]           # ALL verifier-valid
    risk: dict[UUID, GradRiskResponse]
    workload: dict[UUID, WorkloadScore]
    explanation: str                 # LLM trade-off narrative using feasibility + risk
```
**Rules:** `propose_plan` must only return candidates with `validate_plan(...) == []`. Tool inputs are validated; the agent passes typed models, never free dicts. Loop cap + token budget enforced by the graph.
**Acceptance:** no proposed plan ever fails the verifier; calling a write tool without an approval token raises before any DB write.

---

## 8. The action pattern (`services/actions/`) — every write

> **As-built note (see DESIGN.md §4, DECISIONS D-R-001/003).** The implementation
> realizes "approval" as a persisted state machine on the `actions` table + a
> LangGraph resume, rather than a separate `ApprovalToken` object: a write tool
> *stages* a `pending` action (frozen payload + runtime `thread_id`); the student
> approves via `POST /actions/{id}/approve` authenticated by the **verified widget
> JWT**; `execute_node` runs only on the approved resume and the `approved→executed`
> transition is single-use. The safety property below ("nothing reaches the write
> without explicit, verified approval") holds identically. The `ApprovalToken` shape
> in this section is the contract intent; the state-machine form is the equivalent
> realization.

```python
class ActionType(StrEnum):
    ENROLL="enroll"; WAITLIST_JOIN="waitlist_join"; WAITLIST_LEAVE="waitlist_leave"
    GRADUATION="graduation"; MAJOR_CHANGE="major_change"; PETITION="petition"

class ActionRequest(BaseModel):
    tenant_id: UUID; student_id: UUID
    type: ActionType
    payload: dict                    # type-specific, validated per type
    idempotency_key: str

class ActionReceipt(BaseModel):
    action_id: UUID; status: Literal["committed","rejected"]
    violations: list[Violation] = []
    outbox_event_id: UUID | None

def execute_action(req: ActionRequest, approval: ApprovalToken) -> ActionReceipt: ...
```
**The six steps (identical for every action):**
1. Validate `payload` (Pydantic) for the action type.
2. Engine validates preconditions (eligibility / `validate_plan` / graduation requirements). If violations → return `rejected` with violations, **no write**.
3. Require a valid `ApprovalToken` (issued only after explicit student/registrar approval; scoped to this `action_id` + `idempotency_key`). Missing/invalid → reject before any write.
4. **Single DB transaction:** insert the domain row (enrollment / waitlist / request_queue) **and** the `outbox` event. Commit or roll back together.
5. Insert an `audit_log` row (actor, action, tenant, before/after).
6. Worker publishes the outbox event (email / notification).
**Idempotency:** same `idempotency_key` returns the original receipt, never double-writes.
**Per-action notes:** `PETITION` writes to `request_queue` (status `pending`) — the engine still refuses auto-enroll; it's a human override request. `GRADUATION` requires `AuditResult.remaining_credits == 0` and all groups satisfied. `MAJOR_CHANGE` attaches the C4 impact summary.
**Acceptance (security gate):** a fabricated/missing approval token, or an injected tool call, never reaches step 4. Test once, covers all actions.

---

## 9. Outbox & worker jobs (`workers/`)

```python
class OutboxEvent(BaseModel):
    id: UUID; tenant_id: UUID
    kind: Literal["email","notification"]
    payload: dict
    published_at: datetime | None     # null = unpublished

# Jobs:
publish_outbox()         # poll unpublished, deliver (email/notif), retry+backoff, mark published
sync_capacity()          # refresh section enrolled counts from mock registration DB
process_waitlist()       # seat opens -> notify next in line (via outbox)
evaluate_alerts()        # 4 triggers: seat_open, eligibility_unlocked, risk_threshold, registration_window
auto_replan()            # catalog/section/prereq change -> find STALE plans -> re-audit+re-plan -> notify
```
**Rules:** all jobs tenant-scoped, idempotent, deduped (an alert fires once per (student, trigger, subject)). `auto_replan` marks affected plans `STALE`, recomputes a valid alternative, notifies — never silently rewrites the student's active plan without telling them.
**Acceptance:** outbox delivery is at-least-once with dedupe; a duplicate alert is suppressed; replan only touches plans whose validity actually changed.

---

## 10. Guardrails (`infra/guardrails.py`)

```python
def check_input(text: str, tenant_id: UUID) -> GuardResult     # injection / cross-tenant probe
def redact_output(text: str) -> str                            # PII redaction
class GuardResult(BaseModel):
    allowed: bool; reason: str | None
```
**Rules:** runs on **every** inbound message (input rails) and **every** outbound response (output rails). Platform rails (injection, cross-tenant refusal, PII redaction of keys/emails/national-IDs) are hardcoded and **cannot be weakened by tenant config**. A blocked message yields a safe refusal, never a tool call.
**Acceptance (red-team gate):** every injection probe and cross-tenant probe is refused; a fake API key pasted into chat never appears unredacted in any response, log, or trace.

---

## 11. Tenant isolation

**Rules:**
- RLS policy on every tenant-owned table keyed on a per-request `SET app.tenant_id`.
- Every repository is bound to a `tenant_id` and filters by it in its queries (defense in depth, in addition to RLS).
- pgvector retrieval filters by `tenant_id`.
- Widget auth: public `widget_id` → short-lived signed token (HS256, ≤15 min) bound to `tenant_id` + origin; server validates token **and** `Origin` header against the tenant's allowlist. CORS/CSP are defense-in-depth, never the boundary.
- Platform operator endpoints can provision/suspend/erase tenants but cannot read tenant content.
**Acceptance:** a request authenticated for Tenant A cannot read, write, retrieve, or enroll against Tenant B — proven by integration tests at the API, repository, and pgvector layers.

---

## 12. Selected API surface (`api/`)

> **As-built** (see [`DESIGN.md`](DESIGN.md) §3/§4/§6). The token is minted server-to-server
> by the portal, and approval is a state machine on the `actions` row (no separate
> `ApprovalToken` object / `/actions/execute` endpoint). The shape below is the real surface.

```
# widget (auth: widget JWT)
POST /internal/mint-token           # portal service secret -> short-lived widget JWT (server-to-server)
POST /chat                          # {message} -> assistant turn (guardrailed, traced); may return action_id + pending_approval
POST /actions/{id}/approve          # THE gate: verified widget JWT -> resume graph -> execute_node writes
POST /actions/{id}/reject           # cancel a pending action
# plans
POST /plans  ·  GET /plans  ·  GET /plans/{id}  ·  POST /plans/{id}/activate
# admin / operator (auth: role JWT from POST /auth/login)
POST /admin/rag                     # upload catalog.md / policy.md -> RAG corpus (prose only)
GET/PUT /admin/widget-config        # persona, allowed_origins, enabled_tools
GET  /admin/cost                    # per-tenant LLM/embedding cost   ·   GET /admin/audit
POST /platform/tenants              # provision   ·   .../suspend · .../unsuspend · .../erase
GET  /platform/cost  ·  GET /platform/audit        # aggregate-only, no tenant content
```

The registrar works the institutional-request queue in the **mock SIS portal**, not a Keel
`/admin` route — those requests read as plain SIS data (DECISIONS D-P5-000).
**Rules:** routers are thin (parse, authorize, delegate, serialize); no business logic. Every route tenant-scoped via the auth dependency.

---

## 13. Eval gates (`tests/eval/`) — what each asserts

Thresholds in `eval_thresholds.yaml`; report JSON → MinIO each run; diffed vs last green build.

| Gate | Asserts |
|------|---------|
| **planner_correctness** | every plan in the golden set passes `validate_plan` == [] AND every intentionally-broken plan yields the expected violation |
| **intent_f1** | macro-F1 ≥ threshold on held-out test set; 3-way (ML/DL/LLM) comparison committed |
| **grad_risk** | macro-F1 ≥ threshold AND at-risk recall ≥ threshold (minority class); 3-way committed |
| **tool_selection** | agent picks the correct tool (or correctly none) on the message golden set ≥ threshold |
| **rag_ragas** | RAGAS faithfulness, answer relevancy, context recall/precision ≥ thresholds on 25 triples |
| **guardrails_redteam** | 100% of injection + cross-tenant probes refused; PII never leaks; no unapproved write executes |
| **smoke** | `docker compose up` from clean clone → all healthchecks pass |

**Acceptance:** CI fails the PR if any gate regresses below its committed threshold.

---

## How to use this file with Claude Code

1. Before building a component, read its section here; if a contract is missing or vague, **extend this file first**, then implement.
2. For engine components (§3) and the action pattern (§8), **the human writes the edge-case tests against the Acceptance bullets before the implementation is accepted.**
3. When a decision diverges from a spec, update the spec and log it in `DECISIONS.md`.

---

## Phase 4 — Advising, Guidance & Institutional Requests

Full contracts for Phase 4 components (C1–C4, E1–E2, F1–F4) are specified in
`specs/007-phase-4-advise-request/spec.md`. The Pydantic In/Out schemas live in
`src/keel/domain/schemas_day5.py`. Key invariants:

- **C1–C4, E1, E2-chat**: read-only; no writes; LLM narrates engine numbers.
- **E2-save** (`save_career_roadmap`): routes through `propose→verify→repair` loop;
  only a verifier-valid plan is persisted (see `ENGINE.md`).
- **F1–F4**: shared action pattern in `services/actions/institutional.py`;
  `approved=False` always (proposal only from the agent); actual writes gate on
  explicit student approval (Day 6).
- **F3** (`submit_petition`): writes a `PETITION` row in `request_queue`, never an
  enrollment row.
- **F4** (`escalate`): email handoff via outbox only; advisor resolved from
  the `advisors` table (RLS-scoped, program-specific → catch-all fallback).

Security properties: see `SECURITY.md §11`.
Design decisions: see `DECISIONS.md` §"Phase 4".