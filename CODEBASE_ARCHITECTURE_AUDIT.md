# Codebase Architecture Audit — Keel

> **Scope:** Behavior-preserving architectural refactor (Phase 1 — audit only).
> **Non-negotiable:** Nothing in this document proposes changing behavior, business logic,
> APIs, prompts, tool names/signatures, SQL semantics, schema, routes, env vars, outputs, or tests.
> Every proposal below is a **move / extract**, never a rewrite.
>
> **Source of truth:** `docs/` and root Markdown (esp. `docs/reference/ENGINEERING_RULES.md`,
> `CLAUDE.md`). Where code contradicts docs, this audit *reports* the discrepancy and stops —
> it does not "fix" docs to match code.
>
> **Status:** Awaiting approval of the roadmap before any Phase 2 refactor begins.

---

## 1. Executive summary

Keel is a well-documented, layered FastAPI monolith with a genuinely clean **domain engine**
(`domain/engine/` has **zero** SQL and zero framework imports — exactly as `CLAUDE.md §8` requires).
The architecture *documented* in `CLAUDE.md §5` (`api → services → repositories → domain`, infra via DI)
is sound. The problem is **the code has drifted from its own documented layering**, concentrated in
three places:

1. **The repository layer is barely used.** Only two repositories exist
   (`LedgerRepository`, `ActionsRepository`). Meanwhile raw SQL (`sa.text(...)` / `.execute(...)`)
   appears **~210 times across every other layer** — services (94), agent tools (48), workers (34),
   and API routers (29). `CLAUDE.md §5` and `ENGINEERING_RULES.md §10` both say persistence is a
   repository concern; today it is smeared across the whole stack.

2. **The agent tools are god-functions.** `agent/tools/planning.py` (1717 LOC),
   `advising.py` (1234), and `enrollment.py` (1006) each bundle *controller + service + repository +
   presenter + LLM-orchestration* into single 300–1000-line `make_*_tools(deps)` closures. The target
   architecture (both the user's brief and `CLAUDE.md §5`) says tools must be **thin controllers**.

3. **Presentation and LLM orchestration are inlined.** Markdown card builders, widget-payload
   construction, prompt assembly, and LLM ranking/repair loops live inside tool closures and services
   rather than in dedicated `presenters/` and `agent/llm` modules.

The good news: because the domain engine is already clean and the write path already has an
action-pattern skeleton, the refactor is overwhelmingly **mechanical extraction** — low behavioral
risk if done incrementally with the existing test suite as the guard.

**Headline metric:** 10 source files exceed 500 LOC; 3 exceed 1000 LOC (all agent tools).

---

## 2. Methodology

- Enumerated every tracked source file; measured LOC; classified by layer.
- Counted persistence calls (`sa.text(`, `.execute(`) per layer to locate misplaced SQL.
- Read the structural skeleton (functions/classes/tools) of every file >400 LOC.
- Read `docs/reference/ENGINEERING_RULES.md` and `CLAUDE.md` in full as the constraint set.
- Verified the domain engine boundary (no I/O) holds today.

**Excluded from refactor scope** (per brief): `migrations/`, generated files, lock files, `.venv`,
`node_modules`. **Tests are read for coverage assessment but never modified** (brief lists tests under
"do NOT change"). `scripts/` (offline dataset/seed tooling) and `training/`, `ml/`, `model-server/`
are lower priority — they are not in the request path.

**Cohesion score** below is a 1–5 judgment (5 = single clear responsibility; 1 = many unrelated
responsibilities in one unit).

---

## 3. Oversized-file inventory

Thresholds per brief: **500 = Oversized · 800 = High Priority · 1000 = Critical.**

| # | File | LOC | Priority | Dominant problem |
|---|------|-----|----------|------------------|
| 1 | `src/keel/agent/tools/planning.py` | 1717 | 🔴 Critical | Tool = controller+service+repo+presenter+LLM |
| 2 | `frontend/widget/src/ChatWidget.tsx` | 1604 | 🔴 Critical | Chat container + all card components + API + state |
| 3 | `src/keel/agent/tools/advising.py` | 1234 | 🔴 Critical | Same mix + inline SQL loaders |
| 4 | `src/keel/agent/tools/enrollment.py` | 1006 | 🔴 Critical | Same mix + section-resolution SQL |
| 5 | `src/keel/agent/graph.py` | 757 | 🟠 High | Graph wiring + node logic + approval/write branches |
| 6 | `src/keel/services/grad_plans.py` | 726 | 🟠 High | Service + repo (SQL) + presenter (cards) in one module |
| 7 | `src/keel/workers/phase3_jobs.py` | 693 | 🟠 High | 4 unrelated jobs + email + SQL + eligibility recheck |
| 8 | `src/keel/api/routers/admin.py` | 675 | 🟠 High | Router with inline SQL (18×) + schemas + business logic |
| 9 | `frontend/ui/src/components.tsx` | 659 | 🟠 High | Shared UI kit — many components in one file |
| 10 | `src/keel/infra/database/models.py` | 590 | 🟡 Oversized | All ORM models in one file (acceptable, but splittable) |

**Below-threshold but flagged for layer/cohesion violations** (size is only a heuristic):
`agent/tools/institutional.py` (437, inline LLM + staging), `api/routers/platform.py` (420, inline SQL),
`services/actions/institutional.py` (409), `services/router.py` (300, mixes routing + agent invocation +
lite-path handling), `infra/rag.py` (295), `services/ingestion.py` (263).

---

## 4. Systemic (cross-cutting) findings

These recur across many files. Fixing them *is* most of the refactor; the per-file sections
(§5) instantiate them.

### F-1 — Repository layer is under-built; SQL is everywhere 🔴
`sa.text(...)` / `.execute(...)` distribution:

| Layer | SQL sites | Should be |
|-------|-----------|-----------|
| domain | 0 | 0 ✅ |
| repositories | 16 | **all of it** |
| services | 94 | 0 |
| agent tools | 48 | 0 |
| workers | 34 | 0 (via repos) |
| api routers | 29 | 0 |
| infra | 5 | (rag/vault internals — acceptable) |

`CLAUDE.md §5`: *"repositories/ — All DB access. Every method tenant-scoped."*
`ENGINEERING_RULES.md §10`: persistence is a traceable sequence of specific calls.
**Today, agent tools and routers query the DB directly.** This is the single largest violation.

**Direction:** introduce read repositories (e.g. `StudentRepository`, `CatalogRepository`,
`ProgramRepository`, `SectionRepository`, `GradPlanRepository`, `RagDocumentRepository`,
`CostRepository`, `AuditQueryRepository`) that own the existing SQL **verbatim** (same query text,
same params, same result shape). Callers switch from inline `sa.text` to a repository method.
No SQL semantics change.

### F-2 — Agent tools are not thin controllers 🔴
`make_planning_tools`, `make_advising_tools`, `make_enrollment_tools` are 300–1000-line closures that:
open their own DB sessions, run SQL loaders (`_load_student_data`), build engine objects, call the LLM,
run validate/repair loops, compute workload, and format markdown cards — all inline.

Target (brief + `CLAUDE.md §5/§9`): a tool should *receive validated input → call one application
service → return the response → emit widget/event*. **Direction:** extract each tool's body into an
application service (`services/planning_service.py`, `services/advising_service.py`,
`services/enrollment_service.py`); the `@tool` closure keeps only its name, docstring, `args_schema`,
identity resolution, and the service call. Tool names, docstrings, and signatures are **frozen**.

### F-3 — Presentation logic inlined 🟠
Markdown/widget builders live inside tools and services: `_build_plan_cards`, `_build_grad_card`,
`_build_risk_reasons`, `_format_plan` (planning.py); `build_grad_plan_card` (grad_plans.py);
`_staged`, card assembly (enrollment/institutional). `CLAUDE.md` "Presenters" role and the brief both
want these in a dedicated `presenters/` layer. **Direction:** move to `presenters/` (e.g.
`presenters/plan_cards.py`, `presenters/grad_card.py`, `presenters/risk.py`) as pure functions.
Output strings/payloads must be **byte-identical** — these feed widget payloads.

### F-4 — LLM prompt assembly + orchestration inlined 🟠
`_llm_propose_multi`, `_validate_with_repair`, `_llm_rank`, `_llm_propose_grad_paths` (planning.py);
`_llm_mitigation` (advising.py); petition/handoff drafting (institutional.py) build prompts and drive
the LLM inside tool modules. Note `services/prompts/` already exists as the intended home for prompt
templates. **Direction:** move prompt construction + LLM loops into `agent/llm/` modules
(e.g. `agent/llm/plan_proposer.py`, `agent/llm/plan_ranker.py`, `agent/llm/repair.py`).
**Prompt wording is frozen** — this is a cut/paste move, verified by string diff.

### F-5 — Duplicate write-action repository 🟠
Two implementations of the same concept coexist:
- `repositories/core.py::ActionsRepository` (the documented home), and
- `services/actions/__init__.py::ActionRepo` — a second class that *re-imports and delegates to*
  `ActionsRepository` inside its methods.

Agent tools, `graph.py`, `workers`, and `api/routers/actions.py` all import `ActionRepo` from
**services**, meaning the write path bypasses the repository package. **Direction:** collapse to one
(`repositories.core.ActionsRepository`), keep `ActionRepo` as a thin re-export shim during transition
so no import site changes behavior, then migrate imports. Method signatures and the
validate→approve→transaction→audit→outbox sequence stay identical.

### F-6 — Schema/model module fragmentation 🟡
Boundary types are split across `domain/schemas.py`, `domain/schemas_day5.py` (231 LOC, phase-named),
and `domain/models.py` with no clear rule for which lives where. The brief wants framework-facing
schemas in dedicated schema modules, distinct from domain value objects. **Direction (low-risk, later
phase):** rename/regroup by concern (`schemas/tool_io.py`, `schemas/engine.py`, `schemas/requests.py`)
via re-exports so existing import paths keep working. Pure move; no field changes.

### F-7 — Session-management duplication 🟡
Multiple modules re-implement "open a tenant-scoped session" helpers (`tenant_session` in tools,
`_scoped_session`/`_get_session_factory` in `admin.py`, ad-hoc in workers). **Direction:** one shared
`infra/database` helper reused everywhere. Behavior identical (same RLS `SET`, same lifecycle).

---

## 5. Per-file findings (priority order)

> Format per brief: responsibilities · cohesion · SoC violations · dependency violations ·
> misplaced logic · oversized units · risk · what stays / moves / new modules · behavior preservation.

### 5.1 🔴 `agent/tools/planning.py` — 1717 LOC — Critical
- **Responsibilities (too many):** tool metadata; identity resolution; **SQL loading** (via
  `_load_student_data`, shared import); engine orchestration (audit/validate/greedy); **LLM**
  proposal/repair/ranking (`_llm_propose_multi`, `_validate_with_repair`, `_llm_rank`,
  `_llm_propose_grad_paths`); **presentation** (`_build_plan_cards`, `_build_grad_card`,
  `_format_plan`, `_fmt_section_slots`); section conflict/preference math; grad-path validation.
- **Cohesion:** 1/5. **Risk:** High (central to the "verified plan" guarantee).
- **SoC violations:** F-2, F-3, F-4, F-1 all present in one file.
- **Dependency violations:** tool reaches into DB directly (should go tool→service→repo).
- **Oversized units:** `make_planning_tools` ≈ 1000 LOC single function.
- **Stays:** `@tool` declarations (names, docstrings, `args_schema`), identity resolution, service call.
- **Moves:** SQL → `StudentRepository`/`CatalogRepository`/`ProgramRepository`;
  orchestration → `services/planning_service.py`; LLM loops → `agent/llm/plan_*`; card builders →
  `presenters/plan_cards.py`; section helpers → reuse `domain/engine/sections.py` where equivalent
  (verify equivalence first — do **not** merge if semantics differ, report instead).
- **Behavior preservation:** golden planner eval (`tests/unit/test_engine_golden.py`,
  `tests/eval/`) + tool-selection eval must stay green; card strings diffed byte-for-byte.

### 5.2 🔴 `frontend/widget/src/ChatWidget.tsx` — 1604 LOC — Critical
- **Responsibilities:** chat container/state machine; SSE/token handling; **every card component**
  (`PlanCard`, `PlanTabsCard`, `GradPlanView`, `SectionOptionsView`, approval cards); badges;
  modal; avatar; id generation.
- **Cohesion:** 2/5. **Risk:** Medium (UX must be pixel-identical; widget payload contract frozen).
- **SoC violations:** presentation components + container logic + API glue in one file.
- **Stays:** `ChatWidget` container + its state.
- **Moves:** each card/subcomponent → `components/PlanCard.tsx`, `GradPlanView.tsx`,
  `SectionOptionsView.tsx`, `badges.tsx`, `ConfirmModal.tsx`; pure helpers → `format.ts`.
- **Behavior preservation:** identical rendered DOM/props; no restyling; manual smoke of the widget.
  *(Lower priority than the Python agent tools; do after backend extractions.)*

### 5.3 🔴 `agent/tools/advising.py` — 1234 LOC — Critical
- **Responsibilities:** tool metadata; **inline SQL** (`_load_student_data`, `_load_program_engine`,
  transcript/GPA aggregates); engine object construction (`_build_engine_objects`,
  `_build_requirement`); switch-impact computation; **LLM** mitigation; risk-reason presentation;
  two tool factories (`make_advising_tools`, `make_advising_chat_tools`).
- **Cohesion:** 1/5. **Risk:** High (degree audit + risk explanations are user-facing correctness).
- **Misplaced logic:** `_compute_switch_impact`, `_build_requirement` are domain-ish rules living in a
  tool module — candidate to move toward `domain/` (only if provably pure; else a service).
- **Stays:** tool declarations + service calls.
- **Moves:** SQL → repositories; engine assembly → `services/advising_service.py`; risk reasons →
  `presenters/risk.py`; mitigation LLM → `agent/llm/mitigation.py`.
- **Behavior preservation:** grad-risk gate + audit outputs unchanged; reason strings diffed.

### 5.4 🔴 `agent/tools/enrollment.py` — 1006 LOC — Critical
- **Responsibilities:** stage-enrollment/waitlist tools; **section-resolution SQL**
  (`_resolve_sections_for_courses`, `_get_section_row`, `_full_sections_for_course`, `_any_open_seat`,
  `_default_term_year`); conflict/preference math; unavailable-classification LLM; verification.
- **Cohesion:** 1/5. **Risk:** High (writes go through the action pattern — must not be disturbed).
- **Stays:** tool declarations; approval-gated staging call.
- **Moves:** section SQL → `SectionRepository`; resolution/verification → `services/enrollment_service.py`
  (reusing `ActionsRepository`); slot helpers → shared `presenters`/`domain/engine/sections` (verify).
- **Behavior preservation:** `tests/unit/test_write_action_safety.py` and
  `test_institutional_write_safety.py` must stay green; no unapproved write reaches the transaction.

### 5.5 🟠 `agent/graph.py` — 757 LOC — High
- **Responsibilities:** LangGraph assembly (nodes/edges, loop cap, token budget) **and** node bodies
  that branch into approval/write handling (imports `ActionRepo` twice inline at lines 423/578).
- **Cohesion:** 2/5. **Risk:** High (bounded-agent guarantees).
- **Stays:** graph topology, bounds, tool registration.
- **Moves:** approval/write node bodies → a service call; deduplicate the inline `ActionRepo` imports
  (F-5). Keep node/edge structure and budgets **exactly** as-is.

### 5.6 🟠 `services/grad_plans.py` — 726 LOC — High
- **Responsibilities:** service orchestration (save/load/delete/swap/sync active grad plan) + **inline
  SQL** (`_active_grad_plan_row`, `_engine_context`) + **presentation** (`build_grad_plan_card`,
  `serialize_plan_data`, `term_label`, `requirement_label`).
- **Cohesion:** 2/5. **Risk:** Medium-High (grad plan is a persisted entity with a widget contract).
- **Moves:** SQL → `GradPlanRepository`; card/serialize → `presenters/grad_card.py`; keep the
  service as pure orchestration. `GradPlanConflict` and mutation semantics unchanged.

### 5.7 🟠 `workers/phase3_jobs.py` — 693 LOC — High
- **Responsibilities:** four unrelated RQ jobs (`outbox_publisher_job`, `send_outbox_event_job`,
  `capacity_sync_job`, `expiry_sweep_job`) + email sending (`_send_email`) + eligibility recheck +
  in-app notify + SQL.
- **Cohesion:** 1/5. **Risk:** Medium (retry/backoff + outbox semantics must be preserved exactly).
- **Moves:** split into `workers/outbox_jobs.py`, `workers/capacity_jobs.py`, `workers/expiry_jobs.py`;
  email → reuse `infra/email.py`; SQL → repositories; eligibility recheck → engine/service reuse.
  Job entrypoint names + registration must stay identical (RQ references them by path).

### 5.8 🟠 `api/routers/admin.py` — 675 LOC — High
- **Responsibilities:** RAG doc CRUD, widget config, snippet, cost, audit endpoints — **with 18 inline
  SQL statements**, in-router Pydantic schemas, `_safe_filename`/`_resolve_source` helpers, and
  business logic. Violates `CLAUDE.md §7` ("No business logic in routers").
- **Cohesion:** 2/5. **Risk:** Low-Medium (routes/response shapes are the frozen contract).
- **Moves:** SQL → repositories (`RagDocumentRepository`, `WidgetConfigRepository`, `CostRepository`,
  `AuditQueryRepository`); logic → `services/admin_*`; response schemas → `schemas/`. Router keeps
  route decorators, auth deps, and `response_model` **unchanged**.

### 5.9 🟠 `frontend/ui/src/components.tsx` — 659 LOC — High
- Shared UI kit with many components in one file. **Moves:** one component per file under `ui/src/`.
  Pure presentational split; exports preserved via a barrel `index.ts`. Low risk.

### 5.10 🟡 `infra/database/models.py` — 590 LOC — Oversized
- All SQLAlchemy ORM models in one module. Acceptable per `ENGINEERING_RULES §8` example, but
  splittable by bounded context (`models/catalog.py`, `models/enrollment.py`, `models/requests.py`,
  `models/platform.py`) with a re-export barrel so `__tablename__` mappings and import paths are
  unchanged. **Lowest priority** — do only if time permits; no behavioral upside, only readability.

---

## 6. Proposed target module layout (additive; nothing deleted abruptly)

```
src/keel/
  api/routers/        # thin: parse · authorize · delegate · serialize   (SQL removed)
  services/
    planning_service.py       # ← from planning.py tool bodies
    advising_service.py       # ← from advising.py
    enrollment_service.py     # ← from enrollment.py
    grad_plans.py             # slimmed to orchestration only
    admin/…                   # RAG/config/cost/audit use-cases
    actions/                  # write pattern (unchanged shape; ActionRepo → shim)
  repositories/
    students.py catalog.py programs.py sections.py grad_plans.py
    rag_documents.py cost.py audit_queries.py widget_config.py
    core.py                   # ActionsRepository, LedgerRepository (single source)
  presenters/
    plan_cards.py grad_card.py risk.py sections.py
  agent/
    tools/                    # thin @tool closures only
    llm/                      # prompt assembly + LLM loops (wording frozen)
    graph.py                  # topology + bounds only
  schemas/                    # regrouped boundary models (re-exported)
  domain/                     # unchanged (engine stays pristine)
frontend/widget/src/components/  # one card per file
```

Dependency direction after refactor: `agent tool → service → (repositories + domain) → infra`.
Presenters and schemas are cross-cutting leaves depended upon inward-to-outward only.

---

## 7. Incremental refactoring roadmap

Each step is independently shippable, leaves the repo green (`ruff`, `mypy`, `pytest`, eval gates),
and preserves behavior. **No step changes more than one seam at a time.**

**Phase A — Foundations (lowest risk, unlocks the rest)**
- A1. Collapse `ActionRepo`/`ActionsRepository` duplication (F-5) behind a shim; migrate imports.
- A2. Introduce read repositories owning existing SQL **verbatim** (F-1), starting with
  `StudentRepository` + `CatalogRepository` + `ProgramRepository` (used by planning & advising).
- A3. One shared tenant-session helper (F-7).

**Phase B — Presenters (pure, string-diffable)**
- B1. Extract plan/grad/risk card builders → `presenters/` (F-3). Verify byte-identical output.

**Phase C — Agent tool → service extraction (the big win)**
- C1. `planning.py`: move orchestration to `services/planning_service.py`, LLM loops to `agent/llm/`.
- C2. `advising.py` → `services/advising_service.py`.
- C3. `enrollment.py` → `services/enrollment_service.py`.
  (Each: tool keeps name/docstring/`args_schema`; eval + write-safety gates gate the merge.)

**Phase D — Services & workers**
- D1. Slim `services/grad_plans.py` to orchestration; SQL → `GradPlanRepository`.
- D2. Split `workers/phase3_jobs.py` by job; SQL → repos; reuse `infra/email`.

**Phase E — Routers**
- E1. `admin.py` (then `platform.py`): SQL → repos, logic → services, schemas → `schemas/`.

**Phase F — Schema regrouping + ORM split (optional, cosmetic)**
- F1. Regroup `domain/schemas*` (F-6) via re-exports. F2. Split `infra/database/models.py`.

**Phase G — Frontend**
- G1. `ChatWidget.tsx` → per-component files. G2. `ui/components.tsx` → per-component files.

Suggested order: **A → B → C → D → E → G → F.** (Frontend before the cosmetic schema split so the
end-to-end contract is exercised early.)

---

## 8. Behavior-preservation strategy & quality gates

Applied to **every** step before it is considered done:

- ✅ `ruff check` clean · `mypy` clean · full `pytest` green (unit + integration + eval gates).
- ✅ Extracted code is **moved, not rewritten** — verified by diffing the moved block against origin.
- ✅ SQL query text and params **unchanged** — repository methods carry the exact `sa.text(...)`.
- ✅ Prompt strings **unchanged** — `agent/llm` modules hold the exact wording (string diff).
- ✅ Tool names, docstrings, `args_schema` **unchanged** — enforced by `tests/eval/test_tool_selection.py`.
- ✅ Widget payloads / card strings **unchanged** — golden-string comparison before/after.
- ✅ Routes, `response_model`s, env vars, migrations **untouched**.
- ✅ No new dependency edge points "upward" (checked against `CLAUDE.md §5`).

Behavioral guardrails already in the repo that make this safe: planner golden set, intent/grad-risk
eval gates, RAGAS, red-team, and write-action-safety tests. Where a seam has **no** covering test,
the step will add a characterization test *of current behavior* (not a behavior change) before moving.

---

## 9. Discrepancies requiring your decision (per "documentation is source of truth")

I found no case where the code contradicts a *documented behavioral requirement* — the drift is
structural (docs describe the intended layering; code drifted from it), which this refactor is meant
to correct. But three items need your call before Phase 2, because resolving them touches judgment,
not mechanics:

1. **`domain/engine/sections.py` vs. inline section logic in `enrollment.py`/`planning.py`.**
   ✅ **RESOLVED (approved):** *Keep the implementation that is actually on the tested/live code
   path.* The user manually tested the running behavior of every feature, so the exercised copy is
   the source of truth. Before consolidating any duplicate, first determine which copy the flow
   actually invokes; keep that one. If the engine version was never in the flow, do **not** adopt it —
   leaving the duplication is preferable to swapping in an untested equivalent.

2. **`domain/schemas_day5.py` naming (F-6).** ✅ **RESOLVED (approved):** proceed with schema
   regrouping by concern **via re-exports** (old import paths keep working). Deferred to Phase F —
   not part of the approved A–C first tranche.

3. **Frontend scope.** ✅ **RESOLVED (approved):** *A–C first, then reassess.* Frontend (Phase G) and
   Phases D–F are **not** yet approved; they will be revisited after A–C is proven. Current work is
   scoped to Phases **A, B, C** only.

Per `CLAUDE.md` "do not make complex overengineering decisions": every proposal above is extraction of
existing code into its documented home — no new abstractions, no new frameworks, no new patterns
beyond what `CLAUDE.md §5` already prescribes.

---

## 10. Recommendation

Approve **Phases A–C** first (foundations → presenters → agent-tool extraction). They deliver the
largest architectural improvement (kills the three Critical files and the SQL-everywhere violation)
at the lowest behavioral risk, gated by the existing eval/write-safety suites. Phases D–G follow once
the pattern is proven on planning/advising/enrollment.

**Stopping here for approval — no code will change until you approve the roadmap (and answer §9).**
